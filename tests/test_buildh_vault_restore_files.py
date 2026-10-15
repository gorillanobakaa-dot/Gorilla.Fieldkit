# Version: 1.0.0 · updated 26-10-15-17-56
"""vault restore-files: pristine files back WITHOUT git; the current versions go to the trash, never deleted; a file
the release does not have is listed and left alone; a damaged vault is never copied from (owner 2026-10-15)."""
import json
import subprocess

import pytest

from fieldkit.buildh import vault


def _vault(tmp_path):
    base = tmp_path / "vault"
    d = base / "firefox" / "158.0"
    (d / "toolkit" / "modules").mkdir(parents=True)
    (d / "toolkit" / "modules" / "SubDialog.sys.mjs").write_text("pristine\n")
    (d / "browser" / "x").mkdir(parents=True)
    (d / "browser" / "x" / "a.css").write_text("a\n")
    (d / "browser" / "x" / "b.css").write_text("b\n")
    g = lambda *a: subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                  check=True, capture_output=True)
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "release")
    head = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (d / "vault.json").write_text(json.dumps({"kind": "git", "commit": head, "version": "158.0", "product": "firefox"}))
    return base


def test_damaged_files_come_back_and_the_old_ones_go_to_the_trash(tmp_path):
    base = _vault(tmp_path)
    work = tmp_path / "work"
    (work / "toolkit" / "modules").mkdir(parents=True)
    (work / "toolkit" / "modules" / "SubDialog.sys.mjs").write_text("damaged by a model\n")
    (work / "browser" / "x").mkdir(parents=True)
    (work / "browser" / "x" / "a.css").write_text("a\n")                       # already pristine
    r = vault.restore_files("firefox", work, ["toolkit/modules/SubDialog.sys.mjs", "browser/x", "browser/ours.css"],
                            base=base, trash=tmp_path / "trash")
    assert (work / "toolkit" / "modules" / "SubDialog.sys.mjs").read_text() == "pristine\n"
    assert (work / "browser" / "x" / "b.css").read_text() == "b\n"                # a folder: every file under it
    assert r["already"] == ["browser/x/a.css"] and r["not_in_release"] == ["browser/ours.css"]
    assert r["moved_to_trash"] == ["toolkit/modules/SubDialog.sys.mjs"]
    assert list((tmp_path / "trash").rglob("SubDialog.sys.mjs"))[0].read_text() == "damaged by a model\n"


def test_nothing_replaced_means_no_trash_claimed(tmp_path):
    r = vault.restore_files("firefox", tmp_path / "w", ["toolkit/modules/SubDialog.sys.mjs"], base=_vault(tmp_path),
                            trash=tmp_path / "trash")
    assert r["restored"] and r["trash"] is None and not (tmp_path / "trash").exists()


def test_a_damaged_vault_is_never_copied_from(tmp_path):
    base = _vault(tmp_path)
    (base / "firefox" / "158.0" / "toolkit" / "modules" / "SubDialog.sys.mjs").write_text("tampered\n")
    with pytest.raises(RuntimeError, match="damaged"):
        vault.restore_files("firefox", tmp_path / "w", ["toolkit/modules/SubDialog.sys.mjs"], base=base)


def test_paths_must_stay_inside_the_tree(tmp_path):
    with pytest.raises(ValueError):
        vault.restore_files("firefox", tmp_path / "w", ["../../etc/passwd"], base=_vault(tmp_path))
