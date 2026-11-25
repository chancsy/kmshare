"""Load ``kmshare.ini`` plus this PC's ``kmshare.local.ini``. The shared key is never stored; it is typed at launch.

``kmshare.local.ini`` sits next to ``kmshare.ini``, is ignored by git, and its values win. kmshare writes it
only when you agree to save a typed server address; you can put any other setting there by hand too.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

from .edge import SIDES, EdgeSettings
from .hotkey import Hotkey, parse_hotkey

DEFAULT_INI = Path(__file__).resolve().parent.parent / "kmshare.ini"
LOCAL_HEADER = ("; This PC's own kmshare settings: they override kmshare.ini and are ignored by git.\n"
                "; kmshare writes server_host here when you save a typed address; other keys can be added by hand.\n\n")


def local_ini(path: Path) -> Path:
    """``kmshare.ini`` -> ``kmshare.local.ini`` in the same folder."""
    path = Path(path)
    return path.with_name(f"{path.stem}.local{path.suffix}")


def save_local(path: Path, section: str, key: str, value: str) -> Path:
    """Set one value in the local ini next to ``path``, keeping what else is there. Returns its path."""
    local = local_ini(path)
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.read(local, encoding="utf-8")
    if not cp.has_section(section):
        cp.add_section(section)
    cp.set(section, key, value)
    with open(local, "w", encoding="utf-8") as f:
        f.write(LOCAL_HEADER)
        cp.write(f)
    return local


@dataclass(frozen=True)
class Config:
    port: int = 24800
    bind: str = "0.0.0.0"
    server_host: str = ""
    hotkey: Hotkey = field(default_factory=lambda: parse_hotkey("ctrl+shift+f12"))
    beep: bool = True
    ping_interval: float = 2.0
    peer_timeout: float = 6.0
    # features
    edge_switch: bool = True
    clipboard: bool = True
    clipboard_max_kb: int = 1024
    # layout / edge switching (server side; the client uses the server's values)
    client_side: str = "right"
    edge: EdgeSettings = field(default_factory=EdgeSettings)


def _nonneg_int(cp: configparser.ConfigParser, section: str, key: str, default: int, minimum: int = 0) -> int:
    v = cp.getint(section, key, fallback=default)
    if v < minimum:
        raise ValueError(f"[{section}] {key} must be >= {minimum}, got {v}")
    return v


def load_config(path: Path = DEFAULT_INI) -> Config:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.read([path, local_ini(path)], encoding="utf-8")  # a missing local file is skipped
    d = Config()

    port = cp.getint("network", "port", fallback=d.port)
    if not 0 <= port <= 65535:
        raise ValueError(f"[network] port must be 0-65535, got {port}")
    ping = cp.getfloat("misc", "ping_interval", fallback=d.ping_interval)
    timeout = cp.getfloat("misc", "peer_timeout", fallback=d.peer_timeout)
    if ping <= 0 or timeout <= ping:
        raise ValueError("[misc] needs ping_interval > 0 and peer_timeout > ping_interval")
    side = cp.get("layout", "client_side", fallback=d.client_side).strip().lower()
    if side not in SIDES:
        raise ValueError(f"[layout] client_side must be one of {', '.join(SIDES)}, got {side!r}")
    de = d.edge

    return Config(
        port=port,
        bind=cp.get("network", "bind", fallback=d.bind).strip() or d.bind,
        server_host=cp.get("network", "server_host", fallback="").strip(),
        hotkey=parse_hotkey(cp.get("hotkey", "toggle", fallback=d.hotkey.text)),
        beep=cp.getboolean("misc", "beep", fallback=d.beep),
        ping_interval=ping,
        peer_timeout=timeout,
        edge_switch=cp.getboolean("features", "edge_switch", fallback=d.edge_switch),
        clipboard=cp.getboolean("features", "clipboard", fallback=d.clipboard),
        clipboard_max_kb=_nonneg_int(cp, "clipboard", "max_kb", d.clipboard_max_kb, minimum=1),
        client_side=side,
        edge=EdgeSettings(
            push_px=_nonneg_int(cp, "layout", "edge_push", de.push_px, minimum=1),
            delay_ms=_nonneg_int(cp, "layout", "edge_delay_ms", de.delay_ms),
            corner_px=_nonneg_int(cp, "layout", "edge_corner", de.corner_px),
            rearm_px=_nonneg_int(cp, "layout", "edge_rearm", de.rearm_px),
        ),
    )
