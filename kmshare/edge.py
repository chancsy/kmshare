"""Screen-edge switching: detect a push past an outer desktop edge, and map crossings.

Pure logic (no Win32 calls) so it can be unit-tested with any monitor layout.
Monitors are ``(left, top, right, bottom)`` rectangles in virtual-desktop pixels,
right/bottom exclusive (as Windows reports them).

Safeguards against accidental switching, all tunable in ``[layout]``:

- **push_px**: the cursor must be pushed this many pixels *past* the edge in total.
- **delay_ms**: the push has to last at least this long (0 = off).
- **corner_px**: no switching within this distance of either end of the edge,
  so corners (Start button, a window's close button) stay usable.
- **rearm_px**: after any switch, the edge stays off until the cursor has moved
  this far away from it, so the cursor can't bounce straight back.
- No switching while a mouse button is held (a drag stays on its PC).
- Only **outer** edges count: an edge with another monitor beyond it is ignored.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Sequence

Rect = tuple[int, int, int, int]
SIDES = ("left", "right", "top", "bottom")
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


@dataclass(frozen=True)
class EdgeSettings:
    push_px: int = 8
    delay_ms: int = 0
    corner_px: int = 40
    rearm_px: int = 20

    def to_msg(self) -> dict:
        return {"push": self.push_px, "delay": self.delay_ms, "corner": self.corner_px, "rearm": self.rearm_px}

    @classmethod
    def from_msg(cls, m: dict) -> "EdgeSettings":
        return cls(int(m["push"]), int(m["delay"]), int(m["corner"]), int(m["rearm"]))


def monitor_at(x: int, y: int, monitors: Sequence[Rect]) -> Rect | None:
    for m in monitors:
        if m[0] <= x < m[2] and m[1] <= y < m[3]:
            return m
    return None


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _edge_geometry(side: str, mon: Rect, tx: int, ty: int):
    """For a move targeting (tx, ty) from a cursor on ``mon``, return
    (overshoot past the edge, distance inside from the edge, position along the edge,
    edge length, point just beyond the edge)."""
    left, top, right, bottom = mon
    cx, cy = _clamp(tx, left, right - 1), _clamp(ty, top, bottom - 1)
    if side == "right":
        return tx - (right - 1), (right - 1) - cx, cy - top, bottom - top, (right, cy)
    if side == "left":
        return left - tx, cx - left, cy - top, bottom - top, (left - 1, cy)
    if side == "bottom":
        return ty - (bottom - 1), (bottom - 1) - cy, cx - left, right - left, (cx, bottom)
    if side == "top":
        return top - ty, cy - top, cx - left, right - left, (cx, top - 1)
    raise ValueError(f"unknown side {side!r}")


class EdgeDetector:
    """Watches cursor moves and reports when the user has pushed past ``side``."""

    def __init__(self, side: str, settings: EdgeSettings, clock: Callable[[], float] = time.monotonic):
        if side not in SIDES:
            raise ValueError(f"unknown side {side!r}")
        self.side = side
        self.s = settings
        self.clock = clock
        self.armed = True
        self._push = 0
        self._since: float | None = None

    def _reset(self) -> None:
        self._push, self._since = 0, None

    def disarm(self) -> None:
        """Turn the edge off until the cursor moves ``rearm_px`` away from it."""
        self.armed = False
        self._reset()

    def update(self, cur: tuple[int, int], target: tuple[int, int], monitors: Sequence[Rect],
               buttons_held: bool = False) -> float | None:
        """Feed one move from ``cur`` (actual cursor) towards ``target`` (may lie past the edge).

        Returns the crossing position as a fraction 0..1 along the edge when the
        user should switch, else None. After returning a fraction it disarms itself.
        """
        mon = monitor_at(cur[0], cur[1], monitors)
        if mon is None:
            self._reset()
            return None
        over, inside, along, length, beyond = _edge_geometry(self.side, mon, target[0], target[1])

        if not self.armed:
            if inside >= self.s.rearm_px:
                self.armed = True
            self._reset()
            return None
        if over <= 0 or monitor_at(beyond[0], beyond[1], monitors) is not None:
            self._reset()  # not pushing, or an inner edge with another monitor beyond
            return None
        if buttons_held or along < self.s.corner_px or along > length - 1 - self.s.corner_px:
            self._reset()
            return None

        now = self.clock()
        if self._since is None:
            self._since = now
        self._push += over
        if self._push >= self.s.push_px and (now - self._since) * 1000 >= self.s.delay_ms:
            self.disarm()
            return along / max(length - 1, 1)
        return None


def entry_point(side: str, fraction: float, monitors: Sequence[Rect]) -> tuple[int, int]:
    """Where the cursor should appear on THIS PC's ``side`` edge for a crossing at ``fraction``.

    The fraction is mapped across the whole desktop's extent along that edge, then
    placed on the outermost monitor at that height/width (nearest monitor if it
    falls in a gap between monitors of different sizes).
    """
    fraction = min(max(fraction, 0.0), 1.0)
    vertical = side in ("left", "right")
    lo = min(m[1] if vertical else m[0] for m in monitors)
    hi = max(m[3] if vertical else m[2] for m in monitors)
    pos = lo + round(fraction * (hi - lo - 1))

    def span(m):
        return (m[1], m[3]) if vertical else (m[0], m[2])

    cands = [m for m in monitors if span(m)[0] <= pos < span(m)[1]]
    if not cands:
        nearest = min(monitors, key=lambda m: min(abs(pos - span(m)[0]), abs(pos - (span(m)[1] - 1))))
        pos = _clamp(pos, span(nearest)[0], span(nearest)[1] - 1)
        cands = [nearest]
    if side == "left":
        m = min(cands, key=lambda m: m[0])
        return m[0], pos
    if side == "right":
        m = max(cands, key=lambda m: m[2])
        return m[2] - 1, pos
    if side == "top":
        m = min(cands, key=lambda m: m[1])
        return pos, m[1]
    m = max(cands, key=lambda m: m[3])
    return pos, m[3] - 1


class MonitorCache:
    """Monitor rectangles, refreshed at most every ``ttl`` seconds (cheap enough for hooks)."""

    def __init__(self, fetch: Callable[[], list[Rect]], ttl: float = 2.0):
        self._fetch, self._ttl = fetch, ttl
        self._value: list[Rect] = []
        self._at = float("-inf")

    def get(self) -> list[Rect]:
        now = time.monotonic()
        if now - self._at > self._ttl or not self._value:
            self._value = self._fetch()
            self._at = now
        return self._value
