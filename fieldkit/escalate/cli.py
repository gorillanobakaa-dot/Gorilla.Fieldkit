"""fieldkit escalate run|cache|ledger: the cheapest session that passes the check.

    fieldkit escalate run BRIEF.md (--test CMD | --verify CMD | --ci-wait PR) [--cwd DIR] [--wall SEC]
                              [--skip-if-green] [--no-xhigh] [--json]
    fieldkit escalate cache [--settings FILE] [--json]     fail unless the 1h prompt-cache settings and env are set
    fieldkit escalate ledger [--last N] [--json]           the attempts: effort, model, seconds, result, tokens

`run` starts a NEW headless session at low effort and checks the result with a deterministic signal (a test
command's exit code, a verifier command, or `fieldkit ship ci-wait PR`). On a red it starts a NEW session at high
effort whose brief is the original plus the last lines of the failing check, then (only if the model-spend guard
allows it) xhigh on Opus. Every attempt is a ledger line. No model is called when --skip-if-green finds the check
already passing. Exit: 0 passed, 1 every rung failed, 2 bad usage, 3 cache settings missing.
"""
import argparse
import json
import sys
from pathlib import Path

from . import cachecheck, run as R


def _emit(data, as_json, lines, nxt):
    data["next"] = nxt
    if as_json:
        print(json.dumps(data, indent=1, ensure_ascii=False))
    else:
        print("\n".join(lines))
        print(f"NEXT: {nxt}")


def cmd_run(a):
    brief = Path(a.brief)
    if not brief.is_file():
        print(f"REFUSED: {a.brief} is not a file", file=sys.stderr)
        return 2
    try:
        check = R.check_argv(a.test, a.verify, a.ci_wait)
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    rungs = R.RUNGS[:2] if a.no_xhigh else R.RUNGS
    r = R.ladder(brief.read_text(encoding="utf-8"), a.cwd, check, rungs, wall=a.wall, skip_if_green=a.skip_if_green)
    lines = [f"attempt {x.get('attempt', 0)} effort={x['effort']} model={x['model']} {x['seconds']}s {x['result']}"
             + (f" tokens={x['tokens']}" if x.get("tokens") else "") for x in r["attempts"]]
    lines.append("PASS: the check passed" if r["ok"] else "FAIL: every rung failed the check")
    _emit(r, a.json, lines, "nothing to do" if r["ok"] else "read the last output above; park the job in the inbox")
    return 0 if r["ok"] else 1


def cmd_cache(a):
    p = cachecheck.problems(a.settings)
    _emit({"ok": not p, "problems": p}, a.json, p + ["cache settings: " + ("1h everywhere" if not p else "MISSING")],
          "nothing to do" if not p else "set promptCacheTtl/subagentPromptCacheTtl to \"1h\" and the three env vars")
    return 3 if p else 0


def cmd_ledger(a):
    p = R.ledger_path()
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.is_file() else []
    rows = rows[-a.last:]
    _emit({"rows": rows}, a.json, [json.dumps(r, ensure_ascii=False) for r in rows] or ["no attempts yet"],
          "nothing to do")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit escalate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    r = sub.add_parser("run", help="low effort, then fresh high effort on a red")
    r.add_argument("brief")
    r.add_argument("--test")
    r.add_argument("--verify")
    r.add_argument("--ci-wait", dest="ci_wait")
    r.add_argument("--cwd", default=".")
    r.add_argument("--wall", type=int, help="seconds per attempt")
    r.add_argument("--skip-if-green", action="store_true")
    r.add_argument("--no-xhigh", action="store_true")
    r.add_argument("--json", action="store_true")
    c = sub.add_parser("cache", help="enforce the 1h prompt-cache settings")
    c.add_argument("--settings")
    c.add_argument("--json", action="store_true")
    l = sub.add_parser("ledger", help="show the attempts")
    l.add_argument("--last", type=int, default=20)
    l.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "cache": cmd_cache, "ledger": cmd_ledger}[a.action](a)


if __name__ == "__main__":
    sys.exit(main())
