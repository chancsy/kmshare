"""Launcher. Usage: python -m kmshare [--role server|client] [--host H] [--config F] [-v]

With no arguments the launcher is interactive: it asks for the role, then whether to
use the ini's settings as they are (a client is then only asked for the server address).
Answering n asks each setting that role uses (Enter keeps the default from kmshare.ini).
Then it asks for the key.
With any argument it takes settings from the ini + arguments and only asks for
what is still missing (role, client server address) and the key.
"""

from __future__ import annotations

import argparse
import dataclasses
import getpass
import logging
import sys
import threading
from pathlib import Path
from typing import Callable, TypeVar

from . import __version__, adminwatch, winput
from .channel import derive_master_key
from .config import DEFAULT_INI, Config, load_config, local_ini, save_local
from .edge import EdgeSettings
from .hotkey import parse_hotkey

T = TypeVar("T")


# --------------------------------------------------------------------------- prompts
def _ask_role() -> str:
    print("  [s] server : share THIS PC's keyboard & mouse")
    print("  [c] client : be controlled by the server PC")
    while True:
        answer = input("Role [s/c]: ").strip().lower()
        if answer in ("s", "server"):
            return "server"
        if answer in ("c", "client"):
            return "client"


def _ask(label: str, default: str, parse: Callable[[str], T]) -> T:
    """Ask one setting; Enter keeps ``default``. Re-asks until ``parse`` accepts it."""
    while True:
        raw = input(f"{label} [{default}]: " if default else f"{label}: ").strip() or default
        if not raw:
            print("  A value is required.")
            continue
        try:
            return parse(raw)
        except ValueError as e:
            print(f"  Invalid: {e}")


def _port(s: str) -> int:
    try:
        n = int(s)
    except ValueError:
        raise ValueError("enter a number") from None
    if not 1 <= n <= 65535:
        raise ValueError("port must be 1-65535")
    return n


def _yes_no(s: str) -> bool:
    s = s.lower()
    if s in ("y", "yes", "true", "1", "on"):
        return True
    if s in ("n", "no", "false", "0", "off"):
        return False
    raise ValueError("answer y or n")


def _seconds(minimum: float = 0.0) -> Callable[[str], float]:
    def parse(s: str) -> float:
        try:
            v = float(s)
        except ValueError:
            raise ValueError("enter a number of seconds") from None
        if v <= minimum:
            raise ValueError(f"must be more than {minimum:g}")
        return v
    return parse


def _whole(minimum: int = 0) -> Callable[[str], int]:
    def parse(s: str) -> int:
        try:
            v = int(s)
        except ValueError:
            raise ValueError("enter a whole number") from None
        if v < minimum:
            raise ValueError(f"must be {minimum} or more")
        return v
    return parse


def _not_empty(s: str) -> str:
    return s


def _ask_edge(cfg: Config) -> Config:
    """Server: edge switching on/off, then (optionally) its safeguards."""
    on = _ask("  Edge switching: push the cursor past the screen edge to switch (y/n)",
              "y" if cfg.edge_switch else "n", _yes_no)
    if not on:
        return dataclasses.replace(cfg, edge_switch=False)
    if not _ask("  Change the edge-switching safeguards (y/n)", "n", _yes_no):
        return dataclasses.replace(cfg, edge_switch=True)
    e = cfg.edge
    edge = EdgeSettings(
        push_px=_ask("    Push past the edge before switching, pixels", str(e.push_px), _whole(1)),
        delay_ms=_ask("    Keep pushing at least, ms (0 = off)", str(e.delay_ms), _whole()),
        corner_px=_ask("    No switching this close to a corner, pixels", str(e.corner_px), _whole()),
        rearm_px=_ask("    After a switch, move this far from the edge first, pixels", str(e.rearm_px), _whole()),
    )
    return dataclasses.replace(cfg, edge_switch=True, edge=edge)


