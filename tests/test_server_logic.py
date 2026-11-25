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
    assert srv.remote and no_cursor["sets"] == []  # cursor frozen in place, not moved
    assert srv._anchor == (100, 100)
    # Ctrl/Shift went down locally, so their key-ups must also go local (no stuck keys);
    # the F12 key-up is swallowed.
    assert release_hotkey(srv) == [True, False, False]
    assert sent(srv) == [{"t": "enter"}]


def test_remote_forwards_and_suppresses(srv):
    press_hotkey(srv), release_hotkey(srv), sent(srv)
    assert srv.on_key(A, 30, False, True) is True
    assert srv.on_key(A, 30, False, False) is True
    assert srv.on_mouse(WM_MOUSEMOVE, 105, 98, 0) is True  # frozen at (100, 100)
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
    assert no_cursor["sets"] == []  # cursor was frozen at (10, 20) the whole time: nothing to restore
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


# ---------------------------------------------------------------- edge switching
@pytest.fixture
def edge_srv(srv):
    from kmshare.edge import EdgeDetector, EdgeSettings

    srv.side = "right"
    srv._edge = EdgeDetector("right", EdgeSettings(push_px=8))
    return srv


def test_edge_push_enters_remote_with_placement(edge_srv, no_cursor):
    no_cursor["pos"] = (1919, 540)
    assert edge_srv.on_mouse(WM_MOUSEMOVE, 1923, 540, 0) is False   # 4 px past: not yet
    assert edge_srv.on_mouse(WM_MOUSEMOVE, 1925, 540, 0) is True    # 4 + 6 >= 8
    assert edge_srv.remote
    assert sent(edge_srv) == [{"t": "enter", "edge": "left", "f": round(540 / 1079, 4)}]


def test_edge_blocked_while_button_held(edge_srv, no_cursor):
    no_cursor["pos"] = (1919, 540)
    edge_srv.on_mouse(0x0201, 1919, 540, 0)                         # left button down (drag)
    assert edge_srv.on_mouse(WM_MOUSEMOVE, 1950, 540, 0) is False
    edge_srv.on_mouse(0x0202, 1919, 540, 0)                         # released
    assert edge_srv.on_mouse(WM_MOUSEMOVE, 1950, 540, 0) is True


def test_no_edge_switching_without_layout(srv, no_cursor):
    no_cursor["pos"] = (1919, 540)
    assert srv.on_mouse(WM_MOUSEMOVE, 1990, 540, 0) is False and not srv.remote


def test_client_leave_returns_local_at_matching_edge_and_disarms(edge_srv, no_cursor):
    no_cursor["pos"] = (1919, 540)
    edge_srv.on_mouse(WM_MOUSEMOVE, 1950, 540, 0)
    sent(edge_srv)
    edge_srv._on_client_leave(edge_srv._gen, {"t": "leave", "f": 0.25})
    assert not edge_srv.remote
    assert no_cursor["sets"][-1] == (1919, 270)                     # right edge, 25 % down
    assert sent(edge_srv) == [{"t": "rel"}]
    no_cursor["pos"] = (1919, 270)
    assert edge_srv.on_mouse(WM_MOUSEMOVE, 1950, 270, 0) is False   # disarmed: no bounce back


def test_edge_crossing_freezes_cursor_at_the_edge(edge_srv, no_cursor):
    no_cursor["pos"] = (1919, 540)
    edge_srv.on_mouse(WM_MOUSEMOVE, 1950, 540, 0)
    assert edge_srv.remote and edge_srv._anchor == (1919, 540) and no_cursor["sets"] == []
    sent(edge_srv)
    assert edge_srv.on_mouse(WM_MOUSEMOVE, 1925, 543, 0) is True    # past the edge: still measured
    assert sent(edge_srv) == [{"t": "m", "x": 6, "y": 3}]


def test_race_guard_drops_moves_computed_from_frozen_spot(edge_srv, no_cursor):
    """Bug A: a move Windows computed from the frozen spot must not yank the cursor back."""
    no_cursor["pos"] = (1919, 540)
    edge_srv.on_mouse(WM_MOUSEMOVE, 1950, 540, 0)                   # cross at y=540, frozen there
    edge_srv._on_client_leave(edge_srv._gen, {"t": "leave", "f": 0.25})
    assert no_cursor["sets"][-1] == (1919, 270)                     # placed at the return point
    no_cursor["pos"] = (1919, 270)
    assert edge_srv.on_mouse(WM_MOUSEMOVE, 1916, 541, 0) is True    # stale (frozen spot + motion): dropped
    assert edge_srv.on_mouse(WM_MOUSEMOVE, 1915, 268, 0) is False   # normal move: passes, guard ends
    assert edge_srv.on_mouse(WM_MOUSEMOVE, 1914, 541, 0) is False   # guard is over


def test_race_guard_expires(edge_srv, no_cursor):
    no_cursor["pos"] = (1919, 540)
    edge_srv.on_mouse(WM_MOUSEMOVE, 1950, 540, 0)
    edge_srv._on_client_leave(edge_srv._gen, {"t": "leave", "f": 0.25})
    no_cursor["pos"] = (1919, 270)
    edge_srv._stale_until = 0.0                                      # pretend 0.25 s passed
    assert edge_srv.on_mouse(WM_MOUSEMOVE, 1916, 541, 0) is False


def test_hotkey_return_without_moving_needs_no_guard(srv, no_cursor):
    press_hotkey(srv), release_hotkey(srv)
    press_hotkey(srv)                                                # back to LOCAL
    assert not srv.remote and no_cursor["sets"] == [] and srv._stale_anchor is None


def test_stale_leave_ignored(edge_srv):
    edge_srv._on_client_leave(edge_srv._gen, {"t": "leave", "f": 0.5})  # not REMOTE
    assert not edge_srv.remote and sent(edge_srv) == []


def test_slipped_cursor_does_not_offset_every_move(srv, no_cursor, caplog):
    """Rapid-switch bug: the frozen cursor moved, so x - anchor carried a fixed offset forever."""
    import logging

    caplog.set_level(logging.WARNING, "kmshare.server")
    press_hotkey(srv), release_hotkey(srv), sent(srv)              # REMOTE, frozen at (100, 100)
    no_cursor["pos"] = (-200, 100)                                  # an event slipped past: cursor moved 300 px left
    assert srv.on_mouse(WM_MOUSEMOVE, -195, 98, 0) is True          # user moves +5, -2
    assert srv.on_mouse(WM_MOUSEMOVE, -190, 100, 0) is True         # and +10, 0 from there
    assert sent(srv) == [{"t": "m", "x": 5, "y": -2}, {"t": "m", "x": 10, "y": 0}]  # not -295 / -290
    assert srv._anchor == (-200, 100)
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 1 and warnings[0].startswith("server cursor slipped 300 px")
    press_hotkey(srv)                                               # hotkey return: back to the original spot
    assert not srv.remote and no_cursor["sets"][-1] == (100, 100)
