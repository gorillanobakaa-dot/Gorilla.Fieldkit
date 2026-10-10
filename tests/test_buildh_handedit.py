"""A person's edit that no patch asked for is recorded as done hand-port steps, one per hunk, with the diff as the
hunk, so every later judge treats it like any other hand port (live run 16: the MOZ_WEBSPEECH gate across eight
files, upstream code reaching into a component the owner switched off)."""
import subprocess

import pytest

from fieldkit.buildh import handedit, task


def _repo(tmp_path):
    w = tmp_path / "w"
    (w / "dom").mkdir(parents=True)
    (w / "dom/P.ipdl").write_text("include protocol PA;\ninclude protocol PSpeech;\nasync protocol P {};\n", encoding="utf-8", newline="\n")
    (w / "dom/other.cpp").write_text("int x;\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "pristine"], check=True)
    return w


def test_an_edit_becomes_done_hand_steps_and_a_checkpoint(tmp_path, monkeypatch):
    w = _repo(tmp_path)
    t = {"id": "he", "workflow": "firefox-upgrade", "workdir": str(w), "approved": True, "checkpoints": [], "meta": {},
         "steps": [{"id": "final-checks", "kind": "script", "status": "pending"}]}
    monkeypatch.setattr(task, "load", lambda tid: t)
    monkeypatch.setattr(task, "save", lambda t_: None)
    monkeypatch.setattr(task, "journal", lambda t_, ev, **kw: t_.setdefault("_j", []).append((ev, kw)))
    (w / "dom/P.ipdl").write_text("include protocol PA;\n#ifdef MOZ_WEBSPEECH\ninclude protocol PSpeech;\n#endif\nasync protocol P {};\n", encoding="utf-8", newline="\n")
    ids = handedit.record("he", ["dom/P.ipdl"], "gate on MOZ_WEBSPEECH")
    assert ids == [s["id"] for s in t["steps"] if s["id"].startswith("hand-")] and len(ids) == 1
    s = t["steps"][0]
    assert s["status"] == "done" and s["done_by"] == "hand" and s["hand_port"] and s["args"]["file"] == "dom/P.ipdl"
    assert any(l.startswith("+#ifdef MOZ_WEBSPEECH") for l in s["args"]["hunk"]["lines"])
    assert t["steps"][-1]["id"] == "final-checks"                       # recorded before the final checks
    assert t["_j"][0][0] == "hand-edit" and t["_j"][0][1]["files"] == ["dom/P.ipdl"]
    assert not subprocess.run(["git", "-C", str(w), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    # the recorded hunk is judged like a hand port
    from fieldkit.buildh import firefox
    now = (w / "dom/P.ipdl").read_text(encoding="utf-8").splitlines()
    assert firefox.hand_port_holds(now, s["args"]["hunk"]) == []


def test_record_refuses_blind_and_partial_records(tmp_path, monkeypatch):
    w = _repo(tmp_path)
    t = {"id": "he", "workflow": "firefox-upgrade", "workdir": str(w), "approved": True, "checkpoints": [], "meta": {}, "steps": []}
    monkeypatch.setattr(task, "load", lambda tid: t)
    with pytest.raises(task.Refused, match="no diff"):
        handedit.record("he", ["dom/P.ipdl"], "nothing changed")
    (w / "dom/P.ipdl").write_text("changed\n", encoding="utf-8", newline="\n")
    (w / "dom/other.cpp").write_text("int y;\n", encoding="utf-8", newline="\n")
    with pytest.raises(task.Refused, match="other files changed too"):
        handedit.record("he", ["dom/P.ipdl"], "only one named")


def test_record_merges_the_two_halves_of_a_moved_block():
    from fieldkit.buildh import handedit
    h_remove = {"header": "@@ -10,4 +10,2 @@", "lines": [" a", "-    this.embedder = make();", "-    this.db = open();", " b"]}
    h_add = {"header": "@@ -30,2 +28,4 @@", "lines": [" c", "+    this.embedder = make();", "+    this.db = open();", " d"]}
    other = {"header": "@@ -50,1 +50,1 @@", "lines": ["-    old();", "+    new();"]}
    out = handedit.merge_moves([h_remove, other, h_add])
    assert len(out) == 2 and "merged" in out[0]["header"] and out[1] is other
    assert out[0]["lines"] == h_remove["lines"] + h_add["lines"]


def test_save_merges_steps_another_process_added(tmp_path, monkeypatch):
    from fieldkit.buildh import task
    monkeypatch.setattr(task, "STATE", tmp_path)
    a = {"id": "t", "steps": [{"id": "s1"}, {"id": "final-checks"}], "checkpoints": [{"commit": "aaa"}]}
    task.save(a)
    other = task.load("t")                                   # a second process loads the same task
    other["steps"].insert(1, {"id": "hand-x"}); other["checkpoints"].append({"commit": "bbb"})
    task.save(other)                                         # and records a hand step
    a["steps"][0]["status"] = "done"                         # the first process, stale, saves its own change
    task.save(a)
    disk = task.load("t")
    assert [s["id"] for s in disk["steps"]] == ["s1", "hand-x", "final-checks"] and disk["steps"][0]["status"] == "done"
    assert [c["commit"] for c in disk["checkpoints"]] == ["aaa"] and disk["merged_steps"][0]["ids"] == ["hand-x"]   # checkpoints: not merged (rewind removes them on purpose)


def test_a_new_file_gets_its_step_instead_of_being_skipped(tmp_path, monkeypatch):
    # 2026-10-03: about.svg (new, untracked) was committed by the checkpoint but got no step
    w = _repo(tmp_path)
    t = {"id": "he", "workflow": "firefox-upgrade", "workdir": str(w), "approved": True, "checkpoints": [], "meta": {},
         "steps": [{"id": "final-checks", "kind": "script", "status": "pending"}]}
    monkeypatch.setattr(task, "load", lambda tid: t)
    monkeypatch.setattr(task, "save", lambda t_: None)
    monkeypatch.setattr(task, "journal", lambda t_, ev, **kw: None)
    (w / "dom/new.svg").write_text("<svg/>\n", encoding="utf-8", newline="\n")
    ids = handedit.record("he", ["dom/new.svg"], "a new file")
    assert len(ids) == 1
    assert any(l == "+<svg/>" for l in t["steps"][0]["args"]["hunk"]["lines"])


def test_a_line_ending_flip_is_detected(tmp_path):
    # 2026-10-04: text-mode writes on Windows turned LF files into CRLF and whole files were recorded as edited
    w = tmp_path / "w"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "a.ftl").write_bytes(b"one\ntwo\n")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
                    "-m", "x"], check=True)
    (w / "a.ftl").write_bytes(b"one\r\nTWO\r\n")
    assert handedit.eol_flipped(w, "a.ftl")
    (w / "a.ftl").write_bytes(b"one\nTWO\n")
    assert not handedit.eol_flipped(w, "a.ftl")
    assert not handedit.eol_flipped(w, "missing.ftl")
