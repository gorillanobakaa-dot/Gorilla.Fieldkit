"""Briefs for deferred steps: measured facts, plain words, and a drop only through the guarded door."""
import json
import os
import subprocess
import time

import pytest

from fieldkit.buildh import compile as cg, decision, deferred, task

COMMENT_HUNK = {"lines": [" // a context line long enough to anchor it", "-// the old wording of a comment that upstream changed",
                          "+// the new wording the owner wanted for the same comment", " pref(\"x.y\", true);"]}
PREF_HUNK = {"lines": [" // a context line long enough to anchor it", "-pref(\"gone.everywhere.setting\", true);",
                       "+pref(\"gone.everywhere.setting\", false, locked);", " pref(\"x.y\", true);"]}
ELSEWHERE_HUNK = {"lines": [" // a context line long enough to anchor it", "-pref(\"moved.somewhere.else\", true);",
                            "+pref(\"moved.somewhere.else\", false, locked);", " pref(\"x.y\", true);"]}


def git(w, *a):
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", *a], check=True, capture_output=True)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    monkeypatch.setattr(decision, "COOLING_OFF_SECONDS", 0)
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    w = tmp_path / "work"
    (w / "browser").mkdir(parents=True)
    (w / "browser" / "other.js").write_text('// reads moved.somewhere.else\nlet v = "moved.somewhere.else";\n')
    (w / "app.js").write_text('// a context line long enough to anchor it\npref("x.y", true);\n')
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "base")
    key = tmp_path / "harness" / "src"
    key.mkdir(parents=True)
    (key / "app.js").write_text('// a context line long enough to anchor it\npref("x.y", true);\n')
    steps = []
    for n, h in (("c", COMMENT_HUNK), ("p", PREF_HUNK), ("e", ELSEWHERE_HUNK)):
        steps.append({"id": f"port-05.PREFS-app.js-app.js-h{n}", "kind": "model", "title": n, "status": "deferred",
                      "last_why": ["upstream removed or replaced this"], "allowed": ["app.js"],
                      "args": {"patch": "05.PREFS/app.js.patch", "file": "app.js", "hunk": h}})
    task.start("d1", "demo", w, steps, budget_tokens=1000, meta={"upstream": {"version": "157.0"}, "harness_root": str(tmp_path / "harness")})
    task.approve("d1", "owner")
    for s in steps:                       # start() resets statuses: park them again
        pass
    t = task.load("d1")
    for s in t["steps"]:
        s["status"] = "deferred"
    task.save(t)
    return w


def step(c):
    return f"port-05.PREFS-app.js-app.js-h{c}"


def test_listing_shows_every_parked_step(world):
    assert {i for i, _ in deferred.listing("d1")} == {step("c"), step("p"), step("e")}


def test_a_comment_only_change_is_low_risk_and_the_brief_says_why(world):
    b = deferred.build("d1", step("c"))
    assert b["comment_only"] and b["recommended"] == "drop" and "only changes a comment" in b["why"]


def test_a_setting_that_is_gone_everywhere_can_be_dropped(world):
    b = deferred.build("d1", step("p"))
    assert b["recommended"] == "drop" and "not defined or used anywhere" in " ".join(b["evidence"])


def test_a_setting_that_still_exists_elsewhere_is_held_for_a_person(world):
    b = deferred.build("d1", step("e"))
    assert b["recommended"] == "hold" and "still appears in 1 other file" in " ".join(b["evidence"])


def test_the_owners_earlier_port_is_evidence_never_a_source(world):
    b = deferred.build("d1", step("c"))
    assert "does NOT have the old text and does not have the new text" in " ".join(b["evidence"])
    assert "(world" not in (world / "app.js").read_text()          # nothing was copied or changed
    assert (world / "app.js").read_text().count("pref") == 1


