"""CI durations (fieldkit/method/ciplan.py, CI-SPEED-PLAN C1): merge, fail-closed block, workflow wiring."""
import datetime
import importlib.util
import json
from pathlib import Path

import yaml

from fieldkit.method import ciplan

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.datetime(2026, 11, 22, 12, 0, tzinfo=datetime.timezone.utc)
spec = importlib.util.spec_from_file_location("ci_tests", ROOT / ".github" / "ci_tests.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def _complete(files, updated=NOW):
    d = ciplan.empty()
    d["complete"], d["run"], d["updated"] = True, "42", updated.isoformat()
    d["tests"] = {o: {f"{f}::t": s for f, s in files.items()} for o in ciplan.OSES}
    return d


def _junit(tmp_path, cases):
    x = tmp_path / "j.xml"
    body = "".join(f'<testcase classname="{c}" name="{n}" file="{f}" time="{t}"/>' for c, n, f, t in cases)
    x.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>", encoding="utf-8")
    return x


def test_junit_node_ids_keep_the_class_and_skip_skipped(tmp_path):
    x = tmp_path / "j.xml"
    x.write_text('<testsuites><testsuite>'
                 '<testcase classname="tests.test_a" name="t1" file="tests/test_a.py" time="1.5"/>'
                 '<testcase classname="tests.test_a.TestK" name="t2" file="tests/test_a.py" time="0.5"/>'
                 '<testcase classname="tests.test_a" name="s" file="tests/test_a.py" time="9"><skipped/></testcase>'
                 '</testsuite></testsuites>', encoding="utf-8")
    assert ciplan.from_junit(x) == {"tests/test_a.py::t1": 1.5, "tests/test_a.py::TestK::t2": 0.5}


def test_a_complete_run_replaces_an_os_and_a_partial_run_only_updates():
    prev = ciplan.merge(None, "linux", {"a::x": 1, "b::y": 2}, "1", True, NOW)
    part = ciplan.merge(prev, "linux", {"a::x": 5}, "2", False, NOW)
    assert part["tests"]["linux"] == {"a::x": 5, "b::y": 2} and part["complete"] is True
    full = ciplan.merge(part, "linux", {"a::x": 3}, "3", True, NOW)
    assert full["tests"]["linux"] == {"a::x": 3}                    # a deleted test drops out


def test_no_artifact_or_no_whole_run_is_degraded_loudly_not_a_failure():
    for d in (None, {**ciplan.empty(), "run": "9"}):
        b, err = ciplan.block(d, ["tests/a.py"], now=NOW)
        assert err is None and b["degraded"] and b["weights"] == {}
        assert "DEGRADED" in ciplan.explain(b)


def test_stale_durations_fail_closed():
    old = _complete({"tests/a.py": 1.0}, NOW - datetime.timedelta(days=8))
    b, err = ciplan.block(old, ["tests/a.py"], now=NOW)
    assert b is None and "8.0 days old" in err and "tests.yml" in err
    assert ciplan.block(_complete({"tests/a.py": 1.0}, NOW - datetime.timedelta(days=6)), ["tests/a.py"], now=NOW)[1] is None


def test_low_coverage_fails_closed_but_files_the_pr_adds_do_not_count():
    d = _complete({f"tests/t{i}.py": 1.0 for i in range(8)})
    selected = [f"tests/t{i}.py" for i in range(8)] + ["tests/new1.py", "tests/new2.py"]
    b, err = ciplan.block(d, selected, now=NOW)
    assert b is None and "80%" in err and "tests/new1.py" in err
    b, err = ciplan.block(d, selected, new_files=["tests/new1.py", "tests/new2.py"], now=NOW)
    assert err is None and b["coverage"]["linux"] == 1.0
    assert "coverage 100%" in ciplan.explain(b)


def test_an_unknown_test_weighs_the_p90_so_it_never_hides_in_a_light_shard():
    d = _complete({f"tests/t{i}.py": float(i + 1) for i in range(10)})
    b, _ = ciplan.block(d, ["tests/t0.py", "tests/brand_new.py"], new_files=["tests/brand_new.py"], now=NOW)
    assert b["weights"]["linux"]["tests/brand_new.py"] == 9.0


def test_a_corrupt_or_foreign_durations_file_is_an_error_not_silence(tmp_path):
    import pytest
    p = tmp_path / "d.json"
    assert ciplan.read(p) is None
    p.write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError):
        ciplan.read(p)
    p.write_text(json.dumps({"schema": 9, "tests": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        ciplan.read(p)


def test_the_selection_carries_the_block_and_refuses_when_stale(tmp_path, monkeypatch):
    old = _complete({"tests/test_ciplan.py": 1.0}, NOW - datetime.timedelta(days=30))
    path = tmp_path / "d.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.chdir(ROOT)
    b, err = ci.durations_block(str(path), ["tests/test_ciplan.py"], "full")
    assert b is None and "days old" in err
    path.write_text(json.dumps(_complete({"tests/test_ciplan.py": 1.0}, datetime.datetime.now(datetime.timezone.utc))),
                    encoding="utf-8")
    b, err = ci.durations_block(str(path), ["tests/test_ciplan.py"], "full")
    assert err is None and b["weights"]["windows"] == {"tests/test_ciplan.py": 1.0}


def test_plan_explain_prints_the_durations_line(tmp_path, capsys):
    sel = tmp_path / "s.json"
    b, _ = ciplan.block(_complete({"tests/a.py": 1.0}, datetime.datetime.now(datetime.timezone.utc)), ["tests/a.py"])
    sel.write_text(json.dumps({"durations": b}), encoding="utf-8")
    assert ciplan.main(["plan", "--explain", "--selection", str(sel)]) == 0
    assert "ci: durations: run 42, coverage 100%" in capsys.readouterr().out


def test_cli_merge_writes_the_file(tmp_path, capsys):
    x = _junit(tmp_path, [("tests.test_a", "t", "tests/test_a.py", 2)])
    out = tmp_path / "d.json"
    assert ciplan.main(["durations", "merge", "--os", "linux", "--run", "7", "--complete", "--out", str(out), str(x)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["tests"]["linux"] == {"tests/test_a.py::t": 2.0} and d["complete"] and d["run"] == "7"


def test_the_workflow_feeds_and_reads_the_durations_artifact():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
    assert wf[True]["schedule"], "a weekly whole-suite run keeps the durations complete and fresh"
    sel = json.dumps(wf["jobs"]["selection"])
    assert "durations fetch" in sel and "--durations" in sel
    job = wf["jobs"]["durations"]
    assert job["if"].count("pull_request") == 1 and "always()" in job["if"]
    text = json.dumps(job)
    assert "durations merge" in text and '"ci-durations"' in text and "retention-days" in text
    for osn in ("windows-latest", "ubuntu-latest"):
        assert "junit-" in json.dumps(wf["jobs"][f"shard-{osn}"])
