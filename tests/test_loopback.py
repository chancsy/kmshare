"""End-to-end over 127.0.0.1: real server + client + encryption, hooks/injection faked."""

import threading

import pytest

from conftest import wait_for
from kmshare.client import Client
from kmshare.config import Config
from kmshare.server import WM_MOUSEMOVE, Server

LCTRL, LSHIFT, F12, A = 0xA2, 0xA0, 0x7B, 0x41


@pytest.fixture
def running_server(master, no_cursor):
    cfg = Config(port=0, bind="127.0.0.1", beep=False, ping_interval=0.3, peer_timeout=1.5)
    asked = []
    srv = Server(cfg, master, choose_side=lambda peer, default: asked.append(peer) or "right")
    srv.asked = asked
    stop = threading.Event()
    t = threading.Thread(target=srv.serve, kwargs={"stop": stop, "hooks": False}, daemon=True)
    t.start()
    assert wait_for(lambda: srv.port != 0)
    yield srv, cfg
    stop.set()
    t.join(5)


def start_client(cfg, key, port, injector, with_client=False):
    stop = threading.Event()
    c = Client(cfg, key, "127.0.0.1", port=port, injector=injector)
    t = threading.Thread(target=c.run, kwargs={"stop": stop}, daemon=True)
    t.start()
    return (stop, t, c) if with_client else (stop, t)


def test_edge_crossing_round_trip(running_server, master, fake_injector, no_cursor, monkeypatch):
    """Server edge push -> client cursor placed on its left edge -> client edge push -> back."""
    import types

    import kmshare.server
    from kmshare import winput

    # Both ends run in this process: give the server its own (frozen) cursor, so the
    # positions set below for the client don't look like the server's cursor moving.
    server_cursor = {"pos": (1919, 540)}
    proxy = types.SimpleNamespace(**{n: getattr(winput, n) for n in dir(winput) if not n.startswith("__")})
    proxy.cursor_pos = lambda: server_cursor["pos"]
    monkeypatch.setattr(kmshare.server, "winput", proxy)
    srv, cfg = running_server
    stop, t, client = start_client(cfg, master, srv.port, fake_injector, with_client=True)
    try:
        assert wait_for(lambda: srv.side == "right" and client._edge is not None)
        assert client._edge.side == "left"

        no_cursor["pos"] = (1919, 540)                     # server cursor at its right edge
        srv.on_mouse(WM_MOUSEMOVE, 1930, 540, 0)           # push 11 px past it
        assert srv.remote
        assert wait_for(lambda: (0, 540) in no_cursor["sets"])  # client placed at its left edge

        no_cursor["pos"] = (30, 540)                       # client cursor moved inside -> re-arms
        srv.on_mouse(WM_MOUSEMOVE, 1920, 540, 0)           # +1 from the frozen spot (1919, 540)
        assert wait_for(lambda: ("move", 1, 0) in fake_injector.events)
        assert client._edge.armed

        no_cursor["pos"] = (0, 270)                        # client cursor at its left edge, 25 % down
        srv.on_mouse(WM_MOUSEMOVE, 1909, 540, 0)           # -10: push 10 px past the client's edge
        assert wait_for(lambda: not srv.remote)            # client sent "leave"
        assert no_cursor["sets"][-1] == (1919, 270)        # server cursor at its right edge, 25 % down
        assert wait_for(lambda: ("release_all",) in fake_injector.events)
    finally:
        stop.set()
        t.join(5)


def test_side_asked_once_per_client_ip(running_server, master, fake_injector):
    srv, cfg = running_server
    for _ in range(2):  # connect, disconnect, reconnect from the same IP
        stop, t = start_client(cfg, master, srv.port, fake_injector)
        assert wait_for(lambda: srv.side == "right")
        stop.set()
        t.join(5)
        assert wait_for(lambda: srv._chan is None)
    assert len(srv.asked) == 1


def test_edge_switch_off_never_asks(master, no_cursor, fake_injector):
    cfg = Config(port=0, bind="127.0.0.1", beep=False, edge_switch=False)
    asked = []
    srv = Server(cfg, master, choose_side=lambda p, d: asked.append(p) or "right")
    sstop = threading.Event()
    st = threading.Thread(target=srv.serve, kwargs={"stop": sstop, "hooks": False}, daemon=True)
    st.start()
    assert wait_for(lambda: srv.port != 0)
    stop, t, client = start_client(cfg, master, srv.port, fake_injector, with_client=True)
    try:
        assert wait_for(lambda: srv._chan is not None)
        assert not wait_for(lambda: srv.side is not None or client._edge is not None, timeout=0.5)
        no_cursor["pos"] = (1919, 540)
        assert srv.on_mouse(WM_MOUSEMOVE, 1990, 540, 0) is False and not srv.remote
        assert asked == []
    finally:
        stop.set()
        t.join(5)
        sstop.set()
        st.join(5)


def test_events_flow_end_to_end(running_server, master, fake_injector):
    srv, cfg = running_server
    stop, t = start_client(cfg, master, srv.port, fake_injector)
    try:
        assert wait_for(lambda: srv._chan is not None)
        for vk in (LCTRL, LSHIFT, F12):
            srv.on_key(vk, 0, False, True)
        for vk in (F12, LSHIFT, LCTRL):
            srv.on_key(vk, 0, False, False)
        assert srv.remote
        srv.on_key(A, 30, False, True)
        srv.on_key(A, 30, False, False)
        srv.on_mouse(WM_MOUSEMOVE, 110, 105, 0)  # frozen at (100, 100): moves (10, 5)
        srv.on_mouse(0x0201, 960, 540, 0)
        srv.on_mouse(0x0202, 960, 540, 0)
        for vk in (LCTRL, LSHIFT, F12):  # back to LOCAL
            srv.on_key(vk, 0, False, True)
        assert not srv.remote
        assert wait_for(lambda: ("release_all",) in fake_injector.events)
        ev = fake_injector.events
        assert ev[: ev.index(("release_all",))] == [
            ("key", A, True), ("key", A, False), ("move", 10, 5),
            ("button", "left", True), ("button", "left", False),
            ("key", LCTRL, True), ("key", LSHIFT, True),
        ]
        # keep-alive keeps the link up well past peer_timeout
        assert not wait_for(lambda: srv._chan is None, timeout=2.5)
    finally:
        stop.set()
        t.join(5)


def test_client_disconnect_returns_server_to_local(running_server, master, fake_injector):
    srv, cfg = running_server
    stop, t = start_client(cfg, master, srv.port, fake_injector)
    assert wait_for(lambda: srv._chan is not None)
    for vk in (LCTRL, LSHIFT, F12):
        srv.on_key(vk, 0, False, True)
    assert srv.remote
    stop.set()
    t.join(5)
    assert wait_for(lambda: not srv.remote and srv._chan is None)


def test_wrong_key_client_never_connects(running_server, other_master, fake_injector):
    srv, cfg = running_server
    stop, t = start_client(cfg, other_master, srv.port, fake_injector)
    try:
        assert not wait_for(lambda: srv._chan is not None, timeout=1.5)
    finally:
        stop.set()
        t.join(10)
