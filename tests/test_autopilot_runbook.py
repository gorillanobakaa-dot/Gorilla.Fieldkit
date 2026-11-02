# Version: 1.0.0 · updated 26-10-18-21-20
"""fieldkit runbook: the harness runs the steps; the report comes from exit codes and log lines, not from a model."""
import json
import sys

import pytest

from fieldkit.autopilot import hardlines, inbox, runbook


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(hardlines, "STATE", tmp_path / "ap")
    monkeypatch.setattr(hardlines, "STOP_FILE", tmp_path / "ap" / "STOP")
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "ap" / "inbox")
    from fieldkit.core import eta
    monkeypatch.setattr(eta, "HISTORY", tmp_path / "history.jsonl")
    (tmp_path / "ap").mkdir()


PY = sys.executable.replace("\\", "/")


def book(tmp_path, steps):
    p = tmp_path / "rb.md"
    p.write_text("# test runbook\n\n```runbook\n" + json.dumps({"steps": steps}) + "\n```\n", encoding="utf-8")
    return p


def ok_cmd(text="ok"):
    return f'& "{PY}" -c "print(\'{text}\')"' if sys.platform == "win32" else f'"{PY}" -c "print(\'{text}\')"'


def fail_cmd():
    return f'& "{PY}" -c "import sys; sys.exit(3)"' if sys.platform == "win32" else f'"{PY}" -c "import sys; sys.exit(3)"'


def test_steps_run_in_order_and_a_failure_stops_the_run(tmp_path):
    p = book(tmp_path, [{"id": "a", "run": ok_cmd("first")}, {"id": "b", "run": fail_cmd()},
                        {"id": "c", "run": ok_cmd(), "needs": ["b"]}])
    r = runbook.run(p, say=lambda m: None, hold=False)
    assert r["done"] == ["a"] and r["failed"] == ["b"] and not r["ok"]
    rep = p.with_suffix(".report.md").read_text(encoding="utf-8")
    assert "| a | done | 0 |" in rep and "| b | failed | 3 |" in rep and "no model wrote it" in rep


def test_a_finished_step_is_not_run_again(tmp_path):
    marker = tmp_path / "count.txt"
    cmd = (f'& "{PY}" -c "open(r\'{marker}\', \'a\').write(\'x\')"' if sys.platform == "win32"
           else f'"{PY}" -c "open(r\'{marker}\', \'a\').write(\'x\')"')
    p = book(tmp_path, [{"id": "a", "run": cmd}])
    runbook.run(p, say=lambda m: None, hold=False)
    runbook.run(p, say=lambda m: None, hold=False)
    assert marker.read_text() == "x"


def test_done_if_is_the_steps_own_evidence(tmp_path):
    p = book(tmp_path, [{"id": "a", "run": ok_cmd(), "done_if": {"file": str(tmp_path / "never.txt")}}])
    r = runbook.run(p, say=lambda m: None, hold=False)
    assert r["failed"] == ["a"]                       # exit 0 is not enough when the evidence is missing


def test_the_owners_stop_halts_between_steps(tmp_path):
    hardlines.STOP_FILE.write_text("owner: no more tests")
    p = book(tmp_path, [{"id": "a", "run": ok_cmd()}])
    r = runbook.run(p, say=lambda m: None, hold=False)
    assert r["stopped"] and r["done"] == []


def test_an_owner_step_goes_to_the_inbox_and_never_runs(tmp_path):
    p = book(tmp_path, [{"id": "publish", "owner": True, "title": "publish 158 build 2", "run": "fieldkit publish ..."},
                        {"id": "after", "run": ok_cmd()}])
    r = runbook.run(p, say=lambda m: None, hold=False)
    assert r["blocked"] == ["publish"] and r["done"] == ["after"]
    led = inbox.ledger_file()
    rows = [json.loads(l) for l in led.read_text(encoding="utf-8").splitlines()] if led.is_file() else []
    assert not inbox.items() and [r["title"] for r in rows] == ["publish 158 build 2"]   # never the owner
    runbook.run(p, say=lambda m: None, hold=False)
    assert not inbox.items()


def test_a_failed_step_with_fix_calls_the_model_once_then_tries_again(tmp_path):
    calls = []
    p = book(tmp_path, [{"id": "a", "run": fail_cmd(), "fix": True}])
    runbook.run(p, say=lambda m: None, hold=False, claude=lambda prompt, cwd=None: calls.append(prompt))
    assert len(calls) == 1 and "do not weaken a check" in calls[0]


def test_a_bad_runbook_is_refused(tmp_path):
    with pytest.raises(ValueError):
        runbook.load(book(tmp_path, [{"id": "a", "run": "x", "needs": ["z"]}]))


def test_build_harness_runbook_reads_action_and_file_and_refuses_without_a_file(tmp_path, capsys):
    # 2026-10-18 (tool-harness review): "build-harness runbook status FILE" ran the word "status" as the file
    from fieldkit import cli
    rb = tmp_path / "x.md"
    rb.write_text("```runbook\nsteps:\n  - id: a\n    run: echo hi\n```\n", encoding="utf-8")
    assert cli.main(["build-harness", "runbook", "status", str(rb)]) == 0
    assert "RUNBOOK x.md" in capsys.readouterr().out
    assert cli.main(["build-harness", "runbook", "status"]) != 0
