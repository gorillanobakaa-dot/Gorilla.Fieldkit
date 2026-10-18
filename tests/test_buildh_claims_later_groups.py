"""2026-10-18 (158 build 2 post-install): the claims audit failed 44 08.Look branding files that later groups of the
set replace on purpose (icons rebuilt by 21.PORT.FIXES.157/038 and 23.REBASE.158) or delete by their manifest (the
macOS art, 22.EGRESS.LOCKDOWN.157). A new file changed by a later patch that is itself in place, or deleted by a
later group's manifest, is explained by the set; one nothing explains still fails."""
import json
import subprocess

from fieldkit.buildh import claims


def _owner(tmp_path):
    o = tmp_path / "owner"
    (o / "config").mkdir(parents=True)
    groups = {"08.Look": {"status": "enabled"}, "21.X": {"status": "enabled"}, "22.Y": {"status": "enabled"}}
    (o / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "patches", "groups": groups}))
    nf = o / "patches" / "08.Look" / "NEW_FILES" / "b"
    nf.mkdir(parents=True)
    (nf / "icon.txt").write_bytes(b"old\n")
    (nf / "logo.txt").write_bytes(b"logo\n")
    (nf / "lost.txt").write_bytes(b"lost\n")
    (o / "patches" / "21.X").mkdir()
    (o / "patches" / "21.X" / "001-icon.patch").write_text(
        "# rebuilt\n\ndiff --git a/b/icon.txt b/b/icon.txt\n--- a/b/icon.txt\n+++ b/b/icon.txt\n@@ -1 +1 @@\n-old\n+new\n",
        encoding="utf-8")
    (o / "patches" / "22.Y").mkdir()
    (o / "patches" / "22.Y" / "DELETED_FILES.manifest.txt").write_text("b/logo.txt\n", encoding="utf-8")
    return o


def _tree(tmp_path):
    w = tmp_path / "tree"
    (w / "b").mkdir(parents=True)
    (w / "b" / "icon.txt").write_bytes(b"new\n")
    (w / "b" / "keep.txt").write_bytes(b"x\n")
    g = lambda *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                                  capture_output=True)
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "built")
    return w


def test_a_new_file_a_later_group_changes_or_deletes_is_explained_and_a_lost_one_fails(tmp_path):
    o, w = _owner(tmp_path), _tree(tmp_path)
    res = claims.audit_patches(o, w, [])
    st = {x["file"]: (x["status"], x["fail"]) for x in res["new_files"]}
    assert st["b/icon.txt"] == ("CHANGED-BY-LATER-PATCH 21.X/001-icon.patch", False)
    assert st["b/logo.txt"] == ("DELETED-BY-LATER-GROUP 22.Y", False)
    assert st["b/lost.txt"] == ("MISSING", True)                          # nothing explains it: still a failure
