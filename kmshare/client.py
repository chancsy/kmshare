"""Client role: connect to the server and replay its keyboard/mouse events here."""

from __future__ import annotations

import logging
import socket
import threading
import time

from .channel import AuthError, Channel, ChannelClosed, client_handshake
from .config import Config
from .winput import Injector

log = logging.getLogger("kmshare.client")


class Client:
    def __init__(self, cfg: Config, master_key: bytes, host: str, port: int | None = None,
                 injector: Injector | None = None):
        self.cfg = cfg
        self.host = host
        self.port = cfg.port if port is None else port
        self._master = master_key
        self.injector = injector or Injector()

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        last_error = None
        log.info("client: connecting to %s:%d ...", self.host, self.port)
        while not stop.is_set():
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5)
            except OSError as e:
                if str(e) != last_error:  # don't spam the same error every retry
                    log.warning("cannot reach server %s:%d (%s), retrying ...", self.host, self.port, e)
                    last_error = str(e)
                stop.wait(3)
                continue
            last_error = None
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                chan = client_handshake(sock, self._master)
            except (AuthError, OSError) as e:
                log.error("handshake failed: %s", e)
                sock.close()
                stop.wait(5)
                continue
            log.info("connected to %s:%d. Waiting for the server's hotkey to take control", self.host, self.port)
            self._session(chan, stop)
            if not stop.is_set():
                log.info("reconnecting ...")
                stop.wait(2)

    def _session(self, chan: Channel, stop: threading.Event) -> None:
        chan.sock.settimeout(0.5)  # poll so Ctrl+C / stop / silence detection work
        last = time.monotonic()
        try:
            while not stop.is_set():
                try:
                    msg = chan.recv()
                except TimeoutError:
                    if time.monotonic() - last > self.cfg.peer_timeout:
                        raise ChannelClosed("server not responding") from None
                    continue
                last = time.monotonic()
                self._handle(chan, msg)
        except (ChannelClosed, AuthError, OSError, ValueError) as e:
            log.warning("disconnected: %s", e)
        finally:
            self.injector.release_all()  # never leave keys/buttons stuck down
            chan.close()

    def _handle(self, chan: Channel, msg: dict) -> None:
        t = msg.get("t")
        inj = self.injector
        try:
            if t == "m":
                inj.move(int(msg["x"]), int(msg["y"]))
            elif t == "k":
                inj.key(int(msg["v"]), int(msg["s"]), bool(msg["e"]), bool(msg["d"]))
            elif t == "b":
                inj.button(str(msg["b"]), bool(msg["d"]))
            elif t == "w":
                inj.wheel(int(msg["d"]), bool(msg.get("h")))
            elif t == "ping":
                chan.send({"t": "pong"})
            elif t == "enter":
                log.info("server took control")
            elif t == "rel":
                inj.release_all()
                log.info("server released control")
        except (KeyError, TypeError, ValueError) as e:
            log.warning("ignored malformed message %r: %s", msg, e)
