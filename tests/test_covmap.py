"""covmap (CI-SPEED-PLAN C4.1): the map of test -> files -> lines and the receipt key built on it."""
import json

import pytest

from fieldkit.method import covmap


def test_context_names_become_node_ids():
    assert covmap.parse_context("tests.test_x.test_y") == "tests/test_x.py::test_y"
    assert covmap.parse_context("tests.test_x.TestA.test_y") == "tests/test_x.py::TestA::test_y"
    assert covmap.parse_context("") == ""
    assert covmap.parse_context("helpers.setup") == ""


def test_ranges_join_neighbours_only():
    assert covmap.ranges([5, 1, 2, 3, 9, 3]) == [[1, 3], [5, 5], [9, 9]]


def test_from_lines_drops_files_outside_the_repo_and_lines_outside_a_test(tmp_path):
    inside, outside = tmp_path / "pkg" / "a.py", tmp_path.parent / "elsewhere.py"
    cm = covmap.from_lines({str(inside): {1: ["tests.test_a.test_one"], 2: ["tests.test_a.test_one", ""]},
                            str(outside): {1: ["tests.test_a.test_one"]}}, tmp_path)
    assert cm["tests"] == {"tests/test_a.py::test_one": {"pkg/a.py": [[1, 2]]}}


def test_merge_unions_lines_and_rejects_a_foreign_schema():
    a = {"schema": 1, "tests": {"t.py::x": {"f.py": [[1, 2]]}}}
    b = {"schema": 1, "tests": {"t.py::x": {"f.py": [[3, 4]], "g.py": [[1, 1]]}, "t.py::y": {"f.py": [[9, 9]]}}}
    assert covmap.merge([a, b])["tests"] == {"t.py::x": {"f.py": [[1, 4]], "g.py": [[1, 1]]},
                                             "t.py::y": {"f.py": [[9, 9]]}}
    with pytest.raises(ValueError):
        covmap.merge([a, {"schema": 2, "tests": {}}])


def test_key_changes_with_a_covered_file_and_never_exists_without_one():
    cm = {"schema": 1, "tests": {"t.py::x": {"a.py": [[1, 1]], "b.py": [[1, 1]]}}}
    blobs = {"a.py": "A1", "b.py": "B1"}
    k1 = covmap.key(cm, "t.py::x", blobs.get)
    assert k1 and k1 == covmap.key(cm, "t.py::x", blobs.get)
    assert covmap.key(cm, "t.py::x", {**blobs, "b.py": "B2"}.get) != k1       # a covered file changed
    assert covmap.key(cm, "t.py::x", {**blobs, "zzz.py": "Z"}.get) == k1      # an uncovered file did not matter
    assert covmap.key(cm, "t.py::x", {"a.py": "A1"}.get) is None              # a covered file is gone
    assert covmap.key(cm, "t.py::unknown", blobs.get) is None                 # the map does not know the test


def test_build_reads_a_real_coverage_database(tmp_path):
    import coverage

    src = tmp_path / "pkg" / "a.py"
    src.parent.mkdir()
    src.write_text("x = 1\n", encoding="utf-8")
    db = tmp_path / ".coverage"
    data = coverage.CoverageData(basename=str(db))
    data.add_lines({str(src): [1]})
    data.set_context("tests.test_a.test_one")
    data.add_lines({str(src): [1]})
    data.write()
    cm = covmap.build(db, tmp_path)
    assert cm["tests"] == {"tests/test_a.py::test_one": {"pkg/a.py": [[1, 1]]}}


def test_cli_build_merge_show_key(tmp_path, capsys):
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"schema": 1, "tests": {"t.py::x": {"a.py": [[1, 2]]}}}), encoding="utf-8")
    out = tmp_path / "out.json"
    assert covmap.main(["merge", str(m), str(m), str(out)]) == 0
    assert covmap.main(["show", str(out)]) == 0
    assert "1 tests, 1 files" in capsys.readouterr().out
    assert covmap.main(["show", str(out), "--test", "t.py::nope"]) == 3
    assert covmap.main(["merge", str(m), str(out)]) == 2
