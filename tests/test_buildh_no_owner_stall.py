"""P9 (2026-11-11): no owner-only stall. An owner skip needs no note (the mandate is the reason), a blocked check step is retried by the harness when
the tree changed, a job with only an owner decision left names it in its record, and the owner's signed mandate can
pre-approve the owner-only steps the harness takes when no independent reviewer can be had."""
import json
import os
import subprocess
import time

import pytest

from fieldkit.autopilot import inbox
from fieldkit.buildh import audit, autoport, task
from fieldkit.lifecycle import autoaccept, ownerproof
from fieldkit.mandate import mandate as M
from tests.test_buildh_task import job2  # noqa: F401 - the fixture

ROW = "journal: no step skipped without a reason"


def _commit(w, name, ahead=30):
    (w / name).write_text(name)
    env = dict(os.environ, GIT_COMMITTER_DATE=f"@{int(time.time()) + ahead} +0000")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m",
                    name], check=True, env=env)


def _add_steps(tid, steps):
    t = task.load(tid)
    for s in steps:
        s.setdefault("attempts", 0), s.setdefault("max_attempts", 3)
    t["steps"] += steps
    task.save(t)
    return t


def _journal(tid):
    p = task.STATE / tid / "journal.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _row(ev):
    return {n: (ok, e) for n, ok, e in audit.journal_checks(ev)}[ROW]


# --- a. the skip's reason -------------------------------------------------------------------------------------------

def test_the_owner_skip_needs_no_note_and_the_journal_names_the_owner_and_the_mandate(job2, monkeypatch):
    asked = []
    monkeypatch.setattr(ownerproof, "owner_or_proof", lambda *a, **k: asked.append(a) or (True, "terminal"))
    monkeypatch.setattr(task, "_mandate_in_force", lambda: "owner-wide")
    _add_steps("t2", [{"id": "port-x", "kind": "model", "title": "p", "status": "pending", "allowed": []},
                      {"id": "port-y", "kind": "model", "title": "p", "status": "pending", "allowed": []}])
    task.unblock("t2", "port-x", "skip")
    assert asked, "the owner proof is still the one approval"
    s = next(s for s in task.load("t2")["steps"] if s["id"] == "port-x")
    assert s["status"] == "done" and s["skipped_by_owner"] and s["skip_reason"].startswith("the owner's decision")
    task.unblock("t2", "port-y", "skip", note="upstream already carries it")
    ev = [e for e in _journal("t2") if e.get("event") == "unblock"]
    assert [(e.get("by"), e.get("mandate"), e.get("note")) for e in ev] == [
        ("owner", "owner-wide", None), ("owner", "owner-wide", "upstream already carries it")]
    ok, evidence = _row(_journal("t2"))
    assert ok and evidence.startswith("2 skipped by the owner (mandate owner-wide)")


def test_a_model_without_the_owner_proof_still_cannot_skip(job2, monkeypatch):
    monkeypatch.setattr(ownerproof, "owner_or_proof", lambda *a, **k: (False, "an AI agent's process"))
    _add_steps("t2", [{"id": "port-x", "kind": "model", "title": "p", "status": "pending", "allowed": []}])
    with pytest.raises(task.Refused, match="owner"):
        task.unblock("t2", "port-x", "skip")
    assert not [e for e in _journal("t2") if e.get("event") == "unblock"]


def test_a_skip_by_the_harness_counts_with_the_reviewers_reason_and_without_one_it_fails():
    ok = [{"event": "auto-accept", "step": "p", "kind": "skip", "reviewer": "opus", "why": ["checks passed; yes"]}]
    assert _row(ok)[0] and "1 by the harness" in _row(ok)[1]
    assert not _row([dict(ok[0], reviewer=None)])[0], "no reviewer: no reason"
    assert not _row([dict(ok[0], why=[])])[0]
    adapted = [dict(ok[0], kind="adapted")]
    assert _row(adapted)[0] and _row(adapted)[1] == "none skipped"


def test_a_later_retry_undoes_a_bare_skip():
    ev = [{"event": "unblock", "step": "p", "how": "skip"}, {"event": "unblock", "step": "p", "how": "retry"}]
    assert _row(ev)[0]


# --- c. a check step is re-run by the harness ------------------------------------------------------------------------

