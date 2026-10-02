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


def test_a_stuck_sensor_under_load_kills_but_idle_flatness_does_not():
    gov, caps, events, killed = _run([41.85] * 61)
    assert killed and "stuck at 41.85" in gov.verdict
    gov, caps, events, killed = _run([41.85] * 61, perf=20.0)
    assert killed == [] and gov.verdict is None


def test_no_reading_for_too_long_kills():
    gov, caps, events, killed = _run([70] + [None] * 12, interval=3.0)
    assert killed and "no temperature reading" in gov.verdict
