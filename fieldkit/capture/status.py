"""`fieldkit capture status [--json] [--peer]`: proof that capture runs on this computer, and on the other one.

Reports: the store, the last capture, the last HOOK event (from the journal, so it is evidence, not a claim), the last
scan, whether each agent hook and the schedule are installed (read from the agents' own files and the system's own
scheduler, via install.check). Exit 0 when hooks and schedule are in place and the store is readable; 3 when a hook or the
schedule is missing; with --peer, 3 as well when the Linux computer is down or reports its own problem.
"""
import json
import platform
import sys
from pathlib import Path

from . import install
from . import store as st

TAIL = 2_000_000


def last_events(root, tail=TAIL):
    """-> {'event': ts, 'scan': ts} of the newest hook event / scan marker in the journal tail, None when absent."""
    out = {"event": None, "scan": None}
    j = Path(root) / "journal.jsonl"
    try:
        with open(j, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return out
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("type") == "event" and out["event"] is None and (e.get("data") or {}).get("origin", "hook") == "hook":
            out["event"] = e.get("ts")
        elif e.get("type") == "scan" and out["scan"] is None:
            out["scan"] = e.get("ts")
        if out["event"] and out["scan"]:
            break
    return out


def collect(store=None, home=None, system=None, run=install._run):
    s = store or st.Store()
    rec = s.recover()
    state = s.state()
    chk = install.check(home, system, run)
    seen = last_events(s.root)
    missing = sorted(f"{k}:{'+'.join(v)}" for k, v in chk.items() if v)
    return {"host": platform.node(),
            "platform": sys.platform, "store": str(s.root), "last_capture": state["last_ts"], "last_hook_event": seen["event"],
            "last_scan": seen["scan"], "items": len(state["items"]), "hook_events": len(state["events"]),
            "installed": chk, "missing": missing, "recovered": rec, "ok": not missing,
            "next": "nothing to do" if not missing else "`fieldkit capture install` (missing: " + ", ".join(missing) + ")"}


def lines(d):
    out = [f"store {d['store']} ({d.get('host', '')}, {d['platform']})",
           f"last capture: {d['last_capture'] or 'never'}; last hook event: {d['last_hook_event'] or 'never'}; "
           f"last scan: {d['last_scan'] or 'never'}; {d['items']} items, {d['hook_events']} hook events"]
    for k, v in d["installed"].items():
        out.append(f"{k}: " + ("absent on this computer" if v is None else "installed" if not v else "NOT installed (" + ", ".join(v) + ")"))
    return out


def peer(call=None, device=None):
    """-> (data or None, error). Asks the Linux computer over the cable link (the fixed `capture` probe)."""
    try:
        from ..peers import link
        call = call or link.call
    except Exception as exc:  # noqa: BLE001
        return None, f"link unavailable: {exc}"
    r = call({"verb": "probe", "name": "capture"}, timeout=60, device=device)
    if not r.get("ok"):
        return None, str(r.get("error") or "no answer")
    d = r.get("data")
    return (d, None) if isinstance(d, dict) else (None, "peer answered without a capture status (its Fieldkit is older: `fieldkit sync --apply` there)")


def probe(arg=None, **kw):
    """The link probe `capture`: this computer's status, read-only (registered in peers/link_probe.PROBES)."""
    d = collect()
    return lines(d), d


def main(argv=None, call=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit capture status")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--peer", action="store_true", help="also ask the Linux computer over the cable link; non-zero if it is down")
    ap.add_argument("--device", help="with --peer: the household device name")
    a = ap.parse_args(argv)
    d = collect()
    rc = 0 if d["ok"] else 3
    if a.peer:
        pd, err = peer(call, a.device)
        d["peer"] = pd if pd is not None else {"ok": False, "error": err}
        if pd is None or not pd.get("ok"):
            rc = 3
            d["next"] = f"peer: {err or 'it reports a missing hook: `fieldkit capture install` on it'}"
    if a.json:
        print(json.dumps(d, indent=1, ensure_ascii=False))
    else:
        print("\n".join(lines(d)))
        if a.peer:
            p = d["peer"]
            print("-- peer --\n" + ("\n".join(lines(p)) if "store" in p else f"PEER DOWN: {p.get('error')}"))
        print(f"NEXT: {d['next']}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
