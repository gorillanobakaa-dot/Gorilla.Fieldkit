# Version: 1.0.0 · updated 26-10-15-01-50
"""A ported change that compiles without its other half: find the missing half in the archived patch sets.

    fieldkit build-harness companions TASK [LOG] [--apply]

Born 2026-10-15 on the first Linux 158 build. 53 minutes in, PDMFactory.cpp failed: `unknown type name 'Purpose'`,
`use of undeclared identifier 'mForWebCodecs'`, `out-of-line definition of 'IsBlockedVideoCodec' does not match any
declaration`. The 157 patch set carries the .cpp half of the WebCodecs software-VP8 change (01.MEDIA) but not the
header that declares it, nor the DecoderAgent.cpp call that uses it: both live only in
patchset-155.0b4/16.SNAPSHOT.DELTA.2026-08-12. Every port step passed, because each step checks its own hunk; only the
compiler sees that a definition has no declaration.

Deterministic, no model:
  1. errors(log)      the clang lines naming a missing declaration -> {identifier: [file:line, ...]}
  2. candidates(...)  every per-file section of every *.patch under the harness folder whose ADDED lines contain one of
                      those identifiers as a whole word
  3. judge each with git: `apply --reverse --check` ok -> already in the tree; `apply --check` ok -> applies cleanly;
                      otherwise it does not fit. Per target file the newest clean candidate wins.
  4. --apply          applies the winners and commits one checkpoint naming each source patch. Nothing is applied
                      when nothing checks cleanly: a person reads the report.
The next export of the work tree carries the recovered halves into the shared patch set.
"""
import re
import subprocess
from pathlib import Path

PREFIX = re.compile(r"^\s*[\d:.]+\s+[EW]\s+")          # mach's "51:31.26 E " in front of every compiler line
LOC = re.compile(r"^(?P<file>/\S+?):(?P<line>\d+):\d+: error: (?P<msg>.*)$")
NAMED = [re.compile(p) for p in (
    r"unknown type name '(?P<id>\w+)'",
    r"use of undeclared identifier '(?P<id>\w+)'",
    r"out-of-line definition of '(?P<id>\w+)' does not match any declaration",
    r"no member named '(?P<id>\w+)' in '(?:[\w:]+)'",
    r"no type named '(?P<id>\w+)' in '(?:[\w:]+)'",
    r"no matching constructor for initialization of '(?P<id>[\w:]+)'",
)]
SKIP_DIRS = {".git", "obj-x86_64-pc-linux-gnu", "node_modules"}


def errors(text):
    """-> {identifier: ["file:line", ...]} for compiler errors that name a missing declaration."""
    out = {}
    for raw in text.splitlines():
        m = LOC.match(PREFIX.sub("", raw))
        if not m:
            continue
        for rx in NAMED:
            n = rx.search(m["msg"])
            if n:
                ident = n["id"].split("::")[-1]
                where = f"{m['file']}:{m['line']}"
                if where not in out.setdefault(ident, []):
                    out[ident].append(where)
                break
    return out


def sections(text):
    """Split a patch into per-file sections -> [(target path, section text)]."""
    lines = text.splitlines(keepends=True)
    out, cur, target = [], [], None
    for i, l in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if l.startswith("--- ") and nxt.startswith("+++ "):
            if cur and target:
                out.append((target, "".join(cur)))
            t = nxt[4:].strip().split("\t")[0]
            target = t[2:] if t.startswith(("a/", "b/")) else t
            cur = [l]
            continue
        if l.startswith("diff --git "):
            continue                                       # the --- / +++ pair carries everything git apply needs
        if target:
            cur.append(l)
    if cur and target:
        out.append((target, "".join(cur)))
    return out


def _patches(root):
    root = Path(root)
    for p in root.rglob("*.patch"):
        if not SKIP_DIRS.intersection(p.relative_to(root).parts):
            yield p


def candidates(root, idents):
    words = {i: re.compile(rf"\b{re.escape(i)}\b") for i in idents}
    out = []
    for p in _patches(root):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(i in text for i in idents):
            continue
        for target, sec in sections(text):
            added = [l for l in sec.splitlines() if l.startswith("+") and not l.startswith("+++")]
            hit = sorted(i for i, rx in words.items() if any(rx.search(l) for l in added))
            if hit:
                out.append({"patch": str(p), "file": target, "identifiers": hit, "text": sec,
                            "mtime": p.stat().st_mtime})
    return out


def _git(w, *args, input=None):
    r = subprocess.run(["git", "-C", str(w), *args], capture_output=True, input=input)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def judge(workdir, c):
    data = c["text"].encode("utf-8")
    if _git(workdir, "apply", "--reverse", "--check", "-", input=data)[0] == 0:
        return "present"
    rc, _ = _git(workdir, "apply", "--check", "-", input=data)
    return "clean" if rc == 0 else "does-not-fit"


def diagnose(workdir, root, log_text):
    errs = errors(log_text)
    found = candidates(root, set(errs)) if errs else []
    for c in found:
        c["state"] = judge(workdir, c)
    chosen = {}
    for c in sorted(found, key=lambda c: -c["mtime"]):
        if c["state"] == "clean" and c["file"] not in chosen:
            chosen[c["file"]] = c
    # a file whose other half is already present needs nothing more
    present = {c["file"] for c in found if c["state"] == "present"}
    plan = [c for f, c in chosen.items() if f not in present]
    covered = set().union(*[set(c["identifiers"]) for c in found if c["state"] == "present"]) if found else set()
    fixed = bool(errs) and not plan and set(errs) <= covered
    return {"errors": errs, "candidates": [{k: v for k, v in c.items() if k not in ("text", "mtime")} for c in found],
            "plan": plan, "fixed": fixed, "ok": bool(errs) and (bool(plan) or fixed)}


def apply(workdir, plan):
    done = []
    for c in plan:
        rc, msg = _git(workdir, "apply", "-", input=c["text"].encode("utf-8"))
        if rc != 0:
            return {"ok": False, "applied": done, "failed": c["patch"], "why": msg.strip()[:300]}
        done.append(f"{c['file']} from {c['patch']}")
    if done:
        _git(workdir, "add", "-A", "--", *[c["file"] for c in plan])
        _git(workdir, "commit", "-q", "-m", "companions: the missing other half of a ported change\n\n" + "\n".join(done))
    return {"ok": True, "applied": done}


def lines(r, root=None):
    out = []
    for ident, where in r["errors"].items():
        out.append(f"  missing: {ident}  ({len(where)} error(s), first {where[0]})")
    for c in r["candidates"]:
        rel = c["patch"].replace(str(root) + "/", "") if root else c["patch"]
        out.append(f"  {c['state']:13} {c['file']}  <- {rel}  [{', '.join(c['identifiers'])}]")
    if not r["errors"]:
        out.append("COMPANIONS: the log names no missing declaration; this tool does not apply (use build triage)")
    elif r["plan"]:
        out.append(f"COMPANIONS: {len(r['plan'])} missing half/halves found that apply cleanly: "
                   + ", ".join(c["file"] for c in r["plan"]) + ". Run again with --apply.")
    elif r.get("fixed"):
        out.append("COMPANIONS: every missing name is already brought by a patch present in the tree: rebuild")
    else:
        out.append("COMPANIONS: nothing in the archived patch sets applies cleanly; a person writes the declaration")
    return out
