"""fieldkit github (G1): GitHub faked; every known mistake is found by `check`; apply asks first, snapshots first,
verifies, refuses outside a person's terminal; undo restores; launchers exist and call the right command."""
import copy
import json
import subprocess
from pathlib import Path

import pytest

from fieldkit import github
from fieldkit.github import engine
from fieldkit.github.engine import GhError

ROOT = Path(__file__).resolve().parents[1]
REPO = "me/proj"
CI = ["test (ubuntu-latest)", "test (windows-latest)"]


class FakeGitHub:
    """A tiny GitHub: one repository, rulesets, check runs. Records every call."""

    def __init__(self):
        self.info = {"full_name": REPO, "default_branch": "main", "private": True, "allow_auto_merge": False,
                     "delete_branch_on_merge": False, "permissions": {"admin": True},
                     "security_and_analysis": {"secret_scanning": {"status": "disabled"},
                                               "secret_scanning_push_protection": {"status": "disabled"}}}
        self.rulesets, self.next_id = {}, 100
        self.branches = ["main", "feat/old"]
        self.closed_prs = []
        self.ran = list(CI)
        self.actions, self.dependabot = True, False
        self.calls = []

    def add_ruleset(self, **kw):
        rs = {"id": self.next_id, "name": "x", "target": "branch", "enforcement": "active", "bypass_actors": [],
              "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}, "rules": []}
        rs.update(kw)
        self.rulesets[self.next_id] = rs
        self.next_id += 1
        return rs

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        p = path.split("?")[0]
        base = f"repos/{REPO}"
        if p == base:
            if method == "PATCH":
                for k, v in body.items():
                    if k == "security_and_analysis":
                        for kk, vv in v.items():
                            self.info["security_and_analysis"][kk].update(vv)
                    else:
                        self.info[k] = v
                return {}
            return copy.deepcopy(self.info)
        if p == base + "/rulesets":
            if method == "POST":
                rs = self.add_ruleset(**body)
                return copy.deepcopy(rs)
            return [{"id": i, "name": r["name"]} for i, r in self.rulesets.items()]
        if p.startswith(base + "/rulesets/"):
            rid = int(p.rsplit("/", 1)[1])
            if method == "PUT":
                self.rulesets[rid].update(copy.deepcopy(body))
                return copy.deepcopy(self.rulesets[rid])
            return copy.deepcopy(self.rulesets[rid])
        if p == base + "/branches":
            return [{"name": b} for b in self.branches]
        if p == base + "/pulls":
            return self.closed_prs
        if p.endswith("/check-runs"):
            return {"check_runs": [{"name": n} for n in self.ran]}
        if p == base + "/vulnerability-alerts":
            if method == "GET":
                if not self.dependabot:
                    raise GhError("HTTP 404", 404)
                return {}
            self.dependabot = method == "PUT"
            return {}
        if p == base + "/actions/permissions":
            if method == "PUT":
                self.actions = body["enabled"]
            return {"enabled": self.actions}
        raise GhError("HTTP 404 Not Found", 404)


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(engine, "api", fake)
    return fake


def ids(res):
    return {f["id"] for f in res["findings"]}


def run_check(gh_, profile=None):
    return engine.check(engine.read_state(REPO), profile)


# ------------------------------------------------------------------ today's six mistakes

def test_mistake1_menu_path_is_rules_not_security(gh):
    res = run_check(gh)
    f = next(x for x in res["findings"] if x["id"] == "no-protection")
    assert "Settings > Rules > Rulesets" in f["text"] and "not under Settings > Security" in f["text"]
    for sid, s in engine.catalogue()["settings"].items():
        if s["kind"] == "ruleset":
            assert "Settings > Rules > Rulesets" in s["where"] and "Security" not in s["where"].replace("NOT Settings > Security", ""), sid


def test_mistake2_ruleset_with_no_target_branch(gh):
    gh.add_ruleset(name="dead", conditions={"ref_name": {"include": [], "exclude": []}}, rules=[{"type": "deletion"}])
    assert "ruleset-no-target" in ids(run_check(gh))
    gh.rulesets[100]["conditions"]["ref_name"]["include"] = ["refs/heads/mian"]       # a typo'd branch
    assert "ruleset-no-target" in ids(run_check(gh))
    gh.rulesets[100]["conditions"]["ref_name"]["include"] = ["~DEFAULT_BRANCH"]
    assert "ruleset-no-target" not in ids(run_check(gh))


def test_mistake3_required_check_that_never_ran(gh):
    gh.add_ruleset(rules=[{"type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": True,
        "required_status_checks": [{"context": "type test (ubuntu-latest)"}]}}])
    f = [x for x in run_check(gh)["findings"] if x["id"] == "check-name-unknown"]
    assert f and "type test (ubuntu-latest)" in f[0]["text"]
    gh.rulesets[100]["rules"][0]["parameters"]["required_status_checks"] = [{"context": CI[0]}]
    assert "check-name-unknown" not in ids(run_check(gh))


