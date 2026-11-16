"""P28 D: the MSVC telemetry firewall task needs no UAC after the one mandate approval.

A normal user may START the SYSTEM task (its security descriptor grants authenticated users read + execute), so emerge
blocks a vctip.exe a new toolset unpacked before stage 1; the machine verifier re-applies a changed script.
"""
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

from fieldkit.core import telemetry as T
from fieldkit.emerge import runner as E
from fieldkit.mandate import machine

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "fieldkit" / "mandate" / "prereqs" / "telemetry-firewall-task.ps1"
A, B = r"C:\vs\a\vctip.exe", r"C:\vs\b\vctip.exe"


def fake_run(blocked=(), query_rc=0, run_rc=0, calls=None, unblock_after_run=False):
    state = {"blocked": {b.lower() for b in blocked}}

    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        if argv[0] == "powershell":
            return SimpleNamespace(returncode=0, stdout="\n".join(sorted(state["blocked"])), stderr="")
        if argv[:2] == ["schtasks", "/query"]:
            return SimpleNamespace(returncode=query_rc, stdout="", stderr="")
        if argv[:2] == ["schtasks", "/run"]:
            if run_rc == 0 and unblock_after_run:
                state["blocked"] |= {A.lower(), B.lower()}
            return SimpleNamespace(returncode=run_rc, stdout="", stderr="ERROR: Access is denied." if run_rc else "")
        raise AssertionError(argv)
    return run


def test_preflight_is_silent_off_windows_and_without_vctip():
    assert T.vctip_preflight(fake_run(), paths=[A], platform="linux") is None
    assert T.vctip_preflight(fake_run(), paths=[], platform="win32") is None


def test_preflight_ok_when_every_vctip_is_blocked():
    assert T.vctip_preflight(fake_run([A, B]), paths=[A, B], platform="win32") == (
        "ok", "all 2 vctip.exe blocked at the firewall", True)


def test_preflight_warns_and_names_the_way_out():
    st, line, task = T.vctip_preflight(fake_run([A]), paths=[A, B], platform="win32", task=True)
    assert (st, task) == ("warn", True)
    assert line.startswith("1 of 2 vctip.exe not blocked yet") and "runs before stage 1 (no UAC)" in line
    st, line, task = T.vctip_preflight(fake_run([A]), paths=[A, B], platform="win32", task=False)
    assert st == "warn" and task is False and "fieldkit mandate renew" in line


def test_run_firewall_task_waits_until_blocked():
    calls = []
    ok, line = T.run_firewall_task(fake_run(calls=calls, unblock_after_run=True), paths=[A, B], sleep=lambda s: None)
    assert ok and line == "task fieldkit-telemetry-firewall ran: every vctip.exe blocked"
    assert ["schtasks", "/run", "/tn", T.FIREWALL_TASK] in calls


def test_run_firewall_task_times_out_and_reports_refusal():
    t = iter(range(0, 1000, 10))
    ok, line = T.run_firewall_task(fake_run(), paths=[A], wait_s=30, sleep=lambda s: None, clock=lambda: next(t))
    assert not ok and "still open after 30s" in line
    ok, line = T.run_firewall_task(fake_run(run_rc=1), paths=[A], sleep=lambda s: None)
    assert not ok and "exit 1" in line and "fieldkit mandate renew" in line


def _recipe(tmp_path):
    return SimpleNamespace(sources=[], preflight={}, state_dir=tmp_path)


def test_own_checks_appends_the_telemetry_row_or_skips_it(tmp_path):
    rows = E.own_checks(_recipe(tmp_path), "iterate", [], longpath_rows=[], thermal=67,
                        vctip=("warn", "1 of 2 vctip.exe not blocked yet; the task x runs before stage 1", True))
    assert rows[-1] == ("telemetry", "warn", "1 of 2 vctip.exe not blocked yet; the task x runs before stage 1")
    rows = E.own_checks(_recipe(tmp_path), "iterate", [], longpath_rows=[], thermal=67, vctip=False)
    assert all(n != "telemetry" for n, _s, _l in rows)


def test_emerge_starts_the_task_only_on_the_runnable_warn():
    said, started = [], []
    me = SimpleNamespace(say=said.append)

    def task():
        started.append(1)
        return True, "task fieldkit-telemetry-firewall ran: every vctip.exe blocked"
    warn = [("telemetry", E.WARN, "1 of 2 vctip.exe not blocked yet (x); the task t runs before stage 1 (no UAC)")]
    assert E.Emerge._vctip(me, warn, task=task) is True
    assert said == ["  preflight telemetry: OK task fieldkit-telemetry-firewall ran: every vctip.exe blocked"]
    missing = [("telemetry", E.WARN, "task t missing: the owner renews the mandate once (fieldkit mandate renew)")]
    assert E.Emerge._vctip(me, missing, task=task) is None
    assert E.Emerge._vctip(me, [("telemetry", E.OK, "all 2 vctip.exe blocked")], task=task) is None
    assert started == [1]


def test_pin_matches_the_script_and_grants_start_only():
    text = PS1.read_text(encoding="utf-8")
    assert "(A;;GRGX;;;AU)" in text and "GA;;;AU" not in text and "GW;;;AU" not in text
    y = yaml.safe_load((ROOT / "fieldkit" / "mandate" / "owner-wide.prereqs.yaml").read_text(encoding="utf-8"))
    entry = next(e for e in y["machine_prerequisites"] if e["id"] == "telemetry-firewall-task")
    assert entry["sha256"] == hashlib.sha256(PS1.read_bytes()).hexdigest()


def test_verifier_needs_the_task_and_the_current_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    pd = tmp_path / "pd"
    (pd / "Gorilla").mkdir(parents=True)
    ok = lambda argv, **kw: SimpleNamespace(returncode=0)
    assert machine._telemetry_firewall_task(run=ok, programdata=str(pd)) is False            # no copy
    (pd / "Gorilla" / "telemetry-firewall.ps1").write_bytes(b"# an older script\n")
    assert machine._telemetry_firewall_task(run=ok, programdata=str(pd)) is False            # stale copy
    (pd / "Gorilla" / "telemetry-firewall.ps1").write_bytes(PS1.read_bytes())
    assert machine._telemetry_firewall_task(run=ok, programdata=str(pd)) is True
    gone = lambda argv, **kw: SimpleNamespace(returncode=1)
    assert machine._telemetry_firewall_task(run=gone, programdata=str(pd)) is False          # no task


def test_verifier_is_true_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert machine._telemetry_firewall_task() is True