def _interactive_settings(role: str, cfg: Config, ini: Path) -> Config:
    print("Settings (press Enter to keep the value in [brackets], from the ini):")
    if role == "server":
        port = _ask("  Port", str(cfg.port), _port)
        bind = _ask("  Listen address (0.0.0.0 = all adapters)", cfg.bind, _not_empty)
        hotkey = _ask("  Switch hotkey", cfg.hotkey.text, parse_hotkey)
        beep = _ask("  Beep when switching (y/n)", "y" if cfg.beep else "n", _yes_no)
        ping = _ask("  Keep-alive ping interval, seconds", f"{cfg.ping_interval:g}", _seconds())
        timeout_default = f"{max(cfg.peer_timeout, ping * 2):g}"
        timeout = _ask("  Drop the link after silence of, seconds", timeout_default, _seconds(ping))
        cfg = dataclasses.replace(cfg, port=port, bind=bind, hotkey=hotkey, beep=beep,
                                  ping_interval=ping, peer_timeout=timeout)
        return _ask_clipboard(_ask_edge(cfg))
    cfg = _ask_host(cfg, ini)
    port = _ask("  Port", str(cfg.port), _port)
    timeout = _ask("  Drop the link after silence of, seconds", f"{cfg.peer_timeout:g}",
                   _seconds(cfg.ping_interval))
    return _ask_clipboard(dataclasses.replace(cfg, port=port, peer_timeout=timeout))


def _ask_host(cfg: Config, ini: Path) -> Config:
    """Client: the server address; a new one can be saved to this PC's local ini."""
    host = _ask("  Server address (IP or host name)", cfg.server_host, _not_empty)
    if host != cfg.server_host and _ask(f"  Save {host} as the server address for next time (y/n)", "y", _yes_no):
        try:
            saved = save_local(ini, "network", "server_host", host)
            print(f"  Saved to {saved.name} (this PC only, not tracked by git).")
        except OSError as e:
            print(f"  Couldn't save it: {e}")
    return dataclasses.replace(cfg, server_host=host)


def _quick_settings(role: str, cfg: Config, ini: Path) -> Config:
    """The ini's settings as they are; a client is still asked for the server address."""
    if role == "client":
        cfg = _ask_host(cfg, ini)
    on_off = lambda v: "on" if v else "off"  # noqa: E731
    parts = [f"port {cfg.port}"]
    if role == "server":
        parts += [f"hotkey {cfg.hotkey.text}", f"edge switching {on_off(cfg.edge_switch)}"]
    parts.append(f"clipboard sharing {on_off(cfg.clipboard)}")
    print("  Using: " + ", ".join(parts))
    return cfg


def _ask_clipboard(cfg: Config) -> Config:
    on = _ask("  Share copied text with the other PC when control switches (y/n)",
              "y" if cfg.clipboard else "n", _yes_no)
    return dataclasses.replace(cfg, clipboard=on)


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    interactive = not argv

    ap = argparse.ArgumentParser(prog="kmshare", description="Share one PC's keyboard & mouse with another. "
                                 "Run with no arguments to be asked for every setting.")
    ap.add_argument("--role", choices=("server", "client"), help="skip the role prompt")
    ap.add_argument("--config", type=Path, default=DEFAULT_INI, help=f"ini file (default: {DEFAULT_INI.name})")
    ap.add_argument("--host", help="client: server address (overrides server_host in the ini)")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    winput.set_dpi_aware()
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    local = local_ini(args.config)
    shown = f"{args.config} + {local.name}" if local.is_file() else f"{args.config}"
    print(f"kmshare {__version__}: keyboard/mouse sharing  (config: {shown})")
    role = args.role or _ask_role()
    if interactive:
        asked = "the server address and key" if role == "client" else "the key"
        if _ask(f"Use the settings in {args.config.name} and only ask for {asked} (y/n)", "y", _yes_no):
            cfg = _quick_settings(role, cfg, args.config)
        else:
            cfg = _interactive_settings(role, cfg, args.config)
    elif role == "client":
        host = args.host or cfg.server_host
        while not host:
            host = input("Server address (IP or host name): ").strip()
        cfg = dataclasses.replace(cfg, server_host=host)

    passphrase = getpass.getpass("Shared key (must match on both PCs): ")
    while not passphrase:  # an Enter pressed by accident asks again; Ctrl+C quits
        print("  A key is required.")
        passphrase = getpass.getpass("Shared key (must match on both PCs): ")
    if len(passphrase) < 8:
        print("Warning: keys shorter than 8 characters are easy to guess.")
    master = derive_master_key(passphrase)
    del passphrase

    try:  # warn when an admin window takes focus; never block the launch
        adminwatch.start(role, threading.Event(), cfg.hotkey.text)
    except Exception as e:
        logging.getLogger("kmshare").debug("admin focus watch not started: %s", e)

    try:
        if role == "server":
            from .server import Server

            Server(cfg, master).serve()
        else:
            from .client import Client

            Client(cfg, master, cfg.server_host).run()
    except KeyboardInterrupt:
        print("\nStopped.")
    except OSError as e:
        print(f"Network error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
