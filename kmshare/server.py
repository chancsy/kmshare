"""Server role: capture this PC's keyboard/mouse and forward it to the connected client.

Press the hotkey to switch between LOCAL (input stays here) and REMOTE (input is
suppressed here and sent to the client). While REMOTE the cursor is parked at the
centre of the primary screen; each low-level mouse event reports where the
cursor *would* have moved, so ``pt - anchor`` is the relative motion to send.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import socket
import threading
import time
import winsound

from . import winput
from .channel import AuthError, Channel, ChannelClosed, server_handshake
from .config import Config

log = logging.getLogger("kmshare.server")

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x0200, 0x020A, 0x020E
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
CLICKS = {
    0x0201: ("left", True), 0x0202: ("left", False),
    0x0204: ("right", True), 0x0205: ("right", False),
    0x0207: ("middle", True), 0x0208: ("middle", False),
}
LLKHF_EXTENDED, LLKHF_LOWER_IL_INJECTED, LLKHF_INJECTED = 0x01, 0x02, 0x10
LLMHF_INJECTED, LLMHF_LOWER_IL_INJECTED = 0x01, 0x02


class Server:
    def __init__(self, cfg: Config, master_key: bytes):
        self.cfg = cfg
        self.port = cfg.port
        self._master = master_key
        self._lock = threading.RLock()
        self._hs_gate = threading.Lock()  # one handshake at a time -> throttles key guessing
        self._outq: queue.Queue[tuple[int, dict]] = queue.Queue()
        self._chan: Channel | None = None
        self._peer = ""
        self._gen = 0  # bumps per connection; stale queued events are dropped
        self._last_pong = self._last_ping = 0.0
        self.remote = False
        self._anchor = (0, 0)
        self._saved_pos: tuple[int, int] | None = None
        self._pressed: set[int] = set()     # physically held keys (for hotkey detection)
        self._local_down: set[int] = set()  # pressed while LOCAL: their key-ups stay local
        self._swallow_up: set[int] = set()  # hotkey trigger: eat its key-up too
        self._kb = self._mouse = None

    # ------------------------------------------------------------------ lifecycle
    def serve(self, stop: threading.Event | None = None, hooks: bool = True) -> None:
        stop = stop or threading.Event()
        srv = socket.create_server((self.cfg.bind, self.cfg.port))
        srv.settimeout(0.5)
        self.port = srv.getsockname()[1]
        threading.Thread(target=self._sender_loop, args=(stop,), name="kmshare-send", daemon=True).start()
        if hooks:
            self._start_hooks()
        log.info("server listening on %s:%d, waiting for a client ...", self.cfg.bind, self.port)
        try:
            while not stop.is_set():
                try:
                    sock, addr = srv.accept()
                except TimeoutError:
                    pass
                else:
                    threading.Thread(target=self._handshake, args=(sock, addr), daemon=True).start()
                self._heartbeat()
        finally:
            stop.set()
            with self._lock:
                if self.remote:
                    self._leave_remote("server stopping")
                self._close_current("server stopped")
            self._stop_hooks()
            srv.close()

    def _start_hooks(self) -> None:
        from pynput import keyboard, mouse  # imported lazily so tests don't need hooks

        self._kb = keyboard.Listener(win32_event_filter=self._kb_filter)
        self._mouse = mouse.Listener(win32_event_filter=self._mouse_filter)
        self._kb.start()
        self._mouse.start()
        self._kb.wait()
        self._mouse.wait()

    def _stop_hooks(self) -> None:
        for listener in (self._kb, self._mouse):
            if listener is not None:
                listener.stop()

    # ------------------------------------------------------------------ connections
    def _handshake(self, sock: socket.socket, addr) -> None:
        peer = f"{addr[0]}:{addr[1]}"
        with self._hs_gate:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                chan = server_handshake(sock, self._master)
            except (AuthError, OSError) as e:
                log.warning("rejected %s: %s", peer, e)
                sock.close()
                time.sleep(1.0)
                return
        with self._lock:
            if self._chan is not None:
                if self.remote:
                    self._leave_remote("client replaced")
                self._close_current(f"replaced by {peer}")
            self._gen += 1
            gen = self._gen
            self._chan, self._peer = chan, peer
            self._last_pong = time.monotonic()
        log.info("client connected: %s  (press %s to control it)", peer, self.cfg.hotkey.text)
        threading.Thread(target=self._reader_loop, args=(chan, gen), daemon=True).start()

    def _reader_loop(self, chan: Channel, gen: int) -> None:
        try:
            while True:
                if chan.recv().get("t") == "pong":
                    with self._lock:
                        if gen == self._gen:
                            self._last_pong = time.monotonic()
        except (ChannelClosed, AuthError, OSError, ValueError) as e:
            self._drop(gen, str(e) or type(e).__name__)

    def _drop(self, gen: int, reason: str) -> None:
        with self._lock:
            if gen != self._gen or self._chan is None:
                return
            if self.remote:
                self._leave_remote(f"client lost: {reason}")
            self._close_current(reason)

    def _close_current(self, reason: str) -> None:
        chan, self._chan = self._chan, None
        if chan is not None:
            chan.close()
            log.info("client %s disconnected (%s)", self._peer, reason)

    def _heartbeat(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._chan is None:
                return
            if now - self._last_pong > self.cfg.peer_timeout:
                self._drop(self._gen, "no response from client")
            elif now - self._last_ping >= self.cfg.ping_interval:
                self._last_ping = now
                self._send({"t": "ping"})

    def _send(self, msg: dict) -> None:
        """Queue a message for the current connection (call with the lock held)."""
        self._outq.put((self._gen, msg))

    def _sender_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                batch = [self._outq.get(timeout=0.5)]
            except queue.Empty:
                continue
            while len(batch) < 512:
                try:
                    batch.append(self._outq.get_nowait())
                except queue.Empty:
                    break
            with self._lock:
                chan, gen = self._chan, self._gen
            if chan is None:
                continue
            try:
                for g, msg in _coalesce_moves(batch):
                    if g == gen:
                        chan.send(msg)
            except OSError as e:
                self._drop(gen, f"send failed: {e}")

    # ------------------------------------------------------------------ mode switching
    def _toggle(self) -> None:
        if self.remote:
            self._leave_remote("hotkey")
        else:
            self._enter_remote()

    def _enter_remote(self) -> None:
        if self._chan is None:
            log.warning("no client connected, staying LOCAL")
            self._beep(winsound.MB_ICONHAND)
            return
        self._saved_pos = winput.cursor_pos()
        self._anchor = winput.primary_center()
        winput.set_cursor_pos(*self._anchor)
        self.remote = True
        self._send({"t": "enter"})
        log.info("-> REMOTE: keyboard/mouse now control %s  (%s to return)", self._peer, self.cfg.hotkey.text)
        self._beep(winsound.MB_OK)

    def _leave_remote(self, reason: str) -> None:
        self.remote = False
        self._send({"t": "rel"})
        if self._saved_pos is not None:
            winput.set_cursor_pos(*self._saved_pos)
        log.info("<- LOCAL (%s)", reason)
        self._beep(winsound.MB_ICONASTERISK)

    def _beep(self, kind: int) -> None:
        if self.cfg.beep:
            winsound.MessageBeep(kind)  # asynchronous, safe inside a hook

    # ------------------------------------------------------------------ input logic
    def on_key(self, vk: int, scan: int, extended: bool, down: bool) -> bool:
        """Handle one physical key event. Returns True to suppress it locally."""
        with self._lock:
            if down:
                self._pressed.add(vk)
            else:
                self._pressed.discard(vk)

            if vk in self._swallow_up:  # hotkey trigger: eat auto-repeat and the key-up
                if not down:
                    self._swallow_up.discard(vk)
                return True
            if down and self.cfg.hotkey.matches(vk, self._pressed):
                self._swallow_up.add(vk)
                self._toggle()
                return True

            if self.remote:
                if not down and vk in self._local_down:
                    self._local_down.discard(vk)  # pressed before switching: release it here
                    return False
                self._send({"t": "k", "v": vk, "s": scan, "e": extended, "d": down})
                return True

            if down:
                self._local_down.add(vk)
            else:
                self._local_down.discard(vk)
            return False

    def on_mouse(self, msg: int, x: int, y: int, mouse_data: int) -> bool:
        """Handle one physical mouse event. Returns True to suppress it locally."""
        with self._lock:
            if not self.remote:
                return False
            if msg == WM_MOUSEMOVE:
                dx, dy = x - self._anchor[0], y - self._anchor[1]
                if dx or dy:
                    self._send({"t": "m", "x": dx, "y": dy})
            elif msg in CLICKS:
                name, down = CLICKS[msg]
                self._send({"t": "b", "b": name, "d": down})
            elif msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                name = "x1" if (mouse_data >> 16) & 0xFFFF == 1 else "x2"
                self._send({"t": "b", "b": name, "d": msg == WM_XBUTTONDOWN})
            elif msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                delta = ctypes.c_short((mouse_data >> 16) & 0xFFFF).value
                self._send({"t": "w", "d": delta, "h": msg == WM_MOUSEHWHEEL})
            return True

    # pynput filters run on the hook threads: keep them fast and never let them raise
    # anything but the suppress signal, or pynput tears the hook down.
    def _kb_filter(self, msg, data):
        if data.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED):
            return False
        try:
            suppress = self.on_key(data.vkCode, data.scanCode, bool(data.flags & LLKHF_EXTENDED),
                                   msg in (WM_KEYDOWN, WM_SYSKEYDOWN))
        except Exception:
            log.exception("keyboard hook error")
            suppress = False
        if suppress:
            self._kb.suppress_event()
        return False

    def _mouse_filter(self, msg, data):
        if data.flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED):
            return False
        try:
            suppress = self.on_mouse(msg, data.pt.x, data.pt.y, data.mouseData)
        except Exception:
            log.exception("mouse hook error")
            suppress = False
        if suppress:
            self._mouse.suppress_event()
        return False


def _coalesce_moves(batch: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    """Merge runs of consecutive mouse moves into one message."""
    out: list[tuple[int, dict]] = []
    for gen, msg in batch:
        if msg["t"] == "m" and out and out[-1][0] == gen and out[-1][1]["t"] == "m":
            prev = out[-1][1]
            out[-1] = (gen, {"t": "m", "x": prev["x"] + msg["x"], "y": prev["y"] + msg["y"]})
        else:
            out.append((gen, msg))
    return out
