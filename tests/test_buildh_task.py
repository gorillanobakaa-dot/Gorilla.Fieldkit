"""task engine: approval first, one packet at a time, the harness checks, failures are put back, 3 strikes block."""
import subprocess
from pathlib import Path

import pytest

from fieldkit.buildh import task


# -- a tiny workflow the tests drive -----------------------------------------------------

def step_prepare(t, **kw):
    Path(t["workdir"], "notes.txt").write_text("prepared\n")
    return {"ok": True, "summary": "wrote notes.txt",
            "add_steps": [{"id": f"fix-{n}", "kind": "model", "title": f"put {n} into {n}.txt",
                           "packet": "tests.test_buildh_task:pkt", "check": "tests.test_buildh_task:chk",
                           "allowed": [f"{n}.txt"], "args": {"word": n}} for n in ("alpha", "beta")]}


def pkt(t, s, budget_chars, word, **kw):
    return f"Write the word {word} into {word}.txt.\n" + "x" * 100_000


def chk(t, s, word):
    f = Path(t["workdir"], f"{word}.txt")
    ok = f.is_file() and word in f.read_text()
    return {"ok": ok, "why": [] if ok else [f"{word}.txt does not contain {word}"]}


def step_crash(t, **kw):
    raise RuntimeError("the script broke")


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "base.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "commit", "-q", "-m", "base"], check=True)
    steps = [{"id": "prepare", "kind": "script", "title": "prepare", "run": "tests.test_buildh_task:step_prepare"}]
    task.start("t1", "demo", w, steps, budget_tokens=1000)
    return w


def test_nothing_runs_before_the_owner_approves(job):
    with pytest.raises(task.Refused, match="not approved"):
        task.packet("t1")


def test_script_steps_run_then_one_model_packet_trimmed_to_budget(job):
    task.approve("t1", "owner")
    p = task.packet("t1")
    assert p["state"] == "MODEL STEP" and p["step"] == "fix-alpha"
    assert "ONLY these files" in p["packet"] and "alpha.txt" in p["packet"]
    assert p["chars"] <= 1000 * task.PACKET_SHARE * task.CHARS_PER_TOKEN + 60     # 100k of filler trimmed
    assert (job / "notes.txt").exists()


def test_the_harness_checks_not_the_model(job):
    task.approve("t1", "owner")
    task.packet("t1")
    (job / "alpha.txt").write_text("I promise it is done\n")                   # premature confidence
    r = task.submit("t1", note="done, all tests pass")
    assert not r["ok"] and "does not contain alpha" in r["why"][0] and r["reverted"]
    assert not (job / "alpha.txt").exists()                                    # put back


def test_changes_outside_the_step_are_refused_and_undone(job):
    task.approve("t1", "owner")
    task.packet("t1")
    (job / "alpha.txt").write_text("alpha\n")
    (job / "base.txt").write_text("rewritten by an eager model\n")             # scope creep
    r = task.submit("t1")
    assert not r["ok"] and "outside the step" in r["why"][0]
    assert (job / "base.txt").read_text() == "base\n"


def test_pass_makes_a_checkpoint_and_moves_on(job):
    task.approve("t1", "owner")
    task.packet("t1")
    (job / "alpha.txt").write_text("alpha\n")
    r = task.submit("t1")
    assert r["ok"] and task.packet("t1")["step"] == "fix-beta"
    log = subprocess.run(["git", "-C", str(job), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "checkpoint: fix-alpha" in log


def test_three_strikes_block_the_step_for_the_owner(job):
    task.approve("t1", "owner")
    for _ in range(3):
        task.packet("t1")
        (job / "alpha.txt").write_text("wrong\n")
        r = task.submit("t1")
    assert "BLOCKED" in r["next"]
    assert task.packet("t1")["state"] == "BLOCKED"
    task.unblock("t1", "fix-alpha", "retry")
    assert task.packet("t1")["step"] == "fix-alpha"


def test_a_fresh_chat_carries_on_from_disk(job):
    task.approve("t1", "owner")
    task.packet("t1")
    (job / "alpha.txt").write_text("alpha\n")
    task.submit("t1")
    s = task.status("t1")                                                      # nothing kept in memory
    assert s["current"]["id"] == "fix-beta" and s["counts"]["done"] == 2


def test_a_crashing_script_step_blocks_instead_of_looping(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    (tmp_path / "w").mkdir()
    task.start("t2", "demo", tmp_path / "w", [{"id": "boom", "kind": "script", "title": "x",
                                                 "run": "tests.test_buildh_task:step_crash"}])
    task.approve("t2", "owner")
    r = task.advance("t2")
    assert r["state"] == "BLOCKED" and "the script broke" in r["why"][0]
    lines = (tmp_path / "state" / "t2" / "journal.jsonl").read_text().splitlines()
    assert sum('"script-failed"' in l for l in lines) == 3


def test_owner_can_rewind_a_step_that_passed_but_is_wrong(job):
    task.approve("t1", "owner")
    task.packet("t1")
    (job / "alpha.txt").write_text("alpha\n")
    assert task.submit("t1")["ok"]
    task.packet("t1")
    (job / "beta.txt").write_text("beta\n")
    assert task.submit("t1")["ok"]
    out = task.rewind("t1", "fix-alpha")
    assert not (job / "alpha.txt").exists() and not (job / "beta.txt").exists()
    s = task.status("t1")
    assert s["current"]["id"] == "fix-alpha" and s["counts"].get("done") == 1 and out["checkpoints kept"] >= 1
    with pytest.raises(task.Refused, match="never passed"):
        task.rewind("t1", "fix-beta")