def test_mistake4_unattributed_approval(gh):
    pr = {"required_approving_review_count": 0, "dismiss_stale_reviews_on_push": False, "require_code_owner_review": False,
          "require_last_push_approval": True, "required_review_thread_resolution": False}
    gh.add_ruleset(rules=[{"type": "pull_request", "parameters": pr}])
    f = [x for x in run_check(gh)["findings"] if x["id"] == "unattributed-approval"]
    assert f and "fieldkit@localhost" in f[0]["text"]
    pr["require_last_push_approval"] = False
    gh.rulesets[100]["rules"][0]["parameters"] = pr
    assert "unattributed-approval" not in ids(run_check(gh))


def test_mistake5_automerge_without_required_checks(gh):
    gh.info["allow_auto_merge"] = True
    assert "automerge-no-checks" in ids(run_check(gh))
    gh.add_ruleset(rules=[{"type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": True, "required_status_checks": [{"context": CI[0]}]}}])
    assert "automerge-no-checks" not in ids(run_check(gh))


def test_mistake6_deletion_rule_stops_branch_cleanup(gh):
    gh.info["delete_branch_on_merge"] = True
    gh.add_ruleset(name="all-branches", conditions={"ref_name": {"include": ["~ALL"], "exclude": []}},
                   rules=[{"type": "deletion"}])
    f = [x for x in run_check(gh)["findings"] if x["id"] == "branches-not-deleted"]
    assert f and "all-branches" in f[0]["text"]
    gh.rulesets[100]["conditions"]["ref_name"]["include"] = ["~DEFAULT_BRANCH"]      # main only: feature branches go
    assert "branches-not-deleted" not in ids(run_check(gh))


def test_mistake6_merged_branch_still_there(gh):
    gh.info["delete_branch_on_merge"] = True
    gh.closed_prs = [{"merged_at": "2026-10-30T00:00:00Z", "head": {"ref": "feat/old", "sha": "abc",
                                                                   "repo": {"full_name": REPO}}}]
    f = [x for x in run_check(gh)["findings"] if x["id"] == "branches-not-deleted"]
    assert f and "feat/old" in f[0]["text"]


def test_clean_repo_has_no_mistakes(gh):
    gh.add_ruleset(rules=[{"type": "deletion"}, {"type": "non_fast_forward"}])
    assert run_check(gh)["findings"] == []


# ------------------------------------------------------------------ plan

def test_plan_shows_the_right_diff(gh):
    plan = engine.make_plan(engine.catalogue()["profiles"]["long-horizon"], engine.read_state(REPO))
    ch = {c["id"]: c for c in plan["changes"]}
    assert ch["required_checks"]["wanted"] == sorted(CI) and ch["required_checks"]["current"] == []
    assert ch["auto_merge"]["current"] is False and ch["auto_merge"]["wanted"] is True and not ch["auto_merge"]["blocked"]
    assert "approvals" not in ch                         # 0 wanted, 0 now
    assert ch["secret_scanning"]["wanted"] is True


def test_plan_blocks_automerge_when_no_check_has_run(gh):
    gh.ran = []
    ch = {c["id"]: c for c in engine.make_plan(engine.catalogue()["profiles"]["long-horizon"],
                                               engine.read_state(REPO))["changes"]}
    assert ch["required_checks"]["blocked"] and ch["auto_merge"]["blocked"] and ch["checks_up_to_date"]["blocked"]


def test_plan_refuses_a_check_name_that_never_ran(gh):
    st = engine.read_state(REPO)
    ch = {c["id"]: c for c in engine.make_plan(engine.catalogue()["profiles"]["long-horizon"], st,
                                               ["type test (ubuntu-latest)"])["changes"]}
    assert "never ran" in ch["required_checks"]["blocked"]


# ------------------------------------------------------------------ apply and undo

def answers(*a):
    it = iter(a)
    return lambda prompt="": next(it)


def test_apply_refuses_outside_a_persons_terminal(gh):
    out = []
    rc = engine.apply_profile("long-horizon", REPO, ask=answers(), out=out.append, owner=lambda: False)
    assert rc == 1 and "REFUSED" in out[0]
    assert [c for c in gh.calls if c[0] != "GET"] == []


def test_apply_refuses_without_admin(gh):
    gh.info["permissions"] = {"admin": False}
    rc = engine.apply_profile("long-horizon", REPO, ask=answers(), out=lambda s: None, owner=lambda: True)
    assert rc == 1 and [c for c in gh.calls if c[0] != "GET"] == []


