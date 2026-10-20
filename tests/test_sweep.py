"""fieldkit sweep: the weekly walk through the scratch folders."""
import json
import subprocess
import sys
from pathlib import Path

from fieldkit.desk import sweep

ROOT = Path(__file__).resolve().parents[1]


def _tree(tmp_path):
    scratch = tmp_path / "Scripts"
    (scratch / "Carded").mkdir(parents=True)
    (scratch / "Carded" / "tool.py").write_text("print(1)\n")
    (scratch / "NoTest").mkdir()
    (scratch / "NoTest" / "tool.py").write_text("print(2)\n")
    (scratch / "Nobody").mkdir()
    (scratch / "Nobody" / "x.py").write_text("print(3)\n")
    (scratch / "Fieldkit").mkdir()               # the harness itself: never swept
    (scratch / "loose.ps1").write_text("Write-Output 1\n")
    (scratch / "README.md").write_text("docs are not scripts\n")
    (scratch / "left.patch").write_text("--- a\n+++ b\n")
    (scratch / "Ignored").mkdir()
    return scratch


def _tools(scratch):
    return [
        {"id": "carded", "path": str(scratch / "Carded" / "tool.py"), "test": ["python", "-m", "pytest", "x"]},
        {"id": "notest", "path": str(scratch / "NoTest" / "tool.py"), "test": None},
        {"id": "elsewhere", "path": str(scratch.parent / "Other" / "y.py"), "test": ["python", "y"]},
    ]


def test_every_status_is_assigned(tmp_path):
    scratch = _tree(tmp_path)
    tools = _tools(scratch)
    sha = sweep.cardmod.file_sha(scratch / "Carded" / "tool.py")
    rows = sweep.scan([scratch], tools, {"carded": {"ok": True, "sha256": sha}}, ignore={"Fieldkit", "Ignored"})
    by = {r["name"]: r["status"] for r in rows}
    assert by == {"Nobody": "untracked", "loose.ps1": "untracked", "NoTest": "no-test",
                  "left.patch": "leftover", "Carded": "adopt"}    # tested, but still in the dumping ground
    assert "README.md" not in by and "Fieldkit" not in by and "Ignored" not in by
    assert rows[0]["status"] == "untracked"          # worst first
    assert sweep.exit_code(rows) == 3


def test_a_changed_tool_is_stale_and_an_unrun_test_is_untested(tmp_path):
    scratch = _tree(tmp_path)
    tools = _tools(scratch)
    rows = sweep.scan([scratch], tools, {"carded": {"ok": True, "sha256": "0" * 64}}, ignore={"Fieldkit", "Ignored"})
    assert {r["name"]: r["status"] for r in rows}["Carded"] == "stale"
    rows = sweep.scan([scratch], tools, {}, ignore={"Fieldkit", "Ignored"})
    assert {r["name"]: r["status"] for r in rows}["Carded"] == "untested"


def test_a_tested_tool_left_in_scripts_still_fails_until_adopted(tmp_path):
    """Owner, 2026-10-13: Scripts is a temporary dumping ground; a card is not adoption."""
    scratch = tmp_path / "Scripts"
    (scratch / "Carded").mkdir(parents=True)
    (scratch / "Carded" / "tool.py").write_text("print(1)\n")
    tools = [{"id": "carded", "path": str(scratch / "Carded" / "tool.py"), "test": ["python", "x"]}]
    sha = sweep.cardmod.file_sha(scratch / "Carded" / "tool.py")
    rows = sweep.scan([scratch], tools, {"carded": {"ok": True, "sha256": sha}}, ignore=set())
    assert rows[0]["status"] == "adopt" and sweep.exit_code(rows) == 3
    assert "fieldkit adopt NAME" in "\n".join(sweep.lines(rows))


def test_an_empty_scratch_or_only_adopted_copies_exits_zero(tmp_path):
    scratch = tmp_path / "Scripts"
    (scratch / "Old").mkdir(parents=True)
    (scratch / "Old" / ".adopted").write_text("adopted into local/kits/Old\n")
    (scratch / "Old" / "tool.py").write_text("print(1)\n")
    rows = sweep.scan([scratch], [], {}, ignore=set())
    assert rows[0]["status"] == "adopted" and sweep.exit_code(rows) == 0
    assert "adopt --finish" in "\n".join(sweep.lines(rows))
    assert sweep.exit_code(sweep.scan([tmp_path / "Empty"], [], {}, ignore=set())) == 0


def test_a_loose_script_captured_as_a_recipe_is_adopted_and_its_marker_is_no_leftover(tmp_path):
    # 2026-10-19: `fieldkit recipes capture` and `adopt` mark a loose file with NAME.adopted beside it; the sweep
    # listed the file as untracked and the marker as a leftover, so it could never reach exit 0.
    scratch = tmp_path / "Scripts"
    scratch.mkdir()
    (scratch / "probe.py").write_text("print(1)\n")
    (scratch / "probe.py.adopted").write_text("adopted 2026-10-19 into local/recipes/x/probe.py\n")
    rows = sweep.scan([scratch], [], {}, ignore=set())
    assert [(r["name"], r["status"]) for r in rows] == [("probe.py", "adopted")]
    assert sweep.exit_code(rows) == 0


def test_cli_sweep_runs_and_reports_json():
    r = subprocess.run([sys.executable, "-m", "fieldkit", "sweep", "--json"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode in (0, 3), r.stderr[-400:]
    data = json.loads(r.stdout)
    assert "rows" in data and "summary" in data
