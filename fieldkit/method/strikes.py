# Version: 1.0.0 · updated 26-10-29-14-04
"""TWO STRIKES (rules/CORE.md; owner, 2026-10-29): a branch or PR that fails CI twice is PARKED, never tried a third
time, and the plan goes on. Deterministic, no model.

    fieldkit strikes list [--json]               the failed CI rounds per branch and what is parked
    fieldkit strikes park ID [--why TEXT]        park by hand (ID: a branch name or a PR number)
    fieldkit strikes unpark ID [--note TEXT]     owner only: let a parked branch be tried again; logged
    fieldkit strikes check BRANCH                read `gh run list` for the branch and record its failed rounds

A failed round is one red CI verdict on one head commit; the same head read red twice is one round. The second
failed round parks: a record in state/parked.json (git-ignored), an owner inbox item with the PR, the branch and both
failures. Then `fieldkit ship ci-wait` exits 4 (NEXT: parked, go on with the plan), `fieldkit ship push` refuses the
branch, `fieldkit task start` refuses a brief that names the branch or its PR (`#N`), and `fieldkit plan next` shows
the parked items on one line.

Exit 0 fine, 2 bad input, 3 refused or found parked, 4 parked by this call (ci-wait).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import emit

LIMIT = 2                                   # failed rounds that park a branch
PARKED_EXIT = 4
ENV = "FIELDKIT_PARKED_FILE"


class StoreError(RuntimeError):
    """The registry's folder is missing or the file is not JSON: refused, never replaced by a fresh empty one."""


def path():
    if os.environ.get(ENV):
        return Path(os.environ[ENV])
    from ..core import settings
    return Path(settings.state_root()) / "state" / "parked.json"


