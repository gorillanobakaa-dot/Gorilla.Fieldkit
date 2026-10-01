"""The compile gate. The harness does not run the compile (that changes the machine's power scheme and takes
hours: it is the owner's action, through Gorilla.firefox's own orchestrator). It decides, with evidence,
(1) whether the ported source tree is fit to be compiled, and (2) whether what came out really is that tree's build.

  build-gate    BEFORE: every step done, final checks passed and nothing changed since, no hand edits, no skips,
                no copied answer keys, journal chain intact. On success it records exactly which tree and which
                mozconfig the owner is about to build, and prints the owner's commands.
  build-verify  AFTER: the tree and mozconfig are unchanged since the gate; the installer and zip are NEWER than
                the gate (the owner's own harness once recorded a seven-month-old dist); a real size; the built
                firefox.exe reports the pinned version; hashes are written to build-result.json.
"""
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from ..core import settings
from . import audit, task

MIN_ARTIFACT_MB = 50


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(wd, *args):
    return subprocess.run(["git", "-C", str(wd), *args], capture_output=True, text=True, errors="replace").stdout.strip()


def mozconfig_path(harness_root=None):
    return Path(harness_root or settings.expand("${LOCAL:firefox.root}")) / "config" / "mozconfig.win64"


def objdir(mozconfig):
    m = re.search(r"MOZ_OBJDIR=(\S+)", Path(mozconfig).read_text(encoding="utf-8", errors="replace"))
    return Path(m.group(1)) if m else None


def _record_path(task_id):
    return task.STATE / task_id / "build-record.json"


def gate(task_id, harness_root=None, write=True):
    """-> rows [{check, ok, evidence}]; writes build-record.json only when every row passes."""
    t = task.load(task_id)
    wd, rows = Path(t["workdir"]), []

    def row(name, ok, evidence):
        rows.append({"check": name, "ok": bool(ok), "evidence": evidence})

    left = [s["id"] for s in t["steps"] if s["status"] != "done"]
    row("every step is done", not left, "all done" if not left else f"{len(left)} not done, first: {left[0]}")
    dropped = [s["id"] for s in t["steps"] if s.get("dropped_by_owner")]
    row("every dropped change was a briefed decision", all(s.get("drop_fingerprint") for s in t["steps"] if s.get("dropped_by_owner")),
        f"{len(dropped)} dropped by the owner after an explanation: {[d.split('-', 1)[1][:50] for d in dropped][:4]}" if dropped else "none dropped")
    jp = task.STATE / task_id / "journal.jsonl"
    ev = [json.loads(l) for l in jp.read_text(encoding="utf-8").splitlines() if l.strip()] if jp.is_file() else []
    last_final = max((i for i, e in enumerate(ev) if e.get("event") == "script-done" and e.get("step") == "final-checks"), default=-1)
    later = [e for e in ev[last_final + 1:] if e.get("event") in ("submit", "auto-done", "revert", "unblock")]
    row("final checks passed, and nothing changed after them", last_final >= 0 and not later,
        "passed, nothing since" if last_final >= 0 and not later else
        ("final-checks never passed" if last_final < 0 else f"{len(later)} change(s) after the last pass"))
    for name, ok, evidence in audit.journal_checks(ev):
        row(name, ok, evidence)
    problems, count, _ = task.verify_journal(task_id)
    row("journal hash chain intact", not problems, f"{count} lines" if not problems else "; ".join(problems[:2]))
    dirty = _git(wd, "status", "--porcelain")
    row("working copy: no hand edits", not dirty, "clean" if not dirty else f"uncommitted: {dirty.splitlines()[:3]}")
    stray = [p.name for p in wd.rglob("*") if p.suffix in (".rej", ".orig") and ".git" not in p.parts] if wd.exists() else []
    row("no .rej / .orig leftovers", not stray, "none" if not stray else f"{len(stray)}: {stray[:3]}")
    moz = mozconfig_path(harness_root)
    row("mozconfig present", moz.is_file(), str(moz))
    od = objdir(moz) if moz.is_file() else None
    row("object directory named in the mozconfig", od is not None, str(od))
    ok = all(r["ok"] for r in rows)
    if ok and write:
        rec = {"task": task_id, "at": time.time(), "head": _git(wd, "rev-parse", "HEAD"),
               "tree": _git(wd, "rev-parse", "HEAD^{tree}"), "mozconfig_sha256": _sha(moz), "objdir": str(od),
               "version": t["meta"]["upstream"]["version"], "workdir": str(wd)}
        _record_path(task_id).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rows


def verify(task_id, run_binary=True):
    """-> rows. Writes build-result.json (hashes) when every row passes."""
    rows = []

    def row(name, ok, evidence):
        rows.append({"check": name, "ok": bool(ok), "evidence": evidence})

    p = _record_path(task_id)
    if not p.is_file():
        row("a passed build gate exists", False, "run build-gate first: nothing says which tree was built")
        return rows
    rec = json.loads(p.read_text(encoding="utf-8"))
    wd = Path(rec["workdir"])
    row("the tree is the one that was gated", _git(wd, "rev-parse", "HEAD^{tree}") == rec["tree"] and not _git(wd, "status", "--porcelain"),
        "unchanged since the gate" if _git(wd, "rev-parse", "HEAD^{tree}") == rec["tree"] else "the source changed after the gate")
    moz = mozconfig_path()
    row("mozconfig unchanged since the gate", moz.is_file() and _sha(moz) == rec["mozconfig_sha256"],
        "unchanged" if moz.is_file() and _sha(moz) == rec["mozconfig_sha256"] else "mozconfig differs from what was gated")
    dist = Path(rec["objdir"]) / "dist"
    found = {}
    for kind, pattern in (("installer", "*.installer.exe"), ("zip", "*.zip")):
        found[kind] = sorted(dist.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)[:1] if dist.is_dir() else []
        if not found[kind]:
            row(f"{kind} was produced", False, f"none in {dist}")
            continue
        f = found[kind][0]
        fresh = f.stat().st_mtime >= rec["at"]
        big = f.stat().st_size >= MIN_ARTIFACT_MB * 2 ** 20
        row(f"{kind} is from THIS build", fresh, f"{f.name} {'newer' if fresh else 'OLDER (stale dist?)'} than the gate")
        row(f"{kind} has a real size", big, f"{f.stat().st_size / 2 ** 20:.0f} MB, need at least {MIN_ARTIFACT_MB}")
    exe = dist / "bin" / "firefox.exe"
    if run_binary:
        if exe.is_file():
            out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60, errors="replace").stdout
            row("built firefox.exe reports the pinned version", rec["version"] in out, out.strip() or "no output")
        else:
            row("built firefox.exe exists", False, str(exe))
    if all(r["ok"] for r in rows):
        res = {"task": task_id, "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"), "tree": rec["tree"],
               "artifacts": {k: {"file": str(v[0]), "sha256": _sha(v[0])} for k, v in found.items() if v}}
        (task.STATE / task_id / "build-result.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return rows


def lines(rows, title):
    out = [f"  {'PASS' if r['ok'] else 'FAIL'}  {r['check']}: {r['evidence']}" for r in rows]
    bad = [r for r in rows if not r["ok"]]
    return out + [f"{title}: {'PASSED' if not bad else 'NOT PASSED - ' + str(len(bad)) + ' check(s) failed'}"]
