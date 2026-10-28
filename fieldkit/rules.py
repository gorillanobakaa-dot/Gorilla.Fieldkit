# Version: 1.0.0 · updated 26-10-28-17-50
"""fieldkit rules: the one core (rules/CORE.md) in every agent's rules file, and every rules file small
(HARNESS-PLAN R2, 2026-10-28).

The global rules files were sent on every call: CLAUDE.md 13.9 KB, GEMINI.md 32.4 KB, AGENTS.md 24.3 KB, repeating
and contradicting each other. rules/CORE.md is the one source; each agent's global file carries it between its
markers, and each project keeps only its own rules.

    fieldkit rules check [--repo-only] [--json]   size, the core block present and current, no core text repeated
    fieldkit rules install [--file F ...]         put the current core block into each global file

install only inserts or replaces the block between the markers. The rest of a file is the owner's: it is never
trimmed here (overwriting is deleting), so a file can still fail the size check after install. Each file is copied
to state/rules-backup/ before it is changed.
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from .core.settings import state_root

ROOT = Path(__file__).resolve().parents[1]
CORE = "rules/CORE.md"
START = "<!-- fieldkit-core-rules"
END = "<!-- /fieldkit-core-rules -->"
LIMIT = 8 * 1024          # bytes: the core (about 4.7 KB) plus a short section of the agent's or project's own
REPO_FILES = ("AGENTS.md",)


def global_files(home=None):
    """-> {agent: path} of the global rules files every agent loads (whether or not they exist here)."""
    home = Path(home or Path.home())
    return {"claude-code": home / ".claude" / "CLAUDE.md", "codex": home / ".codex" / "AGENTS.md",
            "gemini": home / ".gemini" / "GEMINI.md"}


def core_block(root=None):
    text = (Path(root or ROOT) / CORE).read_text(encoding="utf-8")
    a, b = text.find(START), text.find(END)
    if a < 0 or b < a:
        raise ValueError(f"{CORE} has no {START} ... {END} block")
    return text[a:b + len(END)]


def block_of(text):
    """-> the core block a file carries, or None."""
    a = text.find(START)
    b = text.find(END, a + 1) if a >= 0 else -1
    return text[a:b + len(END)] if a >= 0 and b > a else None


def _core_lines(block):
    """The core's distinctive lines: long enough that a copy of one is a repeat, not a coincidence."""
    return [x.strip() for x in block.splitlines() if len(x.strip()) >= 60 and not x.strip().startswith("<!--")]


def check_file(path, block, kind):
    """kind "global": must carry the current block. kind "project": must not repeat the core. -> {path, ok, problems}"""
    p = Path(path)
    if not p.is_file():
        return {"path": str(p), "kind": kind, "ok": True, "bytes": 0, "problems": [], "missing": True}
    text = p.read_text(encoding="utf-8", errors="replace")
    size, problems = len(text.encode("utf-8")), []
    if size > LIMIT:
        problems.append(f"{size} bytes, over the {LIMIT} limit")
    have = block_of(text)
    if kind == "global":
        if have is None:
            problems.append("no core block (fieldkit rules install puts it in)")
        elif have != block:
            problems.append("the core block is not the current rules/CORE.md (fieldkit rules install updates it)")
    else:
        outside = text.replace(have, "") if have else text
        rep = [x for x in _core_lines(block) if x in outside]
        if have or rep:
            problems.append(f"repeats the core ({'its block' if have else f'{len(rep)} line(s)'}); "
                            "a project file holds only its own rules")
    return {"path": str(p), "kind": kind, "ok": not problems, "bytes": size, "problems": problems}


def check(root=None, home=None, repo_only=False):
    root = Path(root or ROOT)
    block = core_block(root)
    out = [check_file(root / f, block, "project") for f in REPO_FILES]
    if not repo_only:
        out += [check_file(p, block, "global") for p in global_files(home).values()]
    return out


def install(files, root=None, backup_dir=None):
    """Put the current core block into each file: replace the block it carries, else put it first. -> [what was done]"""
    block = core_block(root)
    bdir = Path(backup_dir or (state_root(Path(root or ROOT)) / "state" / "rules-backup"))
    out = []
    for f in files:
        p = Path(f)
        text = p.read_text(encoding="utf-8") if p.is_file() else None
        if text is None:
            out.append({"path": str(p), "done": "missing: not created (an agent that is not installed here)"})
            continue
        have = block_of(text)
        new = text.replace(have, block) if have else block + "\n\n" + text
        if new == text:
            out.append({"path": str(p), "done": "already current"})
            continue
        bdir.mkdir(parents=True, exist_ok=True)
        b = bdir / f"{p.parent.name.lstrip('.')}-{p.name}.{time.strftime('%y-%m-%d-%H-%M-%S')}"
        shutil.copy2(p, b)
        p.write_text(new, encoding="utf-8")
        if block_of(p.read_text(encoding="utf-8")) != block:     # read back: the artifact, not the write call
            raise OSError(f"{p}: the block did not land")
        out.append({"path": str(p), "done": ("updated" if have else "inserted") + f" (backup {b})",
                    "bytes": len(new.encode("utf-8"))})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit rules", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "install"])
    ap.add_argument("--repo-only", action="store_true", help="check: only this repository's rules files (pre-commit)")
    ap.add_argument("--file", action="append", default=[], help="install: a rules file (default: every global one)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "install":
        files = a.file or [p for p in global_files().values()]
        r = install(files)
        print(json.dumps(r, indent=1) if a.json else "\n".join(f"{x['path']}: {x['done']}" for x in r))
        print("NEXT: fieldkit rules check")
        return 0
    r = check(repo_only=a.repo_only)
    bad = [x for x in r if not x["ok"]]
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        for x in r:
            state = "missing" if x.get("missing") else ("ok" if x["ok"] else "FAIL")
            print(f"{state:<7} {x['kind']:<7} {x['bytes']:>6} B  {x['path']}" + "".join(f"\n        {m}" for m in x["problems"]))
        print("NEXT: " + ("nothing" if not bad else
                          "fieldkit rules install (the block); trim the rest by hand, keeping what goes (overwriting is deleting)"))
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
