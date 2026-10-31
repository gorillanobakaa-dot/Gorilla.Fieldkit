"""fieldkit rules lint (I-09, owner 2026-10-31): every check fires on a bad rule and stays quiet on the repository's
own rules; the old texts of the four bottlenecks B1-B3 (and B2's strike rule) are found. Fixture data only."""
import copy
from pathlib import Path

import pytest

from fieldkit import rules, rules_lint as L

ROOT = Path(rules.__file__).resolve().parents[1]


@pytest.fixture
def data():
    return copy.deepcopy(L.load(ROOT))


def kinds(found):
    return {(f["kind"], f["rule"]) for f in found if f["level"] == "error"}


def rule(d, rid):
    return next(r for r in d["core"] if r["id"] == rid)


def test_the_repository_rules_lint_clean_with_no_bottleneck(data):
    found = L.lint(data, ROOT)
    assert kinds(found) == set(), found
    assert not [f for f in found if f["kind"] == "BOTTLENECK"]


def test_drift_when_core_md_is_not_generated(tmp_path, data):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "CORE.md").write_text(L.generate_core(data) + "hand edit\n", encoding="utf-8")
    assert ("drift", "CORE.md") in kinds(L.lint(data, tmp_path))
    (tmp_path / "rules" / "CORE.md").write_text(L.generate_core(data), encoding="utf-8")
    assert ("drift", "CORE.md") not in kinds(L.lint(data, tmp_path))


@pytest.mark.parametrize("old, why", [
    # B3: the question at every job end
    ("DONE. Before ending, name every needed document and ask the owner once: add it to git?", "(1)"),
    # B1: a headless run stops at its push and Zero opens the PR by hand
    ("DONE. open the pull request where your agent can (a headless run reports the branch; the command post opens "
     "it).", "(4)"),
    ("DONE. Zero pushes each job's branch by hand.", "(2)"),
    # B2: strikes on failures that are not the branch's
    ("TWO STRIKES. A PR, job or fix that fails CI twice is PARKED.", "(5)"),
    # serialising with no measured reason
    ("TESTS ONCE. Run the suites one at a time.", "(3)"),
])
def test_each_bottleneck_kind_is_found(data, old, why):
    rid = old.split(".")[0]
    r = rule(data, rid)
    r["text"] = old
    r.pop("serial_reason", None)
    r.pop("owner_decision", None)
    found = [f for f in L.lint(data, ROOT) if f["kind"] == "BOTTLENECK" and f["rule"] == rid]
    assert any(f["evidence"].startswith(why) for f in found), found


def test_a_negated_ask_is_not_a_bottleneck_and_a_listed_owner_decision_may_ask(data):
    assert not L.bottlenecks("X", "No plan drafts waiting for approval, no chains of commands for the owner to paste.", {})
    assert L.bottlenecks("X", "Then ask the owner which one.", {})
    assert not L.bottlenecks("X", "Then ask the owner which one.", {"owner_decision": "owner 2026-10-31"})
    assert not L.bottlenecks("X", "Run them one at a time.", {"serial_reason": "2026-10-27 crash"})
    assert L.bottlenecks("X", "Run them one at a time.", {"serial_reason": "it felt safer"})


def test_budget_duplicate_misplaced_stale_unenforced_and_contradiction(data):
    rule(data, "GOAL")["text"] = "GOAL. " + "x" * L.RULE_BUDGET
    rule(data, "BUILDS")["subject"] = "goal"
    rule(data, "MODELS")["text"] += " Then `fieldkit ship ci-wait`."
    rule(data, "CLEAN UP")["text"] += " Zero runs `fieldkit zero retire`."
    rule(data, "TWO STRIKES")["uses"] = ["fieldkit ship ci-wait", "fieldkit never named"]
    rule(data, "THREAT MODEL")["enforced_by"] = []
    rule(data, "IDEAS")["text"] = "IDEAS. Parked with `fieldkit ideas add`."
    rule(data, "HONESTY")["text"] += " Ask the owner when unsure."
    found = L.lint(data, ROOT)
    k = kinds(found)
    assert ("budget", "GOAL") in k and ("duplicate", "BUILDS") in k and ("duplicate", "MODELS") in k
    assert ("misplaced", "CLEAN UP") in k and ("stale", "TWO STRIKES") in k and ("stale", "IDEAS") in k
    assert ("contradiction", "HONESTY") in k and ("BOTTLENECK", "HONESTY") in k
    assert {"kind": "unenforced", "rule": "THREAT MODEL", "level": "warning",
            "evidence": "no hook, test or tool enforces it: delete it or enforce it"} in found


def test_a_file_over_the_limit_is_found_before_it_is_installed(data):
    data["tails"]["gemini"] += "\n" + "y" * 400
    assert ("budget", "FILE gemini") in kinds(L.lint(data, ROOT))


