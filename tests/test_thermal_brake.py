"""Crash 2026-10-27 ~17:10: the surface ceiling was printed and never applied, turbo stayed on, nothing braked outside
the guard. fieldkit/thermal/brake.py, watch.py, the Governor's ceiling/persistent mode and the guard. Fake sensors,
fake caps, fake boost and a tmp state folder only: the real power plan is never touched."""
import os
import time
import types

import pytest

from fieldkit.buildh import buildrun
from fieldkit.thermal import brake, buildguard, governor, sensors, watch

pytestmark = pytest.mark.real_build_guard


class Boost:
    def __init__(self, mode=2):
        self.mode, self.writes = mode, []

    def read(self):
        return self.mode

    def write(self, m):
        self.mode = m
        self.writes.append(m)
        return True


@pytest.fixture
def fake(tmp_path, monkeypatch):
    b, caps = Boost(), []
    monkeypatch.setattr(brake, "_dir", lambda: tmp_path)
    (tmp_path / "fan").mkdir()
    monkeypatch.setattr(brake, "fan_flag_path", lambda: tmp_path / "fan" / "guard-hold.txt")
    monkeypatch.setattr(brake, "boost_read", b.read)
    monkeypatch.setattr(brake, "boost_set", b.write)
    monkeypatch.setattr(governor, "set_cap", caps.append)
    monkeypatch.setattr(governor, "read_cap", lambda: 100)
    monkeypatch.setattr(sensors, "perf_percent", lambda: 90.0)
    return types.SimpleNamespace(boost=b, caps=caps, dir=tmp_path)


def _sync(gov, temps):
    """Drive a governor inline through `temps` on a fake clock; it stops after the last reading."""
    it, clock, out = iter(temps), [0.0], [False]

    def sensor():
        v = next(it, None)
        out[0] = v is None
        return v
    gov.sensor, gov._clock = sensor, lambda: clock[0]
    gov._sleep = lambda s: clock.__setitem__(0, clock[0] + s)
    gov._halt = type("H", (), {"is_set": lambda self: out[0], "wait": lambda self, s: None})()
    gov.run()


def test_a_live_ceiling_lowers_the_cap_and_lets_it_climb_back(fake):
    gov = governor.Governor(None, target_c=67, set_cap=fake.caps.append, read_cap=lambda: 100,
                            on_event=lambda e: None, perf=lambda: 10.0)
    gov.ceiling = lambda: 80 if gov.samples <= 1 else 100        # a job holds, then ends
    _sync(gov, [50.0] * 40)
    assert fake.caps[0] == 80                                     # held at once, before any heat
    assert max(fake.caps) > 80 and fake.caps[-1] == 100           # climbed back, restored on stop


def test_the_machine_governor_never_exits_past_the_kill_line(fake):
    killed, events = [], []
    gov = governor.Governor(None, target_c=67, kill=killed.append, persistent=True, set_cap=fake.caps.append,
                            read_cap=lambda: 100, on_event=events.append, perf=lambda: 90.0, dead_check=False)
    _sync(gov, [96.0, 96.0, 50.0, 50.0])
    assert not killed and governor.FLOOR in fake.caps
    assert sum("HOLD" in e for e in events) == 1                  # said once, not every 3 s
    assert gov.samples == 5 and gov.verdict is None               # kept watching; cleared once cool


def test_a_hold_never_touches_turbo_or_the_fan_flag(fake):
    """2026-10-28: the guard's power-plan and fan levers are out (439/408 powercfg resets a day, three cutoffs)."""
    alive = {111, 222}
    with brake.hold("a", say=lambda m: None, pid=111, alive=lambda p: p in alive):
        with brake.hold("b", say=lambda m: None, pid=222, alive=lambda p: p in alive):
            assert len(list((fake.dir / "holds").glob("*.json"))) == 2
        assert not (fake.dir / "fan" / "guard-hold.txt").exists()
    assert fake.boost.writes == [] and not (fake.dir / "boost-original.json").exists()


def test_a_crashed_guard_leaves_turbo_off_and_the_next_run_restores_it(fake):
    (fake.dir / "holds").mkdir()
    (fake.dir / "holds" / "999.json").write_text('{"pid": 999}', encoding="utf-8")
    (fake.dir / "boost-original.json").write_text('{"original": 2}', encoding="utf-8")
    fake.boost.mode = 0
    (fake.dir / "fan" / "guard-hold.txt").write_text("999 crashed", encoding="ascii")
    assert brake.restore_stale_boost(alive=lambda p: False, say=lambda m: None) == 2
    assert fake.boost.mode == 2 and not list((fake.dir / "holds").glob("*.json"))
    assert not (fake.dir / "fan" / "guard-hold.txt").exists()


def _guard(monkeypatch, surface=True):
    monkeypatch.setattr(buildguard, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(buildrun, "thermal_sensor", lambda say: ("ec", lambda: 50.0, surface))
    said = []
    return said, buildguard._fan_and_governor(said.append, "t", None, False, None, None)


def test_the_guard_only_watches(fake, monkeypatch):
    said, cm = _guard(monkeypatch)
    with cm:
        time.sleep(0.2)
        assert list((fake.dir / "holds").glob("*.json"))
    assert fake.caps == [] and fake.boost.writes == [] and any("watch only" in s for s in said)


def test_watch_forever_writes_no_cap_and_is_one_per_computer(fake, monkeypatch):
    (fake.dir / "holds").mkdir()
    (fake.dir / "holds" / f"{os.getpid()}.json").write_text(f'{{"pid": {os.getpid()}}}', encoding="utf-8")
    said = []
    rc = watch.forever(target=67, say=said.append, sensor=(lambda: 50.0, True), ticks=20,
                       sleep=lambda s: time.sleep(0.02), folder=fake.dir)
    assert rc == 0 and fake.caps == []
    assert not (fake.dir / "machine-governor.json").exists()                 # lease cleared on stop
    brake.lease_write(fake.dir)
    assert watch.forever(target=67, say=said.append, sensor=(lambda: 50.0, True), ticks=1, folder=fake.dir) == 0
    assert "already runs" in said[-1]


def test_a_stale_cap_marker_at_the_current_cap_writes_nothing(tmp_path, monkeypatch):
    from fieldkit.thermal import governor
    m = tmp_path / "cap-original.json"
    monkeypatch.setattr(governor, "_marker", lambda: m)
    writes = []
    monkeypatch.setattr(governor, "set_cap", lambda pct: writes.append(pct))
    monkeypatch.setattr(governor, "read_cap", lambda: 100)
    m.write_text('{"original": 100}', encoding="utf-8")
    said = []
    assert governor.restore_stale_cap(said.append) is None and writes == [] and not m.exists()
    assert "nothing written" in said[0]
    monkeypatch.setattr(governor, "read_cap", lambda: 40)
    m.write_text('{"original": 100}', encoding="utf-8")
    assert governor.restore_stale_cap(said.append) == 100 and writes == [100] and not m.exists()
