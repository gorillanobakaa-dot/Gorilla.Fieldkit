# Version: 1.0.0 · updated 26-10-26
"""The command layer of the agent net (set 2): orders, ground claims, drift, idle-without-done, the wake, and orders
carried across rotation. The owner, 2026-10-26: "someone has to be in command, to coordinate sessions, to archive
unused sessions, to ask the idle devices why are they doing and why aren't they working towards the common goal".

    fieldkit zero task STATION --objective TEXT --area PATH[,PATH] --done TEXT [--drift-min 45] [--idle-min 45]
                                   Zero gives a station its order; its area is its ground (two live orders never
                                   overlap); writes outside it are refused by the PreToolUse hook (claims.py)
    fieldkit zero release STATION [--finished]   Zero closes the order (done, or released); its ground is free
    fieldkit zero orders                         every order: objective, area, sitrep, last activity, idle query
    fieldkit zero checkin --relieve OLD          a rotated station's relief takes over its order and ground

The comms check (`fieldkit zero check`, every 15 min by the schedule) runs sweep(): plain code, no model, no tokens.

    DRIFT        a station's changes outside its area (git diff from the order's base + the room log of its session)
                 or no activity for --drift-min while its sitrep says WORKING
    IDLE         an open order and the station IDLE (sitrep) or without activity for --idle-min (not WAITING,
                 BLOCKED or DONE): it is asked once per idle period "why idle, what is blocking you"; its next
                 check-in sitrep is its answer, on the Board
    ARCHIVE      a station that reported DONE whose session ended its turn with no open PR (fieldkit rotate finished)
    WAKE         only a NEW finding wakes anyone, once: one net message to Zero (a Claude Code Zero reads it from the
                 room hook at its next prompt; an OpenCode Zero gets it pushed; the far computer over the cable).
                 Zero SILENT or MISSING: a `fieldkit inbox` item names the 2IC (`handover --assume`) and the 2IC is sent
                 the same message. A Claude DESKTOP session that is idle cannot be woken by the harness: no CLI door
                 into the app exists; only the app's own send_message can (said on the Board, never worked around).

Nothing here waits for an answer, asks the owner, or holds a build: it records and flags. Silence is numbers on the
Board row "| Command (fieldkit zero) |".
"""
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from . import channels as ch
from . import claims
from . import net as N

DRIFT_MIN = 45
IDLE_MIN = 45
BOARD_KEY = "| Command (fieldkit zero) |"
SEEN_KEEP_S = 7 * 24 * 3600
RESTING = ("DONE", "WAITING", "BLOCKED")


# ---------------------------------------------------------------- ground

def default_free():
    """Always writable, whatever the order: the coordination folder (PLAN.md), temp and scratchpads, agent memory,
    plans and side-session notes."""
    home = Path.home()
    out = [N.coord(), Path(tempfile.gettempdir()), home / ".claude" / "projects", home / ".claude" / "plans",
           home / ".gemini" / "antigravity" / "brain"]
    if os.environ.get("LOCALAPPDATA"):
        out.append(Path(os.environ["LOCALAPPDATA"]) / "Claude-Data")
    return [claims.norm(p) for p in out]


def active(rec):
    o = (rec or {}).get("order")
    return o if o and not o.get("closed") else None


def claims_of(st, net=None):
    relief = st["meta"].get("relief") or {}
    out = {}
    for s, rec in st["stations"].items():
        o = active(rec)
        if o:
            out[s] = {"order": o["id"], "area": o["area"], "at": o["at"], "objective": o["objective"][:200],
                      "relief": (relief.get(s) or {}).get("ts")}
    return out


def write_claims(st, net=None):
    """net-claims.json: what the PreToolUse hook reads (claims.py). Written at task / release / relief / rotation."""
    net = net or N.load_net()
    free = default_free() + [claims.norm(p) for p in net.get("free") or []]
    N._atomic(N.coord() / "net-claims.json", json.dumps(
        {"written": time.time(), "epoch": net.get("epoch"), "free": free, "claims": claims_of(st, net)},
        indent=1, ensure_ascii=False))


