"""fieldkit ship push --auto: push, the PR (found or created), GitHub auto-merge on. gh and git are faked."""
import json
import subprocess

import pytest

from fieldkit.method import ship, strikes

SHA = "a" * 40
URL = "https://github.com/o/r/pull/7"


@pytest.fixture(autouse=True)
def _own_registry(tmp_path, monkeypatch):
    """F4: the registry is a temp file; the merge base is fixed; the failed test PASSES on the base unless a test says
    otherwise (a real run would build a worktree)."""
    monkeypatch.setenv(strikes.ENV, str(tmp_path / "parked.json"))
    monkeypatch.delenv("FIELDKIT_ATTEMPT_MODEL", raising=False)       # no known model on the ladder: the flat park
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.setattr(strikes, "merge_base", lambda head, base="main", repo=".": "m" * 40)
    monkeypatch.setattr(strikes, "default_runner", lambda base_sha, ids, job="": {i: "pass" for i in ids})


RULES = [{"type": "pull_request", "parameters": {}},
         {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": True,
          "required_status_checks": [{"context": "test (ubuntu-latest)"}, {"context": "test (windows-latest)"}]}}]


class Gh:
    """A fake subprocess.run for gh: answers by the sub-command, records every call."""
    def __init__(self, prs=(), runs=(), fail=None, rules=RULES):
        # a PR given without a base is a PR to main from this repository (the fields gh pr list returns)
        self.prs = [dict({"baseRefName": "main", "isCrossRepository": False}, **p) for p in prs]
        self.runs, self.fail, self.calls, self.rules = list(runs), fail, [], list(rules)

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        key = " ".join(cmd[1:3])
        if cmd[1] == "api":
            key = "api"
        if self.fail == key:
            return subprocess.CompletedProcess(cmd, 1, "", "gh: boom")
        out = {"pr list": json.dumps(self.prs), "run list": json.dumps(self.runs), "pr create": URL + "\n",
               "pr merge": "", "run view": "FAILED tests/test_x.py::test_y - boom\n", "api": json.dumps(self.rules)}[key]
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def did(self, key):
        return [c for c in self.calls if " ".join(c[1:3]) == key]


def pusher(repo, base):
    return {"ok": True, "branch": "feat/x", "local": SHA, "pushed": True, "said": "pushed"}


def go(gh, **kw):
    notes = []
    r = ship.auto(".", "main", run=gh, pusher=pusher, inbox=lambda *a: notes.append(a), **kw)
    return r, notes


def test_existing_pr_gets_auto_merge_on_the_pushed_commit():
    gh = Gh(prs=[{"number": 7, "url": URL}])
    r, _ = go(gh)
    assert r["ok"] and r["auto"] and r["pr_url"] == URL and "auto-merge on" in r["said"]
    assert not gh.did("pr create")
    assert gh.did("pr merge")[0][3:] == ["7", "--auto", "--merge", "--match-head-commit", SHA]


def test_missing_pr_is_created_then_merged_on():
    gh = Gh()
    r, _ = go(gh)
    assert r["ok"] and r["pr"] == 7
    assert gh.did("pr create")[0][3:] == ["--head", "feat/x", "--base", "main", "--fill"]
    assert gh.did("pr merge")


def test_title_and_body_file_go_to_pr_create():
    gh = Gh()
    go(gh, title="T", body_file="b.md")
    assert gh.did("pr create")[0][-4:] == ["--title", "T", "--body-file", "b.md"]


@pytest.mark.parametrize("which", ["pr list", "pr create", "run list", "pr merge"])
def test_gh_failure_is_exit_1_text_and_the_push_stands(which):
    r, _ = go(Gh(fail=which))
    assert not r["ok"] and r["gh_failed"] and "gh: boom" in r["error"] and r["branch"] == "feat/x"


