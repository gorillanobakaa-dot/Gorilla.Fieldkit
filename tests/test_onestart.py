"""One ask at the start (plan P9): every owner-only action a job may need is approved before it begins; mid-job a
missing one takes its fallback and never reaches the owner as a command."""
import re
import subprocess
from pathlib import Path

import pytest

from fieldkit.autopilot import inbox
from fieldkit.lifecycle import onestart

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "life"))
    monkeypatch.delenv("FIELDKIT_ONESTART", raising=False)
    monkeypatch.delenv("FIELDKIT_TASK_ID", raising=False)
    monkeypatch.delenv("FIELDKIT_JOB_DIR", raising=False)
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "inbox")
    return tmp_path


def reg(*acts):
    return {"version": 1, "ttl_hours": 12, "actions": list(acts)}


def act(i, apply=None, check="x", kinds=("any",), commands=("run-" + "me",)):
    return {"id": i, "what": f"what {i}", "kinds": list(kinds), "check": check, "apply": apply,
            "manual": f"type {i}", "commands": list(commands), "fallback": f"fallback {i}"}


OK = {"ok": True, "key": "K", "sig": "s" * 40, "why": "proven"}
NO = {"ok": False, "why": "no passphrase"}


def checks(**kw):
    return {k: (lambda v=v: v) for k, v in kw.items()}


def test_the_shipped_registry_is_complete_and_its_checks_exist():
    d = onestart.load()
    assert len(d["actions"]) >= 6
    ids = [a["id"] for a in d["actions"]]
    assert len(ids) == len(set(ids)) and "claude-cli-login" in ids and "github-ruleset-local-ci" in ids
    for a in d["actions"]:
        assert a["check"] in onestart.CHECKS, a["id"]
        assert a.get("apply") or a.get("manual"), a["id"]
        assert a["commands"], a["id"]


def test_nothing_missing_is_silent_and_asks_nobody(env):
    said = []
    r = onestart.start_check("task", data=reg(act("a")), checks=checks(x=True), say=said.append, ask_present=True,
                             prove=lambda t: pytest.fail("asked"))
    assert r["outcome"] == "none" and said == [] and not (env / "life" / "onestart.jsonl").exists()


def test_one_screen_one_yes_applies_every_action_and_journals_each(env):
    ran = []

    def run(argv, **kw):
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, "fine\n", "")
    said = []
    d = reg(act("a", apply=["echo", "one"]), act("b", apply=["echo", "two"]), act("c"))
    asked = []
    r = onestart.start_check("task", job="J1", data=d, checks=checks(x=False), say=said.append, ask_present=True,
                             prove=lambda t: asked.append(t) or OK, run=run)
    assert len(asked) == 1 and "a, b, c" in asked[0]                       # ONE ask for all of them
    assert [x[0] for x in ran] == ["echo", "echo"] and r["applied"] == ["a", "b"] and r["outcome"] == "approved"
    screen = "\n".join(said)
    assert "this task job" in screen and "1. a" in screen and "3. c" in screen and "type c" in screen
    rows = (env / "life" / "onestart.jsonl").read_text(encoding="utf-8")
    assert rows.count('"event": "applied"') == 2 and '"outcome": "approved"' in rows and '"key": "K"' in rows


def test_a_no_never_stops_the_job_and_is_not_asked_again_at_once(env):
    d = reg(act("a", apply=["echo"]))
    r = onestart.start_check("task", data=d, checks=checks(x=False), say=lambda _x: None, ask_present=True,
                             prove=lambda t: NO)
    assert r["outcome"] == "declined" and r["applied"] == []
    r2 = onestart.start_check("task", data=d, checks=checks(x=False), say=lambda _x: None, ask_present=True,
                              prove=lambda t: pytest.fail("asked again within the ttl"))
    assert r2["outcome"] == "asked-recently"


def test_unattended_start_does_not_ask_and_does_not_wait(env):
    r = onestart.start_check("emerge", data=reg(act("a")), checks=checks(x=False), say=lambda _x: None,
                             ask_present=False, prove=lambda t: pytest.fail("asked"))
    assert r["outcome"] == "unattended" and r["missing"] == ["a"]
    assert '"event": "unattended"' in (env / "life" / "onestart.jsonl").read_text(encoding="utf-8")


def test_a_failing_apply_is_recorded_and_the_job_still_starts(env):
    def boom(argv, **kw):
        raise OSError("no such program")
    r = onestart.start_check("task", data=reg(act("a", apply=["nope"])), checks=checks(x=False), ask_present=True,
                             prove=lambda t: OK, run=boom, say=lambda _x: None)
    assert r["outcome"] == "approved" and r["applied"] == [] and any("FAILED" in x for x in r["lines"])


def test_a_job_kind_only_sees_its_own_actions():
    d = reg(act("t", kinds=("task",)), act("e", kinds=("emerge",)), act("any"))
    assert [a["id"] for a in onestart.actions("emerge", d)] == ["e", "any"]


def test_a_midjob_gap_makes_the_next_start_ask_for_it(env):
    d = reg(act("u", check="unknown"))
    c = {"unknown": lambda: None}
    r0 = onestart.start_check("task", data=d, checks=c, ask_present=True, prove=lambda t: pytest.fail("asked"),
                              say=lambda _x: None)
    assert r0["missing"] == []                                         # unknown and no gap on record: not asked
    onestart.gap("u", job="J", why="a step found it missing")
    r1 = onestart.start_check("task", data=d, checks=c, ask_present=True, prove=lambda t: OK, say=lambda _x: None)
    assert r1["missing"] == ["u"] and r1["outcome"] == "approved"


