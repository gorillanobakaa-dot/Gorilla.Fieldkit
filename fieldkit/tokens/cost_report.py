"""cost report: the AI dollars of BOTH machines over a window, one row per day, split by source (Claude Code,
Antigravity, Codex, ...), and the owner's real bill next to the API-equivalent when the owner gives a bill figure.
Read-only, no model, no network.

usage:
    fieldkit cost report --since 2026-10-27                       from that local midnight to now
    fieldkit cost report --since 2026-10-27 --until 2026-10-29    until is exclusive (local midnight)
    fieldkit cost report ... --bill 3000                          the bill for the same window, in US dollars
    fieldkit cost report ... --peer FILE                          the other machine's numbers (else looked for)
    fieldkit cost report ... --json                               one JSON object, nothing else on stdout

The other machine's numbers come over the cable, never from a guess. There: `fieldkit cost report --since DATE
--mine --json > cost-report-<TAG>-<DATE>.json` (or `cost daily --json > cost-daily-<TAG>-<DATE>.json`, one day each), then
`fieldkit peers link put FILE`. Here the report reads --peer, else the link's received/ folder and the scratch
root's link-* jobs, newest file first per day. A day the other machine sent nothing for is named as missing.

Antigravity is priced from its own records (Gemini on the Gemini API table, Claude on the Google Cloud table);
prices.json holds every table with the date it was read. The bill is a figure the owner types (--bill); this tool
never opens a billing page. The gap between bill and API-equivalent is what the report cannot see: subscriptions,
other machines, other products, or a price table out of date.
I1 of docs/HARNESS-PLAN.md, 2026-10-28 (about $1,000 visible on one machine against about $3,000 paid).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import cost_daily as cd
from . import session_cost as sc


def days_in(since, until):
    """Every local date from since up to (not including) until."""
    d, last, out = since.astimezone().date(), (until - timedelta(microseconds=1)).astimezone().date(), []
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def local_day(ts):
    return sc._parse_ts(ts).astimezone().date().isoformat()


def measure(since, until, adapters=None):
    """This machine's dollars per local day and per source -> dict (the --json 'mine' shape)."""
    adapters = adapters if adapters is not None else sc.adapters_for("all")
    days = {d: {"usd": 0.0, "by_source": {}, "sessions": 0} for d in days_in(since, until)}
    unpriced, sessions, prices = 0, 0, {}
    for ad, path in sc.touched_since(adapters, since):
        calls, s = sc.measure(ad, path, None, since, until)
        if not calls:
            continue
        sessions += 1
        unpriced += sum(u["calls"] for u in s["unpriced"].values())
        prices.update({p: v["fetched"] for p, v in s["prices"].items()})
        seen = set()
        for c in calls:
            if not c["priced"] or not c.get("ts"):
                continue
            day = days.get(local_day(c["ts"]))
            if day is None:
                continue
            day["usd"] += c["usd"]
            day["by_source"][ad.name] = day["by_source"].get(ad.name, 0.0) + c["usd"]
            if id(day) not in seen:
                seen.add(id(day))
                day["sessions"] += 1
    by_source = {}
    for d in days.values():
        d["usd"] = round(d["usd"], 2)
        for k, v in d["by_source"].items():
            by_source[k] = by_source.get(k, 0.0) + v
        d["by_source"] = {k: round(v, 2) for k, v in sorted(d["by_source"].items())}
    return {"kind": "cost-report", "machine": cd._machine(), "since": since.isoformat(), "until": until.isoformat(),
            "usd": round(sum(d["usd"] for d in days.values()), 2),
            "by_source": {k: round(v, 2) for k, v in sorted(by_source.items())},
            "sessions": sessions, "unpriced_calls": unpriced, "prices_fetched": prices, "days": days}


def peer_days(d):
    """One file's JSON -> {date: usd} it vouches for, or {} when it is neither shape."""
    if not isinstance(d, dict):
        return {}
    if d.get("kind") == "cost-report" and isinstance(d.get("days"), dict):
        return {k: float(v["usd"]) for k, v in d["days"].items()
                if isinstance(v, dict) and isinstance(v.get("usd"), (int, float))}
    if isinstance(d.get("date"), str) and isinstance(d.get("usd"), (int, float)):      # cost daily --json
        return {d["date"]: float(d["usd"])}
    return {}


def peer_candidates():
    """Where a cost JSON put over the cable lands, newest first."""
    places = []
    try:
        from ..peers import link
        places.append(link.home() / "received")
    except Exception:  # noqa: BLE001 - no link on this machine: only --peer files count
        pass
    try:
        from .. import scratch
        places += sorted(scratch.root().glob("link-*"))
    except Exception:  # noqa: BLE001
        pass
    files = []
    for p in places:
        if p.is_dir():
            files += list(p.glob("cost-report-*.json")) + list(p.glob("cost-daily-*.json"))
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def peer_numbers(wanted, mine, files):
    """The other machine's dollars per wanted day, the newest file first -> (machine, {date: (usd, file)})."""
    machine, got = "", {}
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("machine") == mine or (machine and d.get("machine") != machine):
            continue
        for day, usd in peer_days(d).items():
            if day in wanted and day not in got:
                got[day] = (round(usd, 2), str(f))
                machine = machine or d.get("machine") or "other"
    return machine, got


