"""The pipeline engine: order, resume, verify-the-artefact, platforms, always-stages."""
import sys
from pathlib import Path

import pytest

from fieldkit.core.host import host
from fieldkit.core.pipeline import Pipeline, PipelineError

PY = sys.executable
HERE = "windows" if host()["is_windows"] else "linux"
OTHER = "linux" if HERE == "windows" else "windows"


def _write(path_var):
    return [PY, "-c", f"import pathlib; pathlib.Path(r'{path_var}').write_text('x')"]


def spec(tmp, stages, vars_=None):
    return {"name": "t", "vars": vars_ or {"out": str(tmp)}, "stages": stages}


def test_runs_in_order_and_resumes(tmp_path):
    s = spec(tmp_path, [
        {"id": "a", "run": {"cmd": _write(f"{tmp_path}/a.txt")}, "verify": [{"files_exist": ["{out}/a.txt"]}]},
        {"id": "b", "run": {"cmd": _write(f"{tmp_path}/b.txt")}, "verify": [{"files_exist": ["{out}/b.txt"]}]},
    ])
    p = Pipeline(s, state_dir=tmp_path / "st")
    r = p.run()
    assert r["ok"] and [x["status"] for x in r["stages"]] == ["done", "done"]
    r2 = Pipeline(s, state_dir=tmp_path / "st").run()
    assert [x["status"] for x in r2["stages"]] == ["up-to-date", "up-to-date"]


def test_rerun_when_artefact_vanishes(tmp_path):
    s = spec(tmp_path, [{"id": "a", "run": {"cmd": _write(f"{tmp_path}/a.txt")},
                         "verify": [{"files_exist": ["{out}/a.txt"]}]}])
    Pipeline(s, state_dir=tmp_path / "st").run()
    (tmp_path / "a.txt").unlink()
    r = Pipeline(s, state_dir=tmp_path / "st").run()
    assert r["stages"][0]["status"] == "done"          # re-ran, did not trust the old state


def test_exit_zero_but_no_artefact_is_a_failure(tmp_path):
    s = spec(tmp_path, [{"id": "a", "run": {"cmd": [PY, "-c", "pass"]},
                         "verify": [{"files_exist": ["{out}/never.txt"]}]},
                        {"id": "b", "run": {"cmd": [PY, "-c", "pass"]}}])
    r = Pipeline(s, state_dir=tmp_path / "st").run()
    assert not r["ok"] and r["stopped_at"] == "a" and len(r["stages"]) == 1


def test_failing_stage_stops_but_always_stage_runs(tmp_path):
    s = spec(tmp_path, [
        {"id": "fail", "run": {"cmd": [PY, "-c", "import sys; sys.exit(2)"]}},
        {"id": "skipped", "run": {"cmd": _write(f"{tmp_path}/skipped.txt")}},
        {"id": "cleanup", "always": True, "run": {"cmd": _write(f"{tmp_path}/clean.txt")}},
    ])
    r = Pipeline(s, state_dir=tmp_path / "st").run()
    assert not r["ok"] and r["stopped_at"] == "fail"
    assert [x["id"] for x in r["stages"]] == ["fail", "cleanup"]
    assert (tmp_path / "clean.txt").exists() and not (tmp_path / "skipped.txt").exists()


def test_other_platform_stage_blocks_unless_optional(tmp_path):
    s = spec(tmp_path, [{"id": "x", "platforms": [OTHER], "run": {"cmd": [PY, "-c", "pass"]}},
                        {"id": "y", "run": {"cmd": [PY, "-c", "pass"]}}])
    r = Pipeline(s, state_dir=tmp_path / "st").run()
    assert not r["ok"] and r["stages"][0]["status"] == "not-this-platform" and len(r["stages"]) == 1
    s["stages"][0]["optional"] = True
    r = Pipeline(s, state_dir=tmp_path / "st2").run()
    assert r["ok"] and [x["status"] for x in r["stages"]] == ["not-this-platform", "ran-unverified"]


def test_changed_definition_reruns(tmp_path):
    s = spec(tmp_path, [{"id": "a", "run": {"cmd": _write(f"{tmp_path}/a.txt")},
                         "verify": [{"files_exist": ["{out}/a.txt"]}]}])
    Pipeline(s, state_dir=tmp_path / "st").run()
    s["stages"][0]["timeout"] = 99                       # any change to the stage = new fingerprint
    r = Pipeline(s, state_dir=tmp_path / "st").run()
    assert r["stages"][0]["status"] == "done"


def test_python_stage_and_file_contains(tmp_path):
    (tmp_path / "cfg").write_text("CONFIG_A=y\n")
    s = spec(tmp_path, [{"id": "p", "run": {"python": "fieldkit.core.checks:always_ok"},
                         "verify": [{"file_contains": {"path": "{out}/cfg", "pattern": "^CONFIG_A=y"}}]}])
    r = Pipeline(s, state_dir=tmp_path / "st").run()
    assert r["ok"], r


