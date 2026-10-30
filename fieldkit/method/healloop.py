# Version: 1.0.0 · updated 26-10-23-19-20
"""A self-healing build loop that cannot oscillate: one hypothesis per round, and a round that does not lower the
error count is undone (its changes kept in the trash, never lost).

    fieldkit heal-round start --repo R --log BUILD.LOG --hypothesis "the one theory this round tests"
    fieldkit heal-round end   --repo R --log NEW.LOG      kept if the errors fell; otherwise undone and kept aside
    fieldkit heal-round status --repo R                   every round: hypothesis, errors before and after, kept or undone

Owner, 2026-10-23 (plan section 17.2 item 7), from Google's Android blog on Gemini 4 Argon (the self-healing build
loop) and the oscillation failure in github.com/techdeveloper-org/lordcode/issues/64 (3 -> 1 -> 5 errors): (a) a
hypothesis file written each round, so the model keeps one theory instead of flailing; (b) a progress gate: if the
error count does not fall, the round is reverted.

Undoing a round never deletes: its diff is written to ~/Agents.Work.Trash/heal-rounds/ as a patch, and files the
round created are moved there, before the tracked files are put back as they were when the round started (house
rule: quarantine, never delete). The start state is `git stash create`, which records a dirty tree as a commit without
touching the working tree or the stash list.
"""
import argparse
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from ..core import settings

ERROR_LINE = re.compile(r"^\s*\d*:?\d*\.?\d*\s*(?:E|ERROR|error)\b|error:|Error:|FAILED|fatal error|: error |\bError \d+\b",
                        re.I | re.M)                      # the build loop's own pattern (fieldkit/buildh/buildrun.py)
TRASH = Path.home() / "Agents.Work.Trash" / "heal-rounds"


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def count_errors(log, pattern=None):
    text = Path(log).read_text(encoding="utf-8", errors="replace") if Path(log).is_file() else ""
    return len((re.compile(pattern, re.M) if pattern else ERROR_LINE).findall(text))


def _dir(repo, state_root=None):
    name = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip() or repo).name
    d = Path(state_root or settings.state_root() / "state") / "heal-loop" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rounds(d):
    p = d / "rounds.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def _untracked(repo):
    return set(_git(repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines())


def start(repo, log, hypothesis, pattern=None, state_root=None):
    d = _dir(repo, state_root)
    rounds = _rounds(d)
    if rounds and rounds[-1].get("outcome") is None:
        return {"ok": False, "error": f"round {rounds[-1]['n']} is still open: end it first (heal-round end)"}
    if len(str(hypothesis or "").strip()) < 10:
        return {"ok": False, "error": "a round tests ONE hypothesis, written down: --hypothesis \"...\""}
    snap = _git(repo, "stash", "create").stdout.strip() or _git(repo, "rev-parse", "HEAD").stdout.strip()
    r = {"n": len(rounds) + 1, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "hypothesis": hypothesis.strip(),
         "snapshot": snap, "errors_before": count_errors(log, pattern), "untracked_before": sorted(_untracked(repo)),
         "outcome": None}
    rounds.append(r)
    (d / "rounds.json").write_text(json.dumps(rounds, indent=1), encoding="utf-8")
    with open(d / "HYPOTHESIS.md", "a", encoding="utf-8") as f:   # the theory, one per round, kept in order
        f.write(f"## Round {r['n']} ({r['started']}), {r['errors_before']} error(s) before\n{r['hypothesis']}\n\n")
    return {"ok": True, "round": r["n"], "errors_before": r["errors_before"]}


def end(repo, log, pattern=None, state_root=None, trash=None):
    d = _dir(repo, state_root)
    rounds = _rounds(d)
    if not rounds or rounds[-1].get("outcome") is not None:
        return {"ok": False, "error": "no round is open: heal-round start"}
    r = rounds[-1]
    after = count_errors(log, pattern)
    r.update(errors_after=after, ended=time.strftime("%Y-%m-%d %H:%M:%S"))
    if after < r["errors_before"]:
        r["outcome"] = "kept"
    else:                                                 # the progress gate: no fall, no keep
        kept = Path(trash or TRASH) / f"{time.strftime('%y-%m-%d-%H-%M')}-{d.name}-round-{r['n']}"
        kept.mkdir(parents=True, exist_ok=True)
        (kept / "round.patch").write_text(_git(repo, "diff", r["snapshot"]).stdout, encoding="utf-8")
        new = sorted(_untracked(repo) - set(r["untracked_before"]))
        for f in new:                                     # files the round created: moved aside, not deleted
            src = Path(repo) / f
            (kept / "new-files" / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(kept / "new-files" / f))
        _git(repo, "restore", f"--source={r['snapshot']}", "--worktree", "--", ".")
        (kept / "README.txt").write_text(
            f"Round {r['n']} of the heal loop in {repo} was undone: {r['errors_before']} error(s) before, {after} "
            f"after.\nHypothesis: {r['hypothesis']}\nround.patch is its change; new-files/ the files it made.\n",
            encoding="utf-8")
        r.update(outcome="undone", kept_in=str(kept))
    (d / "rounds.json").write_text(json.dumps(rounds, indent=1), encoding="utf-8")
    with open(d / "HYPOTHESIS.md", "a", encoding="utf-8") as f:
        f.write(f"   -> {after} error(s) after: {r['outcome'].upper()}"
                + (f" (kept in {r['kept_in']})" if r.get("kept_in") else "") + "\n\n")
    return {"ok": True, "round": r["n"], "errors_before": r["errors_before"], "errors_after": after,
            "outcome": r["outcome"], "kept_in": r.get("kept_in")}


def main(argv=None, prog="fieldkit heal-round"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["start", "end", "status"])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--log", help="the build log of this round")
    ap.add_argument("--hypothesis", help="start: the one theory this round tests")
    ap.add_argument("--pattern", help="the regex of an error line (default: the build loop's)")
    a = ap.parse_args(argv)
    if a.action == "status":
        for r in _rounds(_dir(a.repo)):
            print(f"  round {r['n']}: {r['errors_before']} -> {r.get('errors_after', '?')} "
                  f"{(r.get('outcome') or 'open').upper()}: {r['hypothesis'][:100]}")
        return 0
    if not a.log:
        ap.error("--log is needed")
    r = start(a.repo, a.log, a.hypothesis, a.pattern) if a.action == "start" else end(a.repo, a.log, a.pattern)
    if not r["ok"]:
        print(f"REFUSED: {r['error']}")
        return 2
    if a.action == "start":
        print(f"ROUND {r['round']} started: {r['errors_before']} error(s) now")
    else:
        print(f"ROUND {r['round']}: {r['errors_before']} -> {r['errors_after']} error(s): {r['outcome'].upper()}"
              + (f"; its changes are in {r['kept_in']}" if r.get("kept_in") else ""))
    return 0 if r.get("outcome") != "undone" else 3
