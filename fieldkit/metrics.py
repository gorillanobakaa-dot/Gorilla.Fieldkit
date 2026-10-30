"""Long-horizon metrics: proof, from the harness's own records, of how long and how much it ran (fieldkit metrics).

Owner, 2026-10-29 (plan idea I-01, started 2026-10-30): "Prove the harness runs long-horizon tasks: fieldkit metrics
shows days running, session changes, Zero epochs, subordinate sessions, devices, money, tokens, tool calls
executed/refused/repeated, PRs, CI runs, failures".

Every number names the record it was counted from. A number that cannot be read here is listed under NOT MEASURED
with the reason, never estimated. Read-only, no model; the network only for GitHub (`gh`), and not with --offline.

Sources (all existing harness records):
  span, devices, Zero      state/coordination/net-log.jsonl (fieldkit zero), NET.yaml for the epoch now
  session changes          state/rotate/handovers.jsonl (fieldkit rotate handover) + the net log's open/handover
  sessions, tokens, tools  Claude Code transcripts on THIS machine (~/.claude/projects, subagents apart)
  subordinate sessions     the task ledger, state/lifecycle/tasks.jsonl (fieldkit task start: here and over the cable)
  money                    fieldkit cost report (this machine measured; the other machine from its cost file)
  PRs, CI runs             gh pr list / gh run list for this repository

Tool calls, from the transcripts: executed = tool_use blocks; refused = a tool_result that is an error carrying a
PreToolUse hook's refusal (the tool router, ground claims, the archive guard...); declined = the person said no;
errors = any other error result; repeated = the same tool with the same input as the call right before it in that
session (the circuit breaker's definition, fieldkit/autopilot/breaker.py).

    fieldkit metrics [--since DATE] [--until DATE] [--offline] [--json]
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REFUSAL_MARKS = ("PreToolUse:", "hook error", "permissionDecision")
DECLINED_MARKS = ("doesn't want to proceed", "user rejected", "The user denied")


def _dt(s):
    """An ISO stamp or a 'YYYY-MM-DD HH:MM[:SS]' local stamp -> aware datetime, or None."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.astimezone()


def _inside(d, since, until):
    return d is not None and since <= d < until


def _text(v):
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return " ".join(_text(x) for x in v)
    if isinstance(v, dict):
        return _text(v.get("text") or v.get("content") or "")
    return ""


# ---------------------------------------------------------------- transcripts: sessions, tokens, tool calls

def transcript_files(root):
    root = Path(root)
    if not root.is_dir():
        return [], []
    return sorted(root.glob("*/*.jsonl")), sorted(root.glob("*/*/subagents/*.jsonl"))


