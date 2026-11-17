"""Capture on without the scan timer heals itself from the hook, at most once an hour (2026-11-15: absent for days)."""
from datetime import datetime, timedelta, timezone

from fieldkit.capture import switch


def _setup(tmp_path, monkeypatch, on=True):
    monkeypatch.setattr(switch.st, "capture_dir", lambda: tmp_path)
    monkeypatch.setattr(switch, "is_on", lambda d=None, now=None: on)


def test_absent_timer_is_installed_once_per_hour(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    calls = []
    absent = lambda argv: (1, "")
    t0 = datetime(2026, 11, 15, 21, 0, tzinfo=timezone.utc)
    inst = lambda: calls.append(1) or "installed"
    assert switch.ensure_schedule(now=t0, run=absent, system="linux", install=inst) == "installed"
    assert switch.ensure_schedule(now=t0 + timedelta(minutes=10), run=absent, system="linux", install=inst) == "fresh"
    assert switch.ensure_schedule(now=t0 + timedelta(hours=2), run=absent, system="linux", install=inst) == "installed"
    assert len(calls) == 2


def test_present_timer_is_left_alone(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    inst = lambda: (_ for _ in ()).throw(AssertionError("must not install"))
    r = switch.ensure_schedule(run=lambda argv: (0, "enabled\n"), system="linux", install=inst)
    assert r == "present"


def test_windows_missing_task_is_installed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = switch.ensure_schedule(run=lambda argv: (1, ""), system="win32", install=lambda: "installed")
    assert r == "installed"


def test_capture_off_installs_nothing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, on=False)
    inst = lambda: (_ for _ in ()).throw(AssertionError("must not install"))
    assert switch.ensure_schedule(run=lambda argv: (1, ""), system="linux", install=inst) == "off"
    assert not (tmp_path / "schedule-heal.stamp").exists()
