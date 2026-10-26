"""The boss's side of the household: every device at a glance, and one note to many.

    fieldkit orchestrate devices [--check] [--json]          the household: role, OS, link, brain; --check asks
                                                              each device now (hello) and says who answers
    fieldkit orchestrate note TEXT [--to workers|all|NAME,NAME] [--needs-you] [--json]
                                                              one note to each named device, one line per device
    fieldkit orchestrate job run|agent|status ...             a job on a tree the boss sends (orchestrate_work.py)

Stage O1 of the household orchestrator (local/PROJECT-MANDATE-PLAN.md section 19): one boss, many workers, any OS.
The registry and the wire are fieldkit/peers/link.py (household.json, one household key); this file is only the
boss's fan-out, so later stages (job kinds, the boss loop) grow here and not inside the link.
"""
import argparse
import json
import sys

from .peers import link


def targets(h, to="workers"):
    """-> the device rows `to` names: workers, all (every other device), or a comma list of names."""
    o = link.others(h)
    if to in (None, "", "workers"):
        return [d for d in o if d.get("role") == "worker"]
    if to == "all":
        return o
    names = [x.strip() for x in str(to).split(",") if x.strip()]
    missing = [n for n in names if not link.device(h, n) or n == h.get("me")]
    if missing:
        raise ValueError(f"not another device of this household: {', '.join(missing)} "
                         f"(it has: {', '.join(d['name'] for d in o) or 'none'})")
    return [link.device(h, n) for n in names]


def devices(check=False, call_fn=None):
    """-> [{name, role, os, link, address, brain, me, answers, host, seen_os, error}]"""
    call_fn = call_fn or link.call
    h = link.household()
    rows = []
    for d in h.get("devices", []):
        r = {"name": d["name"], "role": d.get("role") or "", "os": d.get("os") or "", "link": d.get("link"),
             "address": d.get("address", []), "brain": d.get("brain") or "", "me": d["name"] == h.get("me")}
        if check and not r["me"]:
            a = call_fn({"verb": "hello"}, device=d["name"])
            r.update(answers=bool(a.get("ok")), host=a.get("host", ""), seen_os=a.get("os", ""),
                     error="" if a.get("ok") else a.get("error", ""))
        rows.append(r)
    return h, rows


def note(text, to="workers", needs_you=False, call_fn=None):
    """-> [{device, ok, seq | error}], one row per target; a device that does not answer does not stop the rest."""
    call_fn = call_fn or link.call
    h = link.household()
    out = []
    for d in targets(h, to):
        r = call_fn({"verb": "message", "text": text, "needs_you": needs_you}, device=d["name"])
        out.append({"device": d["name"], "ok": bool(r.get("ok")), "seq": r.get("seq"),
                    "error": "" if r.get("ok") else r.get("error", "")})
    return out


def main(argv=None):
    if argv and argv[0] in ("job", "work"):            # stage O3: jobs on a received tree (orchestrate_work.py)
        from . import orchestrate_work
        return orchestrate_work.main(argv)
    ap = argparse.ArgumentParser(prog="fieldkit orchestrate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["devices", "note"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--check", action="store_true", help="devices: ask each device now whether it answers")
    ap.add_argument("--to", default="workers", help="note: workers (default), all, or NAME,NAME")
    ap.add_argument("--needs-you", action="store_true", help="note: the owner must act ([NEEDS YOU])")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not link.household():
        print("ORCHESTRATE  no household here: fieldkit peers link setup, or peers link devices import FILE")
        return 2
    if a.action == "devices":
        h, rows = devices(a.check)
        if a.json:
            print(json.dumps(rows, indent=1, ensure_ascii=False))
        else:
            print(f"HOUSEHOLD  {len(rows)} device(s); this one: {h.get('me')}; boss: "
                  f"{next((r['name'] for r in rows if r['role'] == 'boss'), 'none named')}")
            for r in rows:
                state = ("this device" if r["me"] else "" if "answers" not in r else
                         f"answers ({r['host']}, {r['seen_os']})" if r["answers"] else f"NO ANSWER: {r['error'][:80]}")
                print(f"  {r['name']:<22} {r['role'] or '-':<7} {r['os'] or '?':<9} {r['link']:<6} "
                      f"{','.join(r['address']):<28} brain {r['brain'] or 'none':<10} {state}")
        bad = [r for r in rows if r.get("answers") is False]
        print(f"VERDICT  {len(rows)} device(s)" + (f", {len(bad)} not answering" if bad else
                                                  ", all answer" if a.check else ""))
        return 3 if bad else 0
    if not a.rest:
        print("note TEXT [--to workers|all|NAME,NAME]")
        return 2
    try:
        rows = note(" ".join(a.rest), a.to, a.needs_you)
    except ValueError as e:
        print(f"NOT SENT  {e}")
        return 2
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    for r in rows if not a.json else []:
        print(f"  {'SENT' if r['ok'] else 'NOT SENT':<9} {r['device']:<22} " + (f"#{r['seq']}" if r["ok"] else r["error"]))
    bad = [r for r in rows if not r["ok"]]
    if not a.json:
        print(f"VERDICT  {len(rows) - len(bad)} of {len(rows)} device(s) got the note" if rows else
              "VERDICT  no device matched --to")
    return 3 if bad or not rows else 0


if __name__ == "__main__":
    sys.exit(main())
