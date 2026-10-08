"""build-harness weigh and probe tree-since (2026-10-08): the measuring is faked (no browser), what is tested is the
plumbing - one fresh browser per page and run, interleaved before/after, medians, the report - and that tree-since
maps a source change onto exactly the packaged members that hold the old bytes."""
import json
import subprocess
import zipfile

import pytest

from fieldkit.buildh import probe, weigh

LINES = {
    ("before", "about:newtab"): ["IMG|about:newtab|privilegedabout|chrome:\\\\branding\\content\\about-logo.svg|1400x1400|13000000",
                                 "IMG-PAGE|about:newtab|1|13000000", "IMG-CPU|about:newtab|1|1500"],
    ("after", "about:newtab"): ["IMG|about:newtab|privilegedabout|chrome:\\\\branding\\content\\about-logo.png|1400x1400|4700000",
                                "IMG-PAGE|about:newtab|1|4700000", "IMG-CPU|about:newtab|1|1200"],
}


def test_parse_reads_pictures_total_and_cpu():
    r = weigh.parse(LINES[("before", "about:newtab")] + ["IMG-PICTURE|about:newtab|x.png", "noise"])
    assert r == {"total": 13000000, "cpu": 1500.0,
                 "pictures": {"privilegedabout|chrome:\\\\branding\\content\\about-logo.svg|1400x1400": 13000000}}


def test_run_opens_every_page_in_a_fresh_browser_interleaved(tmp_path, monkeypatch):
    prepared, launched = [], []

    def prepare_copy(install, js, wait=15, omni=None, subs=None, say=print, **k):
        name = "after" if (omni or subs) else "before"
        app = tmp_path / name / "app"
        app.mkdir(parents=True)
        prepared.append((name, js, omni, subs))
        return tmp_path / name, app, (["m"] if name == "after" else [])

    def launch(app, url="about:blank", timeout=90):
        variant = app.parent.name
        page = json.loads((app / "gprobe-image-memory.json").read_text(encoding="utf-8"))["pages"][0]
        launched.append((variant, page))
        return {"lines": LINES[(variant, page)], "done": True, "seconds": 1}

    monkeypatch.setattr(probe, "prepare_copy", prepare_copy)
    monkeypatch.setattr(probe, "launch", launch)
    r = weigh.run("install", pages=("about:newtab",), reps=3, subs=[("a", "b")], say=lambda m: None)
    assert [p[0] for p in prepared] == ["before", "after"] and prepared[0][1] == "image-memory"
    assert launched == [("before", "about:newtab"), ("after", "about:newtab"), ("after", "about:newtab"),
                        ("before", "about:newtab"), ("before", "about:newtab"), ("after", "about:newtab")]
    s = r["summary"]["about:newtab"]
    assert s["before"]["total"] == 13000000 and s["after"]["total"] == 4700000 and s["after"]["cpu"] == 1200
    text = "\n".join(weigh.lines(r["summary"], ["a note"]))
    assert "about-logo.svg" in text and "about-logo.png" in text and "note: a note" in text
    assert "7.9" in text                                   # (13000000 - 4700000) / 1048576 = 7.9 MB saved
    assert not (tmp_path / "before").exists() and not (tmp_path / "after").exists()   # copies removed


def test_tree_since_maps_a_change_onto_the_members_with_the_old_bytes(tmp_path):
    w = tmp_path / "tree"
    (w / "css").mkdir(parents=True)
    (w / "css" / "page.css").write_bytes(b"a { color: red; }\n")
    (w / "css" / "built.scss").write_bytes(b"$x: 1;\n")
    g = lambda *a: subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True).stdout.decode().strip()
    g("init", "-q")
    g("add", ".")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "built")
    since = g("rev-parse", "HEAD")
    (w / "css" / "page.css").write_bytes(b"a { color: black; }\n")
    (w / "css" / "built.scss").write_bytes(b"$x: 2;\n")
    (w / "css" / "new.css").write_bytes(b"b {}\n")
    g("add", ".")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "change")
    inst = tmp_path / "install"
    (inst / "browser").mkdir(parents=True)
    with zipfile.ZipFile(inst / "omni.ja", "w") as z:
        z.writestr("chrome/skin/page.css", b"a { color: red; }\n")          # packaged as-is
        z.writestr("chrome/skin/built.css", b"compiled from scss\n")        # generated: no match
    with zipfile.ZipFile(inst / "browser" / "omni.ja", "w") as z:
        z.writestr("chrome/browser/page-copy.css", b"a { color: red; }\n")  # the same file packaged twice
    got, skipped = probe.tree_since(w, since, inst, tmp_path / "out")
    assert set(got) == {"omni.ja:chrome/skin/page.css", "browser/omni.ja:chrome/browser/page-copy.css"}
    assert all(p.read_bytes() == b"a { color: black; }\n" for p in got.values())
    assert dict(skipped) == {"css/built.scss": "not packaged as-is (preprocessed, generated or bundled at build time)",
                             "css/new.css": "added"}


def test_weigh_needs_a_change():
    import argparse
    from fieldkit.buildh import cli as bh, task
    a = argparse.Namespace(action="weigh", args=["firefox-157.0-truth", "pages=about:newtab"], task=None, note=None,
                           install_dir="C:/nowhere-but-given", json=False)
    with pytest.raises(task.Refused, match="needs a change"):
        bh.run(a, lambda o, f: None)
