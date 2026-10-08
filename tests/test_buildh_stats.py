"""stats: a task's journal counted, the same way every time (2026-10-03: the 157 release story's numbers came from a
throwaway script with the task and dates typed in)."""
import json

import pytest

from fieldkit import cli
from fieldkit.buildh import stats, task

EVENTS = [
    {"t": "2026-10-01 10:00:00", "event": "start"},
    {"t": "2026-10-01 11:00:00", "event": "hand-edit", "steps": ["a", "b"], "files": ["x.js", "y.js"], "kind": "port"},
    {"t": "2026-10-01 23:00:00", "event": "build-start"},
    {"t": "2026-10-01 23:05:00", "event": "build-stop", "signature": "clobber-required", "attempt": 1, "stage": "build"},
    {"t": "2026-10-01 23:06:00", "event": "build-fix", "signature": "clobber-required", "what": "clobber exit 0"},
    {"t": "2026-10-02 01:00:00", "event": "thermal", "peak": 71.0},
    {"t": "2026-10-02 08:57:00", "event": "build-stage-done", "stage": "build", "attempt": 2},
    {"t": "2026-10-02 09:00:00", "event": "build-verified", "ok": True},
    {"t": "2026-10-02 09:16:00", "event": "install", "ok": True},
    {"t": "2026-10-02 09:21:00", "event": "post_install", "ok": False, "results": [["verify_installed_build", 0], ["webrtc", 1]]},
    {"t": "2026-10-02 10:00:00", "event": "build-start"},
    {"t": "2026-10-02 10:30:00", "event": "build-stop", "signature": "thermal", "attempt": 1, "stage": "build"},
    {"t": "2026-10-02 10:40:00", "event": "interrupted", "stage": "build"},
    {"t": "2026-10-02 11:00:00", "event": "hand-edit", "steps": ["c"], "files": ["x.js"]},
    {"t": "2026-10-02 12:00:00", "event": "script-start", "step": "final-checks"},
    {"t": "2026-10-02 12:01:00", "event": "script-failed", "step": "final-checks"},
    {"t": "2026-10-02 12:02:00", "event": "submit", "ok": True},
    {"t": "2026-10-02 12:03:00", "event": "submit", "ok": False},
    {"t": "2026-10-03 09:00:00", "event": "leakgate", "final": "FAIL", "release": True},
    {"t": "2026-10-03 10:00:00", "event": "thermal", "peak": "84.0"},
]


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path)
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "journal.jsonl").write_text("\n".join(json.dumps(e) for e in EVENTS) + "\n", encoding="utf-8")
    return tmp_path


def test_builds_attempts_stops_and_the_rest_are_counted(journal):
    s = stats.run("t1")
    b = s["builds"]
    assert b["runs"] == 2 and b["compiled"] == 1 and b["verified"] == 1 and b["interrupted"] == 1
    assert [r["attempts"] for r in b["list"]] == [2, 1]          # one stop + compiled; one stop, never compiled
    assert b["attempts"] == 3 and b["stops_by_signature"] == {"clobber-required": 1, "thermal": 1}
    assert s["hand"] == {"edits": 2, "steps": 3, "files": 2, "kinds": {"port": 1, "(none)": 1}, "owner_steps": 0,
                         "needs_person": 0}
    assert s["installs"]["rows"] == 2 and s["installs"]["row_failures"] == 1 and s["installs"]["ok"] == 1
    assert s["thermal"]["peak"] == 84.0 and s["thermal"]["top5"] == [84.0, 71.0]
    assert s["scripts"] == {"final-checks": {"runs": 1, "failed": 1}}
    assert s["automation"]["submits"] == 2 and s["automation"]["submits_ok"] == 1
    assert s["other"]["leakgate"] == {"release FAIL": 1}


def test_a_window_of_dates_counts_only_inside_it(journal):
    s = stats.run("t1", since="2026-10-02", until="2026-10-02")           # a bare date: the whole day
    assert s["events"] == 13 and s["first"] == "2026-10-02 01:00:00" and s["last"] == "2026-10-02 12:03:00"
    assert s["builds"]["runs"] == 1                                          # the run that started on the 1st is cut
    assert stats.run("t1", until="2026-10-01 23:00")["builds"]["runs"] == 1


def test_the_command_prints_the_counts_and_refuses_an_unknown_task(journal, capsys):
    assert cli.main(["build-harness", "stats", "t1", "--since", "2026-10-02"]) == 0
    out = capsys.readouterr().out
    assert "builds: 1 run(s), 1 compile attempt(s)" in out and "final-checks 1 (1 failed)" in out
    assert cli.main(["build-harness", "stats", "nope"]) == 2
