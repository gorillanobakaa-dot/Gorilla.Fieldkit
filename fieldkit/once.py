"""fieldkit once: "this command already succeeded" as a state signature, so nobody re-runs or re-asks it (P28 E).

Owner order, 2026-11-16: sessions kept re-checking what a tool had already printed (a watcher repeated the same
"nothing changed" line 10 times) and the owner was asked to run a command the harness could run. The fix is
deterministic: every answer is an exit code plus ONE JSON line `{"done": true|false, "key": ..., "sig": ...}`.

    fieldkit once run   [--key K] [--state PATH ...] -- CMD ...   run CMD unless the journal already holds a success
                                                                   for the same key and state signature
    fieldkit once check [--key K] [--state PATH ...] -- CMD ...   the same question, nothing run
    fieldkit once watch --key K [--file F]                          a watcher's output (stdin or F): unchanged since the
                                                                   last call -> done:true (stop watching)
    fieldkit once forget KEY                                        a later run runs again (a record appended, none erased)
    fieldkit once list [--key K]                                    the latest record of every key

The idempotency key is sha256(argv, cwd) unless --key names it; the state signature is sha256 over the --state paths
(a file by content, a folder by every file's relative path, size and mtime). A success counts only for the SAME
signature: change an input and the command runs again. Exit codes: 0 done (succeeded before, or nothing changed),
3 not done (it must run, or something changed), 2 bad usage; `run` returns the command's own exit code when it ran.
The journal is state/once/journal.jsonl in the main checkout (append-only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

#: a watcher's lines differ only by their clock: these are blanked before the output is compared
_CLOCK = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?\b|\b\d{1,2}:\d{2}(:\d{2})?\b"
                    r"|\b\d+(\.\d+)?\s?(ms|s|sec|min)\b ago|\b(elapsed|age|uptime)[ =:]+\S+", re.I)


def journal_path(root=None):
    env = os.environ.get("FIELDKIT_ONCE_JOURNAL")
    if env:
        return Path(env)
    from .core.settings import state_root
    return Path(state_root(root)) / "state" / "once" / "journal.jsonl"


def command_key(argv, cwd=None):
    """The idempotency key of a command: the same argv in the same folder is the same command."""
    blob = json.dumps({"argv": list(argv), "cwd": str(Path(cwd or os.getcwd()).resolve())}, sort_keys=True)
    return "cmd-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def state_signature(paths):
    """sha256 over the inputs that decide whether a success still holds. A missing path counts as 'missing'."""
    h = hashlib.sha256()
    for p in sorted(str(x) for x in paths):
        path = Path(p)
        h.update(b"\0P" + p.encode("utf-8"))
        if path.is_file():
            h.update(b"F" + hashlib.sha256(path.read_bytes()).digest())
        elif path.is_dir():
            for f in sorted(x for x in path.rglob("*") if x.is_file()):
                st = f.stat()
                h.update(f"D{f.relative_to(path).as_posix()}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8"))
        else:
            h.update(b"missing")
    return h.hexdigest()[:16]


def output_signature(text):
    """A watcher's output with its clocks blanked: the same news at another time is the same news."""
    norm = "\n".join(_CLOCK.sub("#", line).rstrip() for line in text.replace("\r\n", "\n").splitlines() if line.strip())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def read(journal):
    """Every record, oldest first; a torn last line is skipped."""
    out = []
    try:
        lines = Path(journal).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def latest(journal, key):
    recs = [r for r in read(journal) if r.get("key") == key]
    return recs[-1] if recs else None


