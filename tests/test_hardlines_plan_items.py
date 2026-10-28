"""Hard line 3 opened for an approved plan item (owner, 2026-10-28, "Yes, both": "headless autopilot may change
hooks/guards for an approved plan item, with tests that prove the check still fails"). Both ways: what it opens, and
everything that stays refused. Fixture plan, transcripts, repositories and proofs only."""
import json
import subprocess
from pathlib import Path

import pytest

from fieldkit import plan
from fieldkit.autopilot import hardlines as hl
from fieldkit.method import mutate

PLAN_MD = """| id | what | status |
|---|---|---|
| O4 | waits | PLANNED |
| R5 | router | DONE (PR #163) |
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "fk" / "docs").mkdir(parents=True)
    (tmp_path / "fk" / "docs" / "HARNESS-PLAN.md").write_text(PLAN_MD, encoding="utf-8")
    monkeypatch.setattr(plan, "ROOT", tmp_path / "fk")
    monkeypatch.setenv("FIELDKIT_MUTATE_PROOFS", str(tmp_path / "proofs"))
    monkeypatch.setattr(hl, "STOP_FILE", tmp_path / "no-stop")
    return tmp_path


def brief(tmp_path, text):
    t = tmp_path / f"t-{abs(hash(text))}.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"content": text}}) + "\n", encoding="utf-8")
    return str(t)


def edit(path, transcript):
    return hl.decide("Edit", {"file_path": path, "old_string": "a", "new_string": "b"}, transcript=transcript)[0]


def test_an_open_plan_item_opens_hook_and_guard_files_and_nothing_else(env):
    o4, done, none = brief(env, "Do O4: one wake."), brief(env, "Do R5 again."), brief(env, "Tidy R9 and C1.")
    assert edit("fieldkit/autopilot/room.py", None) == "deny"
    assert edit("fieldkit/autopilot/room.py", none) == "deny"              # not in the plan
    assert edit("fieldkit/autopilot/room.py", done) == "deny"              # in the plan, already DONE
    assert edit("fieldkit/autopilot/room.py", o4) == "allow"
    assert edit("hooks/pre-commit", o4) == "allow"
    for f in ["fieldkit/autopilot/hardlines.py", "fieldkit/autopilot/claude_hook.py", "fieldkit/autopilot/budget.py",
              "fieldkit/autopilot/breaker.py", "fieldkit/method/mutate.py", ".git/hooks/pre-commit", "AGENTS.md",
              ".claude/settings.json", "state/mutate-proofs/a.json", ".gitignore"]:
        assert edit(f, o4) == "deny", f                                     # enforcing files and other protections
    cmd = lambda c: hl.decide("Bash", {"command": c}, transcript=o4)[0]   # noqa: E731
    assert cmd("echo x >> fieldkit/autopilot/room.py") == "allow"
    assert cmd("echo x >> fieldkit/autopilot/hardlines.py") == "deny"
    assert cmd("cp a fieldkit/autopilot/room.py && echo y >> AGENTS.md") == "deny"
    assert cmd("cp fieldkit/autopilot/room.py leakgate/allow.json") == "deny"   # one statement, a second protection
    assert hl.decide("Bash", {"command": "echo x >> fieldkit/autopilot/room.py"})[0] == "deny"


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


@pytest.fixture
def repo(env):
    r = env / "repo"
    (r / "fieldkit" / "autopilot").mkdir(parents=True)
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@example.org")
    git(r, "config", "user.name", "t")
    (r / "README.md").write_text("x\n", encoding="utf-8")
    (r / "fieldkit" / "autopilot" / "room.py").write_text("A = 1\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    return r


def prove(repo, caught=True):
    f = repo / "fieldkit" / "autopilot" / "room.py"
    plan_ = [{"file": str(f), "swap": ["1", "2"]}, {"file": str(f), "swap": ["A", "B"]}]
    res = [{"verdict": "caught"}, {"verdict": "caught" if caught else "survived"}]     # one survivor voids it
    return mutate.write_proof({"results": res}, plan_, ["pytest"])


def test_a_commit_of_a_guard_file_needs_the_item_and_a_proof_for_that_content(env, repo):
    o4 = brief(env, "Do O4.")
    commit = lambda t: hl.decide("Bash", {"command": "git commit -m x"}, cwd=str(repo), transcript=t)  # noqa: E731
    (repo / "README.md").write_text("y\n", encoding="utf-8")
    git(repo, "add", "README.md")
    assert commit(None)[0] == "allow"                                      # no guard file staged: not this line's
    (repo / "fieldkit" / "autopilot" / "room.py").write_text("A = 2\n", encoding="utf-8")
    git(repo, "add", "-A")
    assert "names no open" in commit(None)[1]                              # `git add -A` no longer slips one in
    assert "no mutate proof" in commit(o4)[1]
    prove(repo, caught=False)
    assert "no mutate proof" in commit(o4)[1]                              # a survivor is no proof
    prove(repo)
    assert commit(o4)[0] == "allow"
    (repo / "fieldkit" / "autopilot" / "room.py").write_text("A = 3\n", encoding="utf-8")
    git(repo, "add", "-A")
    assert "no mutate proof" in commit(o4)[1]                              # the proof was for other content
    assert "no mutate proof" in hl.decide("Bash", {"command": f"git -C {repo} commit -am x"}, cwd=str(env),
                                         transcript=o4)[1]
    prove(repo)
    assert hl.decide("Bash", {"command": f"git -C {repo} commit -am x"}, cwd=str(env), transcript=o4)[0] == "allow"
    git(repo, "reset", "-q")                                                # the change only in the working tree
    (repo / "fieldkit" / "autopilot" / "room.py").write_text("A = 4\n", encoding="utf-8")
    assert "no mutate proof" in hl.decide("Bash", {"command": f"git -C {repo} commit -am x"}, cwd=str(env),
                                         transcript=o4)[1]


def test_an_enforcing_file_in_a_commit_is_refused_whatever_the_brief(env, repo):
    (repo / "fieldkit" / "autopilot" / "hardlines.py").write_text("X = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    r = hl.decide("Bash", {"command": "git commit -m x"}, cwd=str(repo), transcript=brief(env, "Do O4."))
    assert r[0] == "deny" and "fieldkit/autopilot/hardlines.py" in r[1]
