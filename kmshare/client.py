"""Client role: connect to the server and replay its keyboard/mouse events here."""

from __future__ import annotations

import logging
import socket
import threading
import time

from . import winput
from .channel import AuthError, Channel, ChannelClosed, client_handshake
from .config import Config
from .edge import SIDES, EdgeDetector, EdgeSettings, MonitorCache, entry_point
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
        self._mons = MonitorCache(lambda: winput.monitors())
        self._edge: EdgeDetector | None = None  # set by the server's "layout" message
        self._active = False      # the server is currently controlling this PC
        self._leave_sent = False  # sent "leave", waiting for the server to take over

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
            self._edge, self._active = None, False
            chan.close()

    def _handle(self, chan: Channel, msg: dict) -> None:
        t = msg.get("t")
        inj = self.injector
        try:
            if t == "m":
                dx, dy = int(msg["x"]), int(msg["y"])
                cur = winput.cursor_pos()
                inj.move(dx, dy)
                if self._active and self._edge is not None and not self._leave_sent:
                    fraction = self._edge.update(cur, (cur[0] + dx, cur[1] + dy), self._mons.get(),
                                                 buttons_held=inj.any_button_held())
                    if fraction is not None:
                        chan.send({"t": "leave", "f": round(fraction, 4)})
                        self._leave_sent = True
                        log.info("pushed past the %s edge, handing control back", self._edge.side)
            elif t == "k":
                inj.key(int(msg["v"]), int(msg["s"]), bool(msg["e"]), bool(msg["d"]))
            elif t == "b":
                inj.button(str(msg["b"]), bool(msg["d"]))
            elif t == "w":
                inj.wheel(int(msg["d"]), bool(msg.get("h")))
            elif t == "ping":
                chan.send({"t": "pong"})
            elif t == "enter":
                self._active, self._leave_sent = True, False
                edge = msg.get("edge")
                if edge in SIDES:
                    winput.set_cursor_pos(*entry_point(edge, float(msg["f"]), self._mons.get()))
                    log.info("server took control (came in over my %s edge)", edge)
                else:
                    log.info("server took control")
                if self._edge is not None:
                    self._edge.disarm()  # no bounce: move away from the edge first
            elif t == "rel":
                self._active = False
                inj.release_all()
                log.info("server released control")
            elif t == "layout":
                side = msg.get("side")
                if side in SIDES:
                    self._edge = EdgeDetector(side, EdgeSettings.from_msg(msg))
                    log.info("the server is on my %s: push past my %s edge to go back", side.upper(), side)
                else:
                    self._edge = None
        except (KeyError, TypeError, ValueError) as e:
            log.warning("ignored malformed message %r: %s", msg, e)