def append(journal, rec):
    journal = Path(journal)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with open(journal, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def succeeded(journal, key, sig):
    """The record that proves `key` already succeeded on signature `sig`, else None. A later `forget` or failure
    of the same key cancels an earlier success."""
    r = latest(journal, key)
    if r and r.get("kind") == "run" and r.get("exit") == 0 and r.get("sig") == sig:
        return r
    return None


def check(journal, key, sig):
    r = succeeded(journal, key, sig)
    if r:
        return {"done": True, "key": key, "sig": sig, "exit": 0, "at": r.get("at"), "why": "succeeded before"}
    last = latest(journal, key)
    why = ("never run" if not last else "forgotten" if last.get("kind") == "forget" else
           f"last run exit {last.get('exit')}" if last.get("exit") != 0 else "inputs changed since the success")
    return {"done": False, "key": key, "sig": sig, "why": why}


DEFAULT_TIMEOUT = 4 * 3600      # seconds: a hung command is killed and journalled, never left "running" forever
TIMEOUT_EXIT = 124              # the exit code `timeout(1)` uses


def run(journal, key, sig, argv, runner=subprocess.run, clock=time.time, timeout=DEFAULT_TIMEOUT):
    """Run argv unless it already succeeded. -> (result line dict, exit code). A command still running after
    `timeout` seconds is killed and recorded as exit 124 (a failure: the next run runs again)."""
    c = check(journal, key, sig)
    if c["done"]:
        c["skipped"] = True
        return c, 0
    t0 = clock()
    try:
        rc = runner(list(argv), timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        rc = TIMEOUT_EXIT
        print(f"once: {argv[0]} still running after {timeout} s: killed, recorded as exit {rc}", file=sys.stderr)
    except OSError as e:
        rc = 127
        print(f"once: cannot start {argv[0]}: {e}", file=sys.stderr)
    rec = {"kind": "run", "key": key, "sig": sig, "exit": rc, "argv": list(argv), "at": _iso(clock()),
           "secs": round(clock() - t0, 2)}
    append(journal, rec)
    return {"done": rc == 0, "key": key, "sig": sig, "exit": rc, "skipped": False, "secs": rec["secs"]}, rc


def watch(journal, key, text, clock=time.time):
    """A watcher's latest output: done:true when it says what it said last time (stop watching), else recorded."""
    sig = output_signature(text)
    last = latest(journal, key)
    if last and last.get("kind") == "watch" and last.get("sig") == sig:
        repeats = int(last.get("repeats", 1)) + 1
        append(journal, dict(last, repeats=repeats, at=_iso(clock())))
        return {"done": True, "key": key, "sig": sig, "changed": False, "repeats": repeats,
                "since": last.get("first", last.get("at"))}
    now = _iso(clock())
    append(journal, {"kind": "watch", "key": key, "sig": sig, "repeats": 1, "at": now, "first": now})
    return {"done": False, "key": key, "sig": sig, "changed": True, "repeats": 1}


def forget(journal, key, clock=time.time):
    append(journal, {"kind": "forget", "key": key, "at": _iso(clock())})
    return {"done": False, "key": key, "forgotten": True}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _next(res, verb):
    if verb == "watch":
        return ("nothing changed: stop watching, do not report it again" if res["done"]
                else "something changed: act on it, then watch again")
    if res.get("done"):
        return "already done: do not run or ask it again"
    return f"not done ({res.get('why', '')}): fieldkit once run ... -- CMD"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog="fieldkit once", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["run", "check", "watch", "forget", "list"])
    ap.add_argument("name", nargs="?", help="forget: the key")
    ap.add_argument("--key", help="the idempotency key (default: sha256 of the command and the folder)")
    ap.add_argument("--state", action="append", default=[], help="an input path whose change voids a success")
    ap.add_argument("--file", help="watch: read the watcher's output from this file instead of stdin")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="run: kill the command after this many seconds (default 14400); recorded as exit 124")
    ap.add_argument("--journal", help="the journal (default state/once/journal.jsonl)")
    ap.add_argument("--json", action="store_true", help="only the JSON line (it is always printed last)")
    a = ap.parse_args(argv)
    journal = Path(a.journal) if a.journal else journal_path()

    if a.action in ("run", "check"):
        if not cmd and not a.key:
            ap.error(f"{a.action} needs -- CMD ... or --key")
        if a.action == "run" and not cmd:
            ap.error("run needs -- CMD ...")
        key = a.key or command_key(cmd)
        sig = state_signature(a.state)
        if a.action == "check":
            res, rc = check(journal, key, sig), None
            rc = 0 if res["done"] else 3
        else:
            res, rc = run(journal, key, sig, cmd, timeout=a.timeout)
    elif a.action == "watch":
        if not a.key:
            ap.error("watch needs --key")
        text = Path(a.file).read_text(encoding="utf-8", errors="replace") if a.file else sys.stdin.read()
        res = watch(journal, a.key, text)
        rc = 0 if res["done"] else 3
    elif a.action == "forget":
        key = a.name or a.key
        if not key:
            ap.error("forget needs KEY")
        res, rc = forget(journal, key), 0
    else:
        keys = {}
        for r in read(journal):
            keys[r.get("key")] = r
        if a.key:
            keys = {k: v for k, v in keys.items() if k == a.key}
        if not a.json:
            for k, r in keys.items():
                print(f"{k}  {r.get('kind')}  exit={r.get('exit', '-')}  repeats={r.get('repeats', '-')}  {r.get('at')}")
            print(f"NEXT: {len(keys)} key(s) in {journal}")
        print(json.dumps({"done": True, "keys": len(keys), "journal": str(journal)}, sort_keys=True))
        return 0
    if not a.json:
        print(f"NEXT: {_next(res, a.action)}")
    print(json.dumps(res, sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main())