def test_install_marks_an_unmarked_tail_once_and_check_finds_a_changed_block(tmp_path):
    home = tmp_path
    for p in rules.global_files(home).values():
        p.parent.mkdir(parents=True)
    codex = rules.global_files(home)["codex"]
    codex.write_bytes((rules.core_block(ROOT) + "\n\n# Codex\n- a long old tail line\n\n<!-- fieldkit-lifecycle-rule v1 -->"
                       "\nold\n<!-- /fieldkit-lifecycle-rule -->\n").replace("\n", "\r\n").encode("utf-8"))
    r = rules.install_all(home, ROOT, backup_dir=tmp_path / "bk")
    text = codex.read_text(encoding="utf-8")
    assert "a long old tail line" not in text and text.count(L.TAIL_BEGIN) == 1 and b"\r\n" in codex.read_bytes()
    assert r[1]["done"].startswith("updated") and r[0]["done"].startswith("missing")
    assert all(x["ok"] for x in rules.check(ROOT, home)), rules.check(ROOT, home)
    assert rules.install_all(home, ROOT, backup_dir=tmp_path / "bk")[1]["done"] == "already current"
    codex.write_text(codex.read_text(encoding="utf-8").replace("# Codex", "# Codex edited"), encoding="utf-8")
    bad = [x for x in rules.check(ROOT, home) if not x["ok"]]
    assert bad and "tail block" in bad[0]["problems"][0]
    assert ("identical", "codex") in kinds(L.check_installed({"codex": codex}, root=ROOT))


def test_shared_blocks_must_be_identical_across_files(tmp_path):
    data = L.load(ROOT)
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text(L.assembled(data, "codex"), encoding="utf-8")
    b.write_text(L.assembled(data, "gemini").replace("Session rotation", "Session rotation!"), encoding="utf-8")
    found = L.check_installed({"codex": a, "gemini": b}, data, ROOT)
    assert ("identical", "rotate") in kinds(found)


def test_an_invented_enforcer_is_a_finding_and_a_real_one_resolves(data):
    """Owner order 2026-11-01: every enforced_by entry names a thing that exists (Zero 16 removed three by hand)."""
    cmds = {"task": None, "tools": {"list", "check", "find"}}
    assert L.resolve("fieldkit/lifecycle/watch.py (task watch)", ROOT, cmds)[0]
    assert L.resolve("tests/test_rules_lint.py::test_x", ROOT, cmds)[0]
    assert L.resolve("fieldkit task watch (UNHANDLED)", ROOT, cmds)[0]
    assert not L.resolve("the tool router", ROOT, cmds)[0]
    assert not L.resolve("fieldkit/nope.py", ROOT, cmds)[0]
    assert not L.resolve("fieldkit nosuch", ROOT, cmds)[0]
    assert not L.resolve("fieldkit tools search", ROOT, cmds)[0]          # a real command, an action it lacks
    rule(data, "WAITING")["enforced_by"] = ["the watcher that wakes Zero"]
    assert ("enforcer", "WAITING") in kinds(L.lint(data, ROOT))


def test_a_command_the_text_names_must_exist(data):
    """2026-11-01: USE THE HARNESS told every agent to run `fieldkit tools find`, which did not exist."""
    rule(data, "WAITING")["text"] += " `fieldkit task nosuchverb ID`"
    assert any(f["kind"] == "enforcer" and "nosuchverb" in f["evidence"] for f in L.lint(data, ROOT))


def test_a_proof_must_point_at_a_real_test_and_a_unique_swap(data):
    r = rule(data, "WAITING")
    r["proofs"] = [{"demand": "wakes", "test": "tests/test_lifecycle_watch.py",
                    "mutate": ["fieldkit/lifecycle/watch.py", "return (3 if found else 0), bool(over)", "x"]}]
    assert ("proof", "WAITING") not in kinds(L.lint(data, ROOT))
    r["proofs"][0]["mutate"][1] = "return"                                 # many times in the file: ambiguous
    assert ("proof", "WAITING") in kinds(L.lint(data, ROOT))
    r["proofs"][0]["mutate"][1] = "return (3 if found else 0), bool(over)"
    r["proofs"][0]["test"] = "tests/test_missing.py"
    assert ("proof", "WAITING") in kinds(L.lint(data, ROOT))


def test_the_lifecycle_and_rotate_texts_come_from_rules_yaml(data):
    """A4 2026-11-01: one file holds every rule; the code reads its parts."""
    from fieldkit.lifecycle import protocol
    from fieldkit.tokens import rotate
    g = {e["id"]: e for e in data["generated"]}
    assert g["LIFECYCLE"]["parts"]["side_jobs"] == protocol.side_jobs_rule()
    assert g["ROTATE"]["parts"]["text"] in rotate.rule_text()
    assert protocol.ZERO_ARCHIVE == g["LIFECYCLE"]["parts"]["zero_archive"]
