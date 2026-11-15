"""cost daily: ONE line for the owner - a day's AI dollars on this machine, the most expensive session and what it
did, and the total of both machines when the other machine's numbers came over the cable. Read-only, no model.

usage:
    fieldkit cost daily                       yesterday (local midnight to midnight)
    fieldkit cost daily --date 2026-10-27     that day
    fieldkit cost daily --json                the same as one JSON object, nothing else on stdout
    fieldkit cost daily --peer FILE           the other machine's --json output (else looked for, see below)
    fieldkit cost report --since DATE         both machines over a window, the bill beside it (cost_report.py)

The other machine's numbers: there, `fieldkit cost daily --json > cost-daily-<TAG>-<DATE>.json`, then
`fieldkit peers link put cost-daily-<TAG>-<DATE>.json`. Here the line reads the newest cost-daily-*.json for the same
day from another machine, from --peer, the link's received/ folder or the scratch root's link-* jobs. None found:
the line says so; it never guesses the other machine's dollars.

"What it did": the session's Board row in state/coordination/PLAN.md (its id's first 8 characters), else its first
prompt. Dollars come from session_cost (every source it measures; only the calls stamped inside the day).
I4 of docs/HARNESS-PLAN.md, 2026-10-28 (the owner lost track of the spend over 4 days).
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from ..core import settings
from . import session_cost as sc

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def window(day):
    """-> (since, until): local midnight of day and of the next day, timezone-aware."""
    since = datetime.combine(day, time()).astimezone()
    return since, datetime.combine(day + timedelta(days=1), time()).astimezone()


def _cut(text, n=110):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def board_row(session, plan, title=""):
    """The last Board row in PLAN.md naming the session's first 8 characters, or whose first cell starts with the
    session's title -> its first two cells, or ""."""
    try:
        lines = Path(plan).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    key, title = session[:8], title.strip()
    for line in reversed(lines):
        if line.startswith("|") and (key in line or (title and line[1:].lstrip().startswith(title))):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            return ": ".join(c for c in cells[:2] if c)
    return ""


def first_prompt(path):
    """The first line a person (or a brief) typed into a Claude Code transcript, without injected reminders."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict) or d.get("type") != "user" or d.get("isMeta"):
                    continue
                c = (d.get("message") or {}).get("content")
                if isinstance(c, list):
                    c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                if not isinstance(c, str):
                    continue
                for ln in REMINDER.sub("", c).splitlines():
                    ln = ln.strip()
                    # headings and lead-ins ("Background:") say where the work is, not what it is
                    if ln and not ln.startswith(("<", "---", "#")) and not ln.endswith(":"):
                        return ln
    except OSError:
        pass
    return ""


def _title(adapter, path, s):
    if s.get("title"):
        return s["title"]
    if adapter.name == "claude-code":
        from .rotate import _title_from_transcript
        return _title_from_transcript(path)
    return ""


def _plan():
    try:
        from .rotate import coordination_dir
        return coordination_dir() / "PLAN.md"
    except Exception:  # noqa: BLE001 - no repository state here: no Board to read, the first prompt answers
        return None


def measure_day(day, adapters=None, plan=None):
    """This machine's numbers for the day -> dict (the --json shape)."""
    since, until = window(day)
    adapters = adapters if adapters is not None else sc.adapters_for("all")
    rows = []
    for ad, path in sc.touched_since(adapters, since):
        calls, s = sc.measure(ad, path, None, since, until)
        if calls:
            rows.append((ad, path, s))
    usd = round(sum(s["usd"] for _, _, s in rows), 2)
    out = {"date": day.isoformat(), "machine": _machine(), "since": since.isoformat(), "until": until.isoformat(),
           "usd": usd, "sessions": len(rows), "calls": sum(s["calls"] for _, _, s in rows),
           "unpriced_calls": sum(u["calls"] for _, _, s in rows for u in s["unpriced"].values()), "top": None}
    if rows:
        ad, path, s = max(rows, key=lambda r: r[2]["usd"])
        plan = plan if plan is not None else _plan()
        title = _title(ad, path, s)
        what, src = (board_row(s["session"], plan, title), "board") if plan else ("", "")
        if not what and ad.name == "claude-code":
            what, src = first_prompt(path), "first prompt"
        out["top"] = {"session": s["session"], "source": ad.name, "title": title, "usd": round(s["usd"], 2),
                      "calls": s["calls"], "what": _cut(what) if what else "", "what_from": src if what else ""}
    return out


def _machine():
    try:
        from ..peers import machine
        return machine.current()
    except Exception:  # noqa: BLE001 - an unreadable tag is shown as unknown, never invented
        return ""


def peer_candidates():
    """Where a cost-daily JSON put over the cable lands: the link's received/ folder and the scratch link-* jobs."""
    places = []
    try:
        from ..peers import link
        places.append(link.home() / "received")
    except Exception:  # noqa: BLE001
        pass
    try:
        from .. import scratch
        places += sorted(scratch.root().glob("link-*"))
    except Exception:  # noqa: BLE001
        pass
    files = []
    for d in places:
        if d.is_dir():
            files += d.glob("cost-daily-*.json")
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def peer_numbers(day, mine, files):
    """The newest file holding the same day from another machine -> (dict, path) or (None, None)."""
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("date") == day.isoformat() and isinstance(d.get("usd"), (int, float)) \
                and d.get("machine") != mine:
            return d, str(f)
    return None, None


FRESH_S = 86400        # a cable answer (or the copy of one) older than a day is not counted (row 20)


