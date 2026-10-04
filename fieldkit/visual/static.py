"""Layer 1, static: the ported tree, before anything is built. Fast enough for preflight and the build gate.

    ICON-002    every raster in the branding directory (png/bmp/jpg, every .ico frame, every PNG embedded in an
                SVG) is a real downsample of its master, with the vacuity guard (rasters.provenance)
    ICO-LADDER  every .ico carries the Windows ladder 16..256 (rasters.ICO_REQUIRED)
    ICON-001    a size slot's pixels match its name; no picture copied into several size slots
    ASSET-002   every Gorilla-touched chrome CSS rule that paints a raster gives it 2x headroom (css.py)
    CSS-004     no unclamped content: url() injection (css.py)
    BRAND-001..003  no Mozilla art, wordmark or branding text in the Gorilla branding directory (leftovers.py)

Gorilla-touched CSS = every .css added or modified since the upstream release commit (git diff), every .css that
says GORILLA, the branding CSS, the files the allowlist adds; minus the allowlist's exclusions (with reasons).
"""
import subprocess
from pathlib import Path

from . import allow as allowmod, chrome, css, leftovers, rasters

START_CSS = ("browser/base/content/aboutDialog.css", "browser/themes/shared/master-redirect.css",
             "browser/base/content/aboutRobots.css")


def _git(tree, *args):
    r = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode, r.stdout


def touched_css(tree, branding_rel, upstream_commit=None, allow=None):
    """-> (sorted relative paths, [how each source was found], problems)."""
    tree = Path(tree)
    found, how, problems = set(), [], []
    if (tree / ".git").exists():
        if upstream_commit:
            rc, out = _git(tree, "diff", "--name-only", "--diff-filter=AMR", upstream_commit, "--", "*.css")
            if rc == 0:
                got = {l.strip() for l in out.splitlines() if l.strip()}
                found |= got
                how.append(f"{len(got)} added/modified since {upstream_commit[:12]}")
            else:
                problems.append(f"git diff against {upstream_commit[:12]} failed")
        else:
            rc, out = _git(tree, "log", "--name-only", "--format=", "--grep=hand")
            got = {l.strip() for l in out.splitlines() if l.strip().endswith(".css")}
            found |= got
            how.append(f"{len(got)} from hand-edit commits (no upstream commit known)")
        rc, out = _git(tree, "grep", "-l", "GORILLA", "--", "*.css")
        got = {l.strip() for l in out.splitlines() if l.strip()}
        found |= got
        how.append(f"{len(got)} say GORILLA")
    else:
        problems.append("the tree is not a git repository: only the named start files and branding CSS are checked")
    found |= {p for p in START_CSS if (tree / p).is_file()}
    found |= {p.relative_to(tree).as_posix() for p in (tree / branding_rel).rglob("*.css")} if (tree / branding_rel).is_dir() else set()
    found |= set((allow or {}).get("css_include") or [])
    kept, dropped = [], []
    for rel in sorted(found):
        why = allowmod.excluded(rel, allow or {})
        (dropped if why else kept).append(rel)
    if dropped:
        how.append(f"{len(dropped)} excluded by the allowlist")
    return [r for r in kept if (tree / r).is_file()], how, problems


def _raster_items(tree, branding, owner_root, allow):
    items, slots, cache, sizes = [], [], {}, {}

    def master(rel):
        path, entry = allowmod.master_for(rel, allow, owner_root, tree)
        if path is None:
            return None, "no master is named for this file in the allowlist (masters:)" if entry is None else \
                "its master lives in the owner repository, which is not known for this task"
        key = str(path)
        if key not in cache:
            if not Path(path).is_file():
                cache[key] = (None, f"master not found: {path}")
            else:
                try:
                    cache[key] = (rasters.squarify(rasters.open_raster(path)), str(path))
                except Exception as e:  # noqa: BLE001
                    cache[key] = (None, f"master unreadable: {path}: {type(e).__name__}")
        return cache[key]

    def judge(item, im, rel):
        m, what = master(rel)
        if m is None:
            items.append(_item("ICON-002", item, "UNVERIFIABLE", what))
            return
        region, flip = allowmod.geometry(allowmod.master_for(rel, allow, owner_root, tree)[1])
        v, ev, nums = rasters.provenance(im, m, master_squared=True, cache=sizes, region=region, flip=flip)
        where = (f" region {list(region)}" if region else "") + (" mirrored" if flip else "")
        items.append(dict(_item("ICON-002", item, v, ev + f" [master {Path(what).name}{where}]"), numbers=nums))

    for f in sorted(Path(branding).rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(tree).as_posix()
        ext = f.suffix.lower()
        try:
            if ext == ".ico":
                frames = rasters.ico_frames(f)
                if not frames:
                    items.append(_item("ICO-LADDER", rel, "FAIL", "no frames"))
                    continue
                v, ev, extra = rasters.ico_ladder([s for s, _ in frames])
                items.append(dict(_item("ICO-LADDER", rel, v, ev), recommended_missing=extra))
                for size, im in frames:
                    judge(f"{rel}#{size}", im, rel)
            elif ext in rasters.RASTER_EXT:
                im = rasters.open_raster(f)
                judge(rel, im, rel)
                slots.append((rel, im))
            elif ext == ".svg":
                for i, im in rasters.svg_rasters(f):
                    judge(f"{rel}[embedded {i}]", im, rel)
        except Exception as e:  # noqa: BLE001 - an unreadable raster is a finding, never a skip
            items.append(_item("ICON-002", rel, "UNVERIFIABLE", f"unreadable: {type(e).__name__}: {e}"))
    for rel, v, ev in rasters.slot_rows(slots):
        items.append(_item("ICON-001", rel, v, ev))
    return items


def check(tree, branding_rel, owner_root=None, upstream_commit=None, allow=None):
    """Layer 1. -> summary dict (allow.summarise) plus css_files/how."""
    tree = Path(tree)
    allow = allow if allow is not None else allowmod.load()
    items = []
    if not branding_rel or not (tree / branding_rel).is_dir():
        items.append(_item("BRANDING", str(branding_rel), "UNVERIFIABLE",
                           f"no branding directory {branding_rel!r} in {tree} (mozconfig --with-branding)"))
        res = allowmod.summarise(items, allow, "static")
        res.update(css_files=[], how=[])
        return res
    branding = tree / branding_rel
    items += _raster_items(tree, branding, owner_root, allow)
    files, how, problems = touched_css(tree, branding_rel, upstream_commit, allow)
    index = chrome.Index(tree, branding_rel)
    for rel in files:
        items += css.check_file(tree / rel, rel, index, tree)
    items += leftovers.check(branding, tree)
    res = allowmod.summarise(items, allow, "static")
    res["problems"] += problems
    res["ok"] = res["ok"] and not problems
    res.update(css_files=files, how=how)
    return res


def _item(rule, item, verdict, evidence):
    return {"layer": "static", "rule": rule, "item": item, "verdict": verdict, "evidence": evidence}
