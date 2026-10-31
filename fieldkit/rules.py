# Version: 1.1.0 · updated 26-10-31-19-00
"""fieldkit rules: the one core (rules/CORE.md, generated from rules/rules.yaml) in every agent's rules file, and
every rules file small (HARNESS-PLAN R2, 2026-10-28; I-09 rules as data and lint, 2026-10-31).

The global rules files were sent on every call: CLAUDE.md 13.9 KB, GEMINI.md 32.4 KB, AGENTS.md 24.3 KB, repeating
and contradicting each other. rules/rules.yaml is the one source; rules/CORE.md is generated from it, each agent's
global file carries the core, its own tail and the lifecycle (and rotate) blocks between markers, and each project
keeps only its own rules.

    fieldkit rules check [--repo-only] [--json]   size, every block present and current, no core text repeated, lint
    fieldkit rules lint [--json]                  the rules data: drift, budget, duplicates, misplaced text,
                                                  contradictions, BOTTLENECKS (rules_lint.py)
    fieldkit rules build                          write rules/CORE.md from rules/rules.yaml
    fieldkit rules install [--file F ...]         every block into each global file (--file: the core block only)

install only inserts or replaces the blocks between their markers, and an agent's unmarked tail (the `# Agent`
section right after the core) once, when it is first marked. Anything else in a file is the owner's: it is never
trimmed here (overwriting is deleting). Each file is copied to state/rules-backup/ before it is changed.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from . import rules_lint as lint_mod
from .core.settings import state_root

ROOT = Path(__file__).resolve().parents[1]
CORE = "rules/CORE.md"
START = "<!-- fieldkit-core-rules"
END = "<!-- /fieldkit-core-rules -->"
LIMIT = 8 * 1024          # bytes: the core (about 5.7 KB) plus a short section of the agent's or project's own
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
    """The repository's rules files and the rules data (lint); without repo_only also every global file: size, the
    core, the agent's tail and its generated blocks current, and the shared blocks identical across the files."""
    root = Path(root or ROOT)
    block = core_block(root)
    out = [check_file(root / f, block, "project") for f in REPO_FILES]
    errs = [x for x in lint_mod.lint(root=root) if x["level"] == "error"]
    out.append({"path": str(root / lint_mod.DATA), "kind": "lint", "ok": not errs, "bytes": 0,
                "problems": [f"{x['kind']} {x['rule']}: {x['evidence']}" for x in errs]})
    if repo_only:
        return out
    files = global_files(home)
    for agent, p in files.items():
        r = check_file(p, block, "global")
        if not r.get("missing"):
            r["problems"] += [x["evidence"] for x in lint_mod.check_installed({agent: p}, root=root, skip=("core",))]
            r["ok"] = not r["problems"]
        out.append(r)
    same = [x for x in lint_mod.check_installed(files, root=root) if x["rule"] in lint_mod.BLOCKS]
    if same:
        out.append({"path": "global files", "kind": "same", "ok": False, "bytes": 0,
                    "problems": [f"{x['rule']}: {x['evidence']}" for x in same]})
    return out


def _backup(p, bdir):
    bdir.mkdir(parents=True, exist_ok=True)
    b = bdir / f"{p.parent.name.lstrip('.')}-{p.name}.{time.strftime('%y-%m-%d-%H-%M-%S')}"
    shutil.copy2(p, b)
    return b


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
        b = _backup(p, bdir)
        p.write_text(new, encoding="utf-8")
        if block_of(p.read_text(encoding="utf-8")) != block:     # read back: the artifact, not the write call
            raise OSError(f"{p}: the block did not land")
        out.append({"path": str(p), "done": ("updated" if have else "inserted") + f" (backup {b})",
                    "bytes": len(new.encode("utf-8"))})
    return out


