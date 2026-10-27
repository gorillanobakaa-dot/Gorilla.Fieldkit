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


def test_turbo_is_off_while_any_guarded_job_holds_and_back_after_the_last(fake):
    alive = {111, 222}
    said = []
    with brake.hold("a", say=said.append, pid=111, alive=lambda p: p in alive):
        assert fake.boost.mode == brake.BOOST_OFF
        with brake.hold("b", say=said.append, pid=222, alive=lambda p: p in alive):
            pass
        assert fake.boost.mode == brake.BOOST_OFF                 # a still runs
        assert any("stays off" in s for s in said)
    assert fake.boost.mode == 2 and not (fake.dir / "boost-original.json").exists()


def test_a_crashed_guard_leaves_turbo_off_and_the_next_run_restores_it(fake):
    (fake.dir / "holds").mkdir()
    (fake.dir / "holds" / "999.json").write_text('{"pid": 999}', encoding="utf-8")
    (fake.dir / "boost-original.json").write_text('{"original": 2}', encoding="utf-8")
    fake.boost.mode = 0
    assert brake.restore_stale_boost(alive=lambda p: False, say=lambda m: None) == 2
    assert fake.boost.mode == 2 and not list((fake.dir / "holds").glob("*.json"))


def _guard(monkeypatch, surface=True):
    monkeypatch.setattr(buildguard, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(buildrun, "thermal_sensor", lambda say: ("ec", lambda: 50.0, surface))
    said = []
    return said, buildguard._fan_and_governor(said.append, "t", None, False, None, None)


def test_the_guard_applies_the_surface_ceiling_it_announces(fake, monkeypatch):
    """The 17:10 bug: 'cap held at 80%' was printed, the governor got no max_cap, the cap stayed 100%."""
    monkeypatch.setattr(brake, "machine_alive", lambda *a, **k: False)
    said, cm = _guard(monkeypatch)
    with cm as gov:
        for _ in range(50):
            if fake.caps:
                break
            time.sleep(0.05)
        assert fake.boost.mode == brake.BOOST_OFF
    assert fake.caps[0] == buildrun.SURFACE_CAP and fake.caps[-1] == 100      # applied, then put back
    assert fake.boost.mode == 2


def test_under_the_machine_governor_the_guard_only_watches(fake, monkeypatch):
    monkeypatch.setattr(brake, "machine_alive", lambda *a, **k: True)
    said, cm = _guard(monkeypatch)
    with cm:
        time.sleep(0.2)
        assert fake.boost.mode == brake.BOOST_OFF and list((fake.dir / "holds").glob("*.json"))
    assert fake.caps == [] and any("machine governor holds the cap" in s for s in said)


def test_watch_forever_holds_the_ceiling_while_a_job_holds_and_is_one_per_computer(fake, monkeypatch):
    (fake.dir / "holds").mkdir()
    (fake.dir / "holds" / f"{os.getpid()}.json").write_text(f'{{"pid": {os.getpid()}}}', encoding="utf-8")
    said = []
    rc = watch.forever(target=67, say=said.append, sensor=(lambda: 50.0, True), ticks=20,
                       sleep=lambda s: time.sleep(0.02), folder=fake.dir)
    assert rc == 0 and buildrun.SURFACE_CAP in fake.caps
    assert not (fake.dir / "machine-governor.json").exists()                 # lease cleared on stop
    brake.lease_write(fake.dir)
    assert watch.forever(target=67, say=said.append, sensor=(lambda: 50.0, True), ticks=1, folder=fake.dir) == 0
    assert "already runs" in said[-1]