def _git(args, cwd, timeout=30):
    try:
        r = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def repos_of(area, git=_git):
    """The git repository (top, HEAD) under each area folder, as the base the drift check diffs from."""
    out = []
    for a in area:
        p = Path(a)
        while not p.exists() and p.parent != p:
            p = p.parent
        top = (git(["rev-parse", "--show-toplevel"], p) or "").strip()
        if not top:
            continue
        top = claims.norm(top)
        if any(r["top"] == top for r in out):
            continue
        out.append({"top": top, "base": (git(["rev-parse", "HEAD"], top) or "").strip()})
    return out


# ---------------------------------------------------------------- orders

def task(me, station, objective, area, done, epoch=None, drift_min=None, idle_min=None, now=None, git=_git,
         channels=None):
    now = time.time() if now is None else now
    net = N.load_net()
    N.require_zero(net, me, epoch)
    station = station if "@" in station else f"{station}@{N.machine()}"
    paths = [claims.norm(a) for a in area if str(a).strip()]
    if not (objective or "").strip() or not paths or not (done or "").strip():
        raise N.Refused("an order needs --objective, --area and --done (what done looks like)")
    repos = repos_of(paths, git)
    with N._Lock():
        st = N.load_stations()
        for s, rec in st["stations"].items():
            o = active(rec)
            if s == station or not o:
                continue
            if not claims.live(s, claims_of(st)[s], st["stations"], now):
                continue
            for a in paths:
                for b in o["area"]:
                    if claims.overlap(a, b):
                        raise N.Refused(f"ground overlaps {s}'s order {o['id']} ({b}): two stations never hold the "
                                        f"same ground; release it first (fieldkit zero release {s})")
        rec = st["stations"].get(station)
        if rec is None:                               # tasked before it checked in: a grace lease of one TTL
            rec = st["stations"][station] = {"first": now, "kind": N.kind_of(station), "machine": N.machine_of(station),
                                             "ttl": N.ttl_s(net), "lease_until": now + N.ttl_s(net), "via": "task",
                                             "epoch": net.get("epoch")}
        old = active(rec)
        order = {"id": N.new_id("O"), "objective": " ".join(objective.split()), "area": paths,
                 "done": " ".join(done.split()), "by": me, "at": now,
                 "at_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), "repos": repos,
                 "drift_min": float(drift_min or net.get("drift_min") or DRIFT_MIN),
                 "idle_min": float(idle_min or net.get("idle_min") or IDLE_MIN)}
        if old:
            order["replaces"] = old["id"]
        rec["order"] = order
        rec.pop("asked", None)
        N.save_stations(st)
        write_claims(st, net)
    N.append("order", me, to=station, text=f"ORDER {order['id']}: {order['objective']}", ref=order.get("replaces"),
             data={"order": {k: order[k] for k in ("id", "objective", "area", "done")}}, now=now)
    sent = _send(net, me, station, f"ORDER {order['id']} from Zero: {order['objective']}. Your ground: "
                 f"{', '.join(paths)} (writes outside it are refused). Done when: {order['done']}. Report with "
                 "fieldkit zero checkin.", channels, now)
    return {"order": order, "station": station, "sent": sent}


def release(me, station, finished=False, epoch=None, now=None):
    now = time.time() if now is None else now
    net = N.load_net()
    N.require_zero(net, me, epoch)
    station = station if "@" in station else f"{station}@{N.machine()}"
    with N._Lock():
        st = N.load_stations()
        o = active(st["stations"].get(station))
        if not o:
            raise N.Refused(f"{station} has no open order")
        o.update(closed=now, outcome="done" if finished else "released")
        (st["meta"].get("relief") or {}).pop(station, None)
        N.save_stations(st)
        write_claims(st, net)
    N.append("order", me, to=station, ref=o["id"], text=f"ORDER {o['id']} {'DONE' if finished else 'released'}: "
             f"{station}'s ground is free", now=now)
    return {"order": o, "station": station}


