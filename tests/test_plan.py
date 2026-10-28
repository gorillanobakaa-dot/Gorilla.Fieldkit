"""fieldkit plan next (2026-10-28): the Order table's current phase, the items merged pull requests finished, and the
one line zero, the handovers and the room hook carry."""
import json
import subprocess

import pytest

from fieldkit import plan

PLAN_MD = """# plan

| id | what | status |
|---|---|---|
| R1 | brake | PLANNED |
| R2 | rules | WINDOWS DONE (PR #150). Left: Linux |
| O2 | room | PLANNED |
| O4 | waits | PLANNED |
| C6 | hooks | PLANNED |
| L4 | kept | DONE (exists) |

## Order (approved)

| phase | items | why first |
|---|---|---|
| 1. Brakes | R1 + L4 | money |
| 2. Lighter | R2 + O2, O4 | every call |
| 3. Household | C6 | firepower |
| 4. One system | W10 | last |

## Owner decisions

- R9 is not in the order.
"""


def git(root, *a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "fk"
    (r / "docs").mkdir(parents=True)
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.org")
    git(r, "config", "user.name", "t")
    git(r, "commit", "-q", "--allow-empty", "-m", "old")
    git(r, "switch", "-q", "-c", "feat/old")
    git(r, "commit", "-q", "--allow-empty", "-m", "o")
    git(r, "switch", "-q", "main")
    git(r, "merge", "-q", "--no-ff", "feat/old", "-m", "Merge pull request #100 from x/feat/old\n\nstage (O4)")
    (r / "docs" / "HARNESS-PLAN.md").write_text(PLAN_MD, encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "plan")
    return r


def merge(r, n, title, branch):
    git(r, "switch", "-q", "-c", branch)
    git(r, "commit", "-q", "--allow-empty", "-m", "w")
    git(r, "switch", "-q", "main")
    git(r, "merge", "-q", "--no-ff", branch, "-m", f"Merge pull request #{n} from x/{branch}\n\n{title}")


def test_the_order_and_the_items_are_read_from_the_plan():
    assert plan.items(PLAN_MD) == {"R1": "PLANNED", "R2": "WINDOWS DONE (PR #150). Left: Linux", "O2": "PLANNED",
                                   "O4": "PLANNED", "C6": "PLANNED", "L4": "DONE (exists)"}
    assert plan.order(PLAN_MD) == [("1. Brakes", ["R1", "L4"]), ("2. Lighter", ["R2", "O2", "O4"]),
                                   ("3. Household", ["C6"]), ("4. One system", ["W10"])]


def test_merged_items_close_and_the_phase_moves_on(repo):
    r = plan.next_(repo)
    assert r["phase"] == "1. Brakes" and r["open"] == ["R1"] and r["done"] == ["L4"] and r["merged"] == {}    # #100 (O4) predates the plan
    merge(repo, 152, "rotate: stop (R1)", "feat/r1")
    merge(repo, 162, "room: only what changed (O2, C6)", "fix/o2")
    merge(repo, 170, "a fix that names R2 in passing", "fix/x")                      # not in the trailing parenthesis
    r = plan.next_(repo)
    assert r["merged"] == {"R1": "#152", "O2": "#162", "C6": "#162"}
    assert r["phase"] == "2. Lighter" and r["open"] == ["R2", "O4"] and r["done"] == ["O2"]
    assert r["line"].startswith("PLAN: phase 2. Lighter: open R2 O4 (done O2)")
    assert [p["open"] for p in r["phases"]][2:] == [[], ["W10"]]


def test_write_marks_only_planned_cells_and_the_line_is_saved(repo, capsys):
    merge(repo, 152, "rotate: stop (R1)", "feat/r1")
    merge(repo, 150, "rules (R2)", "feat/r2")
    assert plan.main(["next", "--repo", str(repo), "--write"]) == 0
    text = (repo / "docs" / "HARNESS-PLAN.md").read_text(encoding="utf-8")
    assert "| R1 | brake | DONE (PR #152) |" in text
    assert "| R2 | rules | WINDOWS DONE (PR #150). Left: Linux |" in text                # not PLANNED: left as written
    assert "written DONE: R1" in capsys.readouterr().out
    saved = json.loads(plan.state_file().read_text(encoding="utf-8"))
    assert saved["phase"] == "2. Lighter" and plan.line(live=False) == saved["line"]


def test_no_plan_file_is_said_not_hidden(tmp_path, capsys):
    assert plan.main(["next", "--repo", str(tmp_path)]) == 1
    assert "missing" in capsys.readouterr().out and plan.line(tmp_path) == "PLAN: docs/HARNESS-PLAN.md is missing here"
    assert plan.line(live=False) == ""                                                   # nothing saved yet


def test_the_room_hook_shows_the_saved_line_once_on_a_prompt(monkeypatch, tmp_path, capsys):
    import io
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")
    for f in ("_net", "_hog", "_rotate"):
        monkeypatch.setattr(room, f, lambda call, event, ctx: ctx)
    plan.state_file().write_text(json.dumps({"line": "PLAN: phase 3. X: open R2"}), encoding="utf-8")

    def hook(event):
        room.hook(io.StringIO(json.dumps({"session_id": "s", "hook_event_name": event, "tool_name": "Bash" if
                                          event == "PostToolUse" else None, "tool_input": {"command": "ls"}})))
        out = capsys.readouterr().out
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
    assert hook("PostToolUse") == ""                                                     # tool calls: never
    assert hook("UserPromptSubmit") == "PLAN: phase 3. X: open R2"
    assert hook("UserPromptSubmit") == ""                                                # once
    plan.state_file().write_text(json.dumps({"line": "PLAN: phase 3. X: open R2 O4"}), encoding="utf-8")
    assert hook("UserPromptSubmit") == "PLAN: phase 3. X: open R2 O4"                    # the open items changed


def test_zero_status_prints_the_plan_line_first(monkeypatch, capsys):
    from fieldkit.zero import net
    monkeypatch.setattr(plan, "line", lambda *a, **k: "PLAN: phase 3. X: open R2")
    monkeypatch.setattr(net, "status", lambda: {"established": False, "net": {}, "level": 0})
    monkeypatch.setattr(net, "status_lines", lambda r: ["NET: none"])
    net.main(["status"])
    assert capsys.readouterr().out.splitlines()[:3] == ["PLAN: phase 3. X: open R2", "", "NET: none"]
