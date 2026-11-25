import socket
import struct
import threading

import pytest

from kmshare.channel import AuthError, client_handshake, server_handshake


def _pair(master_s, master_c):
    s_sock, c_sock = socket.socketpair()
    result = {}

    def srv():
        try:
            result["server"] = server_handshake(s_sock, master_s)
        except Exception as e:  # noqa: BLE001
            result["server_err"] = e
            s_sock.close()

    t = threading.Thread(target=srv)
    t.start()
    try:
        result["client"] = client_handshake(c_sock, master_c)
    except Exception as e:  # noqa: BLE001
        result["client_err"] = e
    t.join(10)
    return result


def test_roundtrip_both_directions(master):
    r = _pair(master, master)
    s, c = r["server"], r["client"]
    s.send({"t": "k", "v": 65, "d": True})
    assert c.recv() == {"t": "k", "v": 65, "d": True}
    c.send({"t": "pong"})
    assert s.recv() == {"t": "pong"}
    for i in range(50):
        s.send({"t": "m", "x": i, "y": -i})
    assert [c.recv()["x"] for _ in range(50)] == list(range(50))


def test_wrong_key_rejected_on_both_sides(master, other_master):
    r = _pair(master, other_master)
    assert isinstance(r["server_err"], AuthError) and "wrong key" in str(r["server_err"])
    assert isinstance(r["client_err"], AuthError)


def test_tampered_frame_rejected(master):
    r = _pair(master, master)
    s, c = r["server"], r["client"]
    # Hand-craft a frame with a flipped ciphertext bit.
    ct = s._aead.encrypt(s._nonce(s._send_dir, s._send_ctr), b'{"t":"ping"}', None)
    bad = bytes([ct[0] ^ 1]) + ct[1:]
    s.sock.sendall(struct.pack(">I", len(bad)) + bad)
    with pytest.raises(AuthError):
        c.recv()


def test_replayed_frame_rejected(master):
    r = _pair(master, master)
    s, c = r["server"], r["client"]
    ct = s._aead.encrypt(s._nonce(s._send_dir, 0), b'{"t":"ping"}', None)
    frame = struct.pack(">I", len(ct)) + ct
    s.sock.sendall(frame)
    assert c.recv() == {"t": "ping"}
    s.sock.sendall(frame)  # same frame again -> counter mismatch
    with pytest.raises(AuthError):
        c.recv()


def test_session_keys_differ_per_connection(master):
    a, b = _pair(master, master), _pair(master, master)
    ka = a["server"]._aead.encrypt(b"\0" * 12, b"x", None)
    kb = b["server"]._aead.encrypt(b"\0" * 12, b"x", None)
    assert ka != kb
