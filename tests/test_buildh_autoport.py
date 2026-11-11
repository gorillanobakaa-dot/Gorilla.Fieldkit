"""I-04 for build-harness: proposals, skip and retry requests and renamed-upstream deferred steps are decided by the
harness (checks + an independent reviewer), not by an owner click. No model is started: the reviewer is a one-line
Python command (FIELDKIT_REVIEWER_CMD) and the ladder pick is fixed."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fieldkit.buildh import autoport, firefox, task
from fieldkit.lifecycle import autoaccept as core
from tests.test_buildh_task import job2  # noqa: F401 - the fixture

OLD = "this specific line goes away in the port"
NEW = "this specific line arrives with the port"


def chk_hunk(t, s, word, **kw):
    f = Path(t["workdir"], f"{word}.txt")
    text = f.read_text() if f.is_file() else ""
    ok = (word.lower() in text.lower()) if s.get("hand_port") else (word in text)
    return {"ok": ok, "why": [] if ok else [f"{word}.txt does not contain {word}"]}


def step_prepare_hunk(t, **kw):
    Path(t["workdir"], "gamma.txt").write_text("unrelated\n")
    return {"ok": True, "summary": "x",
            "add_steps": [{"id": "fix-gamma", "kind": "model", "title": "port gamma",
                           "packet": "tests.test_buildh_task:pkt", "check": "tests.test_buildh_autoport:chk_hunk",
                           "allowed": ["gamma.txt"],
                           "args": {"word": "gamma", "file": "gamma.txt",
                                    "hunk": {"header": "@@ -1 +1 @@", "lines": [f"-{OLD}", f"+{NEW}"]}}},
                          {"id": "final-checks", "kind": "script", "title": "final checks",
                           "run": "tests.test_buildh_autoport:step_fail"}]}


def step_fail(t, **kw):
    return {"ok": False, "summary": "the final check found a problem", "why": ["a problem"]}


def verdict(word):
    return json.dumps([sys.executable, "-c", f"import sys; sys.stdin.read(); print('reason\\nVERDICT: {word}')"])


@pytest.fixture
def on(tmp_path, monkeypatch):
    pol = tmp_path / "on.yaml"
    pol.write_text("auto_accept:\n  enabled: true\n  kinds: [adapted, skip, retry]\n  deferred_to_model: true\n"
                   "  deferred_patterns: [renamed, no longer exist]\n  max_retries: 2\n  ledger: aa.jsonl\n")
    monkeypatch.setenv("FIELDKIT_AUTOACCEPT_POLICY", str(pol))
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("FIELDKIT_REVIEWER_CMD", verdict("YES"))
    monkeypatch.setenv("FIELDKIT_ATTEMPT_MODEL", "claude-sonnet-5-5")     # the author; the reviewer is opus
    monkeypatch.setattr(core, "pick_reviewer", lambda author, pol=None, **kw: {"reviewer": "opus", "why": "test"})
    return monkeypatch


def _step(tid, sid):
    return next(x for x in task.load(tid)["steps"] if x["id"] == sid)


def _proposal(job2):
    task.approve("t2", "owner")
    task.packet("t2")
    (job2 / "alpha.txt").write_text("ALPHA\n")
    assert task.submit("t2", adapted="upstream renamed it\nlesson: follow renames")["proposed"]


def test_an_adapted_port_is_accepted_by_the_harness_when_checks_and_reviewer_agree(job2, on):
    _proposal(job2)
    task.advance("t2")
    s = _step("t2", "fix-alpha")
    assert s["status"] == "done" and s["accepted_by"].startswith("harness") and "opus" in s["accepted_by"]
    assert (job2 / "alpha.txt").read_text() == "ALPHA\n"
    rows = core.unseen()
    assert len(rows) == 1 and rows[0]["accepted"] and "rewind t2 fix-alpha" in rows[0]["rewind"]
    j = Path(task.STATE, "t2", "journal.jsonl").read_text()
    assert '"auto-accept"' in j


def test_an_adapted_port_the_reviewer_refuses_is_a_failed_attempt_not_accepted(job2, on):
    _proposal(job2)
    on.setenv("FIELDKIT_REVIEWER_CMD", verdict("NO"))
    task.advance("t2")
    s = _step("t2", "fix-alpha")
    assert s["status"] != "done" and s["attempts"] == 1 and s.get("review_refused")
    assert not (job2 / "alpha.txt").exists() and core.unseen() == []


def test_a_policy_turned_off_leaves_the_proposal_for_the_owner(job2, on, tmp_path):
    _proposal(job2)
    off = tmp_path / "off2.yaml"
    off.write_text("auto_accept:\n  enabled: false\n")
    on.setenv("FIELDKIT_AUTOACCEPT_POLICY", str(off))
    task.advance("t2")
    assert _step("t2", "fix-alpha")["status"] == "proposed" and core.unseen() == []


@pytest.fixture
def job3(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "base.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "commit", "-q", "-m", "base"], check=True)
    task.start("t3", "demo", w, [{"id": "prepare", "kind": "script", "title": "prepare",
                                  "run": "tests.test_buildh_autoport:step_prepare_hunk"}], budget_tokens=1000)
    task.approve("t3", "owner")
    task.packet("t3")
    return w


def test_a_skip_request_with_nothing_left_to_port_is_accepted(job3, on):
    with pytest.raises(task.Refused):
        autoport.request("t3", "fix-gamma", "skip", "")          # a reason is required
    autoport.request("t3", "fix-gamma", "skip", "the line is gone upstream\nlesson: check the file before porting")
    task.advance("t3")
    s = _step("t3", "fix-gamma")
    assert s["status"] == "done" and s.get("skipped_by_harness")


def test_a_skip_request_is_refused_while_the_removed_line_is_still_there(job3, on):
    (job3 / "gamma.txt").write_text(OLD + "\n")
    task.checkpoint(task.load("t3"), "the old line is there")
    autoport.request("t3", "fix-gamma", "skip", "nothing to do\nlesson: none")
    task.advance("t3")
    s = _step("t3", "fix-gamma")
    assert s["status"] != "done" and any("still there" in w for w in s["last_why"])


def test_a_retry_needs_a_blocked_step_a_fix_committed_and_the_reviewer(job3, on):
    t = task.load("t3")
    s = next(x for x in t["steps"] if x["id"] == "final-checks")
    with pytest.raises(task.Refused):
        autoport.request("t3", "final-checks", "retry", "fixed")    # not blocked yet
    s["status"], s["last_why"] = "blocked", ["a problem"]
    task.save(t)
    on.setattr(autoport, "_last_fail_time", lambda t, sid: time.time() - 100)
    autoport.request("t3", "final-checks", "retry", "fixed the problem in gamma.txt")
    rec = autoport._retry(task.load("t3"), _step("t3", "final-checks"), core.policy())
    assert rec["accepted"], rec["why"]
    on.setattr(autoport, "_last_fail_time", lambda t, sid: time.time() + 100)     # nothing committed since
    rec = autoport._retry(task.load("t3"), _step("t3", "final-checks"), core.policy())
    assert not rec["accepted"] and any("tree changed" in w for w in rec["why"])


def test_a_renamed_upstream_deferred_step_is_offered_to_a_model(job3, on):
    t = task.load("t3")
    s = next(x for x in t["steps"] if x["id"] == "fix-gamma")
    s["status"], s["last_why"] = "deferred", ["upstream renamed gammaOld to gammaNew"]
    task.save(t)
    task.advance("t3")
    s = _step("t3", "fix-gamma")
    assert s["status"] == "pending" and "gammaNew" in s["offer"]
    p = task.packet("t3")
    text = json.dumps(p)
    assert "THE HARNESS PARKED THIS STEP EARLIER AND FOUND" in text and "renames: OLD -> NEW" in text


def test_a_deferred_step_for_another_reason_stays_parked(job3, on):
    t = task.load("t3")
    s = next(x for x in t["steps"] if x["id"] == "fix-gamma")
    s["status"], s["last_why"] = "deferred", ["the owner said to wait"]
    task.save(t)
    task.advance("t3")
    assert _step("t3", "fix-gamma")["status"] == "deferred"


def test_the_rename_check_holds_a_port_to_its_declared_rename():
    r = firefox.hand_renames("renames: oldName -> newName\nlesson: x")
    assert r == [("oldName", "newName")]
    assert firefox.rename_problems(r, "oldName()", "newName()") == []
    bad = firefox.rename_problems(r, "", "oldName()")
    assert any("adds the old name" in w for w in bad) and any("new name" in w for w in bad)
    h = firefox.renamed_hunk({"lines": ["-call oldName", "+call oldNameX"]}, r)
    assert h["lines"] == ["-call newName", "+call oldNameX"], "whole names only"


def test_the_offer_drops_the_old_a_person_s_job_verdict(job3, on):
    # 2026-11-11: all 18 firefox-160-linux offers told the model "never by a model" in the packet that handed it the step
    from fieldkit.lifecycle import autoaccept
    on.setenv("FIELDKIT_AUTOACCEPT_POLICY", str(autoaccept.DATA))            # the shipped policy, not the test's
    t = task.load("t3")
    s = next(x for x in t["steps"] if x["id"] == "fix-gamma")
    s["status"], s["last_why"] = "deferred", [
        "upstream renamed identifiers inside this hunk (`gammaOld` is now `gammaNew`); its added lines must be "
        "rewritten with the new names by a person, never by a model",
        "pref 'a.b' no longer exists (upstream may have renamed it: a.c). Not a job for a model; the owner decides"]
    task.save(t)
    task.advance("t3")
    text = json.dumps(task.packet("t3"))
    assert "gammaNew" in text and "a.c" in text
    assert "never by a model" not in text and "Not a job for a model" not in text


# --- checker vs reviewer: the tie-break (firefox-160-linux policies-descriptions.ftl h3, 2026-11-11) -----------------
# Upstream dropped a sentence the project's hunk rewrites. The check refuses a port without the hunk's added text; the
# reviewer refuses a port with it. Sonnet tried both and the step failed. A tie-break settles it, for that step only.

UP_OLD = "msg-agent = Prevent the default browser agent. Only applicable to Windows."
UP_NEW = "msg-agent2 = Prevent the default browser agent."
HUNK_ADD = "msg-agent = Prevent the default Gorilla browser agent. Only applicable to Gorilla Windows."
WITH = "msg-agent2 = Prevent the default Gorilla browser agent. Only applicable to Gorilla Windows.\n"
WITHOUT = "msg-agent2 = Prevent the default Gorilla browser agent.\n"


def chk_agent(t, s, **kw):
    text = Path(t["workdir"], "msgs.ftl").read_text()
    why = [w for w, ok in (("the hunk's 'Gorilla' is missing", "Gorilla" in text),
                           ("the added text's 'applicable' is nowhere near the change", "applicable" in text)) if not ok]
    return {"ok": not why, "why": why}


def pkt_agent(t, s, **kw):
    return "port the hunk into msgs.ftl"


@pytest.fixture
def job4(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "msgs.ftl").write_text(UP_NEW + "\n")                          # the new upstream release, pristine
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "commit", "-q", "-m", "upstream"], check=True)
    sha = subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    task.start("t4", "demo", w, [{"id": "port-h3", "kind": "model", "title": "port h3",
                                  "packet": "tests.test_buildh_autoport:pkt_agent",
                                  "check": "tests.test_buildh_autoport:chk_agent", "allowed": ["msgs.ftl"],
                                  "args": {"file": "msgs.ftl", "hunk": {"header": "@@ -1 +1 @@",
                                                                        "lines": [f"-{UP_OLD}", f"+{HUNK_ADD}"]}}}],
               budget_tokens=1000, meta={"upstream": {"commit": sha}})
    task.approve("t4", "owner")
    monkeypatch.setattr(core, "pick_tiebreaker", lambda author, pol=None, **kw: {"reviewer": "opus", "why": "test"})
    return w


def answer(text):
    return json.dumps([sys.executable, "-c", f"import sys; sys.stdin.read(); print({text!r})"])


def _deadlock(w, on):
    """Sonnet ports WITH the sentence (checks pass, the reviewer refuses), then WITHOUT it (the check refuses)."""
    task.packet("t4")
    (w / "msgs.ftl").write_text(WITH)
    assert task.submit("t4", adapted="renames: msg-agent -> msg-agent2\nlesson: follow the 2")["proposed"]
    on.setenv("FIELDKIT_REVIEWER_CMD", verdict("NO"))
    task.advance("t4")                                       # the reviewer refuses; no check refusal yet: no tie-break
    assert _step("t4", "port-h3")["status"] == "failed" and core.disagreement("t4", "port-h3") is None
    task.packet("t4")
    (w / "msgs.ftl").write_text(WITHOUT)
    r = task.submit("t4", adapted="renames: msg-agent -> msg-agent2\nlesson: upstream dropped the sentence")
    assert not r["ok"] and any("applicable" in x for x in r["why"])
    d = core.disagreement("t4", "port-h3")
    assert d and d["check"]["why"] == ["the added text's 'applicable' is nowhere near the change"]


def test_tiebreak_reviewer_right_overrides_the_check_for_this_step_only(job4, on):
    _deadlock(job4, on)
    on.setenv("FIELDKIT_REVIEWER_CMD", answer(
        "Upstream dropped the sentence when it renamed the message.\nEVIDENCE: + msg-agent2 = Prevent the default "
        "browser agent.\nHOLDS: REVIEWER"))
    task.advance("t4")
    s = _step("t4", "port-h3")
    assert s["status"] == "done" and s["done_by"] == "harness-override"
    assert (job4 / "msgs.ftl").read_text() == WITHOUT, "the work the check refused is in the tree"
    row = core.unseen()[-1]
    assert row["override"] == ["the added text's 'applicable' is nowhere near the change"]
    assert row["check_said"] and row["reviewer_said"] and "rewind t4 port-h3" in row["rewind"]
    assert any("OVERRIDE" in l for l in core.lines([row]))
    j = Path(task.STATE, "t4", "journal.jsonl").read_text()
    assert '"override"' in j and '"tie-break"' in j
    # the check itself is unchanged: the same reason on any other item is still a refusal
    left, waived = core.overridden("t4", "port-h4", row["override"])
    assert left == row["override"] and waived == []
    assert chk_agent(task.load("t4"), s)["ok"] is False


def test_tiebreak_checker_right_refuses_the_override_and_accepts_the_checked_work(job4, on):
    _deadlock(job4, on)
    on.setenv("FIELDKIT_REVIEWER_CMD", answer(
        f"The hunk itself carries the sentence; the project wants it.\nEVIDENCE: +{HUNK_ADD}\nHOLDS: CHECK"))
    task.advance("t4")
    s = _step("t4", "port-h3")
    assert s["status"] == "done" and s["done_by"] == "hand" and "tie-break" in s["accepted_by"]
    assert (job4 / "msgs.ftl").read_text() == WITH
    row = core.unseen()[-1]
    assert row["holds"] == "check" and row["override"] == []
    assert core.overridden("t4", "port-h3", ["the added text's 'applicable' is nowhere near the change"])[1] == []


def test_tiebreak_unsure_or_unquoted_parks_with_both_reasons(job4, on):
    _deadlock(job4, on)
    on.setenv("FIELDKIT_REVIEWER_CMD", answer("Surely the reviewer.\nEVIDENCE: a line nobody showed me at all\n"
                                              "HOLDS: REVIEWER"))
    task.advance("t4")
    s = _step("t4", "port-h3")
    assert s["status"] == "blocked"
    text = " ".join(s["last_why"])
    assert "not in the evidence" in text and "applicable" in text and "check:" in text and "reviewer:" in text
    assert (job4 / "msgs.ftl").read_text() == UP_NEW + "\n" and core.unseen() == []


def test_the_upstream_change_shows_the_dropped_text(job4):
    s = _step("t4", "port-h3")
    text = autoport.upstream_change(task.load("t4"), s)
    assert f"- {UP_OLD}" in text and f"+ {UP_NEW}" in text


def test_tiebreak_override_waives_only_the_reasons_ruled_on(job4, on):
    """The refused attempt, checked again, fails for a reason the tie-break never saw: nothing is waived, it parks."""
    _deadlock(job4, on)
    d = Path(task.STATE, "t4", "proposals", "port-h3.refused.diff")
    d.write_text(d.read_text().replace("default Gorilla browser", "default browser"))
    on.setenv("FIELDKIT_REVIEWER_CMD", answer(
        "Upstream dropped it.\nEVIDENCE: + msg-agent2 = Prevent the default browser agent.\nHOLDS: REVIEWER"))
    task.advance("t4")
    s = _step("t4", "port-h3")
    assert s["status"] == "blocked" and "Gorilla" in " ".join(s["last_why"])
    assert (job4 / "msgs.ftl").read_text() == UP_NEW + "\n" and core.unseen() == []