def test_the_plain_brief_is_jargon_free_and_honest_about_what_it_cannot_know(world):
    text = "\n".join(deferred.render_plain(deferred.build("d1", step("c"))))
    assert "NOTHING HAS BEEN HURT" in text and "Nothing. That is the safe answer" in text
    assert "whether you still WANT this change" in text and "pressing yes" in text
    for jargon in ("hunk", "diff", "git ", "journal", "checkpoint", "fingerprint"):
        assert jargon not in text.lower(), jargon


def test_a_behaviour_change_is_not_pushed_towards_dropping_in_the_plain_text(world):
    text = "\n".join(deferred.render_plain(deferred.build("d1", step("e"))))
    assert "ask someone who knows" in text and "To drop it" not in text


def test_drop_is_refused_without_the_sentence_the_terminal_the_explanation_and_the_pause(world, monkeypatch):
    b = deferred.build("d1", step("c"))
    with pytest.raises(task.Refused, match="type exactly"):
        deferred.apply_drop("d1", step("c"), "yes")
    monkeypatch.setattr(task, "owner_terminal", lambda: False)
    with pytest.raises(task.Refused, match="real terminal"):
        deferred.apply_drop("d1", step("c"), b["confirm"])
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    with pytest.raises(task.Refused, match="not been shown"):
        deferred.apply_drop("d1", step("c"), b["confirm"])
    monkeypatch.setattr(decision, "COOLING_OFF_SECONDS", 60)
    deferred.show(b)
    with pytest.raises(task.Refused, match="wait .* more seconds"):
        deferred.apply_drop("d1", step("c"), b["confirm"])
    assert next(s for s in task.load("d1")["steps"] if s["id"] == step("c"))["status"] == "deferred"


def test_drop_is_refused_while_a_job_is_running(world):
    b = deferred.build("d1", step("c"))
    deferred.show(b)
    (task.STATE / "d1" / "drive.log").write_text("job running\n")
    with pytest.raises(task.Refused, match="job is running"):
        deferred.apply_drop("d1", step("c"), b["confirm"])


def test_the_guarded_drop_is_recorded_with_exactly_what_was_dropped(world):
    b = deferred.build("d1", step("c"))
    deferred.show(b)
    msg = deferred.apply_drop("d1", step("c"), b["confirm"])
    assert "dropped on purpose" in msg
    s = next(s for s in task.load("d1")["steps"] if s["id"] == step("c"))
    assert s["status"] == "done" and s["dropped_by_owner"] and s["drop_fingerprint"] == b["fingerprint"]
    ev = [json.loads(l) for l in (task.STATE / "d1" / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    drop = next(e for e in ev if e["event"] == "owner-drop")
    assert drop["removed"] == ["// the old wording of a comment that upstream changed"] and drop["fingerprint"] == b["fingerprint"]
    assert task.verify_journal("d1")[0] == []                        # the chain is intact


def test_a_drop_is_not_a_skip_for_the_audit_but_the_gate_reports_it(world, tmp_path, monkeypatch):
    b = deferred.build("d1", step("c"))
    deferred.show(b)
    deferred.apply_drop("d1", step("c"), b["confirm"])
    rows = {r["check"]: r for r in cg.gate("d1", harness_root=str(tmp_path / "harness"), write=False)}
    assert "dropped by the owner after an explanation" in rows["every dropped change was a briefed decision"]["evidence"]
    assert not rows["every step is done"]["ok"]                      # the other two are still parked


def test_a_step_that_is_not_deferred_gets_no_options(world):
    t = task.load("d1")
    t["steps"][0]["status"] = "pending"
    task.save(t)
    assert deferred.build("d1", step("c"))["options"] == []


def test_a_failed_search_is_never_read_as_not_used_anywhere(world, monkeypatch):
    """The first draft turned a git error into 'not used anywhere' and recommended dropping."""
    real = subprocess.run

    def broken(cmd, *a, **k):
        if "grep" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: something broke")
        return real(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", broken)
    with pytest.raises(task.Refused, match="nothing is concluded"):
        deferred.build("d1", step("e"))
