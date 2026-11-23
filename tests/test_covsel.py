"""Coverage selection (CI-SPEED-PLAN C4.2-C4.4): fake diffs, fake maps, fake gh; no repository needed."""
import json

import pytest

from fieldkit.method import ciaudit, ciplan, covsel

SRC = "import os\nX = 1\n\n\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n"   # a: 5-6, b: 9-10
CMAP = {"schema": 1, "tests": {
    "tests/test_a.py::t1": {"fieldkit/m.py": [[5, 6]]},
    "tests/test_b.py::t1": {"fieldkit/m.py": [[9, 10]]},
}}


def fb(files):
    return {"tests": [f"fb:{f}" for f in files], "unmapped": []}


def plan(paths, hunks, src=SRC, cmap=CMAP, fallback=fb):
    return covsel.coverage_plan(paths, cmap, lambda p: hunks.get(p), lambda p: src if p in hunks or src else None, fallback)


def test_parse_hunks_old_side_and_pure_insertion():
    d = "@@ -5,2 +5,3 @@ def a\n+x\n@@ -9 +10 @@\n@@ -20,0 +21,2 @@\n"
    assert covsel.parse_hunks(d) == [(5, 6), (9, 9), (20, 21)]


def test_change_inside_a_function_selects_only_the_tests_that_ran_it():
    r = plan(["fieldkit/m.py"], {"fieldkit/m.py": [(6, 6)]})
    assert r["tests"] == ["tests/test_a.py"] and r["unmapped"] == [] and r["fallback"] == {}


def test_top_level_change_goes_to_the_import_fallback():
    r = plan(["fieldkit/m.py"], {"fieldkit/m.py": [(2, 2)]})
    assert r["tests"] == ["fb:fieldkit/m.py"] and r["fallback"] == {"fieldkit/m.py": "change outside any function"}


def test_one_hunk_outside_a_function_sends_the_whole_file_to_the_fallback():
    r = plan(["fieldkit/m.py"], {"fieldkit/m.py": [(6, 6), (2, 2)]})
    assert r["tests"] == ["fb:fieldkit/m.py"]


def test_new_file_goes_to_the_fallback():
    r = covsel.coverage_plan(["fieldkit/new.py"], CMAP, lambda p: [(1, 5)], lambda p: None, fb)
    assert r["fallback"] == {"fieldkit/new.py": "new file"} and r["tests"] == ["fb:fieldkit/new.py"]


def test_file_not_in_the_map_yaml_and_dirty_files_go_to_the_fallback():
    r = plan(["fieldkit/other.py", "a.yaml", "fieldkit/m.py"], {"fieldkit/other.py": [(1, 1)], "a.yaml": [(1, 1)]})
    assert r["fallback"] == {"fieldkit/other.py": "not in the map", "a.yaml": "not python",
                             "fieldkit/m.py": "diff unreadable (deleted or dirty)"}


def test_uncovered_change_in_a_mapped_function_is_refused():
    cmap = {"schema": 1, "tests": {"tests/test_a.py::t1": {"fieldkit/m.py": [[5, 5]]}}}
    r = plan(["fieldkit/m.py"], {"fieldkit/m.py": [(6, 6)]}, cmap=cmap)
    assert r["tests"] == [] and r["unmapped"] == ["fieldkit/m.py"]


def test_a_changed_test_file_runs_itself_via_the_fallback():
    r = plan(["tests/test_z.py"], {"tests/test_z.py": [(1, 1)]})
    assert r["fallback"] == {"tests/test_z.py": "a changed test runs itself"}


def test_gate_combine_never_selects_fewer_than_the_import_rule():
    imp = {"tests": ["tests/a.py", "tests/b.py"], "unmapped": ["x"]}
    cov = {"tests": ["tests/b.py", "tests/c.py"], "unmapped": ["x", "y"]}
    assert covsel.combine("import", imp, cov) == (["tests/a.py", "tests/b.py"], ["x"])
    assert covsel.combine("coverage", imp, cov) == (["tests/b.py", "tests/c.py"], ["x", "y"])
    assert covsel.combine("union", imp, cov) == (["tests/a.py", "tests/b.py", "tests/c.py"], ["x"])
    assert covsel.combine("coverage", imp, None) == (["tests/a.py", "tests/b.py"], ["x"])
    with pytest.raises(ValueError):
        covsel.combine("bogus", imp, cov)


def test_ciplan_gate_default_is_import_and_unknown_values_are_rejected(tmp_path):
    assert ciplan.DEFAULTS["selection_gate"] == "import"
    p = tmp_path / "ci.yaml"
    p.write_text("selection_gate: bogus\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ciplan.load_config(str(p))


# --- replay ---
LOG = "FAILED tests/test_b.py::t1 - assert 1\nERROR tests/test_c.py::t2 - boom\nFAILED tests/test_b.py::t3\n"


def test_red_tests_are_the_failed_and_errored_files_once():
    assert ciaudit.red_tests(LOG) == ["tests/test_b.py", "tests/test_c.py"]


def fake_gh(args):
    if args[:2] == ["run", "list"]:
        return 0, json.dumps([{"databaseId": 7, "headSha": "a" * 40, "headBranch": "feat/x"},
                              {"databaseId": 8, "headSha": "b" * 40, "headBranch": "feat/y"}]), ""
    return 0, LOG if args[2] == "7" else "FAILED tests/test_a.py::t1\n", ""


def hunks_for(sha):
    # run 7 changed function a (covered by test_a only): test_b and test_c went red: two misses.
    # run 8 changed function a: test_a went red: caught.
    return {"fieldkit/m.py": ([(6, 6)], SRC)}


def test_the_replay_counts_a_miss_when_a_red_test_was_not_selected():
    rows = ciaudit.replay(CMAP, 2, gh=fake_gh, hunks_for=hunks_for, fallback=fb)
    assert rows[0]["missed"] == ["tests/test_b.py", "tests/test_c.py"] and rows[1]["missed"] == []
    s = ciaudit.replay_summary(rows)
    assert s["misses"] == 2 and s["runs_with_misses"] == 1 and s["gate"] == "union"


def test_the_replay_skips_runs_it_cannot_judge_and_reports_no_misses_as_import():
    rows = ciaudit.replay(CMAP, 2, gh=fake_gh, hunks_for=lambda sha: None, fallback=fb)
    assert {r["status"] for r in rows} == {"commit not in the local clone"} and all(r["missed"] == [] for r in rows)
    s = ciaudit.replay_summary(rows)
    assert s["replayed"] == 0 and s["misses"] == 0 and s["gate"] == "import"


def test_replay_subcommand_exits_3_on_misses(tmp_path, monkeypatch, capsys):
    m = tmp_path / "covmap.json"
    m.write_text(json.dumps(CMAP), encoding="utf-8")
    monkeypatch.setattr(ciaudit, "_gh", lambda args, run=None: fake_gh(args))
    monkeypatch.setattr(ciaudit, "_git_hunks", lambda root: hunks_for)
    assert ciaudit.main(["replay", "--covmap", str(m), "--runs", "2"]) == 3
    assert "2 miss(es)" in capsys.readouterr().out
