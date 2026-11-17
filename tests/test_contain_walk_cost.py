"""The container's process walk must not ask psutil for "status" on Windows: there it takes a whole-system snapshot
per process (2.1 s per walk vs 0.02 s, 2026-11-17), and set-proof's ~1350 patch steps made the pre-push hook run
for hours. Linux keeps "status" (zombies exist there)."""
from fieldkit.core import contain


class _P:
    def __init__(self, pid, ppid):
        self.info = {"pid": pid, "ppid": ppid, "create_time": 100.0 + pid, "status": "running"}


def _walk_with(monkeypatch, is_win):
    seen = []

    def fake_iter(attrs):
        seen.append(list(attrs))
        return [_P(10, 1), _P(11, 10)]

    monkeypatch.setattr(contain, "IS_WIN", is_win)
    monkeypatch.setattr(contain.sys, "platform", "win32" if is_win else "darwin")
    monkeypatch.setattr(contain.psutil, "process_iter", fake_iter)
    monkeypatch.setattr(contain.os, "getpgid", lambda pid: 0, raising=False)
    c = contain.Container.__new__(contain.Container)
    c.job, c.root, c.root_ct, c.tracked = None, 10, 110.0, {}
    import threading
    c._lock = threading.Lock()
    out = c._walk()
    return seen, out


def test_windows_walk_does_not_request_status(monkeypatch):
    seen, out = _walk_with(monkeypatch, True)
    assert seen and "status" not in seen[0]
    assert set(out) == {11}


def test_posix_walk_keeps_status(monkeypatch):
    seen, _ = _walk_with(monkeypatch, False)
    assert "status" in seen[0]