def test_three_red_commits_park_and_never_call_merge(monkeypatch):
    from fieldkit.lifecycle import models
    monkeypatch.setattr(models, "ladder_cfg", lambda data=None: {"enabled": False, "own_limit": 3})   # ladder off: the flat park
    gh = Gh(prs=[{"number": 7, "url": URL}],
            runs=[{"conclusion": "failure", "headSha": "b" * 40, "databaseId": 1},
                  {"conclusion": "success", "headSha": "c" * 40},
                  {"conclusion": "failure", "headSha": "c" * 40, "databaseId": 2},
                  {"conclusion": "failure", "headSha": "d" * 40, "databaseId": 3}])
    r, notes = go(gh)
    assert not r["ok"] and r["state"] == "parked" and not r.get("gh_failed")
    assert not gh.did("pr merge")
    assert notes == [] and "#7" in r["error"] and "feat/x" in r["error"]      # F4: no inbox item
    assert len(strikes.load()["strikes"]["feat/x"]["rounds"]) == 3


def test_two_red_commits_that_fail_on_main_too_are_not_strikes(monkeypatch):
    """F4: PR 295 was parked for main's red; a failed test that also fails on the merge base is main's."""
    monkeypatch.setattr(strikes, "default_runner", lambda base_sha, ids, job="": {i: "fail" for i in ids})
    gh = Gh(prs=[{"number": 7, "url": URL}],
            runs=[{"conclusion": "failure", "headSha": "b" * 40, "databaseId": 1},
                  {"conclusion": "failure", "headSha": "c" * 40, "databaseId": 2}])
    r, notes = go(gh)
    assert r["ok"] and r["auto"] and gh.did("pr merge") and not notes
    assert strikes.load()["strikes"].get("feat/x", {}).get("rounds", []) == []


def test_one_red_commit_with_two_red_runs_is_one_strike():
    # P14 (I-11): Opencode PR 47, one red commit read by two workflow triggers, was parked as TWO STRIKES
    gh = Gh(prs=[{"number": 7, "url": URL}],
            runs=[{"conclusion": "failure", "headSha": SHA}, {"conclusion": "failure", "headSha": SHA}])
    r, notes = go(gh)
    assert r["ok"] and r["auto"] and not notes
    assert "headSha" in gh.did("run list")[0][gh.did("run list")[0].index("--json") + 1]


def test_one_red_run_still_turns_auto_merge_on():
    r, _ = go(Gh(prs=[{"number": 7, "url": URL}], runs=[{"conclusion": "failure"}]))
    assert r["ok"]


def test_failed_push_stops_before_gh():
    gh = Gh()
    r = ship.auto(".", "main", run=gh, pusher=lambda *a: {"ok": False, "state": "diverged"})
    assert not r["ok"] and not gh.calls


def test_auto_with_tag_is_refused():
    with pytest.raises(SystemExit) as e:
        ship.main(["push", "--auto", "--tag", "v1"])
    assert e.value.code == 2


def test_exit_codes(monkeypatch):
    monkeypatch.setattr(ship, "auto", lambda *a, **k: {"ok": False, "state": "parked"})
    assert ship.main(["push", "--auto"]) == 3
    monkeypatch.setattr(ship, "auto", lambda *a, **k: {"ok": False, "gh_failed": True, "state": "auto-failed"})
    assert ship.main(["push", "--auto"]) == 1
    monkeypatch.setattr(ship, "auto", lambda *a, **k: {"ok": True, "auto": True, "said": "x"})
    assert ship.main(["push", "--auto"]) == 0


def test_no_auto_merge_where_the_base_branch_requires_no_check():
    """2026-10-31: Gorilla.Opencode has no ruleset; `gh pr merge --auto` merged #42-#44 before CI ended."""
    gh = Gh(prs=[{"number": 7, "url": URL}], rules=())
    r, notes = go(gh)
    assert not r["ok"] and r["state"] == "no-required-checks" and not gh.did("pr merge")
    assert notes == []                                           # F2.4: the PR stays open, the inbox gets nothing
    assert [c[2] for c in gh.calls if c[1] == "api"] == ["repos/{owner}/{repo}/rules/branches/main"]


