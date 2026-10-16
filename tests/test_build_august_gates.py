# Version: 1.0.0 · updated 26-10-15-20-20
"""Preflight `theme-text-gates`: the 2026-08-27 Linux theme and text gates are called, and only a run where gates ran
and none failed passes (2026-10-15: nothing called them and the 158 build shipped three of them failing)."""
import subprocess

from fieldkit.build import firefox_preflight as fp

FAILING = """==> PREFLIGHT
  PASS  ASSET-009  every FINAL_TARGET_FILES entry exists
  FAIL  MARK-001: 1 tuned watermark value(s) lost
          about:addons: lost: "right calc(10% - 39px) bottom 50px / 450px"
  NOTE  these were tuned over an afternoon and approved on sight 2026-07-31
==> SUMMARY
     ran     : ASSET-009, MARK-001
     skipped : none
     FAILED  : MARK-001
"""


def _run(text):
    return lambda *a, **k: subprocess.CompletedProcess(a, 1, text, "")


def test_a_failing_gate_blocks_and_names_the_lost_value(tmp_path):
    script = tmp_path / "release_pipeline.py"
    script.write_text("")
    ok, ids, lines = fp.august_gates(tmp_path, script=script, run=_run(FAILING))
    assert not ok and ids == ["MARK-001"]
    assert any("450px" in l for l in lines) and not any(l.startswith("NOTE") for l in lines)


def test_all_passing_passes(tmp_path):
    script = tmp_path / "release_pipeline.py"
    script.write_text("")
    text = "  PASS  ASSET-009  ok\n     ran     : ASSET-009\n     FAILED  : none\n"
    assert fp.august_gates(tmp_path, script=script, run=_run(text))[0]


def test_nothing_ran_is_a_failure(tmp_path):
    script = tmp_path / "release_pipeline.py"
    script.write_text("")
    ok, ids, _ = fp.august_gates(tmp_path, script=script, run=_run("Traceback: boom\n"))
    assert not ok and ids == ["NOTHING-RAN"]


def test_a_missing_script_is_a_failure(tmp_path):
    ok, ids, _ = fp.august_gates(tmp_path, script=tmp_path / "gone.py")
    assert not ok and ids == ["MISSING"]
