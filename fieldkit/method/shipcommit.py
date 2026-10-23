# Version: 1.0.0 · updated 26-10-23-16-17
"""Commit until the hook passes, fixing only what is known, never --no-verify: the commit loop.

    fieldkit ship commit --message-file F [--repo R]     commit what is staged; each refusal classified, a known
                                                         one fixed and tried again, an unknown one reported

2026-10-23: one change took five commit attempts, none caused by the change: a privacy finding in a test, /tmp at the
videokit doctor's 5 GB limit (twice), a temp folder whose name ("commit") tripped a jargon test. Each was diagnosed by
hand. The owner (via the main session, "incepe cu toate"): recovery loops with three rules: (a) the same failure twice
means stop and report, never a third try; (b) progress is measured; (c) every attempt is logged.

The classes, read from what hooks/pre-commit prints:
  no-pytest      "no interpreter with pytest"          fix: VIRTUAL_ENV and PATH of the checkout's .venv (a worktree has
                                                       none of its own)
  env-tests      "tests failed" and every failed test passes on its own with TMPDIR on the home disk (~/.cache/fktemp,
                 a name with no jargon in it): fix: run the hook with that TMPDIR
  platform-index tests/test_platform_gate.py failed     fix: python -m fieldkit.core.platform_gate --write, stage it
  privacy        "privacy findings"                     reported with the findings: the fix is an edit, a person's or
                                                       a model's, never the loop's
  docs-group     "could not be put in a documentation group"   reported: a new folder needs a group decided
  test-failure   a failed test fails on its own too     reported: a real failure
Every attempt goes to state/ship-commit/<time>.log (the hook's whole output) and attempts.jsonl.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

FKTEMP = Path.home() / ".cache" / "fktemp"
FIXABLE = ("no-pytest", "env-tests", "platform-index")


def _state(repo):
    top = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"], capture_output=True,
                         text=True).stdout.strip() or str(repo)
    d = Path(top) / "state" / "ship-commit"
    d.mkdir(parents=True, exist_ok=True)
    return Path(top), d


def venv_for(repo):
    """The .venv of this checkout, or of the main checkout when this is a worktree."""
    common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True).stdout.strip()
    for root in (Path(repo), Path(common).parent if common else None):
        if root and (root / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")).is_dir():
            return root / ".venv"
    return None


def classify(out, passes_alone=None):
    """-> (class, the lines that show it). `passes_alone(test_ids)` -> True when they all pass on their own."""
    if "no interpreter with pytest" in out:
        return "no-pytest", ["no interpreter with pytest"]
    if "could not be put in a documentation group" in out:
        return "docs-group", [ln for ln in out.splitlines() if ln.strip()][-6:]
    if "privacy findings" in out:
        return "privacy", [ln for ln in out.splitlines() if re.search(r"^\S.*\.\w+$|^\s+line \d+", ln)][:12]
    if "tests failed" in out:
        failed = list(dict.fromkeys(t for ln in out.splitlines() for t in ln.split()   # the hook prints them on one line
                                    if re.fullmatch(r"tests/\S+::\S+", t)))
        if any(f.startswith("tests/test_platform_gate.py") for f in failed):
            return "platform-index", failed
        if failed and passes_alone and passes_alone(failed):
            return "env-tests", failed
        return "test-failure", failed or ["no failed test named: a collection error? python -m pytest says"]
    return "unknown", [ln for ln in out.splitlines() if ln.strip()][-8:]


def _passes_alone(repo, env, run=subprocess.run):
    def check(tests):
        py = Path(env.get("VIRTUAL_ENV", "")) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        r = run([str(py) if py.is_file() else sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                cwd=str(repo), env=env, capture_output=True, text=True, timeout=1800)
        return r.returncode == 0
    return check


def fix(cls, repo, env, run=subprocess.run):
    """-> (env for the next try, what was done)."""
    env = dict(env)
    if cls == "no-pytest":
        v = venv_for(repo)
        if v is None:
            return env, "no .venv found for this checkout"
        env["VIRTUAL_ENV"] = str(v)
        env["PATH"] = str(v / ("Scripts" if sys.platform == "win32" else "bin")) + os.pathsep + env.get("PATH", "")
        return env, f"VIRTUAL_ENV={v}"
    if cls == "env-tests":
        FKTEMP.mkdir(parents=True, exist_ok=True)
        env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(FKTEMP)
        return env, f"TMPDIR={FKTEMP} (the home disk, a name with no jargon)"
    if cls == "platform-index":
        py = Path(env.get("VIRTUAL_ENV", "")) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        run([str(py) if py.is_file() else sys.executable, "-m", "fieldkit.core.platform_gate", "--write"],
            cwd=str(repo), env=env, capture_output=True, text=True)
        run(["git", "-C", str(repo), "add", "fieldkit/desk/platform_index.json"], capture_output=True, text=True)
        return env, "platform_index.json regenerated and staged"
    return env, ""


def commit(repo, message_file, run=subprocess.run, env=None, max_tries=5):
    """-> {ok, commit, attempts: [{class, done}], stopped, log}. Never --no-verify; a class seen twice stops."""
    top, d = _state(repo)
    env = dict(env if env is not None else os.environ)
    if not env.get("VIRTUAL_ENV") and venv_for(top):
        env, _ = fix("no-pytest", top, env)           # start with the checkout's venv (rule found by hand today)
    seen, attempts = set(), []
    stamp = time.strftime("%y-%m-%d-%H-%M-%S")
    for n in range(1, max_tries + 1):
        r = run(["git", "-C", str(top), "commit", "-q", "-F", str(message_file)], env=env, capture_output=True,
                text=True)
        out = (r.stdout or "") + (r.stderr or "")
        log = d / f"{stamp}-try{n}.log"
        log.write_text(out, encoding="utf-8")
        if r.returncode == 0:
            sha = run(["git", "-C", str(top), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            attempts.append({"try": n, "class": "passed", "log": str(log)})
            _record(d, attempts)
            return {"ok": True, "commit": sha, "attempts": attempts, "log": str(log)}
        cls, shown = classify(out, _passes_alone(top, env, run))
        attempts.append({"try": n, "class": cls, "shown": shown[:8], "log": str(log)})
        if cls not in FIXABLE:
            _record(d, attempts)
            return {"ok": False, "stopped": f"{cls}: not something this loop fixes", "attempts": attempts,
                    "log": str(log), "shown": shown}
        if cls in seen:                                   # rule (a): the same failure twice is a stop, never a third
            _record(d, attempts)
            return {"ok": False, "stopped": f"{cls} again after its fix: stop and report", "attempts": attempts,
                    "log": str(log), "shown": shown}
        seen.add(cls)
        env, done = fix(cls, top, env, run)
        attempts[-1]["done"] = done
    _record(d, attempts)
    return {"ok": False, "stopped": f"{max_tries} tries", "attempts": attempts, "log": str(log)}


def _record(d, attempts):
    with open(d / "attempts.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "attempts": attempts}) + "\n")


def lines(r):
    out = [f"  try {a['try']}: {a['class']}" + (f" -> {a['done']}" if a.get("done") else "") for a in r["attempts"]]
    if r["ok"]:
        return [f"COMMITTED {r['commit'][:10]}"] + out
    return ([f"NOT COMMITTED: {r['stopped']}"] + out + [f"    {s}" for s in r.get("shown") or []]
            + [f"  the hook's whole output: {r['log']}"])
