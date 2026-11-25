"""Thin ctypes wrappers for the Win32 cursor / SendInput calls kmshare needs."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
KMSHARE_TAG = 0x4B4D5348  # "KMSH" in dwExtraInfo, marks our injected events

# name -> (down flag, up flag, mouseData)
BUTTONS: dict[str, tuple[int, int, int]] = {
    "left": (0x0002, 0x0004, 0),
    "right": (0x0008, 0x0010, 0),
    "middle": (0x0020, 0x0040, 0),
    "x1": (0x0080, 0x0100, 1),
    "x2": (0x0080, 0x0100, 2),
}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
user32.GetSystemMetrics.restype = ctypes.c_int


def set_dpi_aware() -> None:
    """Use physical pixels everywhere so hook coordinates and SetCursorPos agree."""
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.argtypes = (ctypes.c_void_p,)
        if fn(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except OSError:
        user32.SetProcessDPIAware()


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def set_cursor_pos(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def primary_center() -> tuple[int, int]:
    return user32.GetSystemMetrics(0) // 2, user32.GetSystemMetrics(1) // 2


def _send(*inputs: INPUT) -> bool:
    arr = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) == len(inputs)


def _mouse(flags: int, data: int = 0) -> bool:
    return _send(INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, KMSHARE_TAG)))


def _key(vk: int, scan: int, flags: int) -> bool:
    return _send(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, flags, 0, KMSHARE_TAG)))


class Injector:
    """Replays received events on this PC and remembers what is held down."""

    def __init__(self) -> None:
        self._keys: dict[int, tuple[int, bool]] = {}
        self._buttons: set[str] = set()

    def move(self, dx: int, dy: int) -> None:
        # SetCursorPos lands on exact pixels (no double pointer acceleration) and
        # still generates WM_MOUSEMOVE, so hover/drag work.
        x, y = cursor_pos()
        set_cursor_pos(x + dx, y + dy)

    def button(self, name: str, down: bool) -> None:
        down_flag, up_flag, data = BUTTONS[name]
        _mouse(down_flag if down else up_flag, data)
        (self._buttons.add if down else self._buttons.discard)(name)

    def wheel(self, delta: int, horizontal: bool = False) -> None:
        _mouse(MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, delta)

    def key(self, vk: int, scan: int, extended: bool, down: bool) -> None:
        flags = (KEYEVENTF_EXTENDEDKEY if extended else 0) | (0 if down else KEYEVENTF_KEYUP)
        _key(vk, scan, flags)
        if down:
            self._keys[vk] = (scan, extended)
        else:
            self._keys.pop(vk, None)

    def release_all(self) -> None:
        for vk, (scan, extended) in list(self._keys.items()):
            self.key(vk, scan, extended, down=False)
        for name in list(self._buttons):
            self.button(name, down=False)