def test_a_rule_type_without_both_os_contexts_does_not_merge():
    """2026-11-25: a required_status_checks rule naming one OS (or other contexts) is not enough."""
    one = [{"type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "test (ubuntu-latest)"}]}}]
    for rules in (one, [{"type": "required_status_checks", "parameters": {}}], {"message": "Not Found"}):
        gh = Gh(prs=[{"number": 7, "url": URL}], rules=rules)
        r, _ = go(gh)
        assert not r["ok"] and r["state"] == "no-required-checks" and not gh.did("pr merge")
        assert "test (windows-latest)" in r["error"]


def test_missing_contexts_reads_the_rules_list():
    assert ship.missing_contexts(RULES) == []
    assert ship.missing_contexts([]) == list(ship.REQUIRED_CONTEXTS)
    assert ship.missing_contexts(None) == list(ship.REQUIRED_CONTEXTS)


def test_a_fork_or_other_base_pr_on_the_same_branch_name_is_not_ours():
    """2026-11-25: the first open PR by head NAME may be a fork's or aim at another base: a new PR is made instead."""
    gh = Gh(prs=[{"number": 5, "url": URL.replace("7", "5"), "isCrossRepository": True},
                 {"number": 6, "url": URL.replace("7", "6"), "baseRefName": "release-1"}])
    r, _ = go(gh)
    assert r["ok"] and gh.did("pr create") and r["pr"] == 7
    assert gh.did("pr merge")[0][3] == "7"


def test_a_failed_rules_lookup_does_not_merge():
    gh = Gh(prs=[{"number": 7, "url": URL}], fail="api")
    r, _ = go(gh)
    assert not r["ok"] and r["state"] == "auto-failed" and not gh.did("pr merge")


def test_three_red_commits_on_a_ladder_model_escalate_instead_of_parking(monkeypatch):
    """P9: ladder on, Sonnet failed three times: the next rung is named, nothing is parked, no merge is attempted."""
    monkeypatch.setenv("FIELDKIT_ATTEMPT_MODEL", "sonnet")
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(strikes.path().parent / "lc"))
    gh = Gh(prs=[{"number": 7, "url": URL}],
            runs=[{"conclusion": "failure", "headSha": "b" * 40, "databaseId": 1},
                  {"conclusion": "failure", "headSha": "c" * 40, "databaseId": 2},
                  {"conclusion": "failure", "headSha": "d" * 40, "databaseId": 3}])
    r, _ = go(gh)
    assert not r["ok"] and r["state"] == "escalate" and r["escalate_to"] == "opus"
    assert not gh.did("pr " + "merge") and strikes.parked_for("feat/x") is None


def test_required_contexts_reads_rulesets_and_classic_protection():
    rules = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "a", "integration_id": 7}]}}]
    classic = {"contexts": ["b"], "checks": [{"context": "c", "app_id": 9}]}
    assert ship.required_contexts(rules, classic) == {"a": 7, "b": None, "c": 9}
    assert ship.required_contexts({"message": "Not Found"}, None) == {}


def test_a_pr_merge_needs_both_os_contexts():
    assert ship.unrequired(Gh(), ".", "main") == ([], "")
    assert ship.unrequired(Gh(rules=[]), ".", "main")[0] == list(ship.REQUIRED_CONTEXTS)


def test_a_proof_anchor_that_no_longer_holds_is_found(tmp_path, monkeypatch):
    from fieldkit import rules_lint as L
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "proofs.yaml").write_text("{}")
    monkeypatch.setattr(ship, "git", lambda *a, **k: type("R", (), {"stdout": str(tmp_path)})())
    monkeypatch.setattr(L, "load", lambda root: {})
    monkeypatch.setattr(L, "entries", lambda data: [("HARD LINES", "", {})])
    monkeypatch.setattr(L, "proof_findings", lambda rid, e, root: [{"rule": rid, "evidence": "anchor gone"}])
    assert ship.stale_proofs(tmp_path) == ["HARD LINES: anchor gone"]
