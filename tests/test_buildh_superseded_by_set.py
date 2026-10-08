"""Build 28 post-install (2026-10-08): the claims audit failed 4 patches (and so contradicted 237 claims) that later
patches of the set had replaced on purpose, and the build packaged files the tree had deleted. Covered here:
  - a hunk whose file a group's DELETED_FILES.manifest.txt removes later is SUPERSEDED
  - a generic removed line elsewhere in the file does not block "superseded" when the scorer's window was clean
  - a file the set adds has its NEW_FILES copy as the pristine reference
  - buildrun sweeps dist/bin of files the tree deleted that no install manifest installs (omni folders only)"""
import os
import subprocess
from pathlib import Path

from fieldkit.buildh import buildrun, claims, firefox


def test_a_file_the_set_deletes_later_supersedes_its_hunk(monkeypatch):
    class T:
        w = Path(".")
        def body(self, rel): return None
        def raw(self, rel): return None
        def pristine(self, rel): return None
    h = {"lines": [" a", "+gorilla_specific_line_here", " b"], "header": "@@ -1,2 +1,3 @@"}
    st, d, e = claims.hunk_status(T(), "22.X/052.patch", "branding/about-logo.svg", 1, h, {}, {}, {}, {}, 5,
                                   set_deleted={"branding/about-logo.svg"})
    assert (st, e) == ("SUPERSEDED", True) and "DELETED_FILES.manifest.txt" in d
    st, _, e = claims.hunk_status(T(), "22.X/052.patch", "branding/about-logo.svg", 1, h, {}, {}, {}, {}, 5)
    assert (st, e) == ("OBSOLETE", False)


def test_a_clean_window_ignores_generic_removed_lines_elsewhere():
    body = ['  -moz-context-properties: fill;', '  background: url("logo.png") no-repeat;', '  -moz-context-properties: fill;']
    h = {"lines": ['   .x {', '-  -moz-context-properties: fill;', '+  background: url("logo.svg") no-repeat;', '   }']}
    later = {"f.css": [(9, "22/080.patch", {'background: url("logo.svg") no-repeat;'}, {'background: url("logo.png") no-repeat;'})]}
    assert claims._superseded(body, h, "f.css", later, 3) == []                      # the generic line blocks it
    assert claims._superseded(body, h, "f.css", later, 3, window_clean=True) == ["22/080.patch"]


def test_a_file_the_set_adds_uses_its_new_files_copy_as_pristine(tmp_path):
    w = tmp_path / "tree"
    w.mkdir()
    (w / "a.txt").write_bytes(b"x\n")
    subprocess.run(["git", "-C", str(w), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(w), "add", "."], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "p"], check=True)
    src = tmp_path / "NEW_FILES" / "jar.mn"
    src.parent.mkdir()
    src.write_bytes(b"content/branding/about-logo.png\ncontent/branding/about-logo.svg\n")
    t = claims.Tree(w)
    try:
        assert t.pristine("jar.mn") is None
        t2 = claims.Tree(w)
        t2.new_file_sources["jar.mn"] = src
        assert t2.pristine("jar.mn") == ["content/branding/about-logo.png", "content/branding/about-logo.svg"]
        t2.close()
    finally:
        t.close()


def test_the_dist_sweep_removes_only_deleted_uninstalled_files_in_omni_folders(tmp_path):
    w = tmp_path / "tree"
    (w / "branding").mkdir(parents=True)
    for n in ("about-logo.svg", "about-logo.png", "README.txt"):
        (w / "branding" / n).write_bytes(b"x")
    g = lambda *a: subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)
    g("init", "-q")
    g("add", ".")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "pristine")
    g("rm", "-q", "branding/about-logo.svg", "branding/README.txt")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "delete")
    od = tmp_path / "obj"
    chrome = od / "dist" / "bin" / "browser" / "chrome" / "browser" / "content" / "branding"
    chrome.mkdir(parents=True)
    for n in ("about-logo.svg", "about-logo.png"):
        (chrome / n).write_bytes(b"x")
    (od / "dist" / "bin" / "README.txt").write_bytes(b"x")              # not an omni folder: never swept
    (od / "faster").mkdir()
    (od / "faster" / "install_dist_bin_browser").write_text(
        "5\n1\x1fchrome/browser/content/branding/about-logo.png\x1fC:/src/about-logo.png\n", encoding="utf-8")
    removed = buildrun.sweep_deleted_dist(od, w, say=lambda m: None)
    assert removed == ["browser/chrome/browser/content/branding/about-logo.svg"]
    assert (chrome / "about-logo.png").exists() and (od / "dist" / "bin" / "README.txt").exists()
