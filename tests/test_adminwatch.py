"""Warning when an admin window has focus (UIPI hides its input from kmshare)."""

import logging

import pytest

from kmshare import adminwatch
from kmshare.adminwatch import AdminFocusWatch

NOTEPAD, HOYO, LOCKED = (1, 100, "Untitled - Notepad"), (2, 200, "HoYoPlay"), (3, 300, "Protected")
INFO = {100: (False, "notepad.exe"), 200: (True, "HYP.exe"), 300: (None, "")}


def watch(role, focus):
    return AdminFocusWatch(role, "Ctrl+Shift+F12", fetch=lambda: focus[0], info=INFO.__getitem__, own_pid=1)


def messages(caplog):
    out = [r.getMessage() for r in caplog.records]
    caplog.clear()
    return out


def test_server_warns_once_then_says_when_focus_leaves(caplog):
    caplog.set_level(logging.INFO, "kmshare.adminwatch")
    focus = [NOTEPAD]
    w = watch("server", focus)
    w.check()
    assert messages(caplog) == []                              # normal window: silent
    focus[0] = HOYO
    w.check(), w.check()                                       # same window twice: one warning
    (msg,) = messages(caplog)
    assert msg.startswith('focused window "HoYoPlay" (HYP.exe) runs as administrator:')
    assert "edge switching and Ctrl+Shift+F12 don't work" in msg
    focus[0] = NOTEPAD
    w.check()
    assert messages(caplog) == ["admin window lost focus: kmshare sees the mouse and keyboard again"]


def test_client_wording_and_unknown_elevation(caplog):
    caplog.set_level(logging.INFO, "kmshare.adminwatch")
    focus = [LOCKED]
    watch("client", focus).check()
    (msg,) = messages(caplog)
    assert msg.startswith('focused window "Protected" may run as administrator (Windows won\'t say):')
    assert "clicks and keys from the server can't reach it" in msg


@pytest.mark.parametrize("fg", [None, (5, 1, "kmshare console")])   # no focus / our own window
def test_no_focus_or_own_window_is_silent(caplog, fg):
    caplog.set_level(logging.INFO, "kmshare.adminwatch")
    watch("server", [fg]).check()
    assert messages(caplog) == []


def test_real_win32_calls_work_for_this_process():
    import os
    elevated, exe = adminwatch.process_info(os.getpid())
    assert elevated is adminwatch.self_elevated() and exe.lower().startswith("python")
    assert adminwatch.foreground() is None or len(adminwatch.foreground()) == 3
