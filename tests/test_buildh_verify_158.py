# Version: 1.0.0 · updated 26-10-16-21-10
"""2026-10-16, the 158 build gate on Windows: 47 "missing new files" and one false completion that were not.
- a NEW_FILES copy a later patch (text or GIT binary) rewrites or deletes is judged by that patch, not by the copy
- a port step taken by hand is found by its checkpoint label, and its REAL diff counts, not only the hunk it was for"""
import subprocess

from fieldkit.buildh import verify


def test_patched_files_reads_text_and_binary_sections_and_skips_excluded(tmp_path):
    g = tmp_path / "21.FIX"
    g.mkdir()
    (g / "038-icons.patch").write_text(
        "diff --git a/browser/branding/gorilla/newtab.ico b/browser/branding/gorilla/newtab.ico\n"
        "index 1..2 100644\nGIT binary patch\nliteral 3\nabc\n\n"
        "diff --git a/browser/themes/shared/master-redirect.css b/browser/themes/shared/master-redirect.css\n"
        "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", newline="\n")
    (g / "099-off.patch").write_text("diff --git a/off.js b/off.js\n--- a/off.js\n+++ b/off.js\n", newline="\n")
    groups = {"21.FIX": {"status": "enabled", "exclude": ["099-off.patch"]}, "30.OFF": {"status": "disabled"}}
    got = verify.patched_files(tmp_path, groups)
    assert got == {"browser/branding/gorilla/newtab.ico", "browser/themes/shared/master-redirect.css"}


def _git(d, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(d), *a],
                          check=True, capture_output=True, text=True).stdout.strip()


def test_a_hand_port_step_is_found_by_its_checkpoint_and_its_real_diff_supersedes(tmp_path):
    w = tmp_path
    f = w / "a.js"
    f.write_text("init();\nlet x = 1;\nmisplaced();\n", newline="\n")
    _git(w, "init", "-q")
    _git(w, "add", "-A")
    _git(w, "commit", "-qm", "harness applied a block in the wrong place")
    f.write_text("init();\nlet x = 2;\n", newline="\n")       # the hand port: its own change AND the removal
    _git(w, "commit", "-qam", "checkpoint: port-027-h1")
    hc = _git(w, "rev-parse", "HEAD")
    earlier = {"id": "port-006-h2", "status": "done", "done_by": "harness",
               "args": {"file": "a.js", "hunk": {"lines": [" init();", "+misplaced();"]}}}
    later = {"id": "port-027-h1", "status": "done", "hand_port": True,
             "args": {"file": "a.js", "hunk": {"lines": ["-let x = 1;", "+let x = 2;"]}}}
    cps = [{"label": "port-027-h1", "commit": hc}]
    assert verify.hand_step_commit(w, later, checkpoints=cps) == hc
    now = f.read_text().splitlines()
    assert verify.superseded_by_hand([earlier, later], earlier, now) is None          # the hunk alone: not seen
    assert verify.superseded_by_hand([earlier, later], earlier, now, w, cps) == "port-027-h1"