def test_apply_n_changes_nothing_but_snapshots(gh, tmp_path):
    rc = engine.apply_profile("long-horizon", REPO, ask=answers("n"), out=lambda s: None, owner=lambda: True,
                              folder=tmp_path)
    assert rc == 0 and [c for c in gh.calls if c[0] != "GET"] == []
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_apply_snapshots_first_asks_each_time_and_verifies(gh, tmp_path):
    prompts, out = [], []

    def ask(p=""):
        prompts.append(p)
        return "y"
    rc = engine.apply_profile("long-horizon", REPO, ask=ask, out=out.append, owner=lambda: True, folder=tmp_path)
    assert rc == 0
    snaps = list(tmp_path.glob("*.json"))
    assert len(snaps) == 1 and json.loads(snaps[0].read_text())["repo"] == REPO
    writes = [c for c in gh.calls if c[0] != "GET"]
    # one question per applied change: as many prompts as writes
    assert len(prompts) == len(writes)
    assert sum("DONE and verified" in s for s in out) == len(writes)
    values = engine.values(engine.read_state(REPO))
    assert values["auto_merge"] and values["delete_branch_on_merge"] and values["secret_scanning"]
    assert values["required_checks"] == sorted(CI) and values["checks_up_to_date"] and values["block_deletion"]
    assert not engine.check(engine.read_state(REPO), "long-horizon")["findings"]
    assert gh.rulesets and next(iter(gh.rulesets.values()))["name"] == "Fieldkit main"


def test_apply_skip_leaves_that_setting(gh, tmp_path):
    seq = ["y"] * 7 + ["s"] + ["y"] * 30                  # skip the 8th change; whatever it is stays as it was
    out = []
    engine.apply_profile("long-horizon", REPO, ask=answers(*seq), out=out.append, owner=lambda: True, folder=tmp_path)
    left = engine.make_plan(engine.catalogue()["profiles"]["long-horizon"], engine.read_state(REPO))["changes"]
    assert len(left) == 1 and "skipped by you" in "\n".join(out)


def test_apply_never_turns_on_automerge_without_checks(gh, tmp_path):
    gh.ran = []
    out = []
    engine.apply_profile("long-horizon", REPO, ask=answers(*["y"] * 30), out=out.append, owner=lambda: True, folder=tmp_path)
    assert gh.info["allow_auto_merge"] is False
    assert any("NOT APPLIED" in s for s in out)


def test_apply_reports_github_refusal_and_goes_on(gh, tmp_path, monkeypatch):
    real = gh.__call__

    def refusing(method, path, body=None):
        if method == "PATCH" and body and "security_and_analysis" in body:
            raise GhError("HTTP 422 Secret scanning is not available for this repository")
        return real(method, path, body)
    monkeypatch.setattr(engine, "api", refusing)
    out = []
    rc = engine.apply_profile("long-horizon", REPO, ask=answers(*["y"] * 30), out=out.append, owner=lambda: True,
                              folder=tmp_path)
    assert rc == 3 and any("FAILED: GitHub said: HTTP 422" in s for s in out)
    assert gh.info["allow_auto_merge"] is True


def test_apply_public_needs_clean_privacy_scan(gh, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "privacy_scan", lambda path=".": (False, "found 1 secret"))
    gh.dependabot = True
    out = []
    engine.apply_profile("public-release", REPO, ask=answers(*["y"] * 30), out=out.append, owner=lambda: True, folder=tmp_path)
    assert gh.info["private"] is True and any("privacy scan" in s for s in out)


def test_undo_restores_and_never_deletes(gh, tmp_path):
    engine.apply_profile("long-horizon", REPO, ask=answers(*["y"] * 30), out=lambda s: None, owner=lambda: True, folder=tmp_path)
    snap = engine.latest_snapshot(REPO, tmp_path)
    assert engine.values(engine.read_state(REPO))["auto_merge"]
    out = []
    rc = engine.undo(snap, ask=answers("y"), out=out.append, owner=lambda: True)
    assert rc == 0 and "Restored and verified" in out[-1]
    v = engine.values(engine.read_state(REPO))
    assert not v["auto_merge"] and not v["secret_scanning"] and not v["required_checks"] and not v["ruleset_targets_main"]
    assert not [c for c in gh.calls if c[0] == "DELETE"]
    assert gh.rulesets[100]["enforcement"] == "disabled"


def test_undo_asks_and_refuses_outside_terminal(gh, tmp_path):
    engine.apply_profile("freeze", REPO, ask=answers(*["y"] * 5), out=lambda s: None, owner=lambda: True, folder=tmp_path)
    snap = engine.latest_snapshot(REPO, tmp_path)
    assert engine.undo(snap, ask=answers("n"), out=lambda s: None, owner=lambda: True) == 0
    assert engine.values(engine.read_state(REPO))["freeze"] is True
    assert engine.undo(snap, ask=answers("y"), out=lambda s: None, owner=lambda: False) == 1
    assert engine.values(engine.read_state(REPO))["freeze"] is True


