# Version: 1.0.0 · updated 26-10-15-02-15
"""Upstream API changes that break ported code, found and rewritten before the compiler meets them.

    fieldkit build-harness api-drift TASK [--apply]
    preflight row `api-drift` (BLOCKER) before every Linux build

Born 2026-10-15: the second Linux 158 build ran 17 minutes and stopped on six calls to
FindInReadable(pattern, source, nsCaseInsensitiveCStringComparator), an overload 158 removed. A port step checks that
a hunk lands, not that the functions it calls still exist; every Firefox release renames something, so each one
cost a build until now.

The knowledge is data, build/api-drift/firefox.yaml, one row per change (see its header). This module only:
  scan(src)          rows that apply to this tree (applies_when) x files the port changed (diff from the pristine
                     root commit; C/C++ only) -> [{rule, file, count, lines}]
  apply(src, found)  rewrites those files with the row's replace template and checkpoints the work tree
Deterministic: regex in, regex out, no model; a file is rewritten only by a row whose new API the tree has.
"""
import re
import subprocess
from pathlib import Path

from ..core import settings

TABLE = Path(__file__).parent / "api-drift" / "firefox.yaml"
SOURCES = (".cpp", ".cc", ".h", ".hpp", ".mm", ".c")


def rules(path=TABLE):
    data = settings.read_file(path) if Path(path).is_file() else {}
    out = []
    for r in (data or {}).get("rules", []):
        out.append(r | {"_rx": re.compile(r["pattern"], re.S),
                        "_when": re.compile(r["applies_when"]["has"], re.S)})
    return out


def _git(src, *args):
    r = subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def changed_files(src):
    """Files the port changed: diff from the pristine root commit (the vaulted release) to the working tree."""
    rc, root = _git(src, "rev-list", "--max-parents=0", "HEAD")
    if rc != 0 or not root.strip():
        return None
    _, names = _git(src, "diff", "--name-only", root.split()[0])
    return [f for f in names.split() if f.endswith(SOURCES) and (Path(src) / f).is_file()]


def active(src, table=None):
    """The rows whose new API this tree has."""
    out = []
    for r in table if table is not None else rules():
        f = Path(src) / r["applies_when"]["file"]
        if f.is_file() and r["_when"].search(f.read_text(encoding="utf-8", errors="replace")):
            out.append(r)
    return out


def scan(src, table=None):
    files = changed_files(src)
    if files is None:
        return {"ok": False, "error": f"{src} is not a work tree with a pristine root commit", "found": [], "rules": 0}
    rows = active(src, table)
    found = []
    for f in files:
        text = (Path(src) / f).read_text(encoding="utf-8", errors="replace")
        for r in rows:
            hits = list(r["_rx"].finditer(text))
            if hits:
                found.append({"rule": r["id"], "since": r.get("since"), "file": f, "count": len(hits),
                              "lines": [text.count("\n", 0, m.start()) + 1 for m in hits]})
    return {"ok": not found, "found": found, "rules": len(rows), "files": len(files)}


def apply(src, found, table=None):
    by_id = {r["id"]: r for r in (table if table is not None else rules())}
    done = []
    for f in sorted({x["file"] for x in found}):
        p = Path(src) / f
        text = p.read_text(encoding="utf-8")
        n_total = 0
        for x in (x for x in found if x["file"] == f):
            text, n = by_id[x["rule"]]["_rx"].subn(by_id[x["rule"]]["replace"], text)
            n_total += n
        p.write_text(text, encoding="utf-8")
        done.append(f"{f}: {n_total} rewritten")
    if done:
        _git(src, "add", "--", *sorted({x["file"] for x in found}))
        _git(src, "commit", "-q", "-m", "api-drift: upstream API changes rewritten\n\n" + "\n".join(
            f"{x['file']}: {x['rule']} x{x['count']} (since {x['since']})" for x in found))
    return done


def lines(r):
    if r.get("error"):
        return [f"API-DRIFT: {r['error']}"]
    out = [f"  {x['rule']} (since {x['since']}): {x['file']} x{x['count']}, line(s) {', '.join(map(str, x['lines']))}"
           for x in r["found"]]
    out.append(f"API-DRIFT {'OK' if r['ok'] else 'FOUND'}: {r['rules']} known change(s) apply to this tree, "
               f"{r.get('files', 0)} ported C/C++ file(s) scanned, {sum(x['count'] for x in r['found'])} old call(s)")
    return out
