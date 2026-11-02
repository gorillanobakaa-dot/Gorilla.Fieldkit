"""R15: the covering tests are split across every free device, never dropped (fieldkit/tests_split.py)."""
import json
from pathlib import Path

import pytest

from fieldkit import orchestrate_load as L
from fieldkit import tests_split as ts

PASS = ('<testsuites><testsuite><testcase classname="tests.test_t0" name="a" time="2.5"/>'
        '<testcase classname="tests.test_t1" name="b"><skipped message="x"/></testcase></testsuite></testsuites>')
FAIL = ('<testsuites><testsuite><testcase classname="tests.test_t0" name="a"><failure message="boom"/></testcase>'
        '</testsuite></testsuites>')


def row(name, me=False, os="LINUX", free=1, cpu=5.0, ok=True):
    return {"name": name, "me": me, "ok": ok, "os": os, "gate": {"free": free, "held": 0, "offered": 1, "waiting": 0},
            "cpu_pct": cpu, "temp_c": 50, "hot": 85, "can": {"pytest": True, "git": True}}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_TESTCOST_DIR", str(tmp_path / "tc"))
    monkeypatch.setenv("FIELDKIT_LOCAL_TEST_CAP", "15")
    for i in range(70):
        (tmp_path / "tests").mkdir(exist_ok=True)
        (tmp_path / "tests" / f"test_t{i}.py").write_text("x\n" * (i + 1))
    return tmp_path


def names(n):
    return [f"tests/test_t{i}.py" for i in range(n)]


def test_70_files_all_assigned_none_dropped_laptop_within_share(repo):
    rows = [row("L15", me=True, os="WINDOWS"), row("VAIO")]
    a = ts.assign(names(70), rows, repo, setting=None)
    got = [f for fs in a["shares"].values() for f in fs]
    assert sorted(got) == sorted(names(70)) and not a["ci"] and not a["fallback"]
    assert len(a["shares"]["L15"]) <= a["share"] and a["share"] <= 15
    assert len(a["shares"]["VAIO"]) == 70 - len(a["shares"]["L15"])


def test_setting_is_the_share_and_is_not_ignored(repo):
    a = ts.assign(names(70), [row("L15", me=True, os="WINDOWS"), row("VAIO")], repo, setting=4)
    assert a["share"] == 4 and len(a["shares"]["L15"]) == 4


def test_busy_or_down_device_gets_nothing(repo):
    rows = [row("L15", me=True, os="WINDOWS"), row("VAIO", cpu=90), row("Z", ok=False)]
    a = ts.assign(names(70), rows, repo)
    assert a["fallback"] and len(a["ci"]) == 70 and list(a["shares"]) == ["L15"]


def test_no_idle_device_and_small_set_runs_all_here(repo):
    a = ts.assign(names(15), [row("L15", me=True, os="WINDOWS")], repo)
    assert a["shares"]["L15"] == sorted(names(15)) and not a["fallback"]


def test_windows_only_stays_on_a_windows_device(repo):
    (repo / "tests" / "test_t3.py").write_text("@pytest.mark.windows_only\n")
    a = ts.assign(names(70), [row("L15", me=True, os="WINDOWS"), row("VAIO")], repo, setting=1)
    assert "tests/test_t3.py" in a["shares"]["L15"] and "tests/test_t3.py" not in a["shares"]["VAIO"]


def test_balanced_by_cost_largest_first(repo):
    a = ts.assign(names(70), [row("L15", me=True, os="WINDOWS"), row("A"), row("B")], repo, setting=1)
    assert abs(len(a["shares"]["A"]) - len(a["shares"]["B"])) <= 2


def _start(repo, rows, send):
    return ts.start(str(repo), state=None, say=lambda s: None, rows=rows, send=send, tests=names(70))


def _send(ok=True):
    sent = []

    def send(kind, dev, tree, project, argv=None, artefacts=(), wall=0):
        sent.append((dev, argv))
        return (f"job-{dev}", "sent", {"digest": "d" * 64}) if ok else (None, "not taken: busy", None)
    send.sent = sent
    return send


@pytest.fixture
def stubs(monkeypatch, repo):
    monkeypatch.setattr(L, "export_tree", lambda r, o=None: ((repo / "exp" / "t").mkdir(parents=True, exist_ok=True)
                                                              or (repo / "exp" / "t"), "k" * 40))
    from fieldkit import testcost
    monkeypatch.setattr(testcost, "staged_files", lambda r: [])
    monkeypatch.setattr(testcost, "tree_key", lambda r: "k" * 40)


def test_start_sends_the_other_share_and_keeps_ours(repo, stubs):
    s = _send()
    r = _start(repo, [row("L15", me=True, os="WINDOWS"), row("VAIO")], s)
    assert len(r["local"]) + sum(len(p["files"]) for p in r["pending"]) == 70
    assert s.sent[0][1][:4] == ["admit", "suite", "--report", ts.REPORT]


def test_a_device_that_does_not_take_its_share_brings_it_here(repo, stubs):
    r = _start(repo, [row("L15", me=True, os="WINDOWS"), row("VAIO")], _send(ok=False))
    assert r["pending"] == [] and len(r["local"]) + len(r["back"]) == 70


def test_fallback_says_the_number_and_is_recorded(repo, stubs, monkeypatch):
    said, rec = [], []
    from fieldkit import testcost
    monkeypatch.setattr(testcost, "record", lambda *a, **k: rec.append(a))
    r = ts.start(str(repo), None, said.append, [row("L15", me=True, os="WINDOWS")], tests=names(70))
    assert r["ci"] and "70 test files" in said[-1] and "CI runs them" in said[-1]
    assert rec and rec[0][:2] == ("split-fallback", "refused")


def _finish(res, rep_xml, repo, result="pass", resulted=True, arrive=True, tmp=None):
    p = Path(tmp) / "rep.xml"
    p.write_text(rep_xml)
    rec = []
    out = ts.finish(res, str(repo), say=lambda s: None, wait_s=1, wait_fn=lambda j, s: {"status": "pass"},
                    result_fn=lambda j, pr, d: {"status": result, "os": "LINUX"} if resulted else None,
                    check_fn=lambda r: [(ts.REPORT, arrive, str(p))],
                    record=lambda *a, **k: rec.append(a), project="P")
    return out, rec


def pending(files):
    return {"key": "k", "back": [], "pending": [{"device": "VAIO", "job": "j", "digest": "d", "files": files, "t0": 0,
                                                   "os": "LINUX"}]}


def test_timeout_brings_the_share_here(repo, tmp_path):
    f = names(3)
    out, rec = _finish(pending(f), PASS, repo, resulted=False, tmp=tmp_path)
    assert out["status"] == "pass" and out["run_here"] == f and not rec


def test_unsigned_report_is_not_a_pass(repo, tmp_path):
    f = names(3)
    out, rec = _finish(pending(f), PASS, repo, arrive=False, tmp=tmp_path)
    assert out["run_here"] == f and not rec


def test_failure_anywhere_refuses_and_names_the_test(repo, tmp_path):
    out, rec = _finish(pending(names(3)), FAIL, repo, tmp=tmp_path)
    assert out["status"] == "fail" and out["failed"] == [("VAIO", "tests.test_t0", "a")] and not rec


def test_pass_records_the_share_and_skipped_file_runs_here(repo, tmp_path):
    f = names(3)
    out, rec = _finish(pending(f), PASS, repo, tmp=tmp_path)
    assert out["status"] == "pass" and out["run_here"] == ["tests/test_t1.py"]
    assert rec[0][0] == ts.check_name(f) and rec[0][1] == "pass"
