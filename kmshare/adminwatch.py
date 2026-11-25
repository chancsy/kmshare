"""Warn when the focused window runs as administrator and kmshare doesn't.

Windows (UIPI) hides input aimed at an elevated window from a normal-user
process: the server's hooks stop seeing the mouse and keyboard (edge switching
and the hotkey stop working, the cursor stays "trapped"), and on the client
injected clicks and keys don't reach that window. kmshare can't get around this
without running as administrator, so it says what is happening instead.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger("kmshare.adminwatch")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20  # TOKEN_INFORMATION_CLASS.TokenElevation

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD))
advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
advapi32.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD))


def _token_elevated(process: int) -> bool | None:
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        value, size = wintypes.DWORD(), wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, TOKEN_ELEVATION, ctypes.byref(value), 4, ctypes.byref(size)):
            return None
        return bool(value.value)
    finally:
        kernel32.CloseHandle(token)


def self_elevated() -> bool:
    return bool(_token_elevated(kernel32.GetCurrentProcess()))


def process_info(pid: int) -> tuple[bool | None, str]:
    """(elevated, exe name) for a process. elevated is None when Windows won't say."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None, ""  # access denied: elevated or protected (anti-cheat, system)
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        name = os.path.basename(buf.value) if kernel32.QueryFullProcessImageNameW(
            handle, 0, buf, ctypes.byref(size)) else ""
        return _token_elevated(handle), name
    finally:
        kernel32.CloseHandle(handle)


def foreground() -> tuple[int, int, str] | None:
    """(window handle, process id, title) of the focused window, or None."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None  # lock screen, UAC prompt, or focus changing
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    return int(hwnd), pid.value, title.value


class AdminFocusWatch:
    """Logs once each time an admin window gets focus, and once when it loses it."""

    def __init__(self, role: str, hotkey_text: str = "the hotkey",
                 fetch: Callable[[], tuple[int, int, str] | None] = foreground,
                 info: Callable[[int], tuple[bool | None, str]] = process_info,
                 own_pid: int | None = None) -> None:
        self.role = role
        self.hotkey_text = hotkey_text
        self._fetch = fetch
        self._info = info
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._hwnd: int | None = None
        self._warned = False

    def check(self) -> None:
        fg = self._fetch()
        if fg is None:
            return
        hwnd, pid, title = fg
        if hwnd == self._hwnd:
            return
        self._hwnd = hwnd
        elevated, exe = (False, "") if pid == self._own_pid else self._info(pid)
        if elevated is False:
            if self._warned:
                log.info("admin window lost focus: %s", "kmshare sees the mouse and keyboard again"
                         if self.role == "server" else "clicks and keys reach the focused window again")
                self._warned = False
            return
        what = f'"{title or "untitled"}"' + (f" ({exe})" if exe else "")
        runs = "runs as administrator" if elevated else "may run as administrator (Windows won't say)"
        if self.role == "server":
            effect = (f"kmshare can't see the mouse or keyboard while it has focus, so edge switching and "
                      f"{self.hotkey_text} don't work. Click another window, or run kmshare as administrator.")
        else:
            effect = ("clicks and keys from the server can't reach it. "
                      "Run kmshare as administrator on this PC to control it.")
        log.warning("focused window %s %s: %s", what, runs, effect)
        self._warned = True

    def run(self, stop: threading.Event, interval: float = 0.5) -> None:
        while not stop.wait(interval):
            try:
                self.check()
            except Exception:  # a watcher must never take kmshare down
                log.debug("admin focus check failed", exc_info=True)


def start(role: str, stop: threading.Event, hotkey_text: str = "the hotkey") -> bool:
    """Start the watcher thread unless kmshare already runs as administrator."""
    if self_elevated():
        log.info("running as administrator: admin windows can be controlled too")
        return False
    watch = AdminFocusWatch(role, hotkey_text)
    threading.Thread(target=watch.run, args=(stop,), name="kmshare-adminwatch", daemon=True).start()
    return True