# ------------------------------------------------------------------ check-my-work, doctor, data, launchers

def test_check_my_work_picks_closest_profile(gh):
    gh.add_ruleset(rules=[{"type": "deletion"}, {"type": "non_fast_forward"}])
    res = engine.check(engine.read_state(REPO), "check-my-work")
    assert res["profile"] == "full-speed-ahead"


def test_cli_check_exit_codes(gh, capsys):
    assert github.main(["check", "--repo", REPO]) == 3
    gh.add_ruleset(rules=[{"type": "deletion"}])
    assert github.main(["check", "--repo", REPO]) == 0
    assert "Clean" in capsys.readouterr().out


def test_cli_apply_from_a_script_is_refused(gh, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert github.main(["apply", "long-horizon", "--repo", REPO]) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert [c for c in gh.calls if c[0] != "GET"] == []


def test_doctor_names_the_exact_step(monkeypatch):
    monkeypatch.setattr(engine.shutil, "which", lambda n: None if n == "gh" else "/bin/x")
    steps = engine.doctor()
    assert [s["id"] for s in steps] == ["git", "gh"] and not steps[1]["ok"] and steps[1]["fix"]
    monkeypatch.setattr(engine.shutil, "which", lambda n: "/bin/x")
    monkeypatch.setattr(engine, "_exec", lambda argv, **k: (1, "", "not logged in") if argv[1:2] == ["auth"] else (0, "", ""))
    steps = engine.doctor()
    assert steps[-1]["id"] == "login" and "gh auth login" in steps[-1]["fix"]


def test_glossary_words_shown_once():
    g = engine.Gloss()
    first = g.add("Open a pull request and merge it.")
    assert "[pull request]" in first and "[merge]" in first
    assert "[pull request]" not in g.add("Another pull request.")


def test_data_is_consistent():
    cat = engine.catalogue()
    need = {"where", "url", "what", "why", "impact", "risk", "read", "set", "verify", "undo", "title", "kind"}
    for sid, s in cat["settings"].items():
        assert need <= set(s), sid
    for pid, p in cat["profiles"].items():
        assert {"title", "who", "changes", "threat", "settings"} <= set(p), pid
        assert set(p["settings"]) <= set(cat["settings"]), pid
    assert {"full-speed-ahead", "long-horizon", "ai-assisted-auditing", "check-my-work", "first-repository", "team",
            "public-release", "private-backup", "freeze"} <= set(cat["profiles"])
    # every non-manual setting has a working setter
    gh_ = FakeGitHub()
    import fieldkit.github.engine as e
    e_api, e.api = e.api, gh_
    try:
        for sid, s in cat["settings"].items():
            if s["kind"] != "manual":
                val = [CI[0]] if sid in ("required_checks",) else (1 if sid == "approvals" else True)
                e.set_value(e.read_state(REPO), sid, val)
    finally:
        e.api = e_api


def test_launchers_exist_and_call_the_menu():
    for name in ("github-setup.cmd", "github-setup.sh", "github-setup.command"):
        text = (ROOT / "launchers" / name).read_text(encoding="utf-8")
        assert "-m fieldkit github menu" in text and ("pause" in text or "read" in text), name
    assert (ROOT / "launchers" / "github-setup.sh").read_text().startswith("#!/bin/sh")


def test_card_and_skill_exist():
    import yaml
    cards = yaml.safe_load((ROOT / "fieldkit" / "desk" / "tools.yaml").read_text(encoding="utf-8"))
    cards = cards["tools"] if isinstance(cards, dict) else cards
    assert any(c.get("id") == "github" for c in cards)
    assert "fieldkit github" in (ROOT / "skills" / "fieldkit-github" / "SKILL.md").read_text(encoding="utf-8")


def test_first_repository_stops_on_a_secret_file(tmp_path, monkeypatch):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git missing")
    (tmp_path / ".npmrc").write_text("//registry/:_authToken=x")
    (tmp_path / "a.txt").write_text("hi")
    out = []
    rc = engine.first_repository(ask=answers(*["y"] * 10), out=out.append, cwd=str(tmp_path), name="proj")
    assert rc == 3 and any(".npmrc" in s for s in out) and (tmp_path / ".gitignore").exists()


def test_first_repository_creates_private(tmp_path, monkeypatch):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git missing")
    (tmp_path / "a.txt").write_text("hi")
    calls = []
    real = engine._exec

    def fake(argv, **k):
        if argv[0] == "gh":
            calls.append(argv)
            return 0, "", ""
        return real(argv, **k)
    monkeypatch.setattr(engine, "_exec", fake)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    rc = engine.first_repository(ask=answers(*["y"] * 10), out=lambda s: None, cwd=str(tmp_path), name="proj")
    assert rc == 0 and calls and "--private" in calls[0] and "--push" in calls[0]
