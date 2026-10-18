"""The fast loop as a gate (2026-10-18): a chrome change is tried on the last build's unpacked dist/bin before the
next compile, and judged by build-verify's own rows."""
import subprocess

from fieldkit.buildh import fastloop, probe


def _tree(tmp_path):
    w = tmp_path / "src"
    (w / "t").mkdir(parents=True)
    g = lambda *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                                  capture_output=True, text=True).stdout.strip()
    (w / "t" / "m.css").write_bytes(b"menupopup { background: Menu; }\n")
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "built")
    head = g("rev-parse", "HEAD")
    (w / "t" / "m.css").write_bytes(b"menupopup { background: #000; }\n")
    g("commit", "-qam", "theme")
    return w, head


def _dist(tmp_path):
    d = tmp_path / "obj" / "dist" / "bin" / "browser" / "chrome" / "browser" / "skin"
    d.mkdir(parents=True)
    (d / "m.css").write_bytes(b"menupopup { background: Menu; }\n")
    return tmp_path / "obj"


GOOD = ["UI|menu|gorilla-satellite-button|Off|16.75|rgb(0,255,255)|rgb(0,0,0)",
        "UI|popup|app menu|New Tab|16.75|rgb(0,255,255)|rgb(0,0,0)|rgb(101,86,140)|0|0",
        "UI|surface|app menu|1|0"]
BAD = ["UI|popup|tab right-click menu|Reload|18.31|rgb(21,20,26)|rgb(255,255,255)|rgb(255,255,255)|0|0",
       "UI|surface|tab right-click menu|1|0"]


def test_the_changed_css_is_tried_on_the_unpacked_build_and_a_bad_menu_fails_the_gate(tmp_path):
    w, head = _tree(tmp_path)
    obj = _dist(tmp_path)
    seen = {}

    def run(dist, js, files=None, **kw):
        seen.update(files or {})
        return {"done": True, "lines": BAD}
    rows = fastloop.rows("t", {"workdir": str(w)}, say=lambda m: None, run=run,
                         record={"head": head, "objdir": str(obj)})
    assert list(seen) == ["browser/chrome/browser/skin/m.css"]           # found by the OLD version's bytes
    assert open(seen["browser/chrome/browser/skin/m.css"], "rb").read() == b"menupopup { background: #000; }\n"
    assert not all(r["ok"] for r in rows)


def test_a_good_menu_passes_and_no_earlier_build_says_so(tmp_path):
    w, head = _tree(tmp_path)
    obj = _dist(tmp_path)
    rows = fastloop.rows("t", {"workdir": str(w)}, say=lambda m: None, run=lambda *a, **k: {"done": True, "lines": GOOD},
                         record={"head": head, "objdir": str(obj)})
    assert all(r["ok"] for r in rows), rows
    first = fastloop.rows("t", {"workdir": str(w)}, record={"head": head, "objdir": str(tmp_path / "none")})
    assert first[0]["ok"] and "no earlier unpacked build" in first[0]["evidence"]


def test_tree_since_maps_an_unpacked_build_by_content(tmp_path):
    w, head = _tree(tmp_path)
    obj = _dist(tmp_path)
    got, skipped = probe.tree_since(w, head, obj / "dist" / "bin", tmp_path / "out")
    assert list(got) == ["@browser/chrome/browser/skin/m.css"] and skipped == []
