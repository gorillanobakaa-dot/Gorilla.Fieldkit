"""sweep - the weekly walk through the scratch folders: what is not in the harness yet.

    fieldkit sweep [--json]            every folder and loose script under the scratch
                                       roots, with its registry status and age
    fieldkit sweep --run-tests         also run the tests of the registered ones

WHY
    Documents/Scripts is where a model dumps the scripts it writes during a
    session so they do not disappear with the session. The arrangement was that
    once a week a model walks that folder, improves what is worth keeping, turns
    it into deterministic tools and registers them here. Measured 2026-10-12:
    that walk had happened once (the gather of 2026-09-29) and the rule was
    written nowhere, so eleven items had sat unregistered for up to six weeks.
    A rule that nothing checks is a wish. This is the check.

ROOTS
    ${DOCUMENTS}/Scripts always, plus DEFAULT_ROOTS (${FIELDKIT}/local without its kits and tests, and the scratch root's
    scripts folder); more from fieldkit.local.json. An ignore entry is a name or {"name": N, "why": REASON}:
        "sweep": {"roots": ["${DOCUMENTS}/Gorilla.Opencode/scripts/model-tests"],
                  "ignore": ["Fieldkit-review"]}
    The Fieldkit folder itself is never swept (it is the harness, not scratch).

STATUS, per item (a sub-folder, or a loose script at the root)
    (a folder holding .git - a checkout or worktree - is not an item)
    untracked   no registry card points inside it           -> register it or retire it
    no-test     a card exists but has test: null            -> write the test
    untested    a card with a test that has never been run  -> fieldkit tools check --run-tests --id ID
    stale       the tool changed since its test last passed -> run the test again
    ok          carded, tested, unchanged since
    leftover    a file that is not a script (a .patch, a .json) at the root -> keep, move or delete, by hand

Exit 3 when anything is untracked or without a test; the sweep is not done until
the list is empty or every remaining item is a decision the owner has taken.
"""
import os
import sys
import time
from pathlib import Path

from ..core import settings
from . import cards as cardmod
from . import registry

SCRIPT_EXT = {".py", ".ps1", ".cmd", ".bat", ".sh"}
DOC_EXT = {".md", ".txt", ".rst"}
ALWAYS_IGNORE = {"Fieldkit", "__pycache__", ".git", ".pytest_cache"}
#: roots every machine sweeps besides ${DOCUMENTS}/Scripts: (path, folders under it that are not scripts - they hold the
#: harness's own kits, recipes, skills and tests, each registered another way). A missing root is skipped.
DEFAULT_ROOTS = (("${FIELDKIT}/local", ("kits", "tests", "recipes", "skills", "icons", "profilefix")),
                 ("${SCRATCH}/scripts", ()))
ADOPTED = ".adopted"                 # the marker adopt and recipes capture leave (fieldkit.desk.adopt.MARKER)


def _norm(p):
    s = str(Path(p).resolve()) if p else ""
    return s.lower() if sys.platform == "win32" else s


def roots(local=None):
    local = settings.local_settings() if local is None else local
    out = [Path(settings.expand("${DOCUMENTS}")) / "Scripts"]
    for tmpl, _ in DEFAULT_ROOTS:
        out.append(Path(_expand_default(tmpl)))
    for r in (local.get("sweep", {}) or {}).get("roots", []) or []:
        out.append(Path(settings.expand(r, local)))
    return out


def _expand_default(tmpl):
    if "${SCRATCH}" in tmpl:
        from ..scratch import root as scratch_root
        tmpl = tmpl.replace("${SCRATCH}", str(scratch_root()))
    return settings.expand(tmpl)


def _default_root(root):
    """-> the folders to skip under `root` when it is one of DEFAULT_ROOTS, else None."""
    for tmpl, skip in DEFAULT_ROOTS:
        if _norm(_expand_default(tmpl)) == _norm(root):
            return set(skip)
    return None


def _skip_for(root):
    return _default_root(root) or set()


def ignore_reasons(local=None):
    """-> {name: reason} from sweep.ignore: an entry is a name (no reason: old style) or {"name": N, "why": W}."""
    local = settings.local_settings() if local is None else local
    out = {}
    for e in (local.get("sweep", {}) or {}).get("ignore", []) or []:
        if isinstance(e, dict):
            out[str(e.get("name"))] = str(e.get("why") or "")
        else:
            out[str(e)] = ""
    return out


def ignored(local=None):
    return set(ALWAYS_IGNORE) | set(ignore_reasons(local))


def _newest_mtime(path, limit=2000):
    newest = 0.0
    n = 0
    if path.is_file():
        return path.stat().st_mtime
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_IGNORE]
        for f in filenames:
            try:
                newest = max(newest, (Path(dirpath) / f).stat().st_mtime)
            except OSError:
                pass
            n += 1
            if n >= limit:
                return newest
    return newest


def items(root, ignore):
    """The things a person would name when asked 'what is in this folder'."""
    out = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.name in ignore or entry.name.startswith("."):
            continue
        if entry.is_file() and entry.name.endswith(ADOPTED):
            continue                   # the marker beside an adopted loose script, not an item of its own
        if entry.is_dir():
            if (entry / ".git").exists():
                continue               # a git checkout or worktree is a project under git, not a script
            out.append({"name": entry.name, "path": str(entry), "kind": "folder"})
        elif entry.suffix.lower() in SCRIPT_EXT:
            out.append({"name": entry.name, "path": str(entry), "kind": "script"})
        elif entry.suffix.lower() not in DOC_EXT:
            out.append({"name": entry.name, "path": str(entry), "kind": "leftover"})
    return out


