# Version: 1.0.0 · updated 26-10-15-17-56
"""package stage: dangling dist/bin links of files removed from the tree are moved aside before rsync, anything else
is refused; a .deb older than the build is never this build's package (2026-10-15: rsync code 23, then a stale OK)."""
import os
import time
from types import SimpleNamespace

import pytest

from fieldkit.build import firefox_linux as fl

pytestmark = pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="dist/bin links are a Linux build")


def _tree(tmp_path):
    src = tmp_path / "src"
    dist = src / "obj-x86_64-pc-linux-gnu" / "dist" / "bin"
    (src / "browser").mkdir(parents=True)
    dist.mkdir(parents=True)
    kept = src / "browser" / "kept.css"
    kept.write_text("a{}")
    (dist / "kept.css").symlink_to(kept)
    (dist / "aiWindow.ftl").symlink_to(src / "browser" / "aiWindow.ftl")        # removed from the tree
    (dist / "elsewhere.css").symlink_to(tmp_path / "outside" / "x.css")         # not in the tree: a person looks
    return src, dist


def test_links_of_removed_files_are_told_apart_from_other_dangling_links(tmp_path):
    src, dist = _tree(tmp_path)
    gone, other = fl.dangling_links(dist, src)
    assert [p.name for p, _ in gone] == ["aiWindow.ftl"] and [p.name for p, _ in other] == ["elsewhere.css"]


def test_prune_moves_to_the_trash_never_deletes(tmp_path):
    src, dist = _tree(tmp_path)
    (dist / "elsewhere.css").unlink()
    moved, refused = fl.prune_dangling(dist, src, trash=tmp_path / "trash")
    assert len(moved) == 1 and not refused
    assert not (dist / "aiWindow.ftl").is_symlink() and (dist / "kept.css").exists()
    assert list((tmp_path / "trash").rglob("aiWindow.ftl"))[0].is_symlink()


def test_package_stage_refuses_links_it_cannot_explain(tmp_path):
    src, dist = _tree(tmp_path)
    harness = tmp_path / "h"
    (harness / "scripts").mkdir(parents=True)
    (harness / "scripts" / "build_deb.sh").write_text("exit 0")
    ctx = SimpleNamespace(vars={"harness": str(harness), "src": str(src), "release": str(tmp_path / "rel")})
    r = fl.stage_package(ctx, run=lambda *a, **k: (0, ""))
    assert not r["ok"] and "elsewhere.css" in r["detail"]


def test_a_deb_older_than_the_build_is_refused(tmp_path):
    src, dist = _tree(tmp_path)
    rel = tmp_path / "rel"
    rel.mkdir()
    deb = rel / "gorilla-unleashed_158.0-1_amd64.deb"
    deb.write_bytes(b"x")
    old = time.time() - 3600
    os.utime(deb, (old, old))
    (dist / "libxul.so").write_bytes(b"y")
    ctx = SimpleNamespace(vars={"version": "158.0", "release": str(rel), "src": str(src)})
    r = fl.verify_package(ctx, run=lambda *a, **k: (0, "158.0-1"))
    assert not r["ok"] and "OLDER than the build" in r["detail"]
