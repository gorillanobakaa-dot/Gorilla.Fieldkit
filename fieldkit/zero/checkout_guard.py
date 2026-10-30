# Version: 1.0.0 · updated 26-10-29-19-49
"""One writer per primary checkout: in a repository's primary checkout only Zero may change the tree or its history.

Owner order 2026-10-29. Session 0696d32d ran `git checkout -b fix/emerge-fieldkit-cli` and committed in the shared main
checkout while Zero's release emerge ran there; the checkout stayed on that branch. "Own worktree per job" covered
jobs, not interactive sessions. A refusal at the moment of the command is not forgotten the way a rule is.

Rides on the tool router's PreToolUse hook (fieldkit/autopilot/toolrouter.py). Like claims.py it reads two small local
files and runs no subprocess, no git and no YAML parser:

    NET.yaml            "net: open" and the zero station (who owns the primary checkouts)
    <repo>/.git         a folder = a primary checkout; a file = a linked worktree (always free)

Judged: git checkout / switch, commit, merge, reset, rebase, stash (pop, push, drop, apply...), cherry-pick, revert,
run in a primary checkout. Read-only git passes (status, log, diff, fetch, worktree add, stash list / show, and the
file-restore forms `checkout -- PATH`, `reset -- PATH`).

    Zero (fieldkit zero)                       -> allowed, logged on the net log (kind "checkout")
    FIELDKIT_CHECKOUT_OWNER=<reason> in the hook's environment -> allowed, logged with the reason
    any other station                          -> REFUSED, logged (kind "refused")
    no open net, no zero, an error             -> allowed: this hook never locks a session out of normal work
"""
import os
import re
import socket
import time
from pathlib import Path

from . import claims

VERBS = ("checkout", "switch", "commit", "merge", "reset", "rebase", "stash", "cherry-pick", "revert")
ENV = "FIELDKIT_CHECKOUT_OWNER"
GIT = re.compile(r"^\s*(?:\w+=\S*\s+)*(?:[\"']?[^\s\"';&|]*[\\/])?git(?:\.exe)?[\"']?"
                 r"((?:\s+-C\s+(?:\"[^\"]+\"|'[^']+'|\S+)|\s+-c\s+\S+|\s+--[\w-]+(?:=\S+)?)*)"
                 r"\s+(" + "|".join(VERBS) + r")(?=\s|$)([^\n]*)", re.I)
CD = re.compile(r"^\s*(?:cd|Set-Location|pushd)\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)", re.I)
STASH_READ = re.compile(r"^\s*(list|show)\b")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)
MESSAGE = ("the main checkout belongs to Zero; work in a worktree: git worktree add ../wt-NAME -b BRANCH origin/main")


def _unq(s):
    return s[1:-1] if s[:1] in "\"'" and s[-1:] == s[:1] else s


def mutations(command, cwd=""):
    """-> [(verb, folder)] for every git command in the command that changes a tree or history, with the folder it
    acts in (cwd, a leading `cd X`, then git -C X). Quoted words and heredoc bodies are data, never judged."""
    out, where = [], cwd or os.getcwd()
    for part in re.split(r";|&&|\|\||\||\n", HEREDOC.sub("", command or "")):
        m = CD.match(part)
        if m:
            where = os.path.join(where, os.path.expanduser(_unq(m.group(1))))
            continue
        m = GIT.match(part)
        if not m:
            continue
        verb, rest = m.group(2).lower(), m.group(3)
        if verb == "stash" and STASH_READ.match(rest):
            continue
        if verb in ("checkout", "reset") and re.search(r"(^|\s)--(\s|$)", rest):
            continue                                    # restoring files, not moving a branch
        here = where
        for c in re.findall(r"-C\s+(\"[^\"]+\"|'[^']+'|\S+)", m.group(1) or ""):
            here = os.path.join(here, os.path.expanduser(_unq(c)))
        out.append((verb, here))
    return out


def primary_root(folder):
    """-> the normalized root of the primary checkout the folder is in, or None (a linked worktree, no repository)."""
    d = os.path.abspath(folder or ".")
    while True:
        g = os.path.join(d, ".git")
        if os.path.isdir(g):
            return claims.norm(d)
        if os.path.isfile(g):
            return None                                 # a worktree: its own branch, its own writer
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def zero_station(c=None):
    """-> the zero station of an open net, else None (read as lines: no YAML parser on the hook path)."""
    c = c or claims.coord()
    if not claims.net_open(c):
        return None
    try:
        lines = (c / "NET.yaml").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    inside = False
    for ln in lines:
        if re.match(r"^zero:\s*$", ln):
            inside = True
        elif inside and re.match(r"^\S", ln):
            return None
        elif inside:
            m = re.match(r"^\s+station:\s*(\S+)", ln)
            if m:
                return m.group(1).strip("\"'")
    return None


def decide(call, env=None, c=None, host=None):
    """-> None (not judged) or {"station", "zero", "verb", "repo", "allowed", "by"}. Never raises."""
    try:
        if (call.get("tool_name") or "").lower() not in claims.SHELL_TOOLS:
            return None
        cmd = (call.get("tool_input") or {}).get("command") or ""
        if "git" not in cmd.lower():
            return None
        for verb, folder in mutations(cmd, call.get("cwd") or ""):
            repo = primary_root(folder)
            if not repo:
                continue
            zero = zero_station(c)
            if not zero:
                return None
            me = claims.station_of(call.get("session_id"), host)
            env = os.environ if env is None else env
            by = "zero" if me == zero else ("override: " + env[ENV][:120] if env.get(ENV) else "")
            return {"station": me, "zero": zero, "verb": verb, "repo": repo, "allowed": bool(by), "by": by}
        return None
    except Exception:  # noqa: BLE001 - fail open
        return None


def message(r):
    return (f"CHECKOUT GUARD (fieldkit zero): REFUSED git {r['verb']} in {r['repo']}: {MESSAGE}. You are station "
            f"{r['station']}; Zero is {r['zero']}.")


def log(r, call, now=None):
    """Zero's and the override's use of a primary checkout, and every refusal, go on the net log. Never raises."""
    try:
        from . import net
        net.append("checkout" if r["allowed"] else "refused", r["station"], to="*" if r["allowed"] else r["zero"],
                   text=(f"git {r['verb']} in {r['repo']} ({r['by']})" if r["allowed"] else message(r))[:300],
                   data={"repo": r["repo"], "verb": r["verb"], "by": r["by"] or "refused"}, now=now)
    except Exception:  # noqa: BLE001
        pass


def pretool(call, now=None, **kw):
    """The PreToolUse verdict: the deny JSON, or None. Called from toolrouter.verdict."""
    r = decide(call, **kw)
    if not r:
        return None
    log(r, call, now)
    if r["allowed"]:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": message(r)}}
