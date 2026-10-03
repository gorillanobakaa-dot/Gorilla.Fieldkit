"""The thermal suite: sensors proven against load, a governor that moves the cap and kills the build.

2026-10-02 05:28: the laptop reset mid-compile with the governor reading a chassis zone stuck at 41.85 C. Measured
afterwards: TPFanControl's EC `cpu` sensor, read from its window, went 31 -> 36 C under a 20-second all-core load
and back; the chassis zone did not move at all.
"""
from fieldkit.thermal import governor, sensors


def test_status_line_parses_and_placeholders_never_win():
    temp, vec = sensors.parse_status("Fan: 0x04 / Switch: 29° C (29; 0; 29; 0; 0; 29; 0; 148; 0; 1; 0; 0;)")
    assert temp == 29.0 and vec[0] == 29.0 and 148.0 in vec
    assert sensors.parse_status("[10/2/2026] Another instance owns the fan") == (None, [])
    # the Log edit quotes an old 'Switch: 66° C' line: only the Status edit (starts with 'Fan:') is read
    texts = ["[10/2/2026 5:34:02] Fan: 0x80 / Switch: 66° C (44; 0; 44; 0; 0; 44; 0; 148; 0; 1; 66; 0;)",
             "Fan: 0x04 / Switch: 31° C (31; 0; 31; 0; 0; 31; 0; 148; 0; 1; 0; 0;)"]
    sensors._tpfan_window_text = lambda: texts
    assert sensors.tpfan_window() == 31.0
    # the Switch value carries the constant `pwr` sensor (66) once it reads: only the cpu sensor counts
    sensors._tpfan_window_text = lambda: ["Fan: 0x04 / Switch: 66° C (40; 0; 40; 0; 0; 40; 0; 148; 0; 1; 66; 0;)"]
    assert sensors.tpfan_window() == 40.0


def test_csv_source_takes_the_max_real_sensor(tmp_path):
    f = tmp_path / "TPFanControl_csv.txt"
    f.write_text("time,cpu,crd,x7d,no8\n05:40:01,41,39,40,148\n05:40:11,57,40,41,148\n", encoding="utf-8")
    assert sensors.tpfan_csv(f) == 57.0
    assert sensors.tpfan_csv(tmp_path / "missing.txt") is None


def test_prove_live_demands_movement_under_load():
    readings = iter([30.0, 31.0, 33.0, 36.0, 38.0, 39.0, 40.0])
    class P:
        def kill(self): pass
    ok, detail = sensors.prove_live(lambda: next(readings), settle=0, samples=6, interval=0, load=lambda n: [P()])
    assert ok and "idle 30.0 C" in detail
    ok, detail = sensors.prove_live(lambda: 41.85, settle=0, samples=6, interval=0, load=lambda n: [P()])
    assert not ok and "not a live CPU sensor" in detail
    assert sensors.prove_live(lambda: None, settle=0, samples=1, interval=0, load=lambda n: [])[0] is False


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _run(temps, perf=90.0, start_cap=100, interval=3.0):
    """Drive the governor through `temps` synchronously (sleep advances the fake clock). -> (gov, caps, events)."""
    it = iter(temps)
    caps, events, killed, done = [], [], [], []

    def sensor():
        try:
            return next(it)
        except StopIteration:
            done.append(True)
            return None
    clock = Clock()
    gov = governor.Governor(sensor=sensor, target_c=75.0, interval=interval, kill=killed.append,
                            on_event=events.append, set_cap=caps.append, read_cap=lambda: start_cap, perf=lambda: perf,
                            clock=clock, sleep=lambda s: setattr(clock, "t", clock.t + s))
    # run the loop inline; it ends when the readings run out
    gov._halt = type("H", (), {"is_set": lambda self: bool(done), "wait": lambda self, s: None})()
    gov.run()
    return gov, caps, events, killed


def test_cap_steps_down_when_hot_and_back_up_when_cool():
    gov, caps, events, killed = _run([70, 79, 80, 81, 70, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60])
    assert caps[:3] == [90, 80, 70]                       # three hot samples: -10 each
    assert 75 in caps and caps[-1] == 100                 # cool for 45 s: +5 steps; restored to the start cap at the end
    assert killed == [] and gov.verdict is None


def test_hot_goes_to_the_floor_and_ceiling_kills():
    gov, caps, events, killed = _run([70, 91, 85])
    assert caps[0] == governor.FLOOR
    gov, caps, events, killed = _run([70, 96])
    assert killed and "96.0 C >= 95 C" in gov.verdict and caps[0] == governor.FLOOR and caps[-1] == 100


def test_a_surface_sensor_is_never_declared_dead():
    it = iter([30.0] * 70); done = []
    def sensor():
        try: return next(it)
        except StopIteration:
            done.append(True); return None
    clock = Clock(); killed = []
    gov = governor.Governor(sensor=sensor, target_c=75.0, interval=3.0, kill=killed.append, on_event=lambda e: None,
                            set_cap=lambda c: None, read_cap=lambda: 100, perf=lambda: 90.0, clock=clock,
                            sleep=lambda s: setattr(clock, "t", clock.t + s), dead_check=False)
    gov._halt = type("H", (), {"is_set": lambda self: bool(done), "wait": lambda self, s: None})()
    gov.run()
    assert killed == [] and gov.verdict is None


