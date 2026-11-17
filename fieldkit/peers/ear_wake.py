"""The ear supervisor (P28 A, owner 2026-11-16: "a day was lost waiting on idle sessions").

A note addressed to one station waits in the net log until that station's ear reads it. When the ear is not running
(the session is idle, its background command ended, or it never started one), nothing wakes the session: the note
waits for the owner to notice. This supervisor finds every such note older than WAKE_AGE_S and writes ONE net note to
Zero per note: `WAKE <host session or station> <note id>`. Zero is the only session with the desktop app's
send_message tool; Zero's own ear returns the WAKE line and Zero relays it (skill fieldkit-net, rule "WAKE").

No model, no daemon of its own: it runs inside the `relay listen --follow` loop (every WAKE_EVERY_S) and from the hog
5-minute watch, so it works while any ear or the schedule runs. Idempotent: a note is woken once (state in
<link home>/ears/woken.json).

    fieldkit peers link relay wake [--json]       find and send the wakes now; exit 3 when it sent any
"""
import json
import os
import time

from . import link

WAKE_AGE_S = 20
WAKE_EVERY_S = 10
DEAF_WARN = 1          # deaf stations with a waiting note before `zero check` / `hog check` say WARN
KEEP_IDS = 2000
MAX_AGE_S = 86400   # older notes are history, not a wake (no flood on the first run)


def woken_path():
    return link.home() / "ears" / "woken.json"


def load_woken():
    try:
        d = json.loads(woken_path().read_text(encoding="utf-8"))
        return set(d) if isinstance(d, list) else set()
    except (OSError, ValueError):
        return set()


def save_woken(ids):
    p = woken_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(ids)[-KEEP_IDS:]), encoding="utf-8")
    os.replace(tmp, p)


def waiting(stations, ears, entries, zero, now, woken=frozenset(), age=WAKE_AGE_S, traffic=("msg",)):
    """Pure. -> [{station, host_session, id, at}] for every note addressed to ONE station (not a broadcast) that the
    station's ear has not read, older than `age` s, not yet woken. `stations`: {sid: rec} (net-stations.json);
    `ears`: {sid: {"state": running|stopped|never, "heard": offset}}; `entries`: [(offset_after_line, entry)].
    Zero itself is never woken through Zero; an OFF or ended station is skipped."""
    out, seen = [], set(woken)
    for sid, rec in sorted(stations.items()):
        if sid == zero or rec.get("off"):
            continue
        ear = ears.get(sid) or {}
        if ear.get("state") == "running":
            continue
        heard = ear.get("heard")
        for off, e in entries:
            if e.get("to") != sid or e.get("from") == sid or e.get("kind") not in traffic:
                continue
            if heard is not None and off <= int(heard):
                continue
            waited = now - float(e.get("ts") or now)
            if waited < age or waited > MAX_AGE_S or e.get("id") in seen:
                continue
            seen.add(e.get("id"))
            out.append({"station": sid, "host_session": rec.get("host_session") or rec.get("session") or "",
                        "id": e.get("id"), "at": e.get("at", "")})
    return out


def wake_text(w):
    return f"WAKE {w['host_session'] or w['station']} {w['id']}"


def deaf_waiting(items):
    """-> sorted stations that have at least one waiting note (the WARN count of zero check / hog check)."""
    return sorted({w["station"] for w in items})


def scan(now=None):
    """Read the live net. -> (zero station, waiting items). Never raises (a supervisor must not die on a torn file)."""
    from ..zero import net
    from . import link_relay
    now = time.time() if now is None else now
    try:
        n = net.load_net()
        zero = net.zero_of(n)
        st = net.load_stations()["stations"]
        ears = {sid: link_relay.ear_state(sid) for sid in st if net.machine_of(sid) in (None, "", net.machine())}
        st = {s: r for s, r in st.items() if s in ears}
        since = min([int(e["heard"]) for e in ears.values() if e.get("heard") is not None] or [0])
        return zero, waiting(st, ears, net.entries(min(since, net.log_size())), zero, now, load_woken())
    except Exception:  # noqa: BLE001
        return None, []


def run(now=None, send=None):
    """Find the waiting notes and write one WAKE note to Zero per note. -> list of the items woken."""
    from ..zero import net
    zero, items = scan(now)
    if not zero or not items:
        return []
    send = send or (lambda text: net.append("msg", "ear-supervisor@" + net.machine(), to=zero, text=text))
    done = load_woken()
    for w in items:
        send(wake_text(w))
        done.add(w["id"])
    save_woken(done)
    return items


def main(as_json=False):
    items = run()
    if as_json:
        print(json.dumps({"woken": items}))
    elif items:
        for w in items:
            print(f"WAKE SENT to Zero: {wake_text(w)} (note from {w['at']})")
    else:
        print("EARS: no note waits on a deaf station")
    print("NEXT: Zero relays each WAKE with send_message to the host session named" if items else
          "NEXT: nothing to do")
    return 3 if items else 0


def hog_findings(items=None):
    """The hog's row (fieldkit hog check, live pass): deaf stations with a note waiting > WAKE_AGE_S -> WARN."""
    from ..hog import rules
    items = scan()[1] if items is None else items
    deaf = deaf_waiting(items)
    if len(deaf) < DEAF_WARN:
        return []
    return [rules._f("ears", "warn", f"{len(deaf)} deaf station(s) with a waiting note ({', '.join(deaf)}): "
                     "Zero relays the WAKE lines", {"stations": deaf, "notes": len(items)})]
