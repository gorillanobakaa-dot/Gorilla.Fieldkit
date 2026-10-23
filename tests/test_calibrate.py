# Version: 1.0.0 · updated 26-10-22-14-40
"""fieldkit eta calibrate: fixed workloads, the thermal stop at the cap, the store. Small scales only; no real load."""
import json
from contextlib import contextmanager

import pytest

from fieldkit.core import calibrate as cal


def test_workload_is_fixed():
    assert cal._buffer(1) == cal._buffer(1) and len(cal._buffer(1)) == 1 << 20
    assert cal._source(5) == cal._source(5)
    assert cal.WORKLOAD == "fk-cal-1"          # change the name with the workload, never one without the other


def test_cpu_single_and_multi_small():
    s = cal.cpu_single(repeat=2, scale=0.02)
    assert s["units_per_s"] > 0 and s["seconds_best"] == min(s["seconds_all"])
    m = cal.cpu_multi(workers=2, per_worker=1, scale=0.02, single=s)
    assert m["units"] == 2 and m["scaling"] > 0


def test_disk_and_ram_small(tmp_path):
    d = cal.disk(near=tmp_path, mib=2, seconds=0.2)
    assert d["seq_write_mb_s"] > 0 and d["rand4k_sync_write_iops"] > 0
    assert list(tmp_path.iterdir()) == []                          # the trial file and folder are gone
    assert cal.disk(near=tmp_path / "first-run", mib=1, seconds=0.1)["seq_write_mb_s"] > 0   # 2026-10-22: no folder yet
    r = cal.ram(mib=8)
    assert r["total_gb"] > 0 and r["copy_gb_s"] > 0


def test_minutes_to():
    assert cal.minutes_to([(0, 45.0), (60, 60.0), (150, 70.0), (160, 72.0)]) == 2.5
    assert cal.minutes_to([(0, 45.0), (60, None), (120, 69.9)]) is None


class Proc:
    killed = 0

    def kill(self):
        Proc.killed += 1


def test_thermal_stops_load_at_the_cap():
    Proc.killed = 0
    temps = iter([40.0, 50.0, 60.0, 70.5, 99.0, 99.0])
    clock = iter(range(0, 1000, 30))
    r = cal.thermal(lambda: next(temps), minutes=10, interval=0, load=lambda n: [Proc() for _ in range(n)],
                    clock=lambda: next(clock), sleep=lambda s: None)
    assert r["peak_c"] == 70.5 and r["minutes_to_target"] is not None and "70 C after" in r["verdict"]
    assert Proc.killed > 0                                       # the load stopped at the cap, not later


def test_thermal_waits_to_start_cool():
    temps = iter([52.0, 48.0, 44.0, 60.0, 71.0])
    clock = iter(range(0, 1000, 10))
    loaded = []
    r = cal.thermal(lambda: next(temps), minutes=10, interval=0, load=lambda n: loaded.append(n) or [],
                    clock=lambda: next(clock), sleep=lambda s: None)
    assert r["idle_c"] == 44.0 and r["cooled_s"] > 0 and loaded


def test_thermal_never_cool_is_not_measured():
    clock = iter(range(0, 10000, 30))
    r = cal.thermal(lambda: 60.0, interval=0, load=lambda n: pytest.fail("loaded while hot"),
                    clock=lambda: next(clock), sleep=lambda s: None)
    assert r["minutes_to_target"] is None and "not measured" in r["verdict"]


def test_thermal_not_reached():
    clock = iter(range(0, 10000, 60))
    r = cal.thermal(lambda: 55.0, minutes=3, interval=0, load=lambda n: [], clock=lambda: next(clock), sleep=lambda s: None, start_c=60)
    assert r["minutes_to_target"] is None and "not reached in 3 min" in r["verdict"]


def test_store_keeps_previous(tmp_path):
    p = cal.store({"host": "h", "n": 1}, host="h", folder=tmp_path)
    cal.store({"host": "h", "n": 2}, host="h", folder=tmp_path)
    got = json.loads(p.read_text(encoding="utf-8"))
    assert got["n"] == 2 and got["previous"]["n"] == 1 and "previous" not in got["previous"]


def test_ratio():
    a, b = {"cpu_multi": {"units_per_s": 9.0}}, {"cpu_multi": {"units_per_s": 3.0}}
    assert cal.ratio(a, b) == 3.0 and cal.ratio(a, {}) is None


def test_calibrate_runs_inside_the_guard(monkeypatch, tmp_path):
    entered = []

    @contextmanager
    def guard(say, label, sensor):
        entered.append(label)
        yield None

    monkeypatch.setattr(cal, "MACHINES", tmp_path)
    monkeypatch.setattr(cal, "cpu_single", lambda: {"units_per_s": 1.0, "seconds_best": 1.0})
    monkeypatch.setattr(cal, "cpu_multi", lambda single: {"units_per_s": 5.0, "workers": 4, "scaling": 5.0})
    monkeypatch.setattr(cal, "disk", lambda: {"seq_write_mb_s": 1, "rand4k_sync_write_iops": 1})
    monkeypatch.setattr(cal, "ram", lambda: {"total_gb": 1, "available_gb": 1, "copy_gb_s": 1})
    monkeypatch.setattr(cal, "cpu_name", lambda: "test cpu")
    r, p = cal.calibrate(quick=True, say=lambda *a: None, guard=guard, sensor=lambda: 40.0)
    assert entered == ["calibrate"] and p.parent == tmp_path and "not measured" in r["thermal"]["verdict"]
    assert any(l.startswith("VERDICT  CALIBRATED") for l in cal.lines(r, p))


def test_refused_guard_stops_everything(monkeypatch, tmp_path):
    from fieldkit.thermal.buildguard import BuildRefused

    @contextmanager
    def guard(say, label, sensor):
        raise BuildRefused("fan check: not cooling")
        yield None

    monkeypatch.setattr(cal, "MACHINES", tmp_path)
    monkeypatch.setattr(cal, "cpu_single", lambda: pytest.fail("loaded without the guard"))
    with pytest.raises(BuildRefused):
        cal.calibrate(say=lambda *a: None, guard=guard, sensor=lambda: 40.0)
    assert list(tmp_path.iterdir()) == []
