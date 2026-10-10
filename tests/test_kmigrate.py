"""kernel migrate-check / migrate-verify (2026-10-10): the 7.1.2 -> 7.2.9 port, as steps, on a miniature project."""
import difflib
import shutil
from pathlib import Path

import pytest

from fieldkit.build import kmigrate

pytestmark = pytest.mark.skipif(not Path(kmigrate._patch_exe()).exists() and not shutil.which("patch"),
                                reason="GNU patch not installed")

OLD_A = "".join(f"line {i}\n" for i in range(1, 21))
OLD_B = "enum {\n\tA,\n\tB,\n};\nvendor(0x104d, SONY);\n"
NEW_A = "new top line\n" + OLD_A                                        # upstream added a line: an offset
NEW_B = "enum {\n\tA,\n\tB,\n\tC_NEW,\n};\nvendor(0x104d, SONY);\n"    # upstream changed the patched spot


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def _patch(rel, before, after):
    d = difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{rel}", f"b/{rel}")
    return "".join(d)


def _project(tmp_path, a_old, b_old, last="B"):
    """The project patched against a_old/b_old: GORILLA appended at the END of the enum (whose last entry is `last`)."""
    proj = tmp_path / "proj"
    a_pat = a_old.replace("line 10\n", "line 10 GORILLA\n")
    b_pat = b_old.replace(f"\t{last},\n}};", f"\t{last},\n\tGORILLA,\n}};").replace("SONY)", "GORILLA_SONY)")
    assert "GORILLA," in b_pat
    _w(proj / "a.c", a_pat)
    _w(proj / "b.c", b_pat)
    _w(proj / "PATCHED_FILES_PATH_REGISTRY.txt", "# map\na.c -> src/a.c\nb.c -> drv/b.c\n")
    _w(proj / "patches" / "series", "a.c.patch\nb.c.patch\n")
    _w(proj / "patches" / "a.c.patch", _patch("src/a.c", a_old, a_pat))
    _w(proj / "patches" / "b.c.patch", _patch("drv/b.c", b_old, b_pat))
    return proj


@pytest.fixture
def trees(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    _w(old / "src/a.c", OLD_A)
    _w(old / "drv/b.c", OLD_B)
    _w(new / "src/a.c", NEW_A)
    _w(new / "drv/b.c", NEW_B)
    return old, new


def test_check_proves_the_baseline_and_names_what_fits_and_what_to_port(tmp_path, trees):
    old, new = trees
    proj = _project(tmp_path, OLD_A, OLD_B)
    r = kmigrate.check(proj, old, new)
    assert r["ok"] and r["baseline_identical"] == 2
    st = {p["patch"]: p for p in r["patches"]}
    assert st["a.c.patch"]["status"] == "offset" and st["b.c.patch"]["status"] == "fails"
    assert r["upstream"]["drv/b.c"] == {"added": 1, "removed": 0} and len(r["todo"]) == 1
    assert "b.c.patch" in r["next"] and any(l.startswith("DO: port b.c.patch") for l in kmigrate.check_lines(r))


def test_check_stops_when_the_old_tree_is_not_the_pristine(tmp_path, trees):
    old, new = trees
    proj = _project(tmp_path, OLD_A, OLD_B)
    _w(old / "src/a.c", OLD_A.replace("line 3\n", "line 3 already patched\n"))
    r = kmigrate.check(proj, old, new)
    assert not r["ok"] and r["next"] == "fix the baseline first"


def test_verify_proves_a_faithful_port_and_catches_a_changed_line(tmp_path, trees):
    old, new = trees
    before = _project(tmp_path, OLD_A, OLD_B)
    keep = tmp_path / "old-patches"
    shutil.copytree(before / "patches", keep)
    ported = _project(tmp_path / "p2", NEW_A, NEW_B, last="C_NEW")                       # same changes, new anchors
    r = kmigrate.verify(ported, new, keep)
    assert r["ok"] and r["same_lines"] == 2 and r["rebuilt_identical"] == 2, r["findings"]
    # a port that also changes something else is not the same change
    sneaky = ported / "b.c"
    sneaky.write_text(sneaky.read_text().replace("GORILLA_SONY)", "GORILLA_SONY_MORE)"), encoding="utf-8", newline="\n")
    (ported / "patches" / "b.c.patch").write_text(_patch("drv/b.c", NEW_B, sneaky.read_text()), encoding="utf-8",
                                                  newline="\n")
    bad = kmigrate.verify(ported, new, keep)
    assert not bad["ok"] and "b.c.patch changes different lines" in bad["findings"][0]


def test_verify_catches_a_shipped_file_the_patches_do_not_explain(tmp_path, trees):
    old, new = trees
    keep = tmp_path / "old-patches"
    shutil.copytree(_project(tmp_path, OLD_A, OLD_B) / "patches", keep)
    ported = _project(tmp_path / "p2", NEW_A, NEW_B, last="C_NEW")
    (ported / "a.c").write_text(NEW_A.replace("line 10\n", "line 10 GORILLA\n") + "unexplained\n", encoding="utf-8")
    r = kmigrate.verify(ported, new, keep)
    assert not r["ok"] and r["rebuilt_identical"] == 1 and "do not rebuild the shipped ['a.c']" in r["findings"][-1]
