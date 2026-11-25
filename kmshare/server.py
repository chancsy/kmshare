"""Server role: capture this PC's keyboard/mouse and forward it to the connected client.

Press the hotkey (or push past the screen edge) to switch between LOCAL (input stays
here) and REMOTE (input is suppressed here and sent to the client). While REMOTE the
cursor stays frozen where it was; each low-level mouse event reports where the cursor
*would* have moved (even past the screen edge), so ``pt - anchor`` is the relative
motion to send.

Windows computes that target before our hook sees the event, so a move computed from
the frozen spot can still be in the hook when we switch back to LOCAL and place the
cursor elsewhere. Letting it through would yank the cursor back, so for a moment after
returning such stale moves are dropped (see ``_stale_anchor``).
"""

from __future__ import annotations

import ctypes
import logging
import queue
import socket
import sys
import threading
import time
import winsound
from typing import Callable

from . import clipboard, winput
from .channel import AuthError, Channel, ChannelClosed, server_handshake
from .config import Config
from .edge import OPPOSITE, SIDES, EdgeDetector, MonitorCache, entry_point

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
STALE_WINDOW_S = 0.25  # how long after returning to LOCAL moves can still be computed from the frozen spot


def console_choose_side(peer: str, default: str) -> str:
    """Ask in the server console where a newly connected client sits. Enter = ``default``."""
    if not sys.stdin or not sys.stdin.isatty():
        return default
    keys = {s[0]: s for s in SIDES}
    while True:
        try:
            answer = input(f"\nClient {peer} connected. Where is it, seen from this PC?\n"
                           f"  [l]eft  [r]ight  [t]op  [b]ottom   (Enter = {default}): ").strip().lower()
        except EOFError:
            return default
        if not answer:
            return default
        side = keys.get(answer[0]) if answer in keys or answer in SIDES else None
        if side:
            return side
        print("  Please answer l, r, t or b.")