def with_tail(text, tail):
    """-> text with the agent's tail block: replaced where marked; else the unmarked `# Agent` section right after the
    core block (up to the next marker) becomes the block; else the block goes right after the core."""
    b, e = lint_mod.TAIL_BEGIN, lint_mod.TAIL_END
    if b in text and e in text:
        head, rest = text.split(b, 1)
        return head + tail + rest.split(e, 1)[1]
    i = text.find(END)
    if i < 0:
        return text.rstrip("\n") + "\n\n" + tail + "\n"
    i += len(END)
    nxt = text.find("<!--", i)
    region = text[i:nxt if nxt >= 0 else len(text)]
    if region.strip().startswith("# "):
        return text[:i] + "\n\n" + tail + "\n\n" + (text[nxt:] if nxt >= 0 else "")
    return text[:i] + "\n\n" + tail + region


def install_all(home=None, root=None, backup_dir=None):
    """Every block into each global file that exists: core, the agent's tail, lifecycle, rotate (codex, gemini).
    One backup per changed file. -> [what was done]"""
    from .lifecycle import protocol
    from .tokens import rotate
    root = Path(root or ROOT)
    data = lint_mod.load(root)
    bdir = Path(backup_dir or (state_root(root) / "state" / "rules-backup"))
    out = []
    for agent, p in global_files(home).items():
        if not p.is_file():
            out.append({"path": str(p), "done": "missing: not created (an agent that is not installed here)"})
            continue
        before = p.read_bytes()
        nl = "\r\n" if b"\r\n" in before else "\n"
        text = before.decode("utf-8").replace("\r\n", "\n")
        have = block_of(text)
        block = core_block(root).replace("\r\n", "\n")
        text = text.replace(have, block) if have else block + "\n\n" + text
        text = with_tail(text, lint_mod.tail_block(data, agent))
        new = text.replace("\n", nl).encode("utf-8")
        if new != before:
            b = _backup(p, bdir)
            p.write_bytes(new)
        protocol.install_rule(p)
        if agent in ("codex", "gemini"):
            rotate.install_rule(p)
        after = p.read_bytes()
        bad = lint_mod.check_installed({agent: p}, data, root)
        if bad:                                           # read back: the artifact, not the write calls
            raise OSError(f"{p}: " + "; ".join(x["evidence"] for x in bad))
        out.append({"path": str(p), "bytes": len(after),
                    "done": "already current" if after == before else f"updated (backup {b})"
                    if new != before else "updated"})
    return out


def build(root=None):
    """Write rules/CORE.md from rules/rules.yaml. -> "same" | "written"."""
    root = Path(root or ROOT)
    p = root / CORE
    want = lint_mod.generate_core(lint_mod.load(root))
    if p.is_file() and p.read_text(encoding="utf-8").replace("\r\n", "\n") == want:
        return "same"
    p.write_bytes(want.encode("utf-8"))
    return "written"


def _lint_lines(found):
    out = [f"{'WARN ' if x['level'] == 'warning' else ''}{x['kind']:<13} {x['rule']}: {x['evidence']}" for x in found]
    errs = [x for x in found if x["level"] == "error"]
    bn = sum(1 for x in found if x["kind"] == "BOTTLENECK")
    out.append(f"{len(errs)} finding(s), {bn} bottleneck(s), {len(found) - len(errs)} warning(s)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit rules", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "install", "lint", "build"])
    ap.add_argument("--repo-only", action="store_true", help="check: only this repository's rules files (pre-commit)")
    ap.add_argument("--file", action="append", default=[], help="install: a rules file (the core block only)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "build":
        print(f"rules/CORE.md: {build()}\nNEXT: fieldkit rules lint")
        return 0
    if a.action == "lint":
        found = lint_mod.lint()
        print(json.dumps(found, indent=1) if a.json else "\n".join(_lint_lines(found)))
        bad = [x for x in found if x["level"] == "error"]
        print("NEXT: " + ("nothing" if not bad else "fix each finding in rules/rules.yaml, then fieldkit rules build"))
        return 3 if bad else 0
    if a.action == "install":
        r = install(a.file) if a.file else install_all()
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
                          "fieldkit rules install (the blocks); fieldkit rules lint (the data); trim the rest by hand, "
                          "keeping what goes (overwriting is deleting)"))
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
