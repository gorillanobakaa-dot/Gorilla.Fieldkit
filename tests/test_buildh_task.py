"""task engine: approval first, one packet at a time, the harness checks, failures are put back, 3 strikes block."""
import json
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
    assert task.packet("t1")["step"] == "fix-beta"            # a blocked step is a person's; the plan goes on
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


# -- skip is the owner's, at a real terminal (overnight run 2026-10-01 skipped ~70 steps, incl. failing checks) --

def test_skip_is_refused_from_an_agents_shell(job, monkeypatch):
    task.approve("t1", "owner")
    task.packet("t1")          # runs the prepare step, which adds the model steps
    monkeypatch.setattr(task, "owner_terminal", lambda: False)
    with pytest.raises(task.Refused, match="real terminal"):
        task.unblock("t1", "fix-alpha", "skip")
    assert next(s for s in task.load("t1")["steps"] if s["id"] == "fix-alpha")["status"] != "done"


def test_a_check_step_can_never_be_skipped_even_by_the_owner(job, monkeypatch):
    task.approve("t1", "owner")
    task.packet("t1")          # runs the prepare step, which adds the model steps
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    with pytest.raises(task.Refused, match="cannot be skipped"):
        task.unblock("t1", "prepare", "skip")


def test_the_owner_at_a_terminal_can_skip_a_model_step(job, monkeypatch):
    task.approve("t1", "owner")
    task.packet("t1")          # runs the prepare step, which adds the model steps
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    task.unblock("t1", "fix-alpha", "skip")
    s = next(s for s in task.load("t1")["steps"] if s["id"] == "fix-alpha")
    assert s["status"] == "done" and s["skipped_by_owner"]


