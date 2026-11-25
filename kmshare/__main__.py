"""Launcher: pick a role, type the shared key, run. Usage: python -m kmshare [--role server|client]"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from pathlib import Path

from . import __version__, winput
from .channel import derive_master_key
from .config import DEFAULT_INI, load_config


def _ask_role() -> str:
    print("  [s] server : share THIS PC's keyboard & mouse")
    print("  [c] client : be controlled by the server PC")
    while True:
        answer = input("Role [s/c]: ").strip().lower()
        if answer in ("s", "server"):
            return "server"
        if answer in ("c", "client"):
            return "client"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kmshare", description="Share one PC's keyboard & mouse with another.")
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

    print(f"kmshare {__version__}: keyboard/mouse sharing  (config: {args.config})")
    role = args.role or _ask_role()
    host = None
    if role == "client":
        host = args.host or cfg.server_host
        while not host:
            host = input("Server address (IP or host name): ").strip()

    passphrase = getpass.getpass("Shared key (must match on both PCs): ")
    if not passphrase:
        print("A key is required.", file=sys.stderr)
        return 2
    if len(passphrase) < 8:
        print("Warning: keys shorter than 8 characters are easy to guess.")
    master = derive_master_key(passphrase)
    del passphrase

    try:
        if role == "server":
            from .server import Server

            Server(cfg, master).serve()
        else:
            from .client import Client

            Client(cfg, master, host).run()
    except KeyboardInterrupt:
        print("\nStopped.")
    except OSError as e:
        print(f"Network error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
