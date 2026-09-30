"""sensors-gorilla thermal-profile-daemon: its fan decisions, on a fake Sony sysfs and a simulated clock.

Proves the promises in its header: step up at once, step down only after
sustained cool, force the fan after a sustained 93 C and release it below 85 C,
refuse to run without the full profile ladder, and restore 'balanced' on exit.
The real firmware answer belongs to the owner's VAIO.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.core import settings

REPO = Path(settings.expand("${FIELDKIT}")) / "toolbox" / "sensors-gorilla"
pytestmark = pytest.mark.skipif(not (REPO / "thermal-profile-daemon").is_file(), reason="sensors-gorilla not gathered")


class Stop(Exception):
    pass


def daemon(tmp_path, monkeypatch, temps, start="silent", profiles="silent balanced performance", forced="0"):
    """Run the loop over `temps` (one reading per 5 s poll); return (module, sony dir, handlers)."""
    loader = importlib.machinery.SourceFileLoader("thermal_daemon_under_test", str(REPO / "thermal-profile-daemon"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    d = importlib.util.module_from_spec(spec)
    loader.exec_module(d)
    sony = tmp_path / "sony-laptop"
    sony.mkdir()
    (sony / "thermal_control").write_text(start)
    (sony / "thermal_profiles").write_text(profiles)
    (sony / "fan_forced").write_text(forced)
    monkeypatch.setattr(d, "SONY_DIR", str(sony))
    monkeypatch.setattr(d, "THERMAL_CONTROL", str(sony / "thermal_control"))
    monkeypatch.setattr(d, "FAN_FORCED", str(sony / "fan_forced"))
    monkeypatch.setattr(d, "CONFIG_PATH", str(tmp_path / "none.conf"))
    readings = iter(temps)
    clock = [1000.0]                     # a real monotonic clock never reads 0

    def temp():
        try:
            return next(readings)
        except StopIteration:
            raise Stop
    monkeypatch.setattr(d, "package_temp_c", temp)
    monkeypatch.setattr(d.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(d.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    handlers = {}
    monkeypatch.setattr(d.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    d.history = []
    real_set = d.set_profile
    monkeypatch.setattr(d, "set_profile", lambda p, dry=False: (d.history.append((clock[0], p)), real_set(p, dry)))
    try:
        d.cmd_run()
    except Stop:
        pass
    return d, sony, handlers


def state(sony):
    return (sony / "thermal_control").read_text(), (sony / "fan_forced").read_text()


def test_steps_up_immediately(tmp_path, monkeypatch):
    d, sony, _ = daemon(tmp_path, monkeypatch, [60, 75, 85])
    assert [p for _, p in d.history] == ["balanced", "performance"]
    assert d.history[0][0] == 1005.0 and state(sony)[0] == "performance"


def test_steps_down_only_after_sustained_cool(tmp_path, monkeypatch):
    d, sony, _ = daemon(tmp_path, monkeypatch, [85] + [60] * 36, start="performance")
    assert d.history == []                                          # cool for 175 s: not yet
    (tmp_path / "b").mkdir()
    d, sony, _ = daemon(tmp_path / "b", monkeypatch, [85] + [60] * 37, start="performance")
    assert d.history == [(1185.0, "balanced")]                      # cool since 1005, 180 s later


def test_balanced_to_silent_needs_five_cool_minutes(tmp_path, monkeypatch):
    d, sony, _ = daemon(tmp_path, monkeypatch, [50] * 62, start="balanced")
    assert d.history == [(1300.0, "silent")]


def test_fan_forced_after_sustained_heat_and_released_below_85(tmp_path, monkeypatch):
    d, sony, _ = daemon(tmp_path, monkeypatch, [95, 95, 95], start="performance")
    assert state(sony)[1] == "0"                                    # 10 s over: not yet
    (tmp_path / "b").mkdir()
    d, sony, _ = daemon(tmp_path / "b", monkeypatch, [95, 95, 95, 95], start="performance")
    assert state(sony)[1] == "1"                                    # 15 s over: forced
    (tmp_path / "c").mkdir()
    d, sony, _ = daemon(tmp_path / "c", monkeypatch, [95, 95, 95, 95, 86, 84], start="performance")
    assert state(sony)[1] == "0"                                    # held at 86, released at 84


def test_exit_restores_balanced_and_releases_the_fan(tmp_path, monkeypatch):
    d, sony, handlers = daemon(tmp_path, monkeypatch, [95] * 5, start="performance")
    assert state(sony) == ("performance", "1")
    with pytest.raises(SystemExit):
        handlers[d.signal.SIGTERM](d.signal.SIGTERM, None)
    assert state(sony) == ("balanced", "0")


def test_refuses_an_incomplete_profile_ladder(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="does not offer the 'silent' profile"):
        daemon(tmp_path, monkeypatch, [50], profiles="balanced performance")


def test_no_temperature_holds_the_profile(tmp_path, monkeypatch):
    d, sony, _ = daemon(tmp_path, monkeypatch, [None, None, None], start="balanced")
    assert d.history == [] and state(sony)[0] == "balanced"


def test_dashboard_runs_once_without_sysfs():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}               # as a Linux terminal would be
    r = subprocess.run([sys.executable, str(REPO / "sensors.gorilla"), "--once"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60, env=env)
    assert r.returncode == 0, r.stderr[-1500:]
