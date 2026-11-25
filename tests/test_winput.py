"""Injector: cursor moves also report user activity (idle timer), throttled."""

from kmshare import winput


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_moves_report_activity_at_most_once_per_second(monkeypatch, no_cursor):
    sent = []
    monkeypatch.setattr(winput, "_mouse", lambda flags, data=0: sent.append((flags, data)) or True)
    clock = Clock()
    inj = winput.Injector(clock=clock)

    inj.move(5, 0)                                   # first move: activity reported
    clock.t = 0.5
    inj.move(5, 0)                                   # 0.5 s later: throttled
    clock.t = 1.0
    inj.move(5, 0)                                   # 1 s after the first: reported again
    assert sent == [(winput.MOUSEEVENTF_MOVE, 0)] * 2
    assert len(no_cursor["sets"]) == 3               # every move still placed exactly


def test_activity_move_is_zero_distance(monkeypatch):
    captured = []
    monkeypatch.setattr(winput, "_send", lambda inp: captured.append(inp) or True)
    winput._mouse(winput.MOUSEEVENTF_MOVE)
    mi = captured[0].mi
    assert (mi.dx, mi.dy, mi.dwFlags, mi.dwExtraInfo) == (0, 0, winput.MOUSEEVENTF_MOVE, winput.KMSHARE_TAG)
