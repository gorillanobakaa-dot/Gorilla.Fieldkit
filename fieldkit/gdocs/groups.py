"""The group map (docs/groups.yaml), coverage, source hashes, staging and STATE.json.

A group is a named set of source files under fieldkit/ that is documented as
one unit. Its rendered documents live in docs/dual-track/<group>/, next to a
STATE.json (committed) that records the sha256 of every source file at the last
render. A group is stale when that record is missing or no longer matches the
files on disk.
"""
import fnmatch
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from ..core import settings

ROOT = settings.ROOT
PKG = ROOT / "fieldkit"
DOCS = ROOT / "docs"
GROUPS_FILE = DOCS / "groups.yaml"
OUT = DOCS / "dual-track"
MEASUREMENTS = OUT / "MEASUREMENTS.md"
STAGING = DOCS / "_staging"
NOT_REQUIRED = {"__init__.py", "__main__.py"}
NAME_RX = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class GroupError(ValueError):
    pass


def load(path=None):
    """-> list of {name, title, sources} in file order."""
    data = settings.read_file(path or GROUPS_FILE) or {}
    groups = data.get("groups") or []
    seen = set()
    for g in groups:
        if not g.get("name") or not g.get("sources"):
            raise GroupError(f"{path or GROUPS_FILE}: every group needs 'name' and 'sources'")
        if not NAME_RX.match(str(g["name"])):
            # the name becomes a folder that stage() deletes and recreates: never a path
            raise GroupError(f"{path or GROUPS_FILE}: group name {g['name']!r} must be lower-case letters, "
                             f"digits and hyphens")
        if g["name"] in seen:
            raise GroupError(f"{path or GROUPS_FILE}: group {g['name']!r} is listed twice")
        seen.add(g["name"])
        g.setdefault("title", g["name"])
    return groups


def pick(groups, names):
    """The named groups (all when names is empty); unknown names are an error, not a silent skip."""
    if not names:
        return list(groups)
    by = {g["name"]: g for g in groups}
    bad = [n for n in names if n not in by]
    if bad:
        raise GroupError(f"no group {', '.join(bad)}; groups are {', '.join(by)}")
    return [by[n] for n in names]


def all_py(pkg=None):
    """Every .py under fieldkit/, as POSIX paths relative to it, sorted."""
    pkg = Path(pkg or PKG)
    return sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*.py")
                  if "__pycache__" not in p.parts)


def files_of(group, pkg=None):
    """The group's files (relative POSIX paths), matched against the real tree."""
    rels = all_py(pkg)
    out = []
    for pat in group["sources"]:
        out += [r for r in rels if _match(r, pat) and r not in out]
    return sorted(out)


def _match(rel, pat):
    """Glob per path segment: 'core/*.py' matches core/host.py, never core/sub/x.py."""
    a, b = rel.split("/"), pat.split("/")
    return len(a) == len(b) and all(fnmatch.fnmatchcase(x, y) for x, y in zip(a, b))


def coverage(groups, pkg=None):
    """-> {ok, orphans, duplicates, empty}. Every required .py in exactly one group."""
    owner = {}
    dup = {}
    empty = []
    for g in groups:
        fs = files_of(g, pkg)
        if not fs:
            empty.append(g["name"])
        for f in fs:
            if f in owner:
                dup.setdefault(f, [owner[f]]).append(g["name"])
            else:
                owner[f] = g["name"]
    orphans = [r for r in all_py(pkg) if r not in owner and r.rsplit("/", 1)[-1] not in NOT_REQUIRED]
    return {"ok": not orphans and not dup and not empty, "orphans": orphans,
            "duplicates": dup, "empty_groups": empty}