def load(p=None):
    """-> {"strikes": {branch: {...}}, "parked": {branch: {...}}, "log": [...]}. A file not written yet is no strikes;
    a file that cannot be read as JSON is an error (it would be rewritten empty on the next record)."""
    p = Path(p or path())
    if not p.exists():
        return {"strikes": {}, "parked": {}, "log": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {"strikes": dict(d.get("strikes") or {}), "parked": dict(d.get("parked") or {}),
                "log": list(d.get("log") or [])}
    except (OSError, ValueError, AttributeError) as e:
        raise StoreError(f"{p} cannot be read ({e}); fix or move it, nothing was changed") from e


def save(d, p=None):
    p = Path(p or path())
    if not p.parent.is_dir():
        raise StoreError(f"{p.parent} does not exist; this is not a Fieldkit checkout's state folder")
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _match(d, ident):
    """-> the branch a branch name or PR number (`166`, `#166`) stands for, or None."""
    ident = str(ident).strip()
    if ident in d["parked"] or ident in d["strikes"]:
        return ident
    n = ident.lstrip("#")
    for table in (d["parked"], d["strikes"]):
        for br, rec in table.items():
            if n.isdigit() and str(rec.get("pr") or "") == n:
                return br
    return None


def parked_for(branch, p=None):
    """-> the parked record of this branch, or None."""
    return load(p)["parked"].get(branch)


def record_failure(branch, head, why="", pr=None, inbox=None, p=None):
    """One red CI verdict of `head`. -> {"rounds": N, "parked": bool, "new": bool (parked by this call)}.
    `inbox`: callable(title, why, command) -> item; default fieldkit's owner inbox."""
    d = load(p)
    rec = d["strikes"].setdefault(branch, {"pr": pr, "rounds": []})
    if pr:
        rec["pr"] = pr
    if all(r["head"] != head for r in rec["rounds"]):
        rec["rounds"].append({"head": head, "at": _now(), "why": str(why)[:400]})
    n = len(rec["rounds"])
    new = False
    if n >= LIMIT and branch not in d["parked"]:
        d["parked"][branch] = {"pr": rec.get("pr"), "at": _now(), "why": "failed CI twice", "rounds": rec["rounds"][:]}
        new = True
    save(d, p)
    if new:
        _inbox(branch, d["parked"][branch], inbox)
    return {"rounds": n, "parked": branch in d["parked"], "new": new}


def _inbox(branch, rec, inbox=None):
    if inbox is None:
        from ..autopilot import inbox as ib
        inbox = ib.add
    pr = f"PR #{rec['pr']}" if rec.get("pr") else "no PR yet"
    fails = "; ".join(f"{r['head'][:10]}: {r['why'] or 'red'}" for r in rec["rounds"])
    inbox(f"PARKED {branch} ({pr})", f"TWO STRIKES: {branch} ({pr}) failed CI twice - {fails}. Not tried a third time; "
          "left open for a stronger model's repair.", f"fieldkit strikes unpark {rec.get('pr') or branch}")


def park(ident, why="parked by hand", inbox=None, p=None):
    d = load(p)
    br = _match(d, ident) or str(ident)
    if br in d["parked"]:
        return {"ok": True, "branch": br, "state": "already-parked"}
    rec = d["strikes"].get(br, {})
    d["parked"][br] = {"pr": rec.get("pr") or (str(ident).lstrip("#") if str(ident).lstrip("#").isdigit() else None),
                       "at": _now(), "why": why, "rounds": rec.get("rounds", [])}
    save(d, p)
    _inbox(br, d["parked"][br], inbox)
    return {"ok": True, "branch": br, "state": "parked"}


def unpark(ident, note="", who=None, p=None):
    """Owner only: the branch may be tried again, with a clean count. Logged."""
    d = load(p)
    br = _match(d, ident)
    if br is None or br not in d["parked"]:
        return {"ok": False, "state": "not-parked", "error": f"{ident} is not parked"}
    rec = d["parked"].pop(br)
    d["strikes"].pop(br, None)
    d["log"].append({"at": _now(), "action": "unpark", "branch": br, "pr": rec.get("pr"), "note": note,
                     "by": who or os.environ.get("USER") or os.environ.get("USERNAME") or "?"})
    save(d, p)
    return {"ok": True, "branch": br, "state": "unparked"}


def named_in(text, p=None):
    """-> the parked branches a text names: the branch name as a whole word, or its PR as `#N`."""
    d = load(p)
    hits = []
    for br, rec in d["parked"].items():
        word = re.search(r"(?<![\w/.-])" + re.escape(br) + r"(?![\w/.-])", text)
        pr = rec.get("pr")
        num = pr and re.search(r"#" + re.escape(str(pr)) + r"(?!\d)", text)
        if word or num:
            hits.append(br)
    return hits


def refusal(branches, p=None):
    d = load(p)
    out = []
    for br in branches:
        r = d["parked"][br]
        out.append(f"{br}{' (PR #' + str(r['pr']) + ')' if r.get('pr') else ''} is PARKED: it failed CI twice "
                   f"({r['at']}). Not tried a third time; the owner or a stronger model repairs it, then "
                   f"`fieldkit strikes unpark {r.get('pr') or br}`.")
    return " ".join(out)


def parked_line(p=None):
    """-> one line for `fieldkit plan next`, or "" when nothing is parked (or the store cannot be read)."""
    try:
        d = load(p)
    except StoreError:
        return ""
    if not d["parked"]:
        return ""
    return "PARKED (two strikes): " + ", ".join(
        f"{b}" + (f" #{r['pr']}" if r.get("pr") else "") for b, r in sorted(d["parked"].items()))


def runs_from_gh(branch):
    r = subprocess.run(["gh", "run", "list", "--branch", branch, "--limit", "30", "--json",
                        "headSha,conclusion,status,name"], capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError(f"gh run list: {(r.stderr or r.stdout).strip()[-200:]}")
    return json.loads(r.stdout or "[]")


def check(branch, fetch=runs_from_gh, inbox=None, p=None):
    """Record every head of `branch` whose workflow run failed (one round per head). -> the last record's answer."""
    heads = {}
    for run in fetch(branch):
        if run.get("conclusion") == "failure":
            heads.setdefault(run["headSha"], run.get("name") or "red")
    res = {"rounds": len(load(p)["strikes"].get(branch, {}).get("rounds", [])), "parked": parked_for(branch, p) is not None,
           "new": False}
    for sha, name in heads.items():
        res = record_failure(branch, sha, name, inbox=inbox, p=p)
    return dict(res, ok=True, branch=branch)


def _list_lines(d):
    out = [f"{b}{' #' + str(r['pr']) if r.get('pr') else ''}: {len(r['rounds'])} failed round(s)"
           + (" - PARKED" if b in d["parked"] else "") for b, r in sorted(d["strikes"].items())]
    out += [f"{b}: PARKED by hand ({r['why']})" for b, r in sorted(d["parked"].items()) if b not in d["strikes"]]
    return out or ["no failed CI rounds recorded"]


def main(argv=None, prog="fieldkit strikes"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "park", "unpark", "check"])
    ap.add_argument("ident", nargs="?", help="park, unpark: a branch name or PR number; check: the branch")
    ap.add_argument("--why", default="parked by hand")
    ap.add_argument("--note", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.action == "list":
            d = load()
            emit(dict(d, ok=True), a.json, _list_lines)
            return 0
        if not a.ident:
            ap.error(f"{a.action} needs a branch name or PR number")
        if a.action == "park":
            r = park(a.ident, a.why)
        elif a.action == "unpark":
            r = unpark(a.ident, a.note)
        else:
            r = check(a.ident)
    except (StoreError, RuntimeError) as e:
        print(f"strikes: {e}")
        return 1
    emit(r, a.json, lambda d: [f"{d.get('branch', a.ident)}: {d.get('state') or ('PARKED' if d.get('parked') else str(d.get('rounds')) + ' failed round(s)')}"
                               + (f" ({d['error']})" if d.get("error") else "")])
    return 0 if r.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
