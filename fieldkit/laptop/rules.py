"""The rule files AI tools read, kept as one text: one source, every copy identical.

    fieldkit laptop rules check SOURCE COPY...     which copies differ from the source, or are missing
    fieldkit laptop rules sync SOURCE COPY...      write the source over each copy that differs (a .bak of each first)

Each AI tool reads its own file (CLAUDE.md, GEMINI.md, AGENTS.md, an IDE's own GEMINI.md ...). Edited one at a time,
they drift, and one model then follows rules the others no longer have. From the academic harness this generalises
(tools_audit.py sync-config, 2026-10-08): the source is the one a person edits; the copies are only ever written
from it. The list of copies is given each time, or kept in fieldkit.local.json
({"rules": {"source": "...", "copies": ["...", "..."]}}).
"""
import hashlib
import shutil
from pathlib import Path

from ..core import settings


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _paths(source, copies):
    cfg = settings.local_settings().get("rules") or {}
    source = source or cfg.get("source")
    copies = list(copies or cfg.get("copies") or [])
    if not source or not copies:
        raise ValueError("fieldkit laptop rules check SOURCE COPY... (or rules.source and rules.copies in "
                         "fieldkit.local.json)")
    return Path(settings.expand(source)).expanduser(), [Path(settings.expand(c)).expanduser() for c in copies]


def check(source=None, copies=None):
    src, cps = _paths(source, copies)
    if not src.is_file():
        raise FileNotFoundError(f"the source rule file is missing: {src}")
    want = _sha(src)
    rows = [{"copy": str(c), "state": "missing" if not c.is_file() else "same" if _sha(c) == want else "differs"}
            for c in cps]
    bad = [r for r in rows if r["state"] != "same"]
    return {"ok": not bad, "source": str(src), "copies": rows,
            "next": "nothing to do" if not bad else "fieldkit laptop rules sync (the source over each copy)"}


def sync(source=None, copies=None):
    src, cps = _paths(source, copies)
    written = []
    for c in cps:
        if c.is_file() and _sha(c) == _sha(src):
            continue
        if c.is_file():
            shutil.copy2(c, c.with_name(c.name + ".bak"))
        c.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, c)
        written.append(str(c))
    r = check(str(src), [str(c) for c in cps])
    r["written"] = written
    return r


def lines(r):
    return [f"source: {r['source']}"] + [f"  {x['state']:<8} {x['copy']}" for x in r["copies"]] + \
        ([f"written (each old copy kept as .bak): {', '.join(r['written'])}"] if r.get("written") else []) + \
        [f"NEXT: {r['next']}"]
