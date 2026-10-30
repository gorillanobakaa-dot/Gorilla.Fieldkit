"""fieldkit backup usb / remind (fieldkit/core/usbcopy.py): the newest backup copied to a stick and proven there.

The rule held above all: nothing already on the stick is deleted, moved or overwritten, not even when it is full
and not even an older Fieldkit copy. The academic harness this replaces moved old copies off the stick (hand-made
ones included) to make room; a backup can be the only one there is.
"""
import datetime as dt
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from fieldkit.core import usbcopy


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(usbcopy, "_state_file", lambda: tmp_path / "state" / "backup-usb.json")


def _backup(folder, stamp="2026-10-11_1200", body=b"harness files"):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"harness_backup_{stamp}.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Gorilla.Fieldkit/README.md", body)
    return p


def _tree(root):
    """{relative path: sha256} of everything under root: what 'untouched' is checked against."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _stick_with_history(tmp_path):
    stick = tmp_path / "stick"
    (stick / "New.Academic.Harness.2026-09-01").mkdir(parents=True)
    (stick / "New.Academic.Harness.2026-09-01" / "harness_2026-09-01.zip").write_bytes(b"the only copy of September")
    (stick / "PROIECTE.UNIVERSITATE" / "Academic.Work.harness.backup").mkdir(parents=True)
    (stick / "PROIECTE.UNIVERSITATE" / "Academic.Work.harness.backup" / "essay.docx").write_bytes(b"his essay")
    _backup(stick / usbcopy.FOLDER, "2026-10-01_0900", b"an older Fieldkit copy")
    return stick


def test_the_newest_backup_is_copied_verified_and_nothing_else_changes(tmp_path):
    src = tmp_path / "backups"
    _backup(src, "2026-10-10_0800", b"older")
    newest = _backup(src, "2026-10-11_1200", b"newest")
    stick = _stick_with_history(tmp_path)
    before = _tree(stick)
    r = usbcopy.copy(stick=stick, source=src, local={})
    assert r["ok"] and r["status"] == "copied", r
    dest = stick / usbcopy.FOLDER / newest.name
    assert dest.read_bytes() == newest.read_bytes()
    assert (stick / usbcopy.FOLDER / (newest.name + ".sha256")).read_text().split()[0] == usbcopy.sha256(newest)
    after = _tree(stick)
    assert {k: v for k, v in after.items() if k in before} == before          # every earlier file, byte for byte
    assert set(after) - set(before) == {f"{usbcopy.FOLDER}/{newest.name}", f"{usbcopy.FOLDER}/{newest.name}.sha256"}
    assert json.loads((tmp_path / "state" / "backup-usb.json").read_text())["sha256"] == usbcopy.sha256(newest)


def test_a_full_stick_stops_the_copy_and_deletes_nothing(tmp_path):
    src = tmp_path / "backups"
    _backup(src)
    stick = _stick_with_history(tmp_path)
    before = _tree(stick)
    r = usbcopy.copy(stick=stick, source=src, local={}, free=10)
    assert not r["ok"] and r["status"] == "full" and "Nothing was deleted" in r["next"]
    assert _tree(stick) == before


def test_the_same_copy_twice_is_done_once_and_a_different_one_never_overwrites(tmp_path):
    src = tmp_path / "backups"
    b = _backup(src)
    stick = tmp_path / "stick"
    stick.mkdir()
    assert usbcopy.copy(stick=stick, source=src, local={})["status"] == "copied"
    assert usbcopy.copy(stick=stick, source=src, local={})["status"] == "already-there"
    # a file of the same name but other bytes is on the stick: kept, and the new copy goes beside it
    (stick / usbcopy.FOLDER / b.name).write_bytes(b"someone else's file with this name")
    r = usbcopy.copy(stick=stick, source=src, local={})
    assert r["ok"] and r["copy"].endswith("_2.zip")
    assert (stick / usbcopy.FOLDER / b.name).read_bytes() == b"someone else's file with this name"


def test_a_copy_that_does_not_read_back_is_removed_and_reported(tmp_path, monkeypatch):
    src = tmp_path / "backups"
    _backup(src)
    stick = _stick_with_history(tmp_path)
    before = _tree(stick)
    real = usbcopy.sha256
    monkeypatch.setattr(usbcopy, "sha256", lambda p: "0" * 64 if Path(p).parent.name == usbcopy.FOLDER else real(p))
    r = usbcopy.copy(stick=stick, source=src, local={})
    assert not r["ok"] and r["status"] == "failed" and "SHA-256" in r["problems"][0]
    assert _tree(stick) == before                                       # its own bad copy gone; the rest as it was


def test_no_backup_or_no_stick_asks_and_writes_nothing(tmp_path):
    assert usbcopy.copy(stick=tmp_path, source=tmp_path / "none", local={})["status"] == "no-backup"
    _backup(tmp_path / "b")
    r = usbcopy.copy(source=tmp_path / "b", local={}, roots=[])
    assert r["status"] == "ask" and "plug it in" in r["next"]
    two = [tmp_path / "s1", tmp_path / "s2"]
    for s in two:
        s.mkdir()
    assert "several sticks" in usbcopy.copy(source=tmp_path / "b", local={}, roots=[str(s) for s in two])["next"]
    (two[1] / usbcopy.FOLDER).mkdir()                                  # the stick used before is chosen
    assert usbcopy.copy(source=tmp_path / "b", local={}, roots=[str(s) for s in two])["copy"].startswith(str(two[1]))


def test_the_reminder_is_due_after_the_days_set_and_speaks_once_a_day(tmp_path):
    said = []
    now = dt.datetime(2026, 10, 11, 9, 0)
    r = usbcopy.remind(now=now, roots=[], local={}, say=said.append)
    assert r["status"] == "due" and r["last"] is None and said and "No copy" in said[0]
    assert usbcopy.remind(now=now + dt.timedelta(hours=2), roots=[], local={}, say=said.append)["spoken"] is False
    src = tmp_path / "backups"
    _backup(src)
    stick = tmp_path / "stick"
    stick.mkdir()
    usbcopy.copy(stick=stick, source=src, local={}, now=now)
    assert usbcopy.remind(now=now + dt.timedelta(days=2), roots=[], local={}, say=said.append)["status"] == "fine"
    late = usbcopy.remind(now=now + dt.timedelta(days=5), roots=[], local={"backup": {"usb_remind_days": 4}},
                          say=said.append)
    assert late["status"] == "due" and late["days"] == 5 and "5 day(s) old" in said[-1]
    # a connected stick with a newer copy than the record counts
    _backup(stick / usbcopy.FOLDER, "2026-10-15_1000")
    assert usbcopy.remind(now=now + dt.timedelta(days=5), roots=[str(stick)], local={}, say=said.append,
                          speak=False)["status"] == "fine"


def test_backup_carries_the_parked_ideas_of_a_shared_state(tmp_path, monkeypatch):
    """A linked worktree shares the main checkout's state/: its ideas must still reach the archive."""
    import zipfile
    from fieldkit.core import backup, settings
    root, shared = tmp_path / "wt", tmp_path / "main"
    (root).mkdir()
    (root / "a.txt").write_text("a")
    (shared / "state" / "ideas").mkdir(parents=True)
    (shared / "state" / "ideas" / "ideas.json").write_text("{}")
    monkeypatch.setattr(settings, "state_root", lambda r=None: shared)
    r = backup.backup(to=tmp_path / "out", root=root, local={})
    assert r["ok"] and r["ideas_files"] == 1
    assert "wt/state/ideas/ideas.json" in zipfile.ZipFile(r["archive"]).namelist()