def test_python_stage_without_ok_is_a_failure(tmp_path):
    # host() returns facts but no "ok": the engine must not treat that as success
    s = spec(tmp_path, [{"id": "p", "run": {"python": "fieldkit.core.host:host"}}])
    assert not Pipeline(s, state_dir=tmp_path / "st").run()["ok"]


def test_checks_disk_and_tools(tmp_path):
    s = spec(tmp_path, [
        {"id": "disk", "run": {"python": "fieldkit.core.checks:disk_free", "args": {"path": str(tmp_path), "gb": 0.001}}},
        {"id": "tools", "run": {"python": "fieldkit.core.checks:tools_present", "args": {"names": ["no-such-tool-xyz"]}}},
    ])
    r = Pipeline(s, state_dir=tmp_path / "st").run()
    assert r["stages"][0]["status"] == "ran-unverified" and r["stopped_at"] == "tools"
    assert "no-such-tool-xyz" in r["stages"][1]["result"]["detail"]


def test_python_stage_exception_is_reported_not_raised(tmp_path):
    s = spec(tmp_path, [{"id": "boom", "run": {"python": "fieldkit.build.kernel:load_fragment",
                                                "args": {"path": "/no/such/file.yaml"}}}])
    # load_fragment(ctx, path=...) -> TypeError (ctx given) - any crash must become a failed stage
    r = Pipeline(s, state_dir=tmp_path / "st").run()
    assert not r["ok"] and "Error" in r["stages"][0]["result"]["detail"]


def test_dry_run_changes_nothing(tmp_path):
    s = spec(tmp_path, [{"id": "a", "run": {"cmd": _write(f"{tmp_path}/a.txt")}}])
    r = Pipeline(s, state_dir=tmp_path / "st").run(dry_run=True)
    assert r["stages"][0]["status"] == "would-run" and not (tmp_path / "a.txt").exists()


def test_only_and_from(tmp_path):
    stages = [{"id": i, "run": {"cmd": _write(f"{tmp_path}/{i}.txt")}} for i in "abc"]
    r = Pipeline(spec(tmp_path, stages), state_dir=tmp_path / "s1").run(only=["b"])
    assert [x["id"] for x in r["stages"]] == ["b"]
    r = Pipeline(spec(tmp_path, stages), state_dir=tmp_path / "s2").run(start="b")
    assert [x["id"] for x in r["stages"]] == ["b", "c"]
    with pytest.raises(PipelineError):
        Pipeline(spec(tmp_path, stages), state_dir=tmp_path / "s3").run(only=["zzz"])


def test_bad_specs_rejected(tmp_path):
    with pytest.raises(PipelineError):
        Pipeline({"name": "x", "stages": [{"id": "a"}]}, state_dir=tmp_path)
    with pytest.raises(PipelineError):
        Pipeline({"name": "x", "stages": [{"id": "a", "run": {"cmd": ["x"]}}, {"id": "a", "run": {"cmd": ["x"]}}]},
                 state_dir=tmp_path)
    with pytest.raises(PipelineError):
        Pipeline({"stages": []}, state_dir=tmp_path)


def test_vars_fill_but_leave_other_braces(tmp_path):
    s = {"name": "t", "vars": {"v": "7.1.2", "src": "/w/linux-{v}"},
         "stages": [{"id": "a", "run": {"cmd": ["echo", "{src}", "{notavar}", "${{literal}}"]}}]}
    p = Pipeline(s, state_dir=tmp_path, strict=False)
    assert p.stages[0]["run"]["cmd"][1:3] == ["/w/linux-7.1.2", "{notavar}"]


@pytest.mark.parametrize("name", ["debian-kernel", "firefox-windows"])
def test_shipped_pipelines_load_and_plan(tmp_path, name):
    path = Path(__file__).resolve().parents[1] / "fieldkit" / "build" / "pipelines" / f"{name}.yaml"
    p = Pipeline.load(path, state_dir=tmp_path, strict=False)
    plan = p.plan()
    assert plan["stages"] and all("runs_here" in s for s in plan["stages"])
    r = p.run(dry_run=True)
    # on the wrong OS the first stage reports not-this-platform instead of pretending
    assert r["stages"][0]["status"] in ("would-run", "not-this-platform")


def test_output_checks_prove_a_stage_from_what_it_printed(tmp_path):
    s = spec(tmp_path, [{"id": "pre", "run": {"cmd": [PY, "-c", "print('[+] Preflight cleared with 0 warning(s).')"]},
                         "verify": [{"output_contains": r"^\[\+\] Preflight cleared"}, {"output_lacks": "FATAL"}]}])
    r = Pipeline(s, state_dir=tmp_path / "st").run()
    assert r["stages"][0]["status"] == "done", r
    r = Pipeline(s, state_dir=tmp_path / "st").run()
    assert r["stages"][0]["status"] == "done"          # output-proven stages always re-run
    bad = spec(tmp_path, [{"id": "pre", "run": {"cmd": [PY, "-c", "print('checks failing')"]},
                           "verify": [{"output_contains": "Preflight cleared"}]}])
    assert Pipeline(bad, state_dir=tmp_path / "st2").run()["stages"][0]["status"] == "failed"