def combine(mine, peer_machine, peer, bill=None):
    days = {}
    for day, m in mine["days"].items():
        row = {"mine": m["usd"], "by_source": m["by_source"], "peer": None, "both": None}
        if day in peer:
            row["peer"], row["peer_file"] = peer[day]
            row["both"] = round(m["usd"] + peer[day][0], 2)
        days[day] = row
    missing = [d for d, r in days.items() if r["peer"] is None]
    r = {"since": mine["since"], "until": mine["until"], "machine": mine["machine"], "peer_machine": peer_machine,
         "days": days, "usd_mine": mine["usd"], "usd_peer": round(sum(v[0] for v in peer.values()), 2),
         "peer_days_missing": missing, "by_source_mine": mine["by_source"], "unpriced_calls": mine["unpriced_calls"],
         "prices_fetched": mine["prices_fetched"]}
    r["usd_both"] = round(r["usd_mine"] + r["usd_peer"], 2)
    r["bill"] = None
    if bill is not None:
        gap = round(bill - r["usd_both"], 2)
        r["bill"] = {"usd": round(bill, 2), "api_equivalent": r["usd_both"], "gap": gap,
                     "seen_share": round(r["usd_both"] / bill, 4) if bill else None,
                     "complete": not missing}
    return r


def text(r):
    me, peer = r["machine"] or "this machine", r["peer_machine"] or "other machine"
    out = ["cost report %s .. %s (local days; until exclusive)" % (r["since"][:10], r["until"][:16])]
    for day, row in r["days"].items():
        src = ", ".join("%s $%.2f" % kv for kv in row["by_source"].items()) or "nothing"
        p = ("%s $%.2f; both $%.2f" % (peer, row["peer"], row["both"])) if row["peer"] is not None \
            else "%s: not over the cable" % peer
        out.append("  %s  %s $%.2f (%s)  %s" % (day, me, row["mine"], src, p))
    tot = "  total  %s $%.2f" % (me, r["usd_mine"])
    if r["peer_machine"]:
        tot += "  %s $%.2f  both $%.2f" % (peer, r["usd_peer"], r["usd_both"])
    if r["peer_days_missing"]:
        tot += "  (the other machine's numbers are missing for %s)" % ", ".join(r["peer_days_missing"])
    out.append(tot)
    if r["unpriced_calls"]:
        out.append("  %d call(s) on models without a price are not in these dollars" % r["unpriced_calls"])
    if r["prices_fetched"]:
        out.append("  prices as read: " + ", ".join("%s %s" % kv for kv in sorted(r["prices_fetched"].items())))
    b = r["bill"]
    if b:
        out.append("  bill $%.2f against API-equivalent $%.2f: gap $%.2f%s%s" % (
            b["usd"], b["api_equivalent"], b["gap"],
            " (the report sees %.0f%% of the bill)" % (100 * b["seen_share"]) if b["seen_share"] is not None else "",
            "" if b["complete"] else "; INCOMPLETE: a machine's days are missing"))
    return "\n".join(out)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    p = argparse.ArgumentParser(prog="fieldkit cost report", description="Both machines' AI dollars over a window.")
    p.add_argument("--since", required=True, help="first day, YYYY-MM-DD (local midnight) or an ISO time")
    p.add_argument("--until", help="end, exclusive (default now)")
    p.add_argument("--peer", action="append", default=[], help="the other machine's cost report/daily --json file")
    p.add_argument("--bill", type=float, help="the owner's real bill for the same window, US dollars")
    p.add_argument("--json", action="store_true", help="one JSON object, nothing else on stdout")
    p.add_argument("--mine", action="store_true", help="only this machine's numbers (the file to put over the cable)")
    a = p.parse_args(argv)
    try:
        since = sc.when(a.since)
        until = sc.when(a.until) if a.until else datetime.now().astimezone()
    except ValueError as e:
        p.error("--since/--until: %s (use YYYY-MM-DD or an ISO time)" % e)
    if until <= since:
        p.error("--until must be after --since")
    if a.bill is not None and a.bill < 0:
        p.error("--bill: a bill is not negative")
    missing = [f for f in a.peer if not os.path.isfile(f)]
    if missing:
        print("cost: not found: " + ", ".join(missing), file=sys.stderr)
        return 2
    mine = measure(since, until)
    if a.mine:
        print(json.dumps(mine) if a.json else text(combine(mine, "", {})))
        return 0
    pm, peer = peer_numbers(set(mine["days"]), mine["machine"], [Path(f) for f in a.peer] or peer_candidates())
    r = combine(mine, pm, peer, a.bill)
    print(json.dumps(r) if a.json else text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