def read_transcript(path, since, until):
    """One transcript's numbers inside [since, until). A line with no stamp takes the stamp of the line before it."""
    r = {"lines": 0, "first": None, "last": None, "input": 0, "cache_write": 0, "cache_read": 0, "output": 0,
         "api_calls": 0, "executed": 0, "refused": 0, "declined": 0, "errors": 0, "repeated": 0}
    seen, prev, stamp = set(), None, None
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return r
    with f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            stamp = _dt(d.get("timestamp")) or stamp
            if not _inside(stamp, since, until):
                continue
            r["lines"] += 1
            r["first"] = r["first"] or stamp
            r["last"] = stamp
            msg = d.get("message") or {}
            content = msg.get("content") if isinstance(msg.get("content"), list) else []
            if d.get("type") == "assistant":
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        r["executed"] += 1
                        key = (c.get("name"), json.dumps(c.get("input"), sort_keys=True))
                        r["repeated"] += key == prev
                        prev = key
                u, mid = msg.get("usage"), msg.get("id")
                if u and mid not in seen:
                    seen.add(mid)
                    r["api_calls"] += 1
                    r["input"] += u.get("input_tokens", 0) or 0
                    r["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
                    r["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
                    r["output"] += u.get("output_tokens", 0) or 0
            elif d.get("type") == "user":
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                        t = _text(c.get("content"))
                        if any(m in t for m in REFUSAL_MARKS):
                            r["refused"] += 1
                        elif any(m in t for m in DECLINED_MARKS):
                            r["declined"] += 1
                        else:
                            r["errors"] += 1
    return r


def transcripts(root, since, until):
    mains, subs = transcript_files(root)
    tot = {"sessions": 0, "subagents": 0, "active_days": set()}
    keys = ("input", "cache_write", "cache_read", "output", "api_calls", "executed", "refused", "declined", "errors",
            "repeated")
    for k in keys:
        tot[k] = 0
    for kind, files in (("sessions", mains), ("subagents", subs)):
        for p in files:
            try:
                if p.stat().st_mtime < since.timestamp():
                    continue                         # not written in the window: holds no line of it
            except OSError:
                continue
            r = read_transcript(p, since, until)
            if not r["lines"]:
                continue
            tot[kind] += 1
            for k in keys:
                tot[k] += r[k]
            day = r["first"].astimezone().date()
            while day <= r["last"].astimezone().date():
                tot["active_days"].add(day.isoformat())
                day += timedelta(days=1)
    tot["active_days"] = sorted(tot["active_days"])
    return tot


# ---------------------------------------------------------------- the net, rotations, the task ledger

def net_part(since, until, entries=None, net=None):
    from .zero import net as zn
    entries = zn.entries() if entries is None else entries
    net = zn.load_net() if net is None else net
    stamps, changes, stations = [], [], set()
    for _off, e in entries:
        d = _dt(e.get("at"))
        if d is None:
            continue
        stamps.append(d)
        if not _inside(d, since, until):
            continue
        if e.get("kind") in ("open", "handover"):
            changes.append({"at": e.get("at"), "kind": e.get("kind"), "epoch": e.get("epoch"),
                            "text": (e.get("text") or "")[:100]})
        if e.get("kind") == "checkin" and e.get("from"):
            stations.add(e["from"])
    machines = sorted({zn.machine_of(s) for s in stations if zn.machine_of(s)})
    return {"first": min(stamps) if stamps else None, "last": max(stamps) if stamps else None,
            "epoch_now": net.get("epoch"), "zero_now": zn.zero_of(net), "zero_changes": changes,
            "stations": len(stations), "machines": machines}


def rotations(since, until, path=None):
    if path is None:
        from .core import settings
        path = Path(os.environ.get("FIELDKIT_ROTATE_DIR") or settings.state_root() / "state" / "rotate") / \
            "handovers.jsonl"
    n = 0
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            n += _inside(_dt(json.loads(line).get("at")), since, until)
        except ValueError:
            continue
    return n


def tasks_part(since, until, led=None):
    if led is None:                                  # settled as task watch does: a started job whose process is gone
        from .lifecycle import tasks                 # counts as died, not as still running
        led = tasks.ledger()
        tasks.settle(led)
    out = {"total": 0, "by_device": {}, "by_status": {}, "first": None}
    for t in led.values():
        d = _dt(t.get("at"))
        if d is not None:
            out["first"] = min(out["first"], d) if out["first"] else d
        if not _inside(d, since, until):
            continue
        out["total"] += 1
        dev = "here" if t.get("on") in (None, "here") else "peer" if t.get("on") == "peer" else t.get("on")
        out["by_device"][dev] = out["by_device"].get(dev, 0) + 1
        st = t.get("status") or "?"
        out["by_status"][st] = out["by_status"].get(st, 0) + 1
    return out


# ---------------------------------------------------------------- money, GitHub

def money(since, until):
    from .tokens import cost_report as cr
    mine = cr.measure(since, until)
    pm, peer = cr.peer_numbers(set(mine["days"]), mine["machine"], cr.peer_candidates())
    return cr.combine(mine, pm, peer)


def _gh(args, run=subprocess.run):
    r = run(["gh", *args], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200])
    return json.loads(r.stdout or "[]")


def github(since, until, gh=_gh):
    prs = gh(["pr", "list", "--state", "all", "--limit", "1000", "--json", "number,state,createdAt,mergedAt"])
    runs = gh(["run", "list", "--limit", "1000", "--json", "conclusion,status,createdAt"])
    p = [x for x in prs if _inside(_dt(x.get("createdAt")), since, until)]
    r = [x for x in runs if _inside(_dt(x.get("createdAt")), since, until)]
    conc = {}
    for x in r:
        k = x.get("conclusion") or x.get("status") or "?"
        conc[k] = conc.get(k, 0) + 1
    return {"prs_opened": len(p), "prs_merged": sum(1 for x in p if x.get("mergedAt")),
            "prs_open": sum(1 for x in p if x.get("state") == "OPEN"), "ci_runs": len(r), "ci_by_conclusion": conc,
            "runs_listed": len(runs),
            # the cap hides runs only when even the oldest run listed is inside the window
            "runs_cap_hit": len(runs) >= 1000 and all(_inside(_dt(x.get("createdAt")), since, until) for x in runs)}


# ---------------------------------------------------------------- the report

def collect(since=None, until=None, offline=False, root=None, parts=None):
    """-> the report dict. parts: injected callables for tests (net, rotations, tasks, transcripts, money, github)."""
    parts = parts or {}
    until = until or datetime.now().astimezone()
    unmeasured = []
    netp = (parts.get("net") or net_part)(datetime.min.replace(tzinfo=until.tzinfo), until)
    tk_all = (parts.get("tasks") or tasks_part)(datetime.min.replace(tzinfo=until.tzinfo), until)
    if since is None:
        firsts = [d for d in (netp["first"], tk_all["first"]) if d]
        since = min(firsts) if firsts else until - timedelta(days=1)
    netp = (parts.get("net") or net_part)(since, until)
    tk = (parts.get("tasks") or tasks_part)(since, until)
    rot = (parts.get("rotations") or rotations)(since, until)
    if rot is None:
        unmeasured.append(("session rotations", "state/rotate/handovers.jsonl not found"))
    if root is None:
        from .tokens.session_cost import CLAUDE_PROJECTS as root
    tr = (parts.get("transcripts") or transcripts)(root, since, until)
    unmeasured.append(("sessions, tokens and tool calls on the other machine",
                       "transcripts are read on the machine that holds them; run fieldkit metrics there"))
    try:
        mo = (parts.get("money") or money)(since, until)
        if mo.get("peer_days_missing"):
            unmeasured.append(("money on the other machine for %d day(s)" % len(mo["peer_days_missing"]),
                               "no cost file came over the cable for them (fieldkit cost report --mine --json there)"))
    except Exception as e:  # noqa: BLE001 - a missing price table must not hide the rest
        mo = None
        unmeasured.append(("money", f"fieldkit cost report failed: {e}"))
    gh = None
    if offline:
        unmeasured.append(("PRs and CI runs", "--offline"))
    else:
        try:
            gh = (parts.get("github") or github)(since, until)
            if gh["runs_cap_hit"]:
                unmeasured.append(("CI runs before the newest 1000", "gh run list returns at most 1000"))
        except Exception as e:  # noqa: BLE001
            unmeasured.append(("PRs and CI runs", f"gh failed: {e}"))
    span_days = (until - since).total_seconds() / 86400
    failures = {"tasks_died": tk["by_status"].get("died", 0), "tasks_failed": tk["by_status"].get("failed", 0),
                "tasks_blocked": tk["by_status"].get("blocked", 0), "tool_errors": tr["errors"],
                "tool_refusals": tr["refused"],
                "ci_failed": (gh or {}).get("ci_by_conclusion", {}).get("failure") if gh else None}
    return {"kind": "metrics", "since": since.isoformat(timespec="seconds"),
            "until": until.isoformat(timespec="seconds"), "days_running": round(span_days, 1),
            "active_days": tr["active_days"], "net": netp, "rotations": rot, "tasks": tk, "transcripts": tr,
            "money": mo, "github": gh, "failures": failures,
            "unmeasured": [{"what": w, "why": y} for w, y in unmeasured]}


def _n(x):
    return "-" if x is None else f"{x:,}" if isinstance(x, int) else str(x)


def lines(r):
    n, tk, tr, mo, gh, f = r["net"], r["tasks"], r["transcripts"], r["money"], r["github"], r["failures"]
    out = [f"METRICS  {r['since'][:16]} -> {r['until'][:16]}  ({r['days_running']} days, "
           f"{len(r['active_days'])} with sessions on this machine)",
           "",
           f"  Zero        epoch now {_n(n['epoch_now'])}, held by {n['zero_now'] or '-'}; "
           f"{len(n['zero_changes'])} change(s) of Zero in the window   [net-log.jsonl]",
           f"  rotations   {_n(r['rotations'])} session handover(s)   [state/rotate/handovers.jsonl]",
           f"  sessions    {_n(tr['sessions'])} Claude Code session(s) + {_n(tr['subagents'])} subagent(s) on this "
           "machine   [transcripts]",
           f"  stations    {_n(n['stations'])} checked in; machines: {', '.join(n['machines']) or '-'}   [net-log.jsonl]",
           f"  sub-jobs    {_n(tk['total'])} headless task(s): "
           + ", ".join(f"{k} {v}" for k, v in sorted(tk["by_device"].items())) + "; "
           + ", ".join(f"{k} {v}" for k, v in sorted(tk["by_status"].items())) + "   [tasks.jsonl]",
           f"  tokens      {_n(tr['api_calls'])} API calls; in {_n(tr['input'])}, cache write {_n(tr['cache_write'])}, "
           f"cache read {_n(tr['cache_read'])}, out {_n(tr['output'])}   [transcripts]",
           f"  tool calls  executed {_n(tr['executed'])}, refused by a hook {_n(tr['refused'])}, declined by a person "
           f"{_n(tr['declined'])}, errors {_n(tr['errors'])}, repeated {_n(tr['repeated'])}   [transcripts]"]
    if mo:
        out.append(f"  money       ${mo['usd_both']:,.2f} API-equivalent (this machine ${mo['usd_mine']:,.2f}"
                   + (f", {mo['peer_machine']} ${mo['usd_peer']:,.2f}" if mo.get("peer_machine") else "")
                   + f"; {_n(mo['unpriced_calls'])} unpriced call(s))   [fieldkit cost report]")
    if gh:
        out.append(f"  GitHub      PRs opened {gh['prs_opened']}, merged {gh['prs_merged']}, still open {gh['prs_open']};"
                   f" CI runs {gh['ci_runs']} (" + ", ".join(f"{k} {v}" for k, v in sorted(gh["ci_by_conclusion"].items()))
                   + ")   [gh]")
    out.append(f"  failures    tasks died {f['tasks_died']}, failed {f['tasks_failed']}, blocked {f['tasks_blocked']}; "
               f"CI failed {_n(f['ci_failed'])}; tool errors {_n(f['tool_errors'])}, refusals {_n(f['tool_refusals'])}")
    if r["unmeasured"]:
        out += ["", "NOT MEASURED"] + [f"  {u['what']}: {u['why']}" for u in r["unmeasured"]]
    out.append("NEXT: fieldkit metrics --json for the numbers; on the other machine the same command for its sessions")
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    p = argparse.ArgumentParser(prog="fieldkit metrics", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", help="first day YYYY-MM-DD or an ISO time (default: the harness's first record)")
    p.add_argument("--until", help="end, exclusive (default now)")
    p.add_argument("--offline", action="store_true", help="no gh calls (PRs and CI runs listed as not measured)")
    p.add_argument("--json", action="store_true", help="one JSON object")
    a = p.parse_args(argv)
    since, until = _dt(a.since), _dt(a.until)
    if (a.since and since is None) or (a.until and until is None):
        p.error("--since/--until: use YYYY-MM-DD or an ISO time")
    if since and until and until <= since:
        p.error("--until must be after --since")
    r = collect(since, until, a.offline)
    print(json.dumps(r, default=str, indent=1) if a.json else "\n".join(lines(r)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
