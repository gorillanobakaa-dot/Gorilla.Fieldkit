# Version: 1.0.0 · updated 26-10-15-11-20
"""loss-audit: a patch deleted from the shared set's history is still counted against the ported tree."""
import subprocess

from fieldkit.buildh import lossaudit

GPU = ("--- a/widget/gtk/GfxInfo.cpp\n+++ b/widget/gtk/GfxInfo.cpp\n@@ -1,1 +1,4 @@\n int x;\n"
       "+  // GORILLA OVERRIDE: Short-circuit blocklist\n+  *aStatus = FEATURE_STATUS_OK;\n+  }\n")
PREFS = ('--- a/all.js\n+++ b/all.js\n@@ -1 +1,3 @@\n x\n+pref("media.hardware-video-decoding.force-enabled", true);\n'
         '+pref("gfx.webrender.all", true);\n')


def _g(w, *a):
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                   capture_output=True)


def test_deleted_patch_and_lost_pref_are_counted(tmp_path):
    repo = tmp_path / "set"
    (repo / "patches" / "02.GPU").mkdir(parents=True)
    (repo / "patches" / "05.PREFS").mkdir(parents=True)
    (repo / "patches" / "02.GPU" / "widget_gtk_GfxInfo.cpp.patch").write_text(GPU)
    (repo / "patches" / "05.PREFS" / "all.js.patch").write_text(PREFS)
    _g(repo, "init", "-q")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-qm", "linux set")
    _g(repo, "rm", "-q", "patches/02.GPU/widget_gtk_GfxInfo.cpp.patch")
    _g(repo, "commit", "-qm", "regenerated from a windows tree")
    tree = tmp_path / "tree"
    (tree / "widget" / "gtk").mkdir(parents=True)
    (tree / "widget" / "gtk" / "GfxInfo.cpp").write_text("int x;\n")
    (tree / "all.js").write_text('x\npref("gfx.webrender.all", true);\n')
    r = lossaudit.audit(tree, [("git", lossaudit.from_git(repo))])
    by = {f["file"]: f for f in r["files"]}
    assert by["widget/gtk/GfxInfo.cpp"]["state"] == "lost"                   # the deleted patch still counts
    assert "deleted" in by["widget/gtk/GfxInfo.cpp"]["source"]
    assert by["all.js"]["state"] == "partial"
    assert by["all.js"]["lost_prefs"] == ['pref("media.hardware-video-decoding.force-enabled", true);']
    assert r["summary"]["lost_prefs"] == 1


def test_trivial_lines_are_not_counted():
    assert lossaudit.added_lines("+++ b/x\n+  }\n+\n+real();\n") == ["real();"]