def test_a_blocked_step_does_not_halt_the_rest_and_the_run_ends_with_the_list(job):
    task.approve("t1", "owner")
    for _ in range(3):
        task.packet("t1")
        (job / "alpha.txt").write_text("wrong\n")
        task.submit("t1")
    r = task.packet("t1")
    assert r["step"] == "fix-beta"
    (job / "beta.txt").write_text("beta\n")
    assert task.submit("t1")["ok"]
    end = task.packet("t1")
    assert end["state"] == "BLOCKED" and end["steps"] == ["fix-alpha"] and "wait for a person" in end["next"]
    ev = [json.loads(l)["event"] for l in (task.STATE / "t1" / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "done" not in ev



def chk_meaning(t, s, word):
    """A check that is literal unless the step is a declared port: then the word may appear in any case."""
    f = Path(t["workdir"], f"{word}.txt")
    text = f.read_text() if f.is_file() else ""
    ok = (word.lower() in text.lower()) if s.get("hand_port") else (word in text)
    return {"ok": ok, "why": [] if ok else [f"{word}.txt does not contain {word}" + (" (by the letter)" if not s.get("hand_port") else "")]}


def step_prepare_meaning(t, **kw):
    return {"ok": True, "summary": "x",
            "add_steps": [{"id": f"fix-{n}", "kind": "model", "title": f"put {n} into {n}.txt",
                           "packet": "tests.test_buildh_task:pkt", "check": "tests.test_buildh_task:chk_meaning",
                           "allowed": [f"{n}.txt"], "args": {"word": n}} for n in ("alpha", "beta")]}


@pytest.fixture
def job2(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "base.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "commit", "-q", "-m", "base"], check=True)
    task.start("t2", "demo", w, [{"id": "prepare", "kind": "script", "title": "prepare",
                                  "run": "tests.test_buildh_task:step_prepare_meaning"}], budget_tokens=1000)
    return w


def test_an_adapted_port_is_saved_for_the_owner_not_spent_and_the_run_goes_on(job2):
    """2026-10-14, the owner: "I wanna approve it all in one go". The model declares the port adapted; the
    harness judges it by meaning, keeps the diff, puts the tree back, spends no attempt, moves on."""
    task.approve("t2", "owner")
    task.packet("t2")
    (job2 / "alpha.txt").write_text("ALPHA\n")                    # right by meaning, wrong by the letter
    r = task.submit("t2", adapted="upstream renamed the symbol to upper case\nlesson: match a symbol case-insensitively")
    assert r["ok"] and r["proposed"]
    t = task.load("t2")
    s = next(x for x in t["steps"] if x["id"] == "fix-alpha")
    assert s["status"] == "proposed" and s["attempts"] == 0
    assert "by the letter" in s["proposal"]["literal_check_said"][0]
    assert not (job2 / "alpha.txt").exists(), "the tree was put back"
    assert Path(task.STATE, "t2", "proposals", "fix-alpha.diff").read_bytes()
    assert task.packet("t2")["step"] == "fix-beta", "the run goes on"


def test_an_adapted_port_that_does_not_hold_by_meaning_is_an_ordinary_failed_attempt(job2):
    task.approve("t2", "owner")
    task.packet("t2")
    (job2 / "alpha.txt").write_text("nothing here\n")
    r = task.submit("t2", adapted="trust me\nlesson: none")
    assert not r["ok"] and "meaning does not hold" in r["why"][0] and r["attempts"] == "1 of 3"


def test_the_owner_accepts_every_proposal_in_one_go(job2):
    task.approve("t2", "owner")
    task.packet("t2"); (job2 / "alpha.txt").write_text("ALPHA\n"); task.submit("t2", adapted="renamed\nlesson: follow renames")
    task.packet("t2"); (job2 / "beta.txt").write_text("BETA\n"); task.submit("t2", adapted="renamed too\nlesson: follow renames")
    end = task.advance("t2")
    assert end["state"] == "PROPOSED" and end["steps"] == ["fix-alpha", "fix-beta"] and "accept t2 --all" in end["next"]
    assert len(task.proposals("t2")) == 2
    r = task.accept("t2", who="owner")
    assert r["accepted"] == ["fix-alpha", "fix-beta"] and not r["failed"], r
    assert (job2 / "alpha.txt").read_text() == "ALPHA\n" and (job2 / "beta.txt").read_text() == "BETA\n"
    t = task.load("t2")
    assert all(s["status"] == "done" and s.get("done_by") == "hand" and s.get("accepted_by") == "owner"
               for s in t["steps"] if s["kind"] == "model")
    log = subprocess.run(["git", "-C", str(job2), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "checkpoint: fix-alpha" in log and "checkpoint: fix-beta" in log
    assert task.advance("t2")["state"] == "DONE"


def test_a_proposal_that_no_longer_applies_goes_back_to_failed_not_guessed(job2):
    task.approve("t2", "owner")
    task.packet("t2"); (job2 / "alpha.txt").write_text("ALPHA\n"); task.submit("t2", adapted="renamed\nlesson: follow renames")
    (job2 / "alpha.txt").write_text("someone else\n")
    task.checkpoint(task.load("t2"), "something else moved the file")
    r = task.accept("t2", who="owner")
    assert r["accepted"] == [] and r["failed"][0]["step"] == "fix-alpha" and "no longer applies" in r["failed"][0]["why"]
    assert task.load("t2")["steps"][1]["status"] == "failed"


def test_the_printed_accept_all_command_parses():
    """The owner's one-go command must be accepted by the real command line, not only by task.accept() (2026-10-14)."""
    from fieldkit import cli
    a = cli.build_parser().parse_args(["build-harness", "accept", "some-task", "--all"])
    assert a.all is True and "some-task" in a.args
    a = cli.build_parser().parse_args(["build-harness", "submit", "some-task", "--dry-run", "--adapted", "why"])
    assert a.dry_run is True and a.adapted == "why"


def test_port_and_teach_an_adapted_port_without_a_lesson_is_refused_and_lessons_are_collected(job2):
    """Owner, 2026-10-14: porting and teaching the harness happen together, at every step."""
    task.approve("t2", "owner")
    p = task.packet("t2")
    assert "PORT AND TEACH" in p["packet"]
    (job2 / "alpha.txt").write_text("ALPHA\n")
    with pytest.raises(task.Refused, match="PORT AND TEACH"):
        task.submit("t2", adapted="upstream renamed the symbol to upper case")
    task.submit("t2", adapted="upstream renamed it\nlesson: compare symbols case-insensitively")
    got = task.lessons("t2")
    assert [x["lesson"] for x in got] == ["compare symbols case-insensitively"] and got[0]["step"] == "fix-alpha"