def _move(st, old, new, now, how):
    o = active(st["stations"].get(old))
    if not o:
        return None
    rec = st["stations"].setdefault(new, {"first": now, "kind": N.kind_of(new), "machine": N.machine_of(new)})
    rec["order"] = dict(o, relieved=old, relief_at=now)
    st["stations"][old]["order"] = dict(o, closed=now, outcome=f"{how} {new}")
    (st["meta"].get("relief") or {}).pop(old, None)
    return rec["order"]


def relieve(me, old, now=None):
    """`fieldkit zero checkin --relieve OLD`: the relief of a rotated station takes its order and its ground. Allowed
    when OLD left a relief (rotation) less than a day ago, or OLD is MISSING or signed off."""
    now = time.time() if now is None else now
    old = old if "@" in old else f"{old}@{N.machine()}"
    with N._Lock():
        st = N.load_stations()
        orec = st["stations"].get(old)
        if not active(orec):
            raise N.Refused(f"{old} has no open order to hand over")
        pend = (st["meta"].get("relief") or {}).get(old) or {}
        if not (pend and now - float(pend.get("ts") or 0) < claims.PENDING_TTL_S) \
                and N.state_of(orec, now) not in ("MISSING", "OFF"):
            raise N.Refused(f"{old} is {N.state_of(orec, now)} and left no relief (fieldkit rotate handover leaves "
                            "one); Zero re-tasks instead (fieldkit zero task)")
        o = _move(st, old, me, now, "relieved by")
        st["stations"][old]["off"] = True
        N.save_stations(st)
        write_claims(st)
    N.append("relief", me, to=N.zero_of(N.load_net()) or "*", ref=o["id"],
             text=f"{me} relieved {old}: order {o['id']} and its ground carried over", now=now)
    return o


def carry(old, new, now=None):
    """Zero rotated (`handover --take`): the old Zero's order goes with the net. Never raises."""
    try:
        now = time.time() if now is None else now
        with N._Lock():
            st = N.load_stations()
            o = _move(st, old, new, now, "carried to")
            if o:
                N.save_stations(st)
                write_claims(st)
        return o
    except Exception:  # noqa: BLE001
        return None


def on_rotate(station, now=None):
    """Called from net.on_rotate (fieldkit rotate handover): a station with an open order leaves a relief and the
    brief tells the fresh session its first command. -> the brief line or ""; never raises."""
    try:
        now = time.time() if now is None else now
        with N._Lock():
            st = N.load_stations()
            o = active(st["stations"].get(station))
            if not o:
                return ""
            st["meta"].setdefault("relief", {})[station] = {"ts": now, "order": o["id"]}
            N.save_stations(st)
            write_claims(st)
        N.append("msg", "net", text=f"{station} is rotating: order {o['id']} waits for its relief "
                 f"(fieldkit zero checkin --relieve {station})", now=now)
        return (f"\nORDER: the session you continue carries order {o['id']} of the agent net: {o['objective']}. "
                f"Ground: {', '.join(o['area'])}. Done when: {o['done']}. Your FIRST net command: "
                f"fieldkit zero checkin --relieve {station}   (the order and its ground move to you)\n")
    except Exception:  # noqa: BLE001 - a rotation never fails because of the net
        return ""


# ---------------------------------------------------------------- the sweep (the scheduled comms check)

def targeted(station, net=None):
    """The channel that reaches this one station, and no other (a broadcast would wake every OpenCode session)."""
    if N.machine_of(station) and N.machine_of(station) != N.machine():
        return [ch.Cable()]
    k = N.kind_of(station)
    if k == "opencode":
        name = station.split(":", 1)[1].rsplit("@", 1)[0]

        def only():
            from ..peers import peers
            return [s for s in peers.sessions() if s.get("name") == name]
        return [ch.OpenCode(sessions=only)]
    if k == "claude":
        return [ch.Room()]
    return [ch.Pull()]


def _send(net, frm, to, text, channels, now):
    """One message, one station; a refusal (net discipline) is said, never raised: the next sweep tries again."""
    try:
        return N.send(frm, text, to=to, channels=channels or targeted(to, net), now=now)
    except N.Refused as e:
        return {"refused": str(e)}


