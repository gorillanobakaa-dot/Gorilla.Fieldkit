"""fieldkit ciaudit: what CI really ran (2026-11-02: 17 of 38 PR runs ran the whole suite unnoticed)."""
import json

from fieldkit.method import ciaudit

LOGS = {
    1: "test\tstep\t2026Z ci-tests: no test covers releases/parity-pin.yaml: the whole suite\n",
    2: "test\tstep\t2026Z ci-tests: 3 test file(s) cover the change\n",
    3: "ci-tests: no test covers a.yaml, releases/parity-pin.yaml: the whole suite\n",
    4: "ci-tests: no code changed - no tests\n",
    5: "",
}


def fake_gh(args):
    if args[:2] == ["run", "list"]:
        runs = [{"databaseId": i, "event": "pull_request", "headBranch": f"b{i}", "conclusion": "success",
                 "startedAt": "2026-11-01T10:00:00Z", "attempt": 2,
                 "createdAt": "2026-11-01T01:00:00Z", "updatedAt": f"2026-11-01T10:{i * 5:02d}:00Z"} for i in LOGS]
        runs.append({"databaseId": 9, "event": "push", "createdAt": "2026-11-01T10:00:00Z",
                     "updatedAt": "2026-11-01T11:00:00Z"})
        return 0, json.dumps(runs), ""
    return 0, LOGS[int(args[2])], ""


def test_each_run_is_classified_with_its_minutes_and_trigger_files():
    rows = ciaudit.audit("o/r", gh=fake_gh)
    assert [r["kind"] for r in rows] == ["whole", "targeted", "whole", "none", "unknown"]   # the push run is skipped
    assert rows[0]["minutes"] == 5.0 and rows[2]["files"] == ["a.yaml", "releases/parity-pin.yaml"]
    assert rows[0]["lifetime_minutes"] == 545.0 and rows[0]["attempt"] == 2
    s = ciaudit.summary(rows)
    assert s["whole_runs"] == 2 and s["whole_minutes"] == 20.0 and s["whole_share"] == 0.5
    assert s["triggers"][0] == ("releases/parity-pin.yaml", 2)


def test_over_the_budget_exits_3_and_names_the_trigger(monkeypatch, capsys):
    monkeypatch.setattr(ciaudit, "_gh", lambda args, run=None: fake_gh(args))
    assert ciaudit.main(["--repo", "o/r"]) == 3
    out = capsys.readouterr().out
    assert "trigger 2x releases/parity-pin.yaml" in out and "whole-suite share 50%" in out
    assert ciaudit.main(["--repo", "o/r", "--max-whole-share", "0.6"]) == 0


def test_attempt_timeline_counts_queue_and_rerun_gap_once(monkeypatch, capsys):
    attempt_one = {"created_at": "2026-11-01T01:00:00Z", "run_started_at": "2026-11-01T01:01:00Z",
                   "updated_at": "2026-11-01T01:49:00Z", "status": "completed", "conclusion": "failure"}
    attempt_two = {"created_at": "2026-11-01T14:49:00Z", "run_started_at": "2026-11-01T14:51:00Z",
                   "updated_at": "2026-11-01T15:03:00Z", "status": "completed", "conclusion": "success"}
    prefix = "repos/o/r/actions/runs/123"
    records = {prefix: {"run_attempt": 2}, prefix + "/attempts/1": attempt_one,
               prefix + "/attempts/2": attempt_two}
    monkeypatch.setattr(ciaudit, "_gh", lambda args: (0, json.dumps(records[args[1]]), ""))
    assert ciaudit.main(["--repo", "o/r", "--run", "123", "--json"]) == 0
    first, second = json.loads(capsys.readouterr().out)["attempts"]
    assert (first["queue_s"], first["active_s"], first["gap_before_s"]) == (60, 2880, None)
    assert (second["queue_s"], second["active_s"], second["gap_before_s"]) == (120, 720, 46800)
    attempt_two["created_at"] = "2026-11-01T14:51:03Z"
    assert ciaudit.timeline("o/r", 123)["attempts"][1]["queue_s"] is None
