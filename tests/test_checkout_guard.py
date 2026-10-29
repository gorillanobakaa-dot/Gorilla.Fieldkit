# Version: 1.0.0 · updated 26-10-29-19-49
"""One writer per primary checkout (owner order 2026-10-29): only Zero changes a primary checkout; everyone else works
in a worktree. Injected net and git state: no process, no cable, no real session."""
import json
import os

import pytest

from fieldkit.autopilot import toolrouter
from fieldkit.zero import checkout_guard as cg
from fieldkit.zero import claims

HOST = "box"
ZERO = f"claude:81bf1215@{HOST}"
ME_SID = "c006918a-0000"


@pytest.fixture
def world(tmp_path, monkeypatch):
    coord = tmp_path / "coord"
    coord.mkdir()
    (coord / "NET.yaml").write_text("net: open\nzero:\n  machine: box\n  station: " + ZERO + "\nupdated: 'x'\n")
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(coord))
    monkeypatch.delenv(cg.ENV, raising=False)
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n")
    logged = []
    monkeypatch.setattr(cg, "log", lambda r, call, now=None: logged.append(r))
    monkeypatch.setattr(claims.socket, "gethostname", lambda: HOST)
    return main, wt, logged


def call(cmd, cwd, sid=ME_SID, tool="Bash"):
    return {"tool_name": tool, "session_id": sid, "cwd": str(cwd), "tool_input": {"command": cmd}}


@pytest.mark.parametrize("cmd", [
    "git checkout -b fix/x", "git checkout main", "git switch other", "git commit -m x", "git merge feat/y",
    "git reset --hard origin/main", "git rebase main", "git stash", "git stash pop", "git cherry-pick abc",
    "git -C {main} commit -m x", "cd {main} && git commit -m x", "echo hi; git checkout -b z",
    "git -c user.name=a commit -m x", "GIT_X=1 git reset HEAD~1",
])
def test_a_session_other_than_zero_is_refused_in_the_primary_checkout(world, cmd):
    main, wt, logged = world
    v = cg.pretool(call(cmd.format(main=main), main if "{main}" not in cmd or cmd.startswith("cd") else wt))
    assert v["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "the main checkout belongs to Zero; work in a worktree: git worktree add ../wt-NAME -b BRANCH origin/main" \
        in v["hookSpecificOutput"]["permissionDecisionReason"]
    assert logged and not logged[0]["allowed"]


@pytest.mark.parametrize("cmd", [
    "git status", "git log --oneline -5", "git diff", "git fetch origin", "git worktree add ../wt-a -b b origin/main",
    "git stash list", "git stash show -p", "git checkout -- file.txt", "git reset -- file.txt", "git branch",
    "git commit-tree abc", 'echo "git commit -m x"', "grep -rn 'git checkout' docs", "git push origin x",
    "cat <<'EOF'\ngit commit -m x\nEOF",
])
def test_read_only_git_and_look_alikes_pass_in_the_primary_checkout(world, cmd):
    main, wt, logged = world
    assert cg.pretool(call(cmd, main)) is None
    assert not logged


def test_a_worktree_is_free_for_every_session(world):
    main, wt, logged = world
    assert cg.pretool(call("git checkout -b feat/a && git commit -m x", wt)) is None
    assert cg.pretool(call(f"git -C {wt} commit -m x", main)) is None
    sub = wt / "sub"
    sub.mkdir()
    assert cg.pretool(call("git commit -m x", sub)) is None and not logged


def test_a_subfolder_of_the_primary_checkout_is_the_primary_checkout(world):
    main, wt, logged = world
    sub = main / "docs"
    sub.mkdir()
    assert cg.pretool(call("git commit -m x", sub))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_zero_may_and_is_logged(world):
    main, wt, logged = world
    assert cg.pretool(call("git checkout -b release/v1", main, sid="81bf1215-aaaa")) is None
    assert len(logged) == 1 and logged[0]["allowed"] and logged[0]["by"] == "zero"


def test_the_override_is_allowed_and_logged_with_its_reason(world, monkeypatch):
    main, wt, logged = world
    monkeypatch.setenv(cg.ENV, "owner asked: hotfix")
    assert cg.pretool(call("git commit -m x", main)) is None
    assert logged[0]["allowed"] and "hotfix" in logged[0]["by"]


def test_no_open_net_or_no_zero_judges_nothing(world):
    main, wt, logged = world
    coord = claims.coord()
    (coord / "NET.yaml").write_text("net: closed\nzero:\n  station: " + ZERO + "\n")
    assert cg.pretool(call("git commit -m x", main)) is None
    (coord / "NET.yaml").write_text("net: open\n2ic: null\n")
    assert cg.pretool(call("git commit -m x", main)) is None
    (coord / "NET.yaml").unlink()
    assert cg.pretool(call("git commit -m x", main)) is None and not logged


def test_a_zero_key_below_another_section_is_not_zero(world):
    main, wt, logged = world
    (claims.coord() / "NET.yaml").write_text("net: open\nother:\n  x: 1\nzero:\nmirror_of: y\n  station: " + ZERO + "\n")
    assert cg.zero_station() is None


def test_other_tools_are_not_judged(world):
    main, wt, logged = world
    assert cg.pretool(call("git commit -m x", main, tool="Read")) is None


def test_the_tool_router_hook_refuses_it_for_real(world):
    main, wt, logged = world
    v = toolrouter.verdict(call("git commit -m x", main))
    assert v and "belongs to Zero" in v["hookSpecificOutput"]["permissionDecisionReason"]
    assert toolrouter.verdict(call("git commit -m x", wt)) is None


def test_the_refusal_is_written_to_the_net_log(tmp_path, monkeypatch):
    """No injection of log(): the real net log gets a `refused` entry."""
    coord = tmp_path / "coord"
    coord.mkdir()
    (coord / "NET.yaml").write_text("net: open\nepoch: 1\nzero:\n  station: " + ZERO + "\n")
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(coord))
    monkeypatch.delenv(cg.ENV, raising=False)
    monkeypatch.setattr(claims.socket, "gethostname", lambda: HOST)
    main = tmp_path / "m"
    (main / ".git").mkdir(parents=True)
    assert cg.pretool(call("git reset --hard", main))
    lines = [json.loads(ln) for ln in (coord / "net-log.jsonl").read_text().splitlines()]
    assert lines and lines[-1]["kind"] == "refused" and lines[-1]["data"]["verb"] == "reset"


def test_windows_style_paths_are_one_checkout(world, monkeypatch):
    main, wt, logged = world
    assert cg.primary_root(str(main) + os.sep) == cg.primary_root(str(main / "." / "x"))
