"""Fluent files checked with Mozilla's own parser (fluent.syntax): an entry it rejects is dropped by Firefox, and a
message that lost an attribute leaves the code reading it with nothing. 2026-10-08: the check found a select
expression written on one line in the 157 tree's ipProtection.ftl (E0003, the button's label lost); the build had
not stopped for it."""
import json
import subprocess

import pytest

from fieldkit.buildh import firefox, fluent, task, verify

PRISTINE = """# A comment
ip-button =
    .label = Manage website settings
    .accesskey = M
ip-count = { $count ->
    [one] { $count } website
   *[other] { $count } websites
}
ip-gone = Gone soon
-brand = Gorilla
"""
# the 157 tree's defect, reduced: the variants of a select expression on one line
BROKEN = """# A comment
ip-button =
    .label = Manage website Gorilla settings
ip-count = { $count -> [one] { $count } website *[other] { $count } websites }
ip-new = New
-brand = Gorilla
"""


def test_a_select_expression_on_one_line_is_a_parse_error_with_its_line():
    errs = fluent.parse_errors(BROKEN)
    assert len(errs) == 1 and errs[0][0] == 4 and errs[0][1] == "E0003"
    assert fluent.parse_errors(PRISTINE) == []


def test_the_shape_diff_reports_lost_attributes_and_removed_ids_but_not_additions():
    assert fluent.shape(PRISTINE)["ip-button"] == (False, ("accesskey", "label"))
    assert fluent.shape(PRISTINE)["-brand"] == (True, ())
    d = fluent.shape_diff(PRISTINE, BROKEN)
    assert d["lost"] == [("ip-button", [".accesskey"])]
    assert d["removed"] == ["ip-count", "ip-gone"]                  # ip-count is inside the rejected entry
    assert fluent.shape_diff(BROKEN, PRISTINE)["lost"] == []          # a value or attribute added is not a loss


def test_changed_ftl_files_must_parse_unless_preprocessed_or_a_test(tmp_path):
    for rel, text in (("a/bad.ftl", BROKEN), ("a/ok.ftl", PRISTINE), ("a/pre.ftl", "#ifdef X\nfoo = 1\n#endif\n"),
                      ("a/test/bad.ftl", BROKEN)):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    out = firefox.syntax_problems(tmp_path, ["a/bad.ftl", "a/ok.ftl", "a/pre.ftl", "a/test/bad.ftl"])
    assert len(out) == 1 and out[0].startswith("a/bad.ftl: line 4: Fluent parse error E0003")


def test_no_parser_is_a_reported_gap_never_a_pass(tmp_path, monkeypatch):
    (tmp_path / "bad.ftl").write_text(BROKEN, encoding="utf-8")

    def missing(text):
        raise fluent.ParserMissing("fluent.syntax is not installed")
    monkeypatch.setattr(fluent, "_parse", missing)
    assert firefox.syntax_problems(tmp_path, ["bad.ftl"]) == [
        "fluent.syntax is not installed: changed .ftl files were NOT parsed (pip install fluent.syntax)"]


def git(w, *a):
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", *a], check=True,
                   capture_output=True)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    f = w / "browser" / "locales" / "en-US" / "browser" / "ipProtection.ftl"
    f.parent.mkdir(parents=True)
    f.write_text(PRISTINE, encoding="utf-8", newline="")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "pristine")
    h = tmp_path / "Gorilla.firefox"
    (h / "config").mkdir(parents=True)
    (h / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "patchset", "groups": {}}), encoding="utf-8")
    (h / "patchset").mkdir()
    task.start("f1", "demo", w, [], budget_tokens=1000, meta={"upstream": {"version": "157.0"}, "harness_root": str(h)})
    return w, f


def test_the_verifier_fails_a_broken_or_hollowed_fluent_file_and_lists_removed_ids(tree):
    w, f = tree
    f.write_text(BROKEN, encoding="utf-8", newline="")
    git(w, "commit", "-q", "-am", "port")
    rep = verify.verify("f1")
    assert any("ipProtection.ftl: line 4: Fluent parse error E0003" in x for x in rep["syntax"])
    assert rep["ftl_shape"] == ["browser/locales/en-US/browser/ipProtection.ftl: ip-button lost .accesskey"]
    assert "2 id(s)" in rep["ftl_removed"][0]
    rows = {name: (ok, ev) for name, ok, ev in verify.problems(rep)}
    parse_row = next(k for k in rows if "JS file parses" in k)         # buildrun finds the repairable row by these words
    assert not rows[parse_row][0] and ".ftl" in parse_row
    shape_row = rows["tree: no Fluent message lost its value or an attribute (against pristine)"]
    assert not shape_row[0] and "ids removed (on purpose?)" in shape_row[1]
    # removing a message on purpose, the file still parsing and nothing hollowed: both rows pass
    f.write_text(PRISTINE.replace("ip-gone = Gone soon\n", ""), encoding="utf-8", newline="")
    git(w, "commit", "-q", "-am", "cut one message")
    rows = {name: (ok, ev) for name, ok, ev in verify.problems(verify.verify("f1"))}
    assert rows[parse_row][0] and rows["tree: no Fluent message lost its value or an attribute (against pristine)"][0]