def answer(req, now=None):
    """The listener's read-only verb `cost`: this machine's own numbers for the other side's report/daily."""
    try:
        if req.get("kind") == "daily":
            data = measure_day(date.fromisoformat(str(req.get("date"))))
        elif req.get("kind") == "report":
            from . import cost_report
            data = cost_report.measure(sc.when(str(req.get("since"))), sc.when(str(req.get("until"))))
        else:
            return {"ok": False, "error": "cost: kind is daily or report"}
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": "cost: %s" % e}
    return {"ok": True, "measured_at": now if now is not None else datetime.now().timestamp(), "data": data}


def peer_name():
    """The other machine's name for 'not counted' (the household's other device), never invented."""
    try:
        from ..peers import link
        o = link.others(link.household())
        return (o[0].get("name") or "") if o else ""
    except Exception:  # noqa: BLE001
        return ""


def from_cable(req, call=None, now=None, cache_dir=None):
    """Ask the other machine for its numbers over the link (`fieldkit cost ... --json` there), keep the answer in
    state/cost and use it when it is fresher than a day. No cable: the kept copy, if it is fresher than a day.
    -> (path of a JSON file holding the peer's numbers, measured_at epoch) or (None, None). Never a guess (row 20)."""
    now = datetime.now().timestamp() if now is None else now
    key = "-".join(str(req[k]).replace(":", "").replace("/", "-") for k in sorted(req) if k != "verb")
    cdir = Path(cache_dir) if cache_dir else settings.state_root() / "state" / "cost"
    f = cdir / ("cable-%s.json" % key)
    try:
        if call is None:
            from ..peers import link
            call = link.call
        r = call(dict(req, verb="cost"), timeout=120)
    except Exception as e:  # noqa: BLE001 - no link on this machine / not paired: the kept copy below
        r = {"ok": False, "error": str(e)}
    if r.get("ok") and isinstance(r.get("data"), dict) and isinstance(r.get("measured_at"), (int, float)):
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(dict(r["data"], measured_at=r["measured_at"])), encoding="utf-8")
        except OSError:
            pass
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        at = float(d["measured_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    return (str(f), at) if 0 <= now - at < FRESH_S else (None, None)


def when_text(at):
    return datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M")


def line(r):
    tag = r["machine"] or "this machine"
    head = "cost %s %s: $%.2f in %d session(s)" % (r["date"], tag, r["usd"], r["sessions"])
    if r["unpriced_calls"]:
        head += " (+%d unpriced call(s))" % r["unpriced_calls"]
    t = r["top"]
    if t:
        head += "; most expensive: \"%s\" $%.2f (%s %s)" % (_cut(t["title"] or "untitled", 70), t["usd"], t["source"],
                                                             t["session"][:8])
        if t["what"]:
            head += ", did: %s" % t["what"]
    p = r.get("peer")
    if p:
        head += "; both machines $%.2f (%s $%.2f)" % (r["usd_both"], p["machine"] or "other", p["usd"])
        if p.get("measured_at"):
            head += "; the other machine measured it %s" % when_text(p["measured_at"])
    else:
        head += "; %s not counted: the other machine's numbers have not come over the cable" % (
            r.get("peer_name") or "the other machine")
    return head


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["report"]:                        # both machines over a window, the bill beside it (I1)
        from . import cost_report
        return cost_report.main(argv[1:])
    if argv[:1] == ["firstcall"]:                     # the first call's tokens, measured here, by part (O1)
        from . import first_call
        return first_call.main(argv[1:])
    # the report's options are listed here too: one `fieldkit cost --help` shows both actions (its card's inputs are
    # checked against this help, 2026-10-28)
    p = argparse.ArgumentParser(prog="fieldkit cost", description="One daily cost line for the owner.",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="report: both machines per day over a window, the real bill beside it\n"
                                       "  fieldkit cost report --since DATE [--until DATE] [--peer FILE] [--bill USD] "
                                       "[--mine] [--json]\n  (fieldkit cost report --help)")
    p.add_argument("action", choices=["daily", "report", "firstcall"])
    p.add_argument("--date", help="the day, YYYY-MM-DD (default yesterday, local)")
    p.add_argument("--peer", action="append", default=[], help="the other machine's `cost daily --json` file")
    p.add_argument("--json", action="store_true", help="one JSON object, nothing else on stdout")
    a = p.parse_args(argv)
    try:
        day = date.fromisoformat(a.date) if a.date else date.today() - timedelta(days=1)
    except ValueError as e:
        p.error("--date: %s (use YYYY-MM-DD)" % e)
    missing = [f for f in a.peer if not os.path.isfile(f)]
    if missing:
        print("cost: not found: " + ", ".join(missing), file=sys.stderr)
        return 2
    r = measure_day(day)
    peer, where, at = None, None, None
    if not a.peer:                                    # the other machine's own answer over the cable, fresher than a day
        cab, at = from_cable({"kind": "daily", "date": day.isoformat()})
        peer, where = peer_numbers(day, r["machine"], [Path(cab)]) if cab else (None, None)
    if not peer:
        peer, where = peer_numbers(day, r["machine"], [Path(f) for f in a.peer] or peer_candidates())
        at = None
    r["peer"] = {"machine": peer.get("machine") or "", "usd": round(float(peer["usd"]), 2), "file": where,
                 "measured_at": at} if peer else None
    r["peer_name"] = peer_name()
    r["usd_both"] = round(r["usd"] + r["peer"]["usd"], 2) if peer else None
    if a.json:
        print(json.dumps(r))
    else:
        print(line(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