def assign_orphans(path=None, pkg=None):
    """Put every module that is in no group into the group that already holds most of its folder's modules, and write
    docs/groups.yaml. -> {"added": [(rel, group)], "refused": [(rel, why)]}.

    Owner, 2026-10-14: a new tool must never be blocked by the docs map. fluentcheck.py (a new build-harness module)
    was refused by the pre-commit hook because no group named it. The rule is deterministic: the folder's majority
    group wins; a tie, or a folder with no grouped module yet, is refused with the reason (nothing is guessed). The
    pre-commit hook runs this before the tests. Only an explicit `sources` list entry is added; globs are left alone."""
    path = Path(path or GROUPS_FILE)
    groups = load(path)
    cov = coverage(groups, pkg)
    added, refused = [], []
    text = path.read_text(encoding="utf-8")
    for rel in cov["orphans"]:
        folder = rel.rsplit("/", 1)[0] + "/" if "/" in rel else ""
        counts = {}
        for g in groups:
            n = sum(1 for f in files_of(g, pkg) if (f.rsplit("/", 1)[0] + "/" if "/" in f else "") == folder)
            if n:
                counts[g["name"]] = n
        if not counts:
            refused.append((rel, f"no group holds a module of {folder or 'the top level'} yet: add it to docs/groups.yaml by hand"))
            continue
        best = sorted(counts.items(), key=lambda kv: -kv[1])
        if len(best) > 1 and best[0][1] == best[1][1]:
            refused.append((rel, f"tie between groups {best[0][0]} and {best[1][0]}: add it by hand"))
            continue
        name = best[0][0]
        lines = text.split("\n")
        head = next((i for i, l in enumerate(lines) if re.match(rf"^\s*-\s*name:\s*{re.escape(name)}\s*$", l)), None)
        src = next((i for i in range(head + 1, len(lines)) if lines[i].strip() == "sources:"), None) if head is not None else None
        if src is None:
            refused.append((rel, f"group {name} keeps its sources on one line: add it by hand"))
            continue
        k = src + 1
        indent = lines[k][:len(lines[k]) - len(lines[k].lstrip())] if k < len(lines) else "      "
        last = k
        while k < len(lines) and lines[k].startswith(indent + "- "):
            if lines[k].strip()[2:] <= rel:
                last = k + 1
            k += 1
        lines.insert(last, f"{indent}- {rel}")
        text = "\n".join(lines)
        added.append((rel, name))
    if added:
        path.write_text(text, encoding="utf-8", newline="\n")
    return {"added": added, "refused": refused}


def staged_name(rel):
    """build/kernel.py -> build__kernel.py; top-level files keep their name.

    Flat on purpose: dual_track.py skips any folder named build (its IGNORE_DIRS), so a nested
    build/kernel.py would silently drop out of the documentation."""
    return rel.replace("/", "__")


def sha256(path):
    """Of the content with CRLF read as LF, so a Windows checkout with other line-ending
    settings does not look stale (the repository stores LF: .gitattributes eol=lf)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def hashes(group, pkg=None):
    pkg = Path(pkg or PKG)
    return {rel: sha256(pkg / rel) for rel in files_of(group, pkg)}


def stage(group, staging=None, pkg=None):
    """Copy the group's files flat into docs/_staging/<group>/ (emptied first). -> folder."""
    pkg = Path(pkg or PKG)
    dest = Path(staging or STAGING) / group["name"]
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for rel in files_of(group, pkg):
        shutil.copyfile(pkg / rel, dest / staged_name(rel))
    return dest


def out_dir(group, out=None):
    return Path(out or OUT) / group["name"]


def state_path(group, out=None):
    return out_dir(group, out) / "STATE.json"


def read_state(group, out=None):
    try:
        return json.loads(state_path(group, out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(group, data, out=None):
    p = state_path(group, out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return p


def status(group, out=None, pkg=None):
    """-> {state: fresh|stale|never, changed, added, removed, rendered}."""
    st = read_state(group, out)
    now = hashes(group, pkg)
    if not st.get("sources"):
        return {"state": "never", "changed": [], "added": sorted(now), "removed": [], "rendered": None}
    old = st["sources"]
    changed = sorted(f for f in now if f in old and old[f] != now[f])
    added = sorted(f for f in now if f not in old)
    removed = sorted(f for f in old if f not in now)
    return {"state": "stale" if changed or added or removed else "fresh", "changed": changed,
            "added": added, "removed": removed, "rendered": st.get("rendered")}


def today():
    return time.strftime("%Y-%m-%d")
