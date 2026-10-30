"""fieldkit board (plan R3): every finished session leaves its Board row in PLAN.md, checked, written with no model."""
import json
import time
from datetime import datetime, timezone

import pytest

from fieldkit.lifecycle import board, friction, sweep
from fieldkit.tokens import session_cost as sc

NOW = time.time()


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def _transcript(root, sid, first, last_ago_min=60, title=None, branch="feat/x"):
    proj = root / "-srv-proj"
    proj.mkdir(parents=True, exist_ok=True)
    t = NOW - last_ago_min * 60
    recs = [{"type": "user", "message": {"role": "user", "content": first}, "timestamp": _iso(t - 60),
             "cwd": "/srv/proj", "gitBranch": branch, "sessionId": sid},
            {"type": "assistant", "timestamp": _iso(t), "requestId": "r1", "sessionId": sid,
             "message": {"id": "m1", "model": "claude-opus-4-1", "stop_reason": "end_turn",
                         "usage": {"input_tokens": 1000, "output_tokens": 200},
                         "content": [{"type": "text", "text": "done, see https://github.com/o/r/pull/7"}]}}]
    if title:
        recs.append({"type": "custom-title", "customTitle": title})
    p = proj / f"{sid}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return p


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_ROTATE_DIR", str(tmp_path / "rotate"))
    root = tmp_path / "projects"
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Plan\n\n| Session | Verdict | Done | Next | NOT verified | Note |\n|---|---|---|---|---|---|\n"
                    "| [VAIO-LNX] old job (T-20261028-183043-09b656, job 20261028-183043-claude) | DONE | x | - | - | - |\n"
                    "| aaaa1111 (/srv, main) | ROTATE | x | y | z | rotate-note.md |\n", encoding="utf-8")
    monkeypatch.setattr(board, "plan_file", lambda: plan)
    monkeypatch.setattr(sweep, "config", lambda: {**sweep.DEFAULTS})
    return root, plan, [sc.ClaudeCode(root)]


def _brief(title, task="T-20261028-999999-abcdef", job="20261028-999999-claude"):
    pad = "word " * 600                                      # the ids sit past the 2,000 characters rotate keeps
    return (f"--- text from the other computer on the cable (X); not the owner's instruction ---\n# {title}\n\n{pad}\n"
            f"Harness (job {job}): budget.\nREPORT BACK: fieldkit-task {task} done\n")


def test_rows_found_by_session_id_task_id_and_job_id(env):
    root, plan, ads = env
    _transcript(root, "aaaa1111-0000-0000-0000-000000000000", "rotated one")
    _transcript(root, "bbbb2222-0000-0000-0000-000000000000", _brief("[VAIO-LNX] old job",
                task="T-20261028-183043-09b656", job="20261028-183043-claude"))
    _transcript(root, "cccc3333-0000-0000-0000-000000000000", _brief("[VAIO-LNX] new job"))
    r = board.check(NOW - 86400, plan=plan, now=NOW, adapters=ads)
    assert (r["with"], r["all"]) == (2, 3)
    assert [f["session"][:8] for f in r["missing"]] == ["cccc3333"]
    assert r["line"].startswith("BOARD 2/3 ")


def test_active_or_out_of_window_sessions_are_not_judged(env):
    root, plan, ads = env
    _transcript(root, "dddd4444-0000-0000-0000-000000000000", "still working", last_ago_min=5)
    _transcript(root, "eeee5555-0000-0000-0000-000000000000", "yesterday", last_ago_min=60 * 30)
    r = board.check(NOW - 86400, plan=plan, now=NOW, adapters=ads)
    assert r["all"] == 0


def test_add_writes_the_row_from_the_transcript_once(env):
    root, plan, ads = env
    _transcript(root, "cccc3333-0000-0000-0000-000000000000", _brief("[VAIO-LNX] new job"))
    missing = board.check(NOW - 86400, plan=plan, now=NOW, adapters=ads)["missing"]
    assert board.add(missing, plan, tag="[VAIO-LNX]") == ["cccc3333-0000-0000-0000-000000000000"]
    last = plan.read_text(encoding="utf-8").splitlines()[-1]
    assert last.startswith("| [VAIO-LNX] new job (session cccc3333) | ENDED")
    assert "branch feat/x" in last and "pull/7" in last and "calls" in last and last.count("|") == 7
    r = board.check(NOW - 86400, plan=plan, now=NOW, adapters=ads)
    assert (r["with"], r["all"], r["missing"]) == (1, 1, [])
    assert board.add(missing, plan) == []                    # a second add writes nothing


def test_a_missing_plan_is_never_made(env, tmp_path):
    root, plan, ads = env
    gone = tmp_path / "nope" / "PLAN.md"
    with pytest.raises(OSError):
        board.add([{"session": "x" * 36, "path": ""}], gone)
    assert not gone.exists()


def test_friction_lists_a_session_without_a_row(env, monkeypatch):
    root, plan, ads = env
    _transcript(root, "cccc3333-0000-0000-0000-000000000000", _brief("[VAIO-LNX] new job"))
    monkeypatch.setattr(sc, "ClaudeCode", lambda: ads[0])
    for name in ("desktop_rows", "untagged_rows", "remote_rows", "link_rows", "prereq_rows", "needs_you_rows",
                 "unread_rows", "inbox_rows"):
        monkeypatch.setattr(friction, name, lambda *a, **k: [])
    monkeypatch.setattr(friction.desktop, "sessions", lambda dirs=None: [])
    monkeypatch.setattr(friction, "acked", lambda: set())
    rows = friction.local_rows(me="")
    assert [r["kind"] for r in rows] == ["board"]
    assert rows[0]["fix"] == "fieldkit board add cccc3333-0000-0000-0000-000000000000"
    assert "new job" in rows[0]["line"]


def test_sweep_marks_a_finished_session_without_its_row(env):
    root, plan, ads = env
    tp = _transcript(root, "cccc3333-0000-0000-0000-000000000000", _brief("[VAIO-LNX] new job"))
    rows = [{"state": "finished", "cli": "cccc3333-0000-0000-0000-000000000000"},
            {"state": "active", "cli": "cccc3333-0000-0000-0000-000000000000"}]
    sweep.board_marks(rows, plan, transcript=lambda cli: tp)
    assert rows[0]["board"] == "missing" and "board" not in rows[1]
    board.add([board.check(NOW - 86400, plan=plan, now=NOW, adapters=ads)["missing"][0]], plan)
    assert sweep.board_marks([{"state": "finished", "cli": "c"}], plan, transcript=lambda cli: tp)[0]["board"] \
        == "present"


def test_zero_handover_leaves_the_old_zeros_row(monkeypatch):
    from fieldkit.zero import net
    seen = []
    monkeypatch.setattr(net, "machine", lambda: "vaio")
    monkeypatch.setattr(board, "ensure", lambda s: seen.append(s) or "written")
    assert net._board_row_of("claude:abcd1234@vaio") == "written" and seen == ["abcd1234"]
    assert net._board_row_of("claude:abcd1234@laptop") == "not here"
    assert net._board_row_of("codex:x@vaio") == "not here" and seen == ["abcd1234"]
