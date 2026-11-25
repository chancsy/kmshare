"""Authenticated, encrypted TCP channel keyed by a shared passphrase.

Handshake (both sides know ``master = scrypt(passphrase)``):

    server -> client : MAGIC | server_nonce(16)
    client -> server : MAGIC | client_nonce(16) | HMAC(master, "client"|sn|cn)
    server -> client : HMAC(master, "server"|sn|cn)
    session_key      = HMAC(master, "session"|sn|cn)

Fresh nonces from both sides make every session key unique, so recorded
traffic cannot be replayed. After the handshake each frame is
``len(4) | AES-256-GCM(json)`` with a per-direction counter nonce, so frames
cannot be forged, reordered or replayed within a session either.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"KMS1"
NONCE_LEN = 16
MAC_LEN = 32
KDF_SALT = b"kmshare/v1/kdf"
MAX_FRAME = 64 * 1024
HANDSHAKE_TIMEOUT = 5.0

DIR_SERVER_TO_CLIENT = 1
DIR_CLIENT_TO_SERVER = 2


class AuthError(Exception):
    """Handshake or frame authentication failed (wrong key, tampering, protocol mismatch)."""


class ChannelClosed(Exception):
    """The peer closed the connection."""


def derive_master_key(passphrase: str) -> bytes:
    """Slow, memory-hard derivation so a captured handshake is expensive to brute-force."""
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=KDF_SALT, n=2**15, r=8, p=1,
        maxmem=64 * 1024 * 1024, dklen=32,
    )


def _mac(key: bytes, label: bytes, *parts: bytes) -> bytes:
    return hmac.new(key, label + b"".join(parts), hashlib.sha256).digest()


def _recv_exact(sock: socket.socket, n: int, *, retry_partial: bool) -> bytes:
    """Read exactly ``n`` bytes.

    A socket timeout before any byte arrives is re-raised (``TimeoutError``) so
    callers can poll. Once a frame has started, ``retry_partial=True`` keeps
    waiting instead of losing stream sync; ``False`` (handshake) raises.
    """
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except TimeoutError:
            if buf and retry_partial:
                continue
            raise
        if not chunk:
            raise ChannelClosed("connection closed by peer")
        buf += chunk
    return bytes(buf)


class Channel:
    def __init__(self, sock: socket.socket, session_key: bytes, send_dir: int, recv_dir: int):
        self.sock = sock
        self._aead = AESGCM(session_key)
        self._send_dir, self._recv_dir = send_dir, recv_dir
        self._send_ctr = self._recv_ctr = 0

    @staticmethod
    def _nonce(direction: int, counter: int) -> bytes:
        return bytes([direction]) + b"\0\0\0" + counter.to_bytes(8, "big")

    def send(self, msg: dict) -> None:
        data = json.dumps(msg, separators=(",", ":")).encode()
        ct = self._aead.encrypt(self._nonce(self._send_dir, self._send_ctr), data, None)
        self._send_ctr += 1
        self.sock.sendall(struct.pack(">I", len(ct)) + ct)

    def recv(self) -> dict:
        """Next message. Raises ``TimeoutError`` only if no frame has started yet."""
        (n,) = struct.unpack(">I", _recv_exact(self.sock, 4, retry_partial=True))
        if n > MAX_FRAME:
            raise AuthError(f"oversized frame ({n} bytes)")
        ct = _recv_exact(self.sock, n, retry_partial=True)
        try:
            data = self._aead.decrypt(self._nonce(self._recv_dir, self._recv_ctr), ct, None)
        except InvalidTag:
            raise AuthError("frame failed authentication") from None
        self._recv_ctr += 1
        return json.loads(data)

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def server_handshake(sock: socket.socket, master: bytes) -> Channel:
    sock.settimeout(HANDSHAKE_TIMEOUT)
    try:
        sn = os.urandom(NONCE_LEN)
        sock.sendall(MAGIC + sn)
        hello = _recv_exact(sock, len(MAGIC) + NONCE_LEN + MAC_LEN, retry_partial=False)
        if hello[: len(MAGIC)] != MAGIC:
            raise AuthError("not a kmshare client (or a different version)")
        cn = hello[len(MAGIC): len(MAGIC) + NONCE_LEN]
        proof = hello[len(MAGIC) + NONCE_LEN:]
        if not hmac.compare_digest(proof, _mac(master, b"client", sn, cn)):
            raise AuthError("wrong key")
        sock.sendall(_mac(master, b"server", sn, cn))
    except TimeoutError:
        raise AuthError("handshake timed out") from None
    except ChannelClosed:
        raise AuthError("client hung up during handshake") from None
    sock.settimeout(None)
    return Channel(sock, _mac(master, b"session", sn, cn), DIR_SERVER_TO_CLIENT, DIR_CLIENT_TO_SERVER)


def client_handshake(sock: socket.socket, master: bytes) -> Channel:
    sock.settimeout(HANDSHAKE_TIMEOUT)
    try:
        hello = _recv_exact(sock, len(MAGIC) + NONCE_LEN, retry_partial=False)
        if hello[: len(MAGIC)] != MAGIC:
            raise AuthError("not a kmshare server (or a different version)")
        sn = hello[len(MAGIC):]
        cn = os.urandom(NONCE_LEN)
        sock.sendall(MAGIC + cn + _mac(master, b"client", sn, cn))
        try:
            proof = _recv_exact(sock, MAC_LEN, retry_partial=False)
        except ChannelClosed:
            raise AuthError("server rejected the key") from None
        if not hmac.compare_digest(proof, _mac(master, b"server", sn, cn)):
            raise AuthError("server failed the key check (different key?)")
    except TimeoutError:
        raise AuthError("handshake timed out") from None
    except ChannelClosed:
        raise AuthError("server hung up during handshake") from None
    sock.settimeout(None)
    return Channel(sock, _mac(master, b"session", sn, cn), DIR_CLIENT_TO_SERVER, DIR_SERVER_TO_CLIENT)