def _cards_inside(item_path, tools):
    ip = _norm(item_path)
    found = []
    for t in tools:
        for key in ("path", "test"):
            v = t.get(key)
            if not v:
                continue
            parts = v if isinstance(v, list) else [v]
            for part in parts:
                s = _norm(part) if isinstance(part, str) and ("/" in part or "\\" in part) else ""
                if s and (s == ip or s.startswith(ip + os.sep)):
                    found.append(t)
                    break
            else:
                continue
            break
    return found


def status_of(item, tools, results):
    if item["kind"] == "folder" and (Path(item["path"]) / ADOPTED).is_file():
        return "adopted", []           # moved into Fieldkit; this old copy goes once nothing runs from it
    if item["kind"] == "script" and Path(item["path"] + ADOPTED).is_file():
        return "adopted", []           # a loose script: its marker sits beside it (adopt, recipes capture)
    if item["kind"] == "leftover":
        return "leftover", []
    cards = _cards_inside(item["path"], tools)
    if not cards:
        return "untracked", []
    ids = [c["id"] for c in cards]
    with_test = [c for c in cards if c.get("test")]
    if not with_test:
        return "no-test", ids
    worst = "ok"
    for c in with_test:
        r = results.get(c["id"])
        if not r:
            worst = "untested" if worst == "ok" else worst
            continue
        if not r.get("ok"):
            return "stale", ids
        cur = cardmod.file_sha(c.get("path")) if c.get("path") and Path(c["path"]).is_file() else None
        if r.get("sha256") and cur and r["sha256"] != cur:
            return "stale", ids
    # carded and tested, but still in the dumping ground: not in the harness yet (owner, 2026-10-13)
    return ("adopt" if worst == "ok" else worst), ids


def scan(roots_=None, tools=None, results=None, ignore=None, now=None):
    """Pure: given roots, the registry and the recorded test results, the rows."""
    roots_ = roots() if roots_ is None else roots_
    tools = registry.load(strict=False) if tools is None else tools
    results = cardmod.test_results() if results is None else results
    ignore = ignored() if ignore is None else ignore
    now = time.time() if now is None else now
    rows = []
    for root in roots_:
        for it in items(Path(root), ignore | _skip_for(root)):
            if it["kind"] == "leftover" and _default_root(root) is not None:
                continue               # local/ holds config data (yaml, json) beside its scripts: not a leftover
            st, ids = status_of(it, tools, results)
            age = int((now - _newest_mtime(Path(it["path"]))) // 86400) if Path(it["path"]).exists() else None
            rows.append({"root": str(root), "name": it["name"], "kind": it["kind"], "status": st,
                         "cards": ids, "age_days": age, "path": it["path"]})
    order = {"untracked": 0, "no-test": 1, "stale": 2, "untested": 3, "leftover": 4, "ok": 5}
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["root"], r["name"].lower()))
    return rows


def unadopted(rows):
    """The scripts not in the harness: no card (untracked) or a card with no test."""
    return [r for r in rows if r["status"] in ("untracked", "no-test")]


def uncarded(path, roots_=None, tools=None, ignore=None):
    """-> the sweep status ("untracked" | "no-test") of a script a session wrote under a sweep root, else None (not a
    script, not under a root, ignored, adopted or carded). The honesty check names these (owner, 2026-11-01)."""
    p = Path(path)
    if p.suffix.lower() not in SCRIPT_EXT:
        return None
    roots_ = roots() if roots_ is None else roots_
    tools = registry.load(strict=False) if tools is None else tools
    ignore = ignored() if ignore is None else ignore
    pn = _norm(p)
    for root in roots_:
        rn = _norm(root)
        if not pn.startswith(rn + os.sep):
            continue
        top = Path(root) / pn[len(rn) + 1:].split(os.sep)[0]
        name = Path(p).resolve().relative_to(Path(root).resolve()).parts[0]
        if name in ignore or name in _skip_for(root) or name.startswith("."):
            return None
        kind = "folder" if top.is_dir() else "script"
        st, _ = status_of({"name": name, "path": str(top), "kind": kind}, tools, {})
        return st if st in ("untracked", "no-test") else None
    return None


def summary(rows):
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts


def lines(rows):
    counts = summary(rows)
    out = []
    for r in rows:
        age = "" if r["age_days"] is None else f"{r['age_days']:>4}d"
        ids = ",".join(r["cards"])
        out.append(f"{r['status']:<10} {age:>5}  {r['name']:<34} {ids[:60]}")
    out.append("")
    out.append("  ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing under the sweep roots")
    todo = len(unadopted(rows))
    if todo:
        out.append(f"NEXT: {todo} item(s) have no card or no test: card and test each (local/tools.yaml), or add it to sweep.ignore in fieldkit.local.json with a reason.")
    if counts.get("adopt"):
        out.append(f"NEXT: {counts['adopt']} tested tool(s) still live in the Scripts dumping ground: `fieldkit adopt NAME` (plan) then `--apply` moves each into local/kits and repoints its shortcuts, skills and memory.")
    if counts.get("adopted"):
        out.append("NEXT: old copies of adopted tools wait for their programs to stop: `fieldkit adopt --finish --apply` after a restart.")
    if not (todo or counts.get("adopt")):
        out.append("NEXT: Scripts holds nothing the harness lacks. Run `fieldkit sweep --run-tests` to refresh stale and untested ones.")
    return out


def exit_code(rows):
    c = summary(rows)
    return 3 if c.get("untracked") or c.get("no-test") or c.get("adopt") else 0
