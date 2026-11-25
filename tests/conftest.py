import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kmshare.channel import derive_master_key  # noqa: E402


class FakeClipboard:
    """Stands in for the Windows clipboard (one per ClipboardSync), so tests never touch the real one."""

    def __init__(self):
        self.seq, self.text, self.kind, self.busy = 1, None, "not_text", False
        self.writes = []

    def copy(self, text, kind="ok"):  # what the user copying something looks like
        self.seq += 1
        self.text, self.kind = text, kind

    def sequence(self):
        return self.seq

    def read(self):
        from kmshare.clipboard import ClipboardBusy

        if self.busy:
            raise ClipboardBusy("busy")
        return (self.text, "ok") if self.kind == "ok" else (None, self.kind)

    def write(self, text):
        from kmshare.clipboard import ClipboardBusy

        if self.busy:
            raise ClipboardBusy("busy")
        self.copy(text)
        self.writes.append(text)


@pytest.fixture(autouse=True)
def fake_clipboard(monkeypatch):
    from kmshare import clipboard

    monkeypatch.setattr(clipboard, "WinClipboard", FakeClipboard)


@pytest.fixture(scope="session")
def master():
    return derive_master_key("correct horse battery staple")


@pytest.fixture(scope="session")
def other_master():
    return derive_master_key("wrong key entirely")


@pytest.fixture
def no_cursor(monkeypatch):
    """Keep tests from moving the real cursor."""
    from kmshare import winput

    state = {"pos": (100, 100), "sets": []}
    monkeypatch.setattr(winput, "cursor_pos", lambda: state["pos"])
    monkeypatch.setattr(winput, "set_cursor_pos", lambda x, y: state["sets"].append((x, y)))
    monkeypatch.setattr(winput, "primary_center", lambda: (960, 540))
    monkeypatch.setattr(winput, "monitors", lambda: [(0, 0, 1920, 1080)])
    return state


class FakeInjector:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def _rec(self, *e):
        with self.lock:
            self.events.append(e)

    def move(self, dx, dy):
        self._rec("move", dx, dy)

    def button(self, name, down):
        self._rec("button", name, down)

    def wheel(self, delta, horizontal=False):
        self._rec("wheel", delta, horizontal)

    def key(self, vk, scan, extended, down):
        self._rec("key", vk, down)

    def release_all(self):
        self._rec("release_all")

    def any_button_held(self):
        return False


@pytest.fixture
def fake_injector():
    return FakeInjector()


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False
