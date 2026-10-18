# Version: 1.0.0 · updated 26-10-18-20-30
"""fieldkit eta: answers from measurements, with their basis; no basis, no number (owner, 2026-10-18)."""
import os
import time

from fieldkit.core import eta


def test_history_splits_full_and_incremental_builds(tmp_path):
    log = ("20:49:18  build attempt 1: gorilla_build.py build\n00:53:16  build OK (1759 lines)\n"
           "09:12:00  build attempt 1: gorilla_build.py build\n09:14:30  build OK (120 lines)\n"
           "09:19:52  package attempt 1: gorilla_build.py package\n09:21:43  package OK (122 lines)\n")
    got = eta.build_log_durations(log)
    assert got == [("build full", 14638), ("build incremental", 150), ("package", 111)]   # midnight wrap counted


def test_no_history_means_no_number(tmp_path):
    a = eta.from_history("never ran", path=tmp_path / "h.jsonl")
    assert a["remaining_s"] is None and "no recorded run" in a["evidence"]


def test_past_every_recorded_run_says_so_instead_of_a_negative(tmp_path):
    h = tmp_path / "h.jsonl"
    eta.record("firefox build full", 4500, path=h)
    a = eta.from_history("firefox build full", elapsed=5000, path=h)
    assert a["remaining_s"] is None and "past every recorded run" in a["evidence"]
    b = eta.from_history("firefox build full", elapsed=1500, path=h)
    assert b["remaining_s"] == 3000 and b["basis"] == "history"


def test_live_rate():
    a = eta.live(done=30, total=120, started=1000, now=1600, unit="runs")
    assert a["remaining_s"] == 1800 and "3.00 runs/min" in a["evidence"]
    assert eta.live(0, 10, 1000, now=1600)["remaining_s"] is None


def test_the_leak_gate_eta_uses_its_plan_and_measured_overhead(tmp_path):
    plan = eta.leakgate_plan(3, False)
    assert len(plan) == 147 and sum(s for _, s in plan) == 227 * 60 + sum(s for _, s in plan) % 60
    runs = tmp_path / "runs"
    runs.mkdir()
    t0, planned, when = time.time() - 10000, 0, {}
    for name, secs in plan[:11]:
        d = runs / f"profile-{name}"
        d.mkdir()
        when[d.name] = t0 + planned * 1.1           # each run took 10 % longer than planned
        planned += secs
    a = eta.leakgate(tmp_path, 3, False, stamp=lambda p: when[p.name])
    assert a["basis"] == "plan" and "10/147 runs done" in a["evidence"] and "x 1.10" in a["evidence"]
    left = sum(s for _, s in plan[10:])
    assert abs(a["remaining_s"] - left * 1.1) < 2


def test_a_finished_gate_is_finished(tmp_path):
    (tmp_path / "artifacts.json").write_text("{}")
    assert eta.leakgate(tmp_path, 3, False)["remaining_s"] == 0


def test_delete_estimate_is_measured_on_the_same_disk(tmp_path):
    for i in range(50):
        (tmp_path / f"f{i}").write_text("x")
    a = eta.delete(tmp_path, rate=(1000.0, "X:\\"))
    assert a["basis"] == "sample" and "50 files" in a["evidence"] and a["remaining_s"] == 0
    assert all((tmp_path / f"f{i}").exists() for i in range(50))     # only scratch files are ever deleted
