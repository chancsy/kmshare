"""Edge detection + crossing placement, on single and multi-monitor layouts."""

import pytest

from kmshare.edge import EdgeDetector, EdgeSettings, MonitorCache, entry_point, monitor_at

ONE = [(0, 0, 1920, 1080)]
# This dev PC: 2560x1440 primary + 1080x1920 portrait monitor to its right.
TWO = [(2560, 0, 3640, 1920), (0, 0, 2560, 1440)]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def det(side, mons=ONE, **kw):
    clock = Clock()
    return EdgeDetector(side, EdgeSettings(**kw), clock=clock), clock


def push(d, cur, delta, mons=ONE, n=1, buttons=False):
    out = None
    for _ in range(n):
        out = d.update(cur, (cur[0] + delta[0], cur[1] + delta[1]), mons, buttons_held=buttons)
        if out is not None:
            return out
    return out


def test_push_threshold_right():
    d, _ = det("right", push_px=8)
    assert push(d, (1919, 540), (3, 0)) is None          # 3 px past: not yet
    assert push(d, (1919, 540), (3, 0)) is None          # 6
    f = push(d, (1919, 540), (3, 0))                     # 9 >= 8 -> switch
    assert f == pytest.approx(540 / 1079)


def test_fast_flick_crosses_at_once():
    d, _ = det("right", push_px=8)
    assert push(d, (1800, 300), (150, 0)) == pytest.approx(300 / 1079)


def test_moving_away_resets_push():
    d, _ = det("right", push_px=8)
    push(d, (1919, 540), (5, 0))
    push(d, (1919, 540), (-2, 0))                        # moved back in: push resets
    assert push(d, (1919, 540), (5, 0)) is None


@pytest.mark.parametrize("side,cur,delta,expect_along", [
    ("left", (0, 100), (-10, 0), 100 / 1079),
    ("top", (960, 0), (0, -10), 960 / 1919),
    ("bottom", (480, 1079), (0, 10), 480 / 1919),
])
def test_all_sides(side, cur, delta, expect_along):
    d, _ = det(side, push_px=8)
    assert push(d, cur, delta) == pytest.approx(expect_along)


def test_corner_dead_zone():
    d, _ = det("right", push_px=1, corner_px=40)
    assert push(d, (1919, 10), (20, 0)) is None          # 10 px from the top corner
    assert push(d, (1919, 1060), (20, 0)) is None        # near the bottom corner
    assert push(d, (1919, 41), (20, 0)) is not None


def test_button_held_blocks():
    d, _ = det("right", push_px=1)
    assert push(d, (1919, 540), (20, 0), buttons=True) is None
    assert push(d, (1919, 540), (20, 0)) is not None


def test_delay_requires_sustained_push():
    d, clock = det("right", push_px=1, delay_ms=150)
    assert push(d, (1919, 540), (5, 0)) is None          # t=0
    clock.t = 0.1
    assert push(d, (1919, 540), (5, 0)) is None          # 100 ms
    clock.t = 0.16
    assert push(d, (1919, 540), (5, 0)) is not None      # 160 ms


def test_rearm_after_switch():
    d, _ = det("right", push_px=1, rearm_px=20)
    assert push(d, (1919, 540), (5, 0)) is not None
    assert push(d, (1919, 540), (5, 0)) is None          # disarmed: no bounce
    push(d, (1910, 540), (-5, 0))                        # 14 px away: still off
    assert not d.armed
    push(d, (1900, 540), (-5, 0))                        # 24 px away: re-armed
    assert d.armed
    assert push(d, (1919, 540), (5, 0)) is not None


def test_inner_edge_between_monitors_is_ignored():
    d, _ = det("right", push_px=1)
    # Primary's right edge at y=500 has the portrait monitor beyond it.
    assert push(d, (2559, 500), (10, 0), mons=TWO) is None
    # The portrait monitor's right edge is the outer edge.
    assert push(d, (3639, 960), (10, 0), mons=TWO) == pytest.approx(960 / 1919)


def test_bottom_edges_with_uneven_monitors():
    d, _ = det("bottom", push_px=1)
    assert push(d, (100, 1439), (0, 10), mons=TWO) is not None   # primary bottom: outer
    d2, _ = det("bottom", push_px=1)
    assert push(d2, (3000, 1919), (0, 10), mons=TWO) is not None  # portrait bottom: outer


def test_entry_point_single_monitor():
    assert entry_point("left", 0.5, ONE) == (0, 540)
    assert entry_point("right", 0.0, ONE) == (1919, 0)
    assert entry_point("top", 1.0, ONE) == (1919, 0)
    assert entry_point("bottom", 0.25, ONE) == (480, 1079)


def test_entry_point_two_monitors():
    assert entry_point("right", 0.5, TWO) == (3639, 960)      # outermost right = portrait
    assert entry_point("left", 0.25, TWO) == (0, 480)          # primary is leftmost at that height
    assert entry_point("left", 0.9, TWO) == (2560, 1727)       # below the primary: portrait's left edge
    x, y = entry_point("bottom", 0.1, TWO)
    assert (x, y) == (364, 1439)                               # primary's bottom


def test_entry_point_gap_snaps_to_nearest():
    gap = [(0, 0, 1000, 1000), (1000, 500, 2000, 1500)]
    x, y = entry_point("top", 0.2, gap)
    assert monitor_at(x, y, gap) is not None and y == 0


def test_monitor_cache_refreshes():
    calls = []
    cache = MonitorCache(lambda: calls.append(1) or ONE, ttl=0)
    cache.get()
    cache.get()
    assert len(calls) == 2
