import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kmshare.channel import derive_master_key  # noqa: E402


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
