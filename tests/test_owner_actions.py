# Version: 1.0.0 · updated 26-10-28-18-35
"""Owner actions per job (fieldkit/lifecycle/owner_actions.py, HARNESS-PLAN I9): fixture link jobs, transcripts,
emerge journals and inbox items are written here; each cause is counted once, and text that only quotes a cause is not."""
import json
import time

import pytest

from fieldkit.lifecycle import owner_actions as oa
from fieldkit.lifecycle import tasks

DAY = "2026-10-28"
T0 = time.mktime(time.strptime(f"{DAY} 10:00:00", "%Y-%m-%d %H:%M:%S"))


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


def env(tmp_path):
    return {"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"), "HOME": str(tmp_path / "home")}


def bash(cmd):
    return {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": cmd}}


def asst(*blocks, stop="tool_use", mid=None):
    return {"type": "assistant", "message": {"id": mid or f"m{id(blocks)}", "stop_reason": stop, "content": list(blocks)},
            "timestamp": "2026-10-28T10:05:00Z"}


def result(text):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "x", "content": text}]}}


def transcript(tmp_path, name, job, *recs, task="T-20261028-100000-abcdef"):
    d = tmp_path / "cfg" / "projects" / "-proj"
    d.mkdir(parents=True, exist_ok=True)
    first = {"type": "user", "message": {"content": f"brief\nREPORT BACK: fieldkit-task {task} done\n"
                                                    f"Harness (job {job}): your budget"}}
    p = d / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in (first,) + recs) + "\n", encoding="utf-8")
    return p


def link_job(tmp_path, job, checkpoint=None):
    d = tmp_path / "link" / job
    d.mkdir(parents=True)
    (d / "budget.json").write_text(json.dumps({"started": T0}), encoding="utf-8")
    if checkpoint:
        (d / "CHECKPOINT.json").write_text(json.dumps(checkpoint), encoding="utf-8")
    return d


def run(tmp_path, items=()):
    return oa.count(DAY, link_dir=tmp_path / "link", state=tmp_path / "state", env=env(tmp_path),
                    inbox_items=list(items))


def test_every_cause_is_counted(tmp_path):
    link_job(tmp_path, "J1", {"job": "J1", "status": "done", "session_id": "s1"})
    (tmp_path / "link" / "J1" / "session.log").write_text("Do you trust the files in this folder?\n", encoding="utf-8")
    transcript(tmp_path, "s1", "J1",
               asst(bash("fieldkit inbox add 'publish X' 'no credential here'")),
               asst(bash("/x/.venv/bin/python -P -m fieldkit peers link send \"[NEEDS YOU] the key\"")),
               asst(bash("fieldkit peers link send 'gone' --needs-you")),
               asst({"type": "tool_use", "id": "q", "name": "AskUserQuestion", "input": {}}),
               result("The user doesn't want to proceed with this tool use. The tool use was rejected"),
               result("Claude requested permissions to use Bash, but you haven't granted it yet."),
               asst({"type": "text", "text": "Done.\nShall I push it?"}, stop="end_turn"))
    [j] = run(tmp_path)
    causes = sorted(a["cause"] for a in j["actions"])
    assert causes == ["ask", "inbox", "needs-you", "needs-you", "permission", "permission", "question", "trust"]
    assert j["count"] == 8 and j["task"] == "T-20261028-100000-abcdef"
    assert [a["detail"] for a in j["actions"] if a["cause"] == "inbox"] == ["publish X"]
    out = "\n".join(oa.lines([j]))
    assert out.startswith("WARN") and "VERDICT  8 owner action(s) in 1 of 1 job(s)" in out


def test_quoted_causes_are_not_actions(tmp_path):
    """2026-10-28: a done note that MENTIONED [NEEDS YOU] and --needs-you, `inbox add --help`, and grep output quoting
    the rejection text were each counted before the commands were parsed as the shell does."""
    link_job(tmp_path, "J2")                                        # running: found by its "Harness (job J2)" prompt
    transcript(tmp_path, "s2", "J2",
               asst(bash("fieldkit peers link send \"fieldkit-task T-1 done [NEEDS YOU] counts only owner notes, "
                         "--needs-you --to SESSION refused\" --done --evidence ok")),
               asst(bash("fieldkit inbox add --help")),
               asst(bash("grep -c 'fieldkit inbox add' notes.txt")),
               result("3 hits: The user doesn't want to proceed with this tool use"),
               result("grep: Claude requested permissions to use Bash, but you haven't granted it yet"),
               asst({"type": "text", "text": "Is it? Yes. Committed on feat/x."}, stop="end_turn"))
    [j] = run(tmp_path)
    assert j["count"] == 0 and j["status"] == "running" and j["transcripts"]
    assert oa.lines([j])[0].startswith("OK")