def test_a_blocked_check_step_is_rerun_by_the_harness_after_a_commit_and_only_so_often(job2):
    t = _add_steps("t2", [{"id": "final-checks", "kind": "script", "title": "f", "status": "blocked",
                           "run": "tests.test_buildh_autoport:step_fail"}])
    task.journal(t, "script-failed", step="final-checks")
    pol = {"kinds": ["retry"], "max_retries": 1}
    assert autoport.retry_scripts("t2", pol) == [], "no commit since the failure: nothing changed, nothing to retry"
    _commit(job2, "fix.txt")
    assert autoport.retry_scripts("t2", {"kinds": [], "max_retries": 5}) == [], "retry left out of kinds: unchanged"
    assert autoport.retry_scripts("t2", pol) == ["final-checks"]
    s = next(x for x in task.load("t2")["steps"] if x["id"] == "final-checks")
    assert s["status"] == "pending" and s["auto_retries"] == 1
    assert any(e["event"] == "auto-retry" for e in _journal("t2"))
    t = task.load("t2")
    next(x for x in t["steps"] if x["id"] == "final-checks").update(status="blocked")
    task.save(t)
    assert autoport.retry_scripts("t2", pol) == [], "max_retries used up"


def test_a_model_step_is_never_rerun_by_this_path(job2):
    t = _add_steps("t2", [{"id": "port-z", "kind": "model", "title": "z", "status": "blocked", "allowed": []}])
    task.journal(t, "script-failed", step="port-z")
    _commit(job2, "fix2.txt")
    assert autoport.retry_scripts("t2", {"kinds": ["retry"], "max_retries": 3}) == []


# --- d. a job with only an owner decision left says so ------------------------------------------------------------

def test_a_job_with_only_an_owner_decision_left_names_it_in_its_record(job2, monkeypatch):
    filed = []
    monkeypatch.setattr(inbox, "add", lambda *a, **k: filed.append((a, k)))
    task.approve("t2", "owner")
    t = task.load("t2")
    for x in t["steps"]:
        x["status"] = "done"
    task.save(t)
    _add_steps("t2", [{"id": "port-y", "kind": "model", "title": "y", "status": "blocked", "allowed": [],
                       "last_why": ["needs the owner"]}])
    r = task.advance("t2")
    assert r["state"] == "BLOCKED" and r["waiting_owner"]["steps"] == ["port-y"]
    assert task.status("t2")["waiting_owner"]["state"] == "BLOCKED"
    task.advance("t2")
    assert len(filed) == 1, "filed once, not on every look"
    assert filed[0][1]["kind"] == "decision"
    t = task.load("t2")
    next(x for x in t["steps"] if x["id"] == "port-y")["status"] = "done"
    task.save(t)
    assert task.advance("t2")["state"] == "DONE"
    assert task.status("t2")["waiting_owner"] is None, "cleared once nothing waits"


# --- e. the mandate's owner_steps door -------------------------------------------------------------------------------

DOOR = {"unblock": ["skip", "retry"], "accept": True, "when": ["evidence"]}


def _m(door=DOOR):
    m = {"project": "owner-wide", "goal": "g", "expires": "2099-01-01", "repos": {}, "never": []}
    return dict(m, owner_steps=door) if door is not None else m


def _allows(m, monkeypatch, tmp_path, **p):
    f = tmp_path / "m.yaml"
    f.write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(M, "verify", lambda path, today=None: (True, "in force"))
    return M.allows(f, "owner_step", **p)[0]


def test_the_mandate_door_opens_only_what_it_lists_and_only_with_evidence(monkeypatch, tmp_path):
    good = dict(evidence="checks passed; reason", checks_passed=True)
    assert _allows(_m(), monkeypatch, tmp_path, verb="unblock", how="skip", **good) == "allow"
    assert _allows(_m(), monkeypatch, tmp_path, verb="accept", **good) == "allow"
    assert _allows(_m(None), monkeypatch, tmp_path, verb="unblock", how="skip", **good) == "ask", "no door: as before"
    assert _allows(_m(dict(DOOR, unblock=["retry"])), monkeypatch, tmp_path, verb="unblock", how="skip", **good) == "ask"
    assert _allows(_m(dict(DOOR, accept="yes")), monkeypatch, tmp_path, verb="accept", **good) == "ask", "only true"
    assert _allows(_m(), monkeypatch, tmp_path, verb="unblock", how="skip", evidence="", checks_passed=True) == "ask"
    assert _allows(_m(), monkeypatch, tmp_path, verb="unblock", how="skip", evidence="x", checks_passed=False) == "ask"
    assert _allows(_m(dict(DOOR, when=["vibes"])), monkeypatch, tmp_path, verb="accept", **good) == "ask"
    assert _allows(_m(), monkeypatch, tmp_path, verb="drop", **good) == "ask"
    assert any("HARNESS ITSELF" in d for d in M.doors(_m())), "the approve screen shows the door"
    assert not any("HARNESS ITSELF" in d for d in M.doors(_m(None)))


