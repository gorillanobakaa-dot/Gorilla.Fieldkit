"""compile gate: only a fully ported, unmodified, honestly-checked tree may be compiled, and only a FRESH
artifact from that tree counts as its build."""
import os
import subprocess
import time

import pytest

from fieldkit.buildh import compile as cg, task


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "x"], check=True)
    obj = tmp_path / "obj"
    (obj / "dist").mkdir(parents=True)
    h = tmp_path / "harness"
    (h / "config").mkdir(parents=True)
    (h / "config" / "mozconfig.win64").write_text(f"mk_add_options MOZ_OBJDIR={obj.as_posix()}\n")
    monkeypatch.setattr(cg, "mozconfig_path", lambda root=None: h / "config" / "mozconfig.win64")
    t = task.start("c1", "demo", w, [], budget_tokens=1000, meta={"upstream": {"version": "157.0"}})
    task.approve("c1", "owner")
    t = task.load("c1")
    task.journal(t, "script-done", step="final-checks")
    return {"w": w, "obj": obj, "h": h}


def failing(rows):
    return [r["check"] for r in rows if not r["ok"]]


def test_a_finished_clean_tree_passes_the_gate_and_is_recorded(world):
    assert failing(cg.gate("c1")) == []
    assert (task.STATE / "c1" / "build-record.json").is_file()


def test_unfinished_steps_block_the_gate(world):
    t = task.load("c1")
    t["steps"].append({"id": "port-x", "kind": "model", "status": "pending", "attempts": 0, "max_attempts": 3, "title": "x"})
    task.save(t)
    assert "every step is done" in failing(cg.gate("c1"))
    assert not (task.STATE / "c1" / "build-record.json").exists()


def test_final_checks_that_never_passed_block_the_gate(world):
    (task.STATE / "c1" / "journal.jsonl").write_text("")
    assert "final checks passed, and nothing changed after them" in failing(cg.gate("c1"))


def test_a_change_after_the_final_checks_blocks_the_gate(world):
    task.journal(task.load("c1"), "auto-done", step="port-1", notes=[])
    assert "final checks passed, and nothing changed after them" in failing(cg.gate("c1"))


def test_a_hand_edit_or_leftover_blocks_the_gate(world):
    (world["w"] / "a.txt").write_text("hand edit\n")
    (world["w"] / "x.rej").write_text("")
    f = failing(cg.gate("c1"))
    assert "working copy: no hand edits" in f and "no .rej / .orig leftovers" in f


def test_a_skip_or_an_answer_key_copy_blocks_the_gate(world):
    t = task.load("c1")
    task.journal(t, "unblock", step="s", how="skip")
    assert "journal: no step skipped" in failing(cg.gate("c1"))


def _build(world, age=0, mb=60, version_ok=True):
    rec_at = time.time()
    for name in ("firefox-157.0.en-US.win64.installer.exe", "firefox-157.0.en-US.win64.zip"):
        f = world["obj"] / "dist" / name
        with open(f, "wb") as fh:
            fh.truncate(mb * 2 ** 20)
        os.utime(f, (rec_at + age, rec_at + age))


def test_a_fresh_artifact_from_the_gated_tree_verifies_and_is_hashed(world):
    cg.gate("c1")
    _build(world, age=+5)
    rows = cg.verify("c1", run_binary=False)
    assert failing(rows) == [], failing(rows)
    assert (task.STATE / "c1" / "build-result.json").is_file()


def test_a_stale_artifact_is_not_this_build(world):
    cg.gate("c1")
    _build(world, age=-86400 * 200)                   # the owner's own harness once recorded a 7-month-old dist
    assert any("is from THIS build" in c for c in failing(cg.verify("c1", run_binary=False)))


def test_a_tiny_artifact_is_not_a_build(world):
    cg.gate("c1")
    _build(world, age=+5, mb=1)
    assert any("real size" in c for c in failing(cg.verify("c1", run_binary=False)))


def test_a_source_change_after_the_gate_is_caught(world):
    cg.gate("c1")
    (world["w"] / "a.txt").write_text("changed after the gate\n")
    subprocess.run(["git", "-C", str(world["w"]), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "late"], check=True)
    _build(world, age=+5)
    assert "the tree is the one that was gated" in failing(cg.verify("c1", run_binary=False))


def test_a_changed_mozconfig_after_the_gate_is_caught(world):
    cg.gate("c1")
    (world["h"] / "config" / "mozconfig.win64").write_text("ac_add_options --enable-something-else\n")
    _build(world, age=+5)
    assert "mozconfig unchanged since the gate" in failing(cg.verify("c1", run_binary=False))


def test_verify_without_a_gate_refuses(world):
    assert failing(cg.verify("c1", run_binary=False)) == ["a passed build gate exists"]


def test_a_missing_binary_fails_when_it_is_asked_for(world):
    cg.gate("c1")
    _build(world, age=+5)
    assert "built firefox.exe exists" in failing(cg.verify("c1"))