def test_question_needs_the_turn_to_end_on_it(tmp_path):
    link_job(tmp_path, "J3", {"job": "J3", "session_id": "s3"})
    transcript(tmp_path, "s3", "J3",
               asst({"type": "text", "text": "Which one? Checking."}, mid="a"), asst(bash("ls"), mid="a"),
               asst({"type": "text", "text": "Want me to merge it?"}, mid="b"), asst(stop="end_turn", mid="b"))
    [j] = run(tmp_path)
    assert [(a["cause"], a["detail"]) for a in j["actions"]] == [("question", "Want me to merge it?")]


def test_emerge_run_counts_its_build_items_in_its_window(tmp_path):
    d = tmp_path / "state" / "emerge" / "gorilla-size"
    d.mkdir(parents=True)
    at = lambda s: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 + s))   # noqa: E731 - runner._now's format
    ev = [{"t": at(0), "event": "build-start", "recipe": "gorilla-size"},
          {"t": at(600), "event": "build-done", "recipe": "gorilla-size"}]
    (d / "journal.jsonl").write_text("\n".join(map(json.dumps, ev)) + "\n", encoding="utf-8")
    items = [{"n": 1, "kind": "build", "title": "stage pkg failed", "command": "fieldkit emerge run gorilla-size",
              "added": at(60)},
             {"n": 2, "kind": "build", "title": "later", "command": "fieldkit emerge run gorilla-size", "added": at(900)},
             {"n": 3, "kind": "build", "title": "other recipe", "command": "fieldkit emerge run demo", "added": at(60)}]
    [j] = run(tmp_path, items)
    assert j["kind"] == "emerge" and j["status"] == "done"
    assert [a["detail"] for a in j["actions"]] == ["#1 stage pkg failed"]


def test_since_drops_older_jobs(tmp_path):
    d = link_job(tmp_path, "OLD")
    (d / "budget.json").write_text(json.dumps({"started": T0 - 86400}), encoding="utf-8")
    assert run(tmp_path) == []


def test_task_status_and_board_carry_the_count(tmp_path):
    jobs = [{"id": "J1", "task": "T-1", "count": 2, "actions": [{"cause": "inbox", "detail": "a"},
                                                                 {"cause": "question", "detail": "b?"}]},
            {"id": "J2", "task": "T-2", "count": 0, "actions": []}]
    led = {"T-1": {"id": "T-1", "status": "done", "on": "here", "title": "one"},
           "T-2": {"id": "T-2", "status": "done", "on": "here", "title": "two"},
           "T-3": {"id": "T-3", "status": "done", "on": "win", "title": "elsewhere"}}
    tasks.attach_owner_actions(led, jobs)
    out = tasks.lines(led)
    assert "owner actions 2 WARN" in out[0] and "owner actions 0" in out[1] and "WARN" not in out[1]
    assert "owner actions" not in out[2]
    assert led["T-1"]["owner_causes"] == ["inbox: a", "question: b?"]
    (tmp_path / "coord").mkdir()
    (tmp_path / "coord" / "PLAN.md").write_text("| Board |\n", encoding="utf-8")
    tasks._append({"id": "T-1", "status": "done", "on": "here", "title": "one", "note": "n"})
    assert tasks.board("T-1", jobs)
    assert "n; owner actions 2 WARN |" in (tmp_path / "coord" / "PLAN.md").read_text(encoding="utf-8")


def test_cli_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(oa, "count", lambda since: [])
    assert oa.main([]) == 0 and "VERDICT  0 owner actions in 0 job(s)" in capsys.readouterr().out
    monkeypatch.setattr(oa, "count", lambda since: [{"id": "J", "kind": "link", "start": T0, "status": "done",
                                                     "task": "", "transcripts": ["x"], "count": 1,
                                                     "actions": [{"cause": "inbox", "detail": "a"}]}])
    assert oa.main(["--since", DAY]) == 3
    from fieldkit import lifecycle
    monkeypatch.setattr(oa, "count", lambda since: [])
    assert lifecycle.cmd_friction(["jobs"]) == 0
