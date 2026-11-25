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
    srv, stop = Server(cfg, master), threading.Event()
    t = threading.Thread(target=srv.serve, kwargs={"stop": stop, "hooks": False}, daemon=True)
    t.start()
    assert wait_for(lambda: srv.port != 0)
    yield srv, cfg
    stop.set()
    t.join(5)


def start_client(cfg, key, port, injector):
    stop = threading.Event()
    c = Client(cfg, key, "127.0.0.1", port=port, injector=injector)
    t = threading.Thread(target=c.run, kwargs={"stop": stop}, daemon=True)
    t.start()
    return stop, t


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
        srv.on_mouse(WM_MOUSEMOVE, 970, 545, 0)
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