class Server:
    def __init__(self, cfg: Config, master_key: bytes,
                 choose_side: Callable[[str, str], str] | None = None):
        self.cfg = cfg
        self._choose_side = choose_side or console_choose_side
        self._prompt_lock = threading.Lock()
        self._sides: dict[str, str] = {}  # client IP -> side, remembered while the server runs
        self.side: str | None = None       # where the current client sits (None = hotkey only)
        self._edge: EdgeDetector | None = None
        self._mons = MonitorCache(lambda: winput.monitors())
        self._buttons_down: set[str] = set()  # physical mouse buttons held (blocks edge switching)
        self._stale_anchor: tuple[int, int] | None = None  # frozen spot we just left (race guard)
        self._stale_until = 0.0
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
        self._clip = clipboard.ClipboardSync(cfg.clipboard, cfg.clipboard_max_kb * 1024, "the client")
        self._slip_warned = False  # one warning per REMOTE session
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
        self._clip.start()
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
            self._clip.connected(self._send_now)
            self._send(self._clip.caps_msg())
        log.info("client connected: %s  (press %s to control it)", peer, self.cfg.hotkey.text)
        threading.Thread(target=self._reader_loop, args=(chan, gen), daemon=True).start()
        if self.cfg.edge_switch:
            self._setup_layout(addr[0], peer, gen)

    def _setup_layout(self, ip: str, peer: str, gen: int) -> None:
        """Ask (once per client IP) where the client sits, then enable edge switching."""
        side = self._sides.get(ip)
        if side is None:
            with self._prompt_lock:
                side = self._choose_side(peer, self.cfg.client_side)
            self._sides[ip] = side
        with self._lock:
            if gen != self._gen or self._chan is None:
                return  # the client left while we were asking
            self.side = side
            self._edge = EdgeDetector(side, self.cfg.edge)
            self._send({"t": "layout", "side": OPPOSITE[side], **self.cfg.edge.to_msg()})
        log.info("client %s is to the %s: push the cursor past this PC's %s edge to switch (or %s)",
                 peer, side.upper(), side, self.cfg.hotkey.text)

    def _reader_loop(self, chan: Channel, gen: int) -> None:
        try:
            while True:
                msg = chan.recv()
                t = msg.get("t")
                if t == "pong":
                    with self._lock:
                        if gen == self._gen:
                            self._last_pong = time.monotonic()
                elif t == "leave":
                    self._on_client_leave(gen, msg)
                elif t in ("caps", "clip"):
                    with self._lock:
                        if gen != self._gen:
                            continue
                    (self._clip.on_caps if t == "caps" else self._clip.receive)(msg)
        except (ChannelClosed, AuthError, OSError, ValueError) as e:
            self._drop(gen, str(e) or type(e).__name__)

    def _on_client_leave(self, gen: int, msg: dict) -> None:
        """The client's cursor was pushed past its edge back towards us."""
        try:
            fraction = float(msg["f"])
        except (KeyError, TypeError, ValueError):
            return
        with self._lock:
            if gen != self._gen or not self.remote or self.side is None:
                return  # e.g. the hotkey already switched back
            place = entry_point(self.side, fraction, self._mons.get())
            self._leave_remote("edge", place=place)

    def _drop(self, gen: int, reason: str) -> None:
        with self._lock:
            if gen != self._gen or self._chan is None:
                return
            if self.remote:
                self._leave_remote(f"client lost: {reason}")
            self._close_current(reason)

    def _close_current(self, reason: str) -> None:
        chan, self._chan = self._chan, None
        self.side, self._edge = None, None
        self._clip.disconnected()
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

    def _send_now(self, msg: dict) -> None:
        """Queue a message from outside the lock (clipboard worker)."""
        with self._lock:
            if self._chan is not None:
                self._send(msg)

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

    def _enter_remote(self, edge_fraction: float | None = None) -> None:
        """Switch to REMOTE. ``edge_fraction`` set = crossed the edge (client places its cursor)."""
        if self._chan is None:
            log.warning("no client connected, staying LOCAL")
            self._beep(winsound.MB_ICONHAND)
            return
        # Freeze the cursor where it is: suppressed moves don't move it, and their targets
        # relative to this spot give the motion to send.
        self._clip.offer()  # text copied here goes along (worker thread, after "enter")
        self._saved_pos = self._anchor = winput.cursor_pos()
        self._stale_anchor = None
        self._slip_warned = False
        self.remote = True
        if edge_fraction is not None and self.side is not None:
            self._send({"t": "enter", "edge": OPPOSITE[self.side], "f": round(edge_fraction, 4)})
            how = f"{self.side} edge"
        else:
            self._send({"t": "enter"})
            how = "hotkey"
        log.info("-> REMOTE (%s): keyboard/mouse now control %s  (%s to return)", how, self._peer,
                 self.cfg.hotkey.text)
        self._beep(winsound.MB_OK)

    def _leave_remote(self, reason: str, place: tuple[int, int] | None = None) -> None:
        """Switch to LOCAL. ``place`` = where to put the cursor (edge return), else where it was."""
        self.remote = False
        self._send({"t": "rel"})
        target = place or self._saved_pos
        if target is not None and target != self._anchor:
            winput.set_cursor_pos(*target)
            # Moves Windows already computed from the frozen spot may still be in the hook.
            self._stale_anchor = self._anchor
            self._stale_until = time.monotonic() + STALE_WINDOW_S
        if self._edge is not None:
            self._edge.disarm()  # no bounce: move away from the edge before it can switch again
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
            if msg in CLICKS:
                name, down = CLICKS[msg]
                (self._buttons_down.add if down else self._buttons_down.discard)(name)
            elif msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                name = "x1" if (mouse_data >> 16) & 0xFFFF == 1 else "x2"
                (self._buttons_down.add if msg == WM_XBUTTONDOWN else self._buttons_down.discard)(name)

            if not self.remote:
                if msg == WM_MOUSEMOVE and self._is_stale_move(x, y):
                    return True  # computed from the frozen spot: would yank the cursor back
                if msg == WM_MOUSEMOVE and self._edge is not None and self._chan is not None:
                    fraction = self._edge.update(winput.cursor_pos(), (x, y), self._mons.get(),
                                                 buttons_held=bool(self._buttons_down))
                    if fraction is not None:
                        self._enter_remote(edge_fraction=fraction)
                        return True
                return False
            if msg == WM_MOUSEMOVE:
                # Measure from where the cursor really is, not only from the frozen spot: if an
                # event ever got past the hook and moved it, a fixed anchor would add that
                # offset to every later move (client cursor pinned to one edge).
                real = winput.cursor_pos()
                if real != self._anchor:
                    self._on_slip(real)
                dx, dy = x - real[0], y - real[1]
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

    def _on_slip(self, real: tuple[int, int]) -> None:
        """The frozen cursor moved while REMOTE: re-anchor there (no jump on the client).

        Moving it back would turn moves Windows already computed from the slipped spot into
        one big jump. ``_saved_pos`` still holds where to return to on a hotkey switch.
        """
        ax, ay = self._anchor
        dist = round(((real[0] - ax) ** 2 + (real[1] - ay) ** 2) ** 0.5)
        if not self._slip_warned:
            log.warning("server cursor slipped %d px while controlling the client (an input event got "
                        "past the hook, or another program moved it); corrected", dist)
            self._slip_warned = True
        else:
            log.debug("server cursor slipped again: %d px", dist)
        self._anchor = real

    def _is_stale_move(self, x: int, y: int) -> bool:
        """Right after returning to LOCAL: is this move's target computed from the frozen spot?

        A move's target is (where the cursor was) + (motion). If it's nearer the spot we just
        left than the cursor's real position, it was computed before we moved the cursor.
        The guard ends at the first normal move or after ``STALE_WINDOW_S``.
        """
        stale = self._stale_anchor
        if stale is None:
            return False
        if time.monotonic() > self._stale_until:
            self._stale_anchor = None
            return False
        cx, cy = winput.cursor_pos()
        if (x - stale[0]) ** 2 + (y - stale[1]) ** 2 < (x - cx) ** 2 + (y - cy) ** 2:
            return True
        self._stale_anchor = None
        return False

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
