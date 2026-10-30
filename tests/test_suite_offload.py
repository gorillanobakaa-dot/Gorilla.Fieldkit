"""The pre-commit suite on the other computer (owner, 2026-10-26: "every MB counts ... delegate more jobs to the linux
box"): the OS-neutral part runs on an idle Linux device, this computer runs only the files that device skipped."""
from pathlib import Path

import pytest

from fieldkit import orchestrate_load as L
from fieldkit import suite_offload as so

PASSED = ('<testsuites><testsuite tests="4">'
          '<testcase classname="tests.test_a" name="test_x"/>'
          '<testcase classname="tests.test_win.TestK" name="test_p[1]"><skipped message="Windows-only behaviour"/>'
          '</testcase>'
          '<testcase classname="" name="tests.test_tool"><skipped message="collection skipped"/></testcase>'
          '<testcase classname="tests.test_a" name="test_y"/>'
          '</testsuite></testsuites>')
FAILED = ('<testsuites><testsuite><testcase classname="tests.test_a" name="test_x"><failure message="boom"/>'
          '</testcase><testcase classname="tests.test_a" name="test_y"/></testsuite></testsuites>')
EMPTY = '<testsuites><testsuite tests="0"></testsuite></testsuites>'


def row(name, me=False, ok=True, os="LINUX", free=1, cpu=5.0, pytest_=True):
    return {"name": name, "me": me, "ok": ok, "os": os, "gate": {"free": free, "held": 0, "offered": 1, "waiting": 0},
            "cpu_pct": cpu, "temp_c": 50, "hot": 85, "can": {"pytest": pytest_, "git": True},
            "error": "" if ok else "connection refused"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    for f in ("tests/test_a.py", "tests/test_win.py", "tests/test_tool.py"):
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        (r / f).write_text("")
    monkeypatch.setattr(so, "setting", lambda: None)
    monkeypatch.delenv("FIELDKIT_SUITE_LOCAL", raising=False)

    def export(repo_, out_dir=None):
        d = tmp_path / "export" / "tree-k"
        d.mkdir(parents=True, exist_ok=True)
        return d, "k" * 40
    monkeypatch.setattr(L, "export_tree", export)
    return r


def go(repo, tmp_path, xml=PASSED, rows=None, worker_status="pass", arrived=True, said="sent", signed=True):
    rep = tmp_path / "report.xml"
    rep.write_text(xml)
    sent, recorded = [], []

    def send(kind, device, tree, project, argv=None, artefacts=(), wall=60):
        sent.append({"device": device, "argv": argv, "artefacts": list(artefacts), "wall": wall})
        return "job-1", said, {"digest": "d" * 64}
    result = {"status": worker_status, "os": "LINUX", "evidence": "exit 0 in 700 s"} if signed else None
    r = so.offload(repo, os_tag="WINDOWS", rows=rows or [row("me", me=True, os="WINDOWS", free=0), row("vaio")],
                   send=send, wait_fn=lambda j, s: {"status": worker_status}, result_fn=lambda j, p, d: result,
                   check_fn=lambda res: [(so.REPORT, arrived, str(rep))],
                   record=lambda *a: recorded.append(a), say=lambda s: None)
    return r, sent, recorded


def test_a_signed_passing_report_leaves_only_the_files_the_device_skipped_to_run_here(repo, tmp_path):
    r, sent, recorded = go(repo, tmp_path)
    assert r["status"] == "pass" and r["where"] == "vaio", r
    assert r["local"] == ["tests/test_tool.py", "tests/test_win.py"]          # the module skip is mapped too
    assert sent == [{"device": "vaio", "argv": ["admit", "suite", "--report", so.REPORT],
                     "artefacts": [so.REPORT], "wall": so.WALL_MIN}]
    assert recorded[0][0] == "full-suite@LINUX" and recorded[0][1] == "pass" and recorded[0][3] == "k" * 40


def test_a_report_that_names_failures_refuses_and_records_the_failure(repo, tmp_path):
    r, _, recorded = go(repo, tmp_path, xml=FAILED, worker_status="fail")
    assert r["status"] == "fail" and r["report"]["failures"] == [("tests.test_a", "test_x")]
    assert recorded[0][1] == "fail"


@pytest.mark.parametrize("kw,why", [
    ({"xml": EMPTY}, "nothing was tested"),                          # pytest never tested anything: never a pass
    ({"arrived": False}, "did not arrive"),                          # an older worker with no --report
    ({"signed": False}, "no result signed"),
    ({"said": "not taken: a job is running here already"}, "did not take it"),
    ({"xml": "not xml"}, "not a JUnit report"),
    ({"worker_status": "fail"}, "its result says fail"),             # the report and the signed result disagree
    ({"xml": PASSED.replace('name="tests.test_tool"', 'name="tests.test_gone"')}, "map to no test file"),
])
def test_anything_less_than_a_proven_pass_runs_the_full_suite_here_and_records_nothing(repo, tmp_path, kw, why):
    r, _, recorded = go(repo, tmp_path, **kw)
    assert r["status"] == "local" and why in r["why"], r
    assert recorded == []


@pytest.mark.parametrize("rows,why", [
    ([row("me", me=True, os="WINDOWS"), row("vaio", ok=False)], "vaio down"),
    ([row("me", me=True, os="WINDOWS"), row("vaio", cpu=80.0)], "vaio BUSY"),
    ([row("me", me=True, os="WINDOWS"), row("vaio", free=0)], "vaio BUSY"),
    ([row("me", me=True, os="WINDOWS"), row("vaio", pytest_=False)], "lacks pytest"),
    ([row("me", me=True, os="WINDOWS")], "no other device"),
])
def test_a_device_that_is_down_busy_or_incapable_is_never_waited_for(repo, tmp_path, rows, why):
    r, sent, _ = go(repo, tmp_path, rows=rows)
    assert r["status"] == "local" and why in r["why"] and sent == [], r


def test_only_windows_sends_its_suite_away_unless_the_setting_says_otherwise():
    assert so.wanted("WINDOWS") == (True, "")
    assert so.wanted("LINUX")[0] is False
    assert so.wanted("LINUX", "on")[0] is True
    assert so.wanted("WINDOWS", "off")[0] is False
    assert so.wanted("WINDOWS", env={"FIELDKIT_SUITE_LOCAL": "1"})[0] is False


def test_the_hook_door_speaks_in_exit_codes(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(so, "offload", lambda tree, project: {"status": "pass", "where": "vaio", "why": "3 passed",
                                                              "local": ["tests/test_win.py"]})
    assert so.hook([]) == 0 and capsys.readouterr().out.split() == ["tests/test_win.py"]
    monkeypatch.setattr(so, "offload", lambda tree, project: {"status": "fail", "where": "vaio", "why": "1 failed",
                                                              "report": {"failures": [("tests.test_a", "test_x")]}})
    assert so.hook([]) == 1 and "FAILED tests.test_a::test_x" in capsys.readouterr().err
    monkeypatch.setattr(so, "offload", lambda tree, project: {"status": "local", "why": "vaio down"})
    assert so.hook([]) == so.LOCAL and capsys.readouterr().out == ""


def test_a_real_pytest_report_is_read_the_same_way(tmp_path):
    import subprocess
    import sys
    t = tmp_path / "t"
    (t / "tests").mkdir(parents=True)
    (t / "tests" / "test_a.py").write_text("import pytest\npytestmark = pytest.mark.skipif(True, reason='w')\n"
                                           "def test_x(): pass\n")
    (t / "tests" / "test_b.py").write_text("def test_y(): pass\n")
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml", "r.xml", "tests"],
                   cwd=t, capture_output=True)
    rep = so.read_report(t / "r.xml")
    assert (rep["tests"], rep["passed"], rep["failed"]) == (2, 1, 0)
    assert so.local_part(rep, t) == (["tests/test_a.py"], [])
    assert so.judge(rep)[0] == "pass"