def test_an_owner_wide_mandate_without_the_key_has_the_door_on_and_can_still_close_it(monkeypatch, tmp_path):
    # owner, 2026-11-11: "One single approval. That was our mandate." The signed owner-wide copy names no owner_steps
    good = dict(evidence="checks passed; reason", checks_passed=True)
    wide = dict(_m(None), scope="owner-wide")
    assert _allows(wide, monkeypatch, tmp_path, verb="unblock", how="skip", **good) == "allow"
    assert _allows(wide, monkeypatch, tmp_path, verb="accept", **good) == "allow"
    assert _allows(wide, monkeypatch, tmp_path, verb="accept", evidence="", checks_passed=True) == "ask", \
        "the door's own condition (evidence) stays"
    assert _allows(dict(wide, owner_steps=False), monkeypatch, tmp_path, verb="accept", **good) == "ask"
    assert _allows(dict(wide, owner_steps={}), monkeypatch, tmp_path, verb="accept", **good) == "ask"
    assert any("HARNESS ITSELF" in d for d in M.doors(wide))
    assert M.owner_steps(M.load(M.Path(M.__file__).with_name("owner-wide.yaml"))) == M.owner_steps(wide), \
        "the default is exactly the packaged template's door"


def test_an_unsigned_mandate_grants_no_owner_step(tmp_path):
    f = tmp_path / "m.yaml"
    f.write_text(json.dumps(_m()), encoding="utf-8")
    assert M.allows(f, "owner_step", verb="accept", evidence="x", checks_passed=True)[0] == "ask"


def _decide(monkeypatch, verdict, kind="adapted", project="essay-hist101", checks=None, ask=None):
    """A NON-CODE item (I-04: an essay paragraph rewritten by a model), no reviewer to be had."""
    calls = []
    monkeypatch.setattr(autoaccept, "_mandate_decide",
                        lambda **kw: calls.append(kw) or (verdict, "owner_steps.accept", "/x/owner-wide.yaml"))
    pol = dict(autoaccept.policy(), enabled=True, kinds=["adapted", "skip", "retry"])
    rec = autoaccept.decide(kind, "para-3", "sonnet", checks if checks is not None else
                            [("word count within 10% of the brief", True, "512 of 500"),
                             ("every citation is in the reference list", True, "4 of 4")],
                            "Is the rewritten paragraph faithful to the sources?", evidence="the paragraph",
                            project=project, pol=pol, pick=lambda a, p: {"reviewer": None, "why": "no rung above"},
                            asker=ask)
    return rec, calls


def test_no_reviewer_and_a_mandate_granting_it_the_harness_accepts_under_the_grant(monkeypatch):
    rec, calls = _decide(monkeypatch, "allow")
    assert rec["accepted"] and not rec.get("wait_owner")
    assert rec["reviewer"] == "mandate owner-wide" and "pre-approved by mandate owner-wide" in rec["why"][0]
    assert calls[0]["verb"] == "accept" and calls[0]["checks_passed"] is True and "512 of 500" in calls[0]["evidence"]
    assert any(r["id"] == rec["id"] for r in autoaccept.unseen()), "listed for the owner's review"


def test_without_the_grant_it_waits_as_before(monkeypatch):
    rec, _ = _decide(monkeypatch, "ask")
    assert rec.get("wait_owner") and not rec["accepted"]


def test_a_failed_check_never_reaches_the_mandate(monkeypatch):
    rec, calls = _decide(monkeypatch, "allow", checks=[("every citation is in the reference list", False, "3 of 4")])
    assert not rec["accepted"] and not calls


def test_a_reviewers_no_is_never_overruled_by_the_mandate(monkeypatch):
    calls = []
    monkeypatch.setattr(autoaccept, "_mandate_decide", lambda **kw: calls.append(kw) or ("allow", "x", "/x/m.yaml"))
    pol = dict(autoaccept.policy(), enabled=True, kinds=["skip"])
    rec = autoaccept.decide("skip", "port-a", "sonnet", [("c", True, "")], "q", pol=pol,
                            pick=lambda a, p: {"reviewer": "opus", "why": "above"},
                            asker=lambda m, p: {"yes": False, "why": "the hunk is still needed"})
    assert not rec["accepted"] and not calls


def test_the_policy_switch_turns_the_door_off(monkeypatch):
    monkeypatch.setattr(autoaccept, "_mandate_decide", lambda **kw: ("allow", "x", "/x/m.yaml"))
    pol = dict(autoaccept.policy(), enabled=True, kinds=["skip"], mandate_door=False)
    rec = autoaccept.decide("skip", "port-a", "sonnet", [("c", True, "")], "q", pol=pol,
                            pick=lambda a, p: {"reviewer": None, "why": "none"})
    assert rec.get("wait_owner") and not rec["accepted"]
