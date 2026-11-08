# Version: 1.0.0 · updated 26-10-30
"""NO PASSIVE WAITING (plan Z1): find the started task that nobody is looking at.

    fieldkit task watch [ID[,ID...]] [--once] [--act] [--stalled MIN] [--hours H] [--json]
                                                with ids: only those tasks (B4, 2026-10-31: a watch woke on
                                                old tasks of another Zero instead of the one it watched)
    fieldkit task watch --schedule [--apply|--remove]        every 5 minutes: `task watch --act --scheduled`

One line per task that needs a decision, exit 3 when there is one:

    UNHANDLED  ended blocked or failed (or never started, or died) and no later task of the same brief was started
    STALLED    still running but silent for more than MIN minutes (default 10): no report, no log growth, no note
               from its device, and, for a task here, its process is alive (a dead one is DIED)
    DIED       started here, its process is gone, no report came: the run ended without saying so

Silence is the newest of: the start, the last report, the job log's mtime, the newest cable note that names the task
or comes from its device. Liveness of a process here is `settle` (tasks.py), the same call `task status` makes; a
task on a peer cannot be proven dead from here, only silent.

--act: pings the peer of a silent peer task (a status request over the cable, one per STALLED task per 30 minutes),
and for UNHANDLED and DIED adds an owner-inbox line that names the exact next command (the inbox keeps one open
item per title). Every finding is also written to state/lifecycle/task-watch.json; Zero's hook reads that file
(zero/net.py) so an idle Zero is told at its next prompt, and a room line makes every session see it.

Evidence (2026-10-29 23:17 to 00:04): a VAIO job ended `blocked` at 23:2x; Zero sat idle for about 45 minutes because
nothing woke it and no one polled. Zero tokens: plain code, no model.
"""
import json
import os
import re
import time

from . import tasks

STALLED_MIN = 10
HOURS = 24                              # an ended task older than this is history, not a finding
PING_EVERY_S = 1800
ENDED = ("blocked", "failed", "not-started", "died")
#: a task a Zero (or the owner) closed by hand with `fieldkit task report ID --status failed --note "closed by ..."`
#: is handled: it is history, not a finding (B4: "closed by Zero 13" tasks kept waking every later Zero)
CLOSED = re.compile(r"\bclosed\b", re.I)


