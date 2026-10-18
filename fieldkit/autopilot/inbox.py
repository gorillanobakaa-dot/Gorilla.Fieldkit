# Version: 1.0.0 · updated 26-10-18-17-50
"""The owner inbox: everything an unattended run needs the owner for, collected for ONE sitting, never mid-run.

Owner, 2026-10-18: approvals batched for when the owner is awake (publish, leak-gate dispositions, decision records,
changes to protections). A run (or its model) adds an item with the full context and the exact command; the run
goes on with whatever does not depend on it. The owner reads and acts with:

    fieldkit inbox list | show N | done N [note]   items in <autopilot state>/inbox/*.json
    fieldkit publish [--repo DIR] [--release TAG --notes FILE --asset FILE ...] [--dry-run]

publish runs OUTSIDE autopilot (the credential is reachable only there): it shows what would leave this machine,
runs the privacy checks, and pushes / creates the release only after the owner types yes at a real terminal.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

from . import hardlines

INBOX = hardlines.STATE / "inbox"


def add(title, why, command="", kind="approval", files=()):
    INBOX.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(p.stem.split("-")[0]) for p in INBOX.glob("*.json") if p.stem.split("-")[0].isdigit()] or [0])
    item = {"n": n, "kind": kind, "title": title, "why": why, "command": command, "files": list(files),
            "added": time.strftime("%Y-%m-%d %H:%M:%S"), "state": "open"}
    (INBOX / f"{n:04d}-{kind}.json").write_text(json.dumps(item, indent=1, ensure_ascii=False), encoding="utf-8")
    return item


def items(state="open"):
    out = []
    for p in sorted(INBOX.glob("*.json")) if INBOX.is_dir() else []:
        try:
            it = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if state in (None, it.get("state")):
            out.append(it)
    return out


def done(n, note=""):
    for p in INBOX.glob(f"{int(n):04d}-*.json"):
        it = json.loads(p.read_text(encoding="utf-8"))
        it.update(state="done", done=time.strftime("%Y-%m-%d %H:%M:%S"), note=note)
        p.write_text(json.dumps(it, indent=1, ensure_ascii=False), encoding="utf-8")
        return it
    raise KeyError(f"no inbox item {n}")


def lines(its):
    if not its:
        return ["INBOX: empty - nothing waits for the owner"]
    out = [f"INBOX: {len(its)} item(s) wait for the owner"]
    for it in its:
        out.append(f"  [{it['n']}] {it['kind']}: {it['title']}")
        out.append(f"      why: {it['why'][:300]}")
        if it.get("command"):
            out.append(f"      run: {it['command']}")
    return out


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def publish_plan(repo, release=None, notes=None, assets=()):
    """What would leave this machine -> {lines, blockers}."""
    repo = Path(repo)
    lines_, blockers = [], []
    br = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    ahead = _git(repo, "log", "--oneline", "@{u}..HEAD").stdout.strip().splitlines()
    lines_.append(f"repository {repo} branch {br}: {len(ahead)} commit(s) to push")
    lines_ += [f"    {c}" for c in ahead[:20]]
    if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        blockers.append("uncommitted changes: commit or set them aside first")
    if release:
        lines_.append(f"release {release}: notes {notes}, assets: {', '.join(Path(a).name for a in assets) or 'none'}")
        for a in assets:
            if not Path(a).is_file():
                blockers.append(f"asset missing: {a}")
        if notes and not Path(notes).is_file():
            blockers.append(f"release notes missing: {notes}")
        blockers += sums_blockers(assets)
    return {"lines": lines_, "blockers": blockers, "branch": br, "ahead": ahead}


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_sums(assets, out):
    """SHA256SUMS.txt for the release's files, from their bytes now (sha256sum format)."""
    lines = [f"{_sha256(a)}  {Path(a).name}" for a in assets if not Path(a).name.upper().startswith("SHA256SUMS")]
    Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return lines


def sums_blockers(assets):
    """A release ships a checksum file that matches what it ships (2026-10-18: the SHA256SUMS.txt beside build 2's
    files was build 1's; the page told users to check against it). No sums file, a file missing from it, a line
    for a file not uploaded, or a wrong hash: each one blocks."""
    sums = [a for a in assets if Path(a).name.upper().startswith("SHA256SUMS")]
    files = {Path(a).name: a for a in assets if a not in sums}
    if not files:
        return []
    if not sums:
        return ["no SHA256SUMS.txt among the assets (fieldkit publish --sums OUT writes it from the assets)"]
    out, listed = [], {}
    for line in Path(sums[0]).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            listed[parts[-1].lstrip("*")] = parts[0].lower()
    for name, path in files.items():
        if name not in listed:
            out.append(f"{Path(sums[0]).name} does not list {name}")
        elif listed[name] != _sha256(path):
            out.append(f"{Path(sums[0]).name} has the wrong hash for {name} (a file from another build?)")
    out += [f"{Path(sums[0]).name} lists {n}, which is not uploaded" for n in listed if n not in files]
    return out


def publish(repo, release=None, notes=None, assets=(), dry_run=False, confirm=None, run=subprocess.run, say=print):
    """Push (and optionally release) after the privacy checks and the owner's typed yes. -> exit code."""
    import os
    if os.environ.get("FIELDKIT_AUTOPILOT") == "1":
        say("REFUSED: publish never runs inside an autopilot run (no credential there by design)")
        return 3
    plan = publish_plan(repo, release, notes, assets)
    for l in plan["lines"]:
        say(l)
    for b in plan["blockers"]:
        say(f"  BLOCKER: {b}")
    if plan["blockers"]:
        return 3
    chk = run([sys.executable, "-m", "fieldkit", "privacy", "push-check", str(repo), "--range", "@{u}..HEAD"],
              capture_output=True, text=True)
    say((chk.stdout or chk.stderr).strip()[-800:])
    if chk.returncode != 0:
        say("REFUSED: the privacy check found something (above)")
        return 3
    if dry_run:
        say("DRY RUN: nothing sent")
        return 0
    if confirm is None:
        if not sys.stdin.isatty():
            say("REFUSED: publish needs the owner typing yes at a real terminal")
            return 3
        confirm = input("Send this to GitHub? Type yes: ").strip().lower() == "yes"
    if not confirm:
        say("not sent")
        return 1
    r = run(["git", "-C", str(repo), "push", "origin", plan["branch"]], capture_output=True, text=True)
    say((r.stdout + r.stderr).strip()[-600:])
    if r.returncode != 0:
        return r.returncode
    if release:
        # the tag on the commit just pushed (2026-10-18: v158.0-win64 was created server-side on an older commit)
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        args = ["gh", "release", "create", release, "--target", head, "--notes-file", str(notes), *map(str, assets)]
        r = run(args, cwd=str(repo), capture_output=True, text=True)
        say((r.stdout + r.stderr).strip()[-600:])
        return r.returncode
    return 0