def test_inbox_add_of_a_registry_action_is_refused_inside_a_job(env, monkeypatch):
    monkeypatch.setenv("FIELDKIT_TASK_ID", "T-1")
    it = onestart.guard_inbox("Allow local CI", "ruleset needs a change", "bash state/owner/ruleset-allow-local-ci.sh")
    assert it["routed"] == "refused" and "never mid-job" in it["why"] and it["action"] == "github-ruleset-local-ci"
    assert "github-ruleset-local-ci" in onestart.open_gaps()           # the next start asks for it up front


def test_inbox_add_outside_a_job_and_for_other_commands_is_unchanged(env, monkeypatch):
    assert onestart.guard_inbox("Allow local CI", "why", "bash state/owner/ruleset-allow-local-ci.sh") is None
    monkeypatch.setenv("FIELDKIT_TASK_ID", "T-1")
    assert onestart.guard_inbox("A real decision", "which pack ships", "fieldkit decide X") is None


def test_a_non_code_example_job_a_cv_pack_starts_through_the_same_check(env):
    """Not a build: a CV job whose one prerequisite is a login. Present: silent. Missing: one ask, then it goes on."""
    d = reg(act("career-board-login", check="login", kinds=("task",), commands=("career login",)))
    assert onestart.start_check("task", data=d, checks=checks(login=True), ask_present=True,
                                prove=lambda t: pytest.fail("asked"), say=lambda _x: None)["outcome"] == "none"
    r = onestart.start_check("task", data=d, checks=checks(login=False), ask_present=True, prove=lambda t: OK,
                             say=lambda _x: None)
    assert r["outcome"] == "approved" and r["missing"] == ["career-board-login"]


def test_the_switch_off_skips(monkeypatch):
    monkeypatch.setenv("FIELDKIT_ONESTART", "off")
    assert onestart.start_check("task", data=reg(act("a")), checks=checks(x=False))["outcome"] == "off"


# ---- lint (CI): the start check is the only place an owner command is asked ----------------------------------------
ENTRY_POINTS = {"fieldkit/cli.py": 'onestart.at_start("goal"', "fieldkit/emerge/__init__.py": 'onestart.at_start("emerge"',
                "fieldkit/lifecycle/__init__.py": 'onestart.at_start("task"'}

# every `inbox.add(` call outside inbox.py, with why it is not a mid-job owner command (the CLI guards a registry action inside a job; a NEW file here must be justified)
INBOX_CALLERS = {
    "fieldkit/cli.py": "the `fieldkit inbox add` command; refused mid-job for a registry action by onestart.guard_inbox",
    "fieldkit/zero/net.py": "kind=net: the agent ledger, never the owner",
    "fieldkit/zero/command.py": "kind=net: the agent ledger, never the owner",
    "fieldkit/autopilot/runbook.py": "a runbook step's own `run:`; lint-only: no registry command in it",
    "fieldkit/autopilot/inbox_selftest.py": "the self-test of the inbox routing",
    "fieldkit/buildh/task.py": "task.owner_wait: a decision the build-harness cannot make; lint-only",
    "fieldkit/lifecycle/routing.py": "kind=approval: the agent ledger",
    "fieldkit/method/ship.py": "ship's hard-line notes; lint-only",
}


def _py():
    return [p for p in (ROOT / "fieldkit").rglob("*.py")]


def test_every_entry_point_of_a_long_job_runs_the_start_check():
    for rel, needle in ENTRY_POINTS.items():
        assert needle in (ROOT / rel).read_text(encoding="utf-8"), f"{rel} does not run the start check"


def test_no_new_code_path_files_an_owner_command_outside_the_registry():
    found = {}
    for p in _py():
        rel = p.relative_to(ROOT).as_posix()
        if rel in ("fieldkit/autopilot/inbox.py", "fieldkit/lifecycle/onestart.py"):
            continue
        if re.search(r"\binbox\.add\(", p.read_text(encoding="utf-8")):
            found[rel] = True
    unknown = sorted(set(found) - set(INBOX_CALLERS))
    assert not unknown, (f"{unknown} file an owner inbox item: an owner-only action is asked once at the START "
                         "(fieldkit/lifecycle/onestart.yaml); add the action there, or justify the call in INBOX_CALLERS")


def test_the_inbox_add_command_runs_the_mid_job_guard():
    src = (ROOT / "fieldkit/cli.py").read_text(encoding="utf-8")
    assert "onestart.guard_inbox(" in src


def test_no_inbox_add_call_site_carries_a_registry_command():
    cmds = [c for a in onestart.load()["actions"] for c in a.get("commands", [])]
    bad = []
    for p in _py():
        rel = p.relative_to(ROOT).as_posix()
        if rel in ("fieldkit/autopilot/inbox.py", "fieldkit/lifecycle/onestart.py"):
            continue
        t = p.read_text(encoding="utf-8")
        for m in re.finditer(r"\binbox\.add\(", t):
            seg = t[m.start():m.start() + 400]
            bad += [(rel, c) for c in cmds if c and c in seg]
    assert not bad, f"an inbox item carries a registry command outside the start check: {bad}"
