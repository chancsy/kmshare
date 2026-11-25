"""Text clipboard sharing: Win32 access, chunked messages, and a worker thread.

The PC giving up control offers its clipboard; the other PC puts the text on its own.
Only plain text, only when it changed since the last sync, only up to ``max_bytes``.
All clipboard calls run on one worker thread, never inside the input hooks.

Messages (inside the encrypted channel):
    {"t": "caps", "clip": true}                        after connecting: is sharing on here?
    {"t": "clip", "id": 3, "i": 0, "n": 2, "d": "..."}  chunk i of n of clipboard text id
"""

from __future__ import annotations

import ctypes
import itertools
import logging
import queue
import threading
import time
from ctypes import wintypes
from typing import Callable, Protocol

log = logging.getLogger("kmshare.clipboard")

# 8000 characters is at most 48 KB of JSON (\uXXXX escapes are 6 bytes), under the 64 KB frame limit.
CHUNK_CHARS = 8000
OPEN_TRIES, OPEN_WAIT_S = 5, 0.02  # another app may hold the clipboard open for a moment

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
HWND_MESSAGE = wintypes.HWND(-3)
# Set by password managers and similar apps: "don't sync or record this".
PRIVATE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")


class ClipboardBusy(Exception):
    """Another app kept the clipboard open."""


class ClipboardIO(Protocol):
    def sequence(self) -> int: ...
    def read(self) -> tuple[str | None, str]: ...  # (text, "ok" | "not_text" | "private")
    def write(self, text: str) -> None: ...


# --------------------------------------------------------------------------- Win32
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = (wintypes.HWND,)
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.RegisterClipboardFormatW.argtypes = (wintypes.LPCWSTR,)
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = (wintypes.HWND,)
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalFree.restype = wintypes.HGLOBAL


def _open(hwnd) -> None:
    for attempt in range(OPEN_TRIES):
        if user32.OpenClipboard(hwnd):
            return
        if attempt < OPEN_TRIES - 1:
            time.sleep(OPEN_WAIT_S)
    raise ClipboardBusy("another app has the clipboard open")


class WinClipboard:
    """The real Windows clipboard (plain text only)."""

    def __init__(self) -> None:
        self._private = [user32.RegisterClipboardFormatW(name) for name in PRIVATE_FORMATS]

    def sequence(self) -> int:
        return user32.GetClipboardSequenceNumber()

    def read(self) -> tuple[str | None, str]:
        _open(None)
        try:
            if any(f and user32.IsClipboardFormatAvailable(f) for f in self._private):
                return None, "private"
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return None, "not_text"
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            ptr = kernel32.GlobalLock(handle) if handle else None
            if not ptr:
                return None, "not_text"
            try:
                return ctypes.wstring_at(ptr), "ok"
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def write(self, text: str) -> None:
        # SetClipboardData needs the clipboard opened by a window; a hidden message-only one will do.
        hwnd = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)
        try:
            _open(hwnd)
            try:
                if not user32.EmptyClipboard():
                    raise ctypes.WinError(ctypes.get_last_error())
                buf = ctypes.create_unicode_buffer(text)
                size = ctypes.sizeof(buf)
                mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
                if not mem:
                    raise MemoryError("GlobalAlloc failed")
                ptr = kernel32.GlobalLock(mem)
                ctypes.memmove(ptr, buf, size)
                kernel32.GlobalUnlock(mem)
                if not user32.SetClipboardData(CF_UNICODETEXT, mem):
                    kernel32.GlobalFree(mem)  # still ours only if Windows didn't take it
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                user32.CloseClipboard()
        finally:
            if hwnd:
                user32.DestroyWindow(hwnd)


# --------------------------------------------------------------------------- chunks
def split(text: str, msg_id: int) -> list[dict]:
    parts = [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)] or [""]
    return [{"t": "clip", "id": msg_id, "i": i, "n": len(parts), "d": p} for i, p in enumerate(parts)]


