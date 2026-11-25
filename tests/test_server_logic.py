"""Hotkey / suppression state machine, without hooks or network."""

import pytest

from kmshare.config import Config
from kmshare.server import WM_MOUSEMOVE, WM_MOUSEWHEEL, Server, _coalesce_moves

LCTRL, LSHIFT, F12, A = 0xA2, 0xA0, 0x7B, 0x41


@pytest.fixture
def srv(master, no_cursor):
    s = Server(Config(beep=False), master)
    s._chan = object()  # pretend a client is connected
    return s


def sent(s):
    out = []
    while not s._outq.empty():
        out.append(s._outq.get_nowait()[1])
    return out


def press_hotkey(s):
    return [s.on_key(LCTRL, 0, False, True), s.on_key(LSHIFT, 0, False, True), s.on_key(F12, 0, False, True)]


def release_hotkey(s):
    return [s.on_key(F12, 0, False, False), s.on_key(LSHIFT, 0, False, False), s.on_key(LCTRL, 0, False, False)]


def test_local_mode_passes_everything(srv):
    assert srv.on_key(A, 30, False, True) is False
    assert srv.on_mouse(WM_MOUSEMOVE, 5, 5, 0) is False
    assert sent(srv) == []


def test_hotkey_enters_remote_and_keeps_modifier_ups_local(srv, no_cursor):
    assert press_hotkey(srv) == [False, False, True]  # modifiers pass, trigger is eaten
    assert srv.remote and no_cursor["sets"] == [(960, 540)]
    # Ctrl/Shift went down locally, so their key-ups must also go local (no stuck keys);
    # the F12 key-up is swallowed.
    assert release_hotkey(srv) == [True, False, False]
    assert sent(srv) == [{"t": "enter"}]


def test_remote_forwards_and_suppresses(srv):
    press_hotkey(srv), release_hotkey(srv), sent(srv)
    assert srv.on_key(A, 30, False, True) is True
    assert srv.on_key(A, 30, False, False) is True
    assert srv.on_mouse(WM_MOUSEMOVE, 965, 538, 0) is True
    assert srv.on_mouse(WM_MOUSEWHEEL, 960, 540, (-120 & 0xFFFF) << 16) is True
    assert srv.on_mouse(0x0201, 960, 540, 0) is True
    assert sent(srv) == [
        {"t": "k", "v": A, "s": 30, "e": False, "d": True},
        {"t": "k", "v": A, "s": 30, "e": False, "d": False},
        {"t": "m", "x": 5, "y": -2},
        {"t": "w", "d": -120, "h": False},
        {"t": "b", "b": "left", "d": True},
    ]


def test_hotkey_returns_to_local_and_releases_remote(srv, no_cursor):
    no_cursor["pos"] = (10, 20)
    press_hotkey(srv), release_hotkey(srv), sent(srv)
    # In REMOTE, the hotkey's modifiers are forwarded; the trigger switches back.
    assert press_hotkey(srv) == [True, True, True]
    assert not srv.remote
    assert no_cursor["sets"][-1] == (10, 20)  # cursor restored
    msgs = sent(srv)
    assert msgs[-1] == {"t": "rel"}  # client releases the forwarded Ctrl/Shift
    assert release_hotkey(srv) == [True, False, False]


def test_autorepeat_of_trigger_does_not_flip_back(srv):
    press_hotkey(srv)
    assert srv.on_key(F12, 0, False, True) is True  # auto-repeat
    assert srv.remote


def test_no_client_stays_local(master, no_cursor):
    s = Server(Config(beep=False), master)
    press_hotkey(s)
    assert not s.remote and no_cursor["sets"] == []


def test_drop_while_remote_returns_local(srv):
    press_hotkey(srv)
    srv._chan = _Closable()
    srv._drop(srv._gen, "test")
    assert not srv.remote and srv._chan is None


def test_coalesce_moves():
    batch = [(1, {"t": "m", "x": 1, "y": 1}), (1, {"t": "m", "x": 2, "y": 3}),
             (1, {"t": "k", "v": 1}), (1, {"t": "m", "x": 1, "y": 0}), (2, {"t": "m", "x": 5, "y": 5})]
    assert _coalesce_moves(batch) == [
        (1, {"t": "m", "x": 3, "y": 4}), (1, {"t": "k", "v": 1}),
        (1, {"t": "m", "x": 1, "y": 0}), (2, {"t": "m", "x": 5, "y": 5}),
    ]


class _Closable:
    def close(self):
        pass
