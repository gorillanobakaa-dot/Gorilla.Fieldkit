"""snapshot: files diff by content, not timestamps; system parts are read-only and diffable."""
import os
import time

from fieldkit.core import snapshot


def test_file_diff_added_removed_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path / "snaps")
    d = tmp_path / "proj"
    d.mkdir()
    (d / "keep.txt").write_text("same")
    (d / "edit.txt").write_text("before")
    (d / "gone.txt").write_text("bye")
    snapshot.take("a", paths=[d])
    (d / "edit.txt").write_text("after!")
    (d / "gone.txt").unlink()
    (d / "new.txt").write_text("hello")
    snapshot.take("b", paths=[d])
    ch = snapshot.diff("a", "b")["files"]
    assert ch["added"] == [str(d / "new.txt")] and ch["removed"] == [str(d / "gone.txt")]
    assert list(ch["changed"]) == [str(d / "edit.txt")]


def test_touching_a_file_is_not_a_change(tmp_path, monkeypatch):
    """Content decides: a new timestamp with the same bytes is not reported."""
    monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path / "snaps")
    f = tmp_path / "x.txt"
    f.write_text("same")
    snapshot.take("a", paths=[f])
    later = time.time() + 100
    os.utime(f, (later, later))
    snapshot.take("b", paths=[f])
    assert snapshot.diff("a", "b") == {}
    assert snapshot.summary_lines({}) == ["no changes"]


def test_system_parts_diff(tmp_path):
    a = {"services": {"Spooler": {"state": "Running", "start": "Auto"}, "Old": {"state": "Stopped", "start": "Manual"}}}
    b = {"services": {"Spooler": {"state": "Stopped", "start": "Disabled"}, "New": {"state": "Running", "start": "Auto"}}}
    ch = snapshot.diff(a, b)["services"]
    assert ch["added"] == ["New"] and ch["removed"] == ["Old"] and list(ch["changed"]) == ["Spooler"]
    lines = snapshot.summary_lines({"services": ch})
    assert lines[0].startswith("services: +1 added, -1 removed, ~1 changed") and "   ~ Spooler" in lines


def test_parts_only_compared_when_in_both():
    assert snapshot.diff({"files": {}}, {"services": {"x": {}}}) == {}


def test_processes_part_reads_this_machine():
    procs = snapshot.processes()
    assert procs and all(v["count"] >= 1 for v in procs.values())