class Assembler:
    """Puts chunks back together. A gap, a new id mid-way or a bad field drops that text."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._id: object = None
        self._parts: list[str] = []
        self._n = 0

    def feed(self, msg: dict) -> str | None:
        try:
            msg_id, i, n, d = msg["id"], int(msg["i"]), int(msg["n"]), str(msg["d"])
        except (KeyError, TypeError, ValueError):
            self.reset()
            return None
        if i == 0:
            self._id, self._parts, self._n = msg_id, [], n
        elif msg_id != self._id or i != len(self._parts) or n != self._n:
            self.reset()  # missing or out-of-order chunk
            return None
        self._parts.append(d)
        if len(self._parts) == self._n:
            text = "".join(self._parts)
            self.reset()
            return text
        return None


def _size(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1024 * 1024:
        return f"{n_bytes / 1024:.1f} KB"
    return f"{n_bytes / 1024 / 1024:.1f} MB"


# --------------------------------------------------------------------------- sync
class ClipboardSync:
    """One per PC. ``offer()`` when giving up control, ``receive()`` for incoming chunks."""

    def __init__(self, enabled: bool, max_bytes: int, peer_name: str, io: ClipboardIO | None = None) -> None:
        self.enabled = enabled
        self.max_bytes = max_bytes
        self.peer_name = peer_name
        self.peer_enabled = False
        self.send: Callable[[dict], None] | None = None  # set while connected
        self._io = io or WinClipboard()
        self._asm = Assembler()
        self._ids = itertools.count(1)
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        # What's on the clipboard when kmshare starts is never sent; only later copies are.
        self._last_seq = self._safe_sequence()

    @property
    def active(self) -> bool:
        return self.enabled and self.peer_enabled and self.send is not None

    def caps_msg(self) -> dict:
        return {"t": "caps", "clip": self.enabled}

    def connected(self, send: Callable[[dict], None]) -> None:
        self.send, self.peer_enabled = send, False
        self._asm.reset()

    def disconnected(self) -> None:
        self.send, self.peer_enabled = None, False
        self._asm.reset()

    def on_caps(self, msg: dict) -> None:
        self.peer_enabled = bool(msg.get("clip"))
        if not self.enabled:
            log.info("clipboard sharing off (turned off on this PC)")
        elif not self.peer_enabled:
            log.info("clipboard sharing off (turned off on %s)", self.peer_name)
        else:
            log.info("clipboard sharing on: copied text follows when control switches (up to %s)",
                     _size(self.max_bytes))

    # called from the hook / network threads: only queue work
    def offer(self) -> None:
        if self.active:
            self._submit(self._do_offer)

    def receive(self, msg: dict) -> None:
        if not self.enabled:
            return
        text = self._asm.feed(msg)
        if text is not None:
            self._submit(lambda: self._do_apply(text))

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, name="kmshare-clipboard", daemon=True)
            self._thread.start()

    def drain(self, timeout: float = 2.0) -> bool:
        """Wait until queued work is done (tests)."""
        done = threading.Event()
        self._submit(done.set)
        return done.wait(timeout)

    # worker thread
    def _submit(self, job: Callable[[], None]) -> None:
        self._jobs.put(job)
        if self._thread is None:
            self.start()

    def _worker(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception:  # never let one bad clipboard take the thread down
                log.exception("clipboard error")

    def _safe_sequence(self) -> int | None:
        try:
            return self._io.sequence()
        except Exception:
            return None

    def _do_offer(self) -> None:
        send = self.send
        if not self.active or send is None:
            return
        seq = self._io.sequence()
        if seq == self._last_seq:
            return  # nothing new since the last sync (or since we wrote what the peer sent)
        try:
            text, why = self._io.read()
        except ClipboardBusy:
            log.warning("clipboard: another app had the clipboard open, not sent (tries again on the next switch)")
            return
        self._last_seq = seq  # look at each new clipboard once, sent or not
        if why == "private":
            log.info("clipboard: marked private by the app that copied it (e.g. a password manager), not sent")
            return
        if why != "ok":
            log.info("clipboard: not text, not sent")
            return
        if not text:
            return
        size = len(text.encode("utf-8", "surrogatepass"))
        if size > self.max_bytes:
            log.warning("clipboard: %s of text is over the %s limit, not sent", _size(size), _size(self.max_bytes))
            return
        try:
            for chunk in split(text, next(self._ids)):
                send(chunk)
        except OSError as e:  # link dropped mid-way; the reconnect starts fresh
            log.warning("clipboard: link lost while sending (%s)", e)
            return
        log.info("clipboard: sent %s of text to %s", _size(size), self.peer_name)

    def _do_apply(self, text: str) -> None:
        size = len(text.encode("utf-8", "surrogatepass"))
        if size > self.max_bytes:
            log.warning("clipboard: received %s, over this PC's %s limit, not applied",
                        _size(size), _size(self.max_bytes))
            return
        try:
            self._io.write(text)
        except ClipboardBusy:
            log.warning("clipboard: another app had the clipboard open, received text not applied")
            return
        self._last_seq = self._io.sequence()  # don't send it straight back
        log.info("clipboard: got %s of text from %s", _size(size), self.peer_name)