def test_a_stuck_sensor_under_load_kills_but_idle_flatness_does_not():
    gov, caps, events, killed = _run([41.85] * 61)
    assert killed and "stuck at 41.85" in gov.verdict
    gov, caps, events, killed = _run([41.85] * 61, perf=20.0)
    assert killed == [] and gov.verdict is None
    gov, caps, events, killed = _run([31.0, 32.0] + [33.0] * 70)         # a skin sensor plateaus after moving: alive
    assert killed == [] and gov.verdict is None


def test_no_reading_for_too_long_kills():
    gov, caps, events, killed = _run([70] + [None] * 12, interval=3.0)
    assert killed and "no temperature reading" in gov.verdict


def test_status_file_source_reads_cpu_and_rejects_stale(tmp_path):
    import os, time
    f = tmp_path / "status.txt"
    f.write_text("ts=123;mode=5;cpu=58;max=58;load=97;fan=0x07;rpm=4312\r\n", encoding="utf-8")
    assert sensors.tpfan_status(f) == 58.0
    f.write_text("ts=1;mode=5;cpu=148;max=148;load=0;fan=0x04;rpm=2900\r\n", encoding="utf-8")
    assert sensors.tpfan_status(f) is None                           # placeholder register, not a reading
    f.write_text("ts=1;mode=5;cpu=58;max=58;load=0;fan=0x04;rpm=2900\r\n", encoding="utf-8")
    os.utime(f, (time.time() - 120, time.time() - 120))
    assert sensors.tpfan_status(f) is None                           # two minutes old: the engine is not writing
    assert sensors.tpfan_status(tmp_path / "none.txt") is None
    assert sensors.PROVIDERS[0][0] == "tpfan_status"


def test_surface_sensors_are_graded_and_capped():
    assert sensors.grade(30.0, 45.0) == "die" and sensors.grade(30.0, 36.0) == "surface"
    gov, caps, events, killed = _run([40, 40, 40], start_cap=100)
    assert caps == []                                           # no ceiling: nothing to apply
    it = iter([40, 40, 40]); caps, events = [], []
    done = []
    def sensor():
        try: return next(it)
        except StopIteration:
            done.append(True); return None
    clock = Clock()
    gov = governor.Governor(sensor=sensor, target_c=75.0, interval=3.0, kill=lambda w: None, on_event=events.append,
                            set_cap=caps.append, read_cap=lambda: 100, perf=lambda: 90.0, clock=clock,
                            sleep=lambda s: setattr(clock, "t", clock.t + s), max_cap=80)
    gov._halt = type("H", (), {"is_set": lambda self: bool(done), "wait": lambda self, s: None})()
    gov.run()
    assert caps[0] == 80 and caps[-1] == 100 and gov.start_cap == 80   # ceiling applied at start, original put back


def test_a_slow_die_sensor_is_regraded_after_a_longer_load(monkeypatch):
    class P:
        def kill(self): pass
    readings = iter([30.0, 31, 32, 33, 34, 35, 36, 37,        # the 20-second test: +7, "surface"
                     38, 45, 52, 58, 63, 68, 72, 75, 78])     # the 60-second test: +48, "die"
    monkeypatch.setattr(sensors, "PROVIDERS", (("fake", lambda: next(readings)),))
    name, fn, detail = sensors.best(prove=True, settle=0, samples=6, interval=0, load=lambda n: [P()])
    assert name == "fake" and "[die sensor]" in detail and "after a 60 s load" in detail


def test_thermal_watch_stops_and_joins_the_governor_on_ctrl_c(monkeypatch):
    """fieldkit/cli.py `thermal watch`: a KeyboardInterrupt in the watch loop must still run gov.stop() and
    gov.join(): the governor restores the original processor cap when its thread ends. Fake sensor, fake
    governor: no powercfg, no thread."""
    import argparse
    import time as _time
    import pytest
    from fieldkit import cli
    events = []

    class FakeGov:
        cap, peak = 80, 41.0

        def __init__(self, sensor, **kw):
            events.append(("init", kw.get("target_c")))

        def start(self):
            events.append("start")

        def is_alive(self):
            return True

        def stop(self):
            events.append("stop")

        def join(self, timeout=None):
            events.append(("join", timeout))

    def ctrl_c(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(sensors, "best", lambda prove=False: ("fake-cpu", lambda: 40.0, "proven by the test"))
    monkeypatch.setattr(governor, "Governor", FakeGov)
    monkeypatch.setattr(_time, "sleep", ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_thermal(argparse.Namespace(action="watch", seconds=60, target=75.0))
    assert events == [("init", 75.0), "start", "stop", ("join", 15)]


def test_the_sensor_proof_stops_loading_at_the_ceiling():
    from fieldkit.thermal import sensors
    readings = iter([40.0] + [82.0] * 50)
    killed = []

    class P:
        def kill(self):
            killed.append(1)

    ok, detail = sensors.prove_live(lambda: next(readings), settle=8, samples=6, interval=0.0, threads=2,
                                    load=lambda n: [P() for _ in range(n)], ceiling=80.0)
    assert ok and killed == [1, 1] and "82.0" in detail