def room_lines(st, path, now):
    """The room log since the last sweep: {session_id: [(ts, verb, obj)]}; the offset is kept in the stations meta."""
    out = {}
    try:
        size = path.stat().st_size
    except OSError:
        return out
    off = st["meta"].get("cmd_room_off")
    off = max(0, size - 262144) if off is None or int(off) > size else int(off)
    with open(path, "rb") as f:
        f.seek(off)
        chunk = f.read(size - off).decode("utf-8", "replace")
    st["meta"]["cmd_room_off"] = size
    for ln in chunk.splitlines():
        p = ln.split("\t")
        if len(p) < 5:
            continue
        try:
            ts = time.mktime(time.strptime(p[0], "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        out.setdefault(p[2], []).append((ts, p[3], p[4]))
    return out


def _outside(path, own, free, others):
    return (path and not any(claims.within(path, a) for a in own) and not any(claims.within(path, f) for f in free)
            and not any(claims.within(path, a) for a in others))


def git_drift(o, own, free, others, git=_git):
    """Paths changed in the order's repositories (committed since its base, or in the working tree) outside its
    area. A repository whose top is inside the area cannot drift; it is not asked."""
    out = []
    for r in o.get("repos") or []:
        if any(claims.within(r["top"], a) for a in own):
            continue
        names = []
        if r.get("base"):
            names += (git(["diff", "--name-only", r["base"], "HEAD"], r["top"]) or "").splitlines()
        names += [x[3:].split(" -> ")[-1].strip('"') for x in (git(["status", "--porcelain"], r["top"]) or "").splitlines()]
        for n in names:
            p = claims.norm(r["top"] + "/" + n.strip())
            if n.strip() and _outside(p, own, free, others) and p not in out:
                out.append(p)
    return out[:20]


def _key(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def sweep(net, now=None, channels_for=None, inbox_add=None, git=_git, finished=None, room_log=None, board=True):
    """Drift, idle-without-done, the archive list and the wake. Plain code; a message only for a NEW finding."""
    now = time.time() if now is None else now
    channels_for = channels_for or (lambda s: targeted(s, net))
    st = N.load_stations()
    stations = st["stations"]
    if not any(r.get("order") for r in stations.values()):
        return {"orders": 0, "findings": [], "new": [], "sent": [], "archive": []}
    if room_log is None:
        from ..autopilot import room
        room_log = room._log()
    rl = room_lines(st, Path(room_log), now)
    free = default_free() + [claims.norm(p) for p in net.get("free") or []]
    held = claims_of(st, net)
    findings, rows, done_st = [], [], []
    for s, rec in sorted(stations.items()):
        o = active(rec)
        if not o:
            continue
        own = o["area"]
        others = [a for t, c in held.items() if t != s and claims.live(t, c, stations, now) for a in c["area"]]
        sid = str(rec.get("session") or "")
        mine_room = [x for k, v in rl.items() if sid and k[:8] == sid[:8] for x in v] if N.kind_of(s) == "claude" else []
        if mine_room:
            rec["room_last"] = max(float(rec.get("room_last") or 0), max(x[0] for x in mine_room))
        act = max(float(rec.get("last") or 0), float(rec.get("room_last") or 0), float(o.get("at") or 0))
        state = ((rec.get("sitrep") or {}).get("state") or "").upper()
        drift = [claims.norm(x[2]) for x in mine_room if x[1] in ("edit", "commit", "ff", "rebase")
                 and _outside(claims.norm(x[2]), own, free, [])]
        drift += [p for p in git_drift(o, own, free, others, git) if p not in drift]
        for p in drift[:20]:
            findings.append({"key": _key("drift", s, o["id"], p), "kind": "DRIFT", "station": s,
                             "text": f"{s} changed {p}: outside its order {o['id']}"})
        quiet_min = (now - act) / 60
        if state == "WORKING" and quiet_min > o.get("drift_min", DRIFT_MIN):
            findings.append({"key": _key("stall", s, o["id"], act), "kind": "NO PROGRESS", "station": s,
                             "text": f"{s} says WORKING on {o['id']} but nothing moved for {int(quiet_min)} min"})
        idle = state == "IDLE" or (quiet_min > o.get("idle_min", IDLE_MIN) and state not in RESTING)
        if idle:
            findings.append({"key": _key("idle", s, o["id"], act), "kind": "IDLE", "station": s,
                             "text": f"{s} idle {int(quiet_min)} min, order {o['id']} not done", "order": o})
        if state == "DONE":
            done_st.append(s)
        asked = rec.get("asked") or {}
        answer = None
        if asked and float(rec.get("sitrep_at") or 0) > float(asked.get("ts") or 0):
            sr = rec.get("sitrep") or {}
            answer = f"{sr.get('state')}: waiting on {sr.get('waiting')}"
        rows.append({"station": s, "order": o["id"], "objective": o["objective"], "state": state or "-",
                     "quiet_min": int(quiet_min), "drift": len(drift), "asked": asked.get("at"), "answer": answer})
    archive = []
    if done_st:
        try:
            from ..tokens import rotate
            fin, _held = (finished or (lambda: rotate.finished(use_gh=True)))()
            for s in done_st:
                sid = str(stations[s].get("session") or "")
                if sid and any(str(f.get("session") or "").startswith(sid[:8]) for f in fin):
                    archive.append(s)
                    findings.append({"key": _key("archive", s, active(stations[s])["id"]), "kind": "ARCHIVE",
                                     "station": s, "text": f"{s} reported DONE, its session ended with no open PR: "
                                                           "archive it"})
        except Exception:  # noqa: BLE001 - the archive list is a hint
            pass
    seen = st["meta"].setdefault("cmd_seen", {})
    for k in [k for k, v in seen.items() if now - float(v) > SEEN_KEEP_S]:
        seen.pop(k)
    new = [f for f in findings if f["key"] not in seen]
    sent, zero = [], N.zero_of(net)
    for f in new:
        if f["kind"] != "IDLE":
            continue
        o = f["order"]
        r = _send(net, "net", f["station"], f"Zero asks (automatic): why idle, what is blocking you? Order {o['id']}: "
                  f"{o['objective']}. Answer with: fieldkit zero checkin --job .. --state WORKING|BLOCKED|WAITING|DONE "
                  "--branch .. --waiting .. --not-verified .. --shared ..", channels_for(f["station"]), now)
        if "refused" in r:
            f["unsent"] = True
            continue
        stations[f["station"]]["asked"] = {"ts": now, "at": time.strftime("%H:%M", time.localtime(now)),
                                           "id": r["entry"]["id"], "order": o["id"]}
        sent.append(r["entry"]["id"])
    wake = [f for f in new if not f.get("unsent")]
    woke = None
    if wake and zero:
        text = (f"COMMAND ({len(wake)} new): " + "; ".join(f"{f['kind']} {f['text']}" for f in wake[:8])
                + (f"; +{len(wake) - 8} more" if len(wake) > 8 else "") + " - fieldkit zero orders")
        zstate = N.state_of(stations.get(zero) or {}, now) if zero in stations else "MISSING"
        if zstate == "UP":
            r = _send(net, "net", zero, text, channels_for(zero), now)
            woke = zero if "refused" not in r else None
        else:
            sec = (net.get("2ic") or {}).get("station")
            title = f"net: Zero {zero} {zstate} with {len(wake)} new command finding(s)"
            why = (text + f". Zero cannot act; the 2IC {sec or '(none named)'} assumes with: "
                   "fieldkit zero handover --assume")
            try:
                if inbox_add:
                    inbox_add(title, why, "fieldkit zero handover --assume")
                else:
                    from ..autopilot import inbox
                    inbox.add(title, why, "fieldkit zero handover --assume", kind="net")
                woke = "inbox"
            except Exception:  # noqa: BLE001
                woke = None
            if sec:
                r = _send(net, "net", sec, f"Zero {zero} is {zstate}. " + text, channels_for(sec), now)
                woke = sec if "refused" not in r else woke
    if woke or not wake:
        for f in wake:
            seen[f["key"]] = now
    with N._Lock():
        cur = N.load_stations()
        for s, rec in stations.items():
            if s in cur["stations"]:
                for k in ("asked", "room_last"):
                    if k in rec:
                        cur["stations"][s][k] = rec[k]
        cur["meta"]["cmd_seen"] = seen
        cur["meta"]["cmd_room_off"] = st["meta"].get("cmd_room_off")
        N.save_stations(cur)
    out = {"orders": len(rows), "rows": rows, "findings": findings, "new": [f["text"] for f in new], "sent": sent,
           "woke": woke, "archive": archive}
    if board:
        try:
            out["board"] = N.write_board(board_row(rows, findings, archive), key=BOARD_KEY)
        except OSError as e:
            out["board"] = f"not written: {e}"
    return out


def board_row(rows, findings, archive):
    kinds = {}
    for f in findings:
        kinds.setdefault(f["kind"], set()).add(f["station"])
    parts = [f"{len(rows)} open order(s)"] + [f"{k}: {', '.join(sorted(v))}" for k, v in sorted(kinds.items())]
    asked = [f"{r['station']} asked {r['asked']}: {r['answer'] or 'no answer yet'}" for r in rows if r.get("asked")]
    return (f"{BOARD_KEY} {'; '.join(parts)} | " + ("; ".join(asked) or "no idle queries")
            + (f"; ARCHIVE: {', '.join(archive)}" if archive else "")
            + " (written by the comms check) | `fieldkit zero orders`; an idle Desktop Zero is woken only by the app |")


# ---------------------------------------------------------------- what the person reads

def orders(now=None):
    now = time.time() if now is None else now
    st = N.load_stations()["stations"]
    out = []
    for s, rec in sorted(st.items()):
        o = rec.get("order")
        if not o:
            continue
        act = max(float(rec.get("last") or 0), float(rec.get("room_last") or 0), float(o.get("at") or 0))
        out.append({"station": s, "order": o, "open": not o.get("closed"), "state": N.state_of(rec, now),
                    "sitrep": rec.get("sitrep"), "quiet_min": int((now - act) / 60), "asked": rec.get("asked")})
    return out


def order_lines(rows):
    if not rows:
        return ["no orders: Zero gives them with fieldkit zero task STATION --objective .. --area .. --done .."]
    out = []
    for r in rows:
        o = r["order"]
        out.append(f"  {'OPEN' if r['open'] else o.get('outcome', 'closed').upper():<10} {o['id']}  {r['station']} "
                   f"({r['state']}, quiet {r['quiet_min']} min)")
        out.append(f"      objective: {o['objective']}")
        out.append(f"      ground:    {', '.join(o['area'])}")
        out.append(f"      done when: {o['done']}")
        if r.get("sitrep"):
            out.append(f"      sitrep:    {r['sitrep'].get('state')}: {r['sitrep'].get('job')} "
                       f"(waiting on {r['sitrep'].get('waiting')})")
        if r.get("asked"):
            out.append(f"      asked why idle at {r['asked'].get('at')} ({r['asked'].get('id')})")
    return out


def hook_doctor(settings=None):
    """-> (ok, detail, snippet): does the tool router's PreToolUse entry also see Edit / Write (the ground claims)?
    The settings file is the owner's: this prints the change, never edits it."""
    settings = Path(settings or (Path.home() / ".claude" / "settings.json"))
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    want = {"Bash", "PowerShell", "Edit", "Write", "MultiEdit", "NotebookEdit"}
    for m in (data.get("hooks") or {}).get("PreToolUse", []):
        if any("fieldkit.autopilot.toolrouter" in (h.get("command") or "") for h in m.get("hooks", [])):
            have = set((m.get("matcher") or "").split("|"))
            missing = sorted(want - have) if m.get("matcher") not in ("*", "") else []
            return (not missing, "tool router hook sees " + (m.get("matcher") or "*")
                    + (f"; add {'|'.join(missing)} to its matcher for the ground claims on edits" if missing else ""),
                    {"matcher": "|".join(sorted(want))})
    return (False, "no tool router PreToolUse hook: python -m fieldkit.autopilot.toolrouter --hook",
            {"matcher": "|".join(sorted(want))})