def _t(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return 0.0


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except (OSError, TypeError, ValueError):
        return 0.0


def restart_cmd(t):
    """The command that starts the task again: its brief, or for a no-AI run the line to rerun from `task status`."""
    if not t.get("brief"):
        return f"fieldkit task status   # {t['id']} was a no-AI run ({t.get('how') or '?'}): read why, then rerun its line"
    return f"fieldkit task start {t['brief']} --again" + (f" --on {t['on']}" if t.get("on") not in (None, "here") else "")


def _followed(t, led):
    """True when a later task of the same brief (same hash, else same title) is started or done."""
    for o in led.values():
        if o["id"] == t["id"] or o.get("status") in ENDED:
            continue
        same = (o.get("hash") and o.get("hash") == t.get("hash")) or (o.get("title") and o.get("title") == t.get("title"))
        if same and _t(o.get("at")) >= _t(t.get("at")):
            return True
    return False


def silence(t, notes=()):
    """-> the newest sign of life (epoch s): start, report, log, a cable note naming the task or from its device."""
    seen = [_t(t.get("at")), _t(t.get("reported")), _mtime(t.get("log"))]
    for n in notes:
        if t["id"] in (n.get("text") or "") or (t.get("on") not in (None, "here") and n.get("from_host") == t.get("on")):
            seen.append(_t(n.get("at")))
    return max(seen)


def closed(t):
    return bool(t.get("closed")) or bool(CLOSED.search(t.get("note") or ""))


def findings(led, now=None, stalled_min=STALLED_MIN, hours=HOURS, notes=(), alive=None, read_upto=None, ids=None):
    """-> [{state, id, line, next}]. Pure apart from the files `silence` stats. Marks died tasks in `led` (settle).
    ids: only these tasks (and no note findings)."""
    now = time.time() if now is None else now
    tasks.settle(led, alive)
    out = []
    for t in led.values():
        st, tid, title = t.get("status"), t["id"], (t.get("title") or "")[:50]
        if ids and tid not in ids:
            continue
        if st in ENDED and closed(t):
            continue
        if st in ENDED:
            ended = _t(t.get("reported")) or _t(t.get("at"))
            if now - ended > hours * 3600 or _followed(t, led):
                continue
            if st == "died":
                out.append({"state": "DIED", "id": tid, "line": f"DIED {tid} on {t.get('on')}: {title}: process "
                            f"{t.get('pid')} ended with no report; log {t.get('log') or '-'}",
                            "next": restart_cmd(t)})
            else:
                why = (t.get("note") or t.get("answer") or "")[:120]
                out.append({"state": "UNHANDLED", "id": tid, "line": f"UNHANDLED {tid} {st} on {t.get('on')}: {title}"
                            f" ({int((now - ended) / 60)} min ago, no follow-up started): {why}",
                            "next": restart_cmd(t)})
        elif st == "started":
            quiet = (now - silence(t, notes)) / 60
            if quiet > stalled_min:
                out.append({"state": "STALLED", "id": tid, "line": f"STALLED {tid} on {t.get('on')}: {title}: silent "
                            f"{int(quiet)} min (limit {stalled_min})",
                            "next": "fieldkit peers link inbox; if the peer is gone: fieldkit task report "
                                    f"{tid} --status failed --note stalled"})
    # plan C7 (inbox 204): an idle session never reads a cable note. A [NEEDS YOU] note nobody read within the wait
    # (link_receipt.unread, 10 min) is a finding for Zero, who pokes the addressed session with the app's
    # send_message (Remote Control) instead of waiting for the next hour
    try:
        from ..peers import link_receipt
        late = [] if ids else link_receipt.unread(list(notes), now=now, upto=read_upto)
    except Exception:  # noqa: BLE001 - no receipt files: nothing is judged
        late = []
    for n in late:
        if "[NEEDS YOU]" not in (n.get("text") or "").upper():
            continue
        first = (n.get("text") or "").strip().splitlines()[0][:100] if (n.get("text") or "").strip() else ""
        out.append({"state": "UNREAD", "id": f"note-{n.get('n')}",
                    "line": f"UNREAD note #{n.get('n')} from {n.get('from_host')} ({n['age_minutes']} min, NEEDS YOU): "
                            f"{first}",
                    "next": "Zero: send the addressed session a short message with the app's send_message "
                            f"(Remote Control) saying note #{n.get('n')} waits in `fieldkit peers link inbox`"})
    return out


def _state_file():
    return tasks.state_dir() / "task-watch.json"


def hook_lines(max_age_s=1800, now=None):
    """The findings of the last `--act` run, for Zero's prompt hook; nothing when they are old or there are none."""
    now = time.time() if now is None else now
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if now - float(d.get("at") or 0) > max_age_s or not d.get("findings"):
        return []
    return ["TASK WATCH (a started task needs you, do not wait): " + f["line"] + f"  -> {f['next']}"
            for f in d["findings"][:5]]


def act(found, ping=None, inbox_add=None, led=None, now=None, room=None):
    """Write the state file, the room line, the owner-inbox lines (UNHANDLED, DIED) and ping silent peers.
    -> the list of what was done, one line each."""
    now = time.time() if now is None else now
    done = []
    if ping is None:
        def ping(dev, tid):
            from ..peers import link
            return link.call({"verb": "message", "text": f"fieldkit-task {tid}: status? reply with one line: "
                              "running, blocked or done"}, timeout=15, device=dev)
    if inbox_add is None:
        from ..autopilot.inbox import add as inbox_add
    room = room or tasks._room
    sf = _state_file()
    try:
        prev = json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    pinged = dict(prev.get("pinged") or {})
    for f in found:
        t = (led or {}).get(f["id"]) or {}
        if f["state"] == "STALLED" and t.get("on") not in (None, "here") and now - pinged.get(f["id"], 0) > PING_EVERY_S:
            try:
                r = ping(t["on"], f["id"])
                done.append(f"pinged {t['on']} for {f['id']}: " + ("sent" if r.get("ok", True) else str(r)[:80]))
            except Exception as e:  # noqa: BLE001 - a dead cable is itself the finding
                done.append(f"ping of {t['on']} for {f['id']} failed: {str(e)[:80]}")
            pinged[f["id"]] = now
        if f["state"] in ("UNHANDLED", "DIED"):
            inbox_add(f"{f['state']} task {f['id']}", f["line"], command=f["next"], kind="task-watch")
            done.append(f"inbox: {f['state']} {f['id']}")
    tasks_key = sorted(f["id"] + f["state"] for f in found)
    if tasks_key != prev.get("key") and found:
        room("task-watch", "; ".join(f["line"] for f in found)[:400])
    sf.write_text(json.dumps({"at": now, "key": tasks_key, "pinged": pinged, "findings": found}, indent=1,
                             ensure_ascii=False), encoding="utf-8")
    return done


def schedule(a, plan=None, apply=None):
    """--schedule: print (or --apply, --remove) the 5-minute timer for `task watch --act --scheduled`."""
    from ..zero import schedule as S
    steps = (plan or (S.remove_plan if a.remove else S.plan))(job=S.WATCH)
    for s_ in steps:
        print(" ".join(map(str, s_[1:])) if s_[0] == "run" else f"{s_[0]} {s_[1]}")
    if a.apply or a.remove:
        return (apply or S.apply)(steps)
    print("NEXT: fieldkit task watch --schedule --apply")
    return 0


def main(a, ledger=None, notes=None, sleep=time.sleep):
    """a: the parsed `task watch` arguments. Without --once (and --scheduled) it keeps looking every `a.every` s and
    returns when a finding appears, so run as a background command it wakes the session that started it.
    -> exit code (0 nothing, 3 findings)."""
    if a.schedule or a.remove:
        return schedule(a)
    while True:
        code, over = _once(a, ledger, notes)
        if code or over or a.once or a.scheduled or ledger is not None:
            return code
        sleep(a.every)


def ended(led, ids):
    """-> [line] for each watched id that is no longer running (done, closed, blocked...; C1 2026-10-31: a watch on
    an id never woke when that task ended DONE), or None while any watched id still runs. An id the ledger does not
    know counts as ended (a typo must not keep a background watch alive forever)."""
    out = []
    for tid in sorted(ids or ()):
        t = led.get(tid)
        if t is None:
            out.append(f"UNKNOWN {tid}: no such task in the ledger")
        elif t.get("status") == "started":
            return None
        else:
            out.append(f"ENDED {tid} {t.get('status')} on {t.get('on')}: {(t.get('title') or '')[:50]}")
    return out


def _once(a, ledger, notes):
    new = tasks.ingest()
    tasks.collect_jobs(tasks.ledger())
    led = ledger if ledger is not None else tasks.ledger()
    if notes is None:
        try:
            from ..peers import link
            notes = link.inbox()
        except Exception:  # noqa: BLE001 - no cable: silence is judged from the files
            notes = []
    ids = {x.strip() for x in (getattr(a, "arg", None) or "").split(",") if x.strip()} or None
    found = findings(led, stalled_min=a.stalled, hours=a.hours, notes=notes, ids=ids)
    done = act(found, led=led) if a.act else []
    over = ended(led, ids) if ids else None
    if a.json:
        print(json.dumps({"findings": found, "done": done, "new_reports": len(new), "ended": over},
                         indent=1, ensure_ascii=False))
    elif over and not found:
        print("\n".join(over) + "\nNEXT: fieldkit task status   # read its report")
    elif found:
        print("\n".join(f["line"] + f"\n  NEXT: {f['next']}" for f in found))
        print("\n".join(done))
    elif not a.scheduled:
        print("task watch: nothing waits on you (no unhandled, stalled or dead task)\nNEXT: carry on")
    return (3 if found else 0), bool(over)
