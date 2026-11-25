"""Load ``kmshare.ini``. The shared key is never stored here; it is typed at launch."""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

from .hotkey import Hotkey, parse_hotkey

DEFAULT_INI = Path(__file__).resolve().parent.parent / "kmshare.ini"


@dataclass(frozen=True)
class Config:
    port: int = 24800
    bind: str = "0.0.0.0"
    server_host: str = ""
    hotkey: Hotkey = field(default_factory=lambda: parse_hotkey("ctrl+shift+f12"))
    beep: bool = True
    ping_interval: float = 2.0
    peer_timeout: float = 6.0


def load_config(path: Path = DEFAULT_INI) -> Config:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.read(path, encoding="utf-8")
    d = Config()

    port = cp.getint("network", "port", fallback=d.port)
    if not 0 <= port <= 65535:
        raise ValueError(f"[network] port must be 0-65535, got {port}")
    ping = cp.getfloat("misc", "ping_interval", fallback=d.ping_interval)
    timeout = cp.getfloat("misc", "peer_timeout", fallback=d.peer_timeout)
    if ping <= 0 or timeout <= ping:
        raise ValueError("[misc] needs ping_interval > 0 and peer_timeout > ping_interval")

    return Config(
        port=port,
        bind=cp.get("network", "bind", fallback=d.bind).strip() or d.bind,
        server_host=cp.get("network", "server_host", fallback="").strip(),
        hotkey=parse_hotkey(cp.get("hotkey", "toggle", fallback=d.hotkey.text)),
        beep=cp.getboolean("misc", "beep", fallback=d.beep),
        ping_interval=ping,
        peer_timeout=timeout,
    )
