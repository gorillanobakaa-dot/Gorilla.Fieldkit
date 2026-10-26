# Version: 1.1.0 · updated 26-10-26
"""fieldkit net / fieldkit zero: the agent net. "Zero has been established", the military way, in deterministic code.

Owner, 2026-10-26 (former soldier, verbatim): "i will always think in terms of 0 has been established the military
way, maybe add a mention or an alias there so the harness does not get confused as of what the heck i mean when i
ask for a sit rep or asking it whether the zero has been established" / "a 0 protocol for all the comms between you
too, like time syn, comms check the whole shebang that makes a network stay alive so it does not become inactive
like last night". Correction, same day: "there is no A deputy (Zero Alpha) there is a 2IC a second in command".
The night before, the cable net went quiet with BOTH listeners down and nobody noticed until morning.

    fieldkit zero                         is Zero established? who, epoch, 2IC, every station UP / SILENT / MISSING
    fieldkit zero open [--2ic ST] [--interval 15] [--answer 10] [--quiet-hours 23:00-07:00] [--name TEXT]
                                          establish Zero (alias: establish): this session becomes net control
    fieldkit zero rollcall [--wait MIN] [--status]
                                          ask every station for its sitrep (alias: sitrep); LATE / MISSING listed
    fieldkit zero checkin [--ref ID] [--job .. --state .. --branch .. --waiting .. --not-verified .. --shared ..]
                                          what a station runs at start and on its interval (and to answer a roll)
    fieldkit zero check [--no-heal]       the comms check (alias: comms): leases, misses, cable revive, retries
    fieldkit zero time                    the clocks: this OS's sync state, each cable peer's skew
    fieldkit zero handover --to ST | --take | --assume [--2ic ST]
                                          change of Zero; epoch +1, announced to every station
    fieldkit zero send TEXT [--to ST] [--flash] [--no-ack]   a message with an id that wants an ack
    fieldkit zero ack ID                  acknowledge a message
    fieldkit zero log [N] [--verify]      the net log; --verify checks the hash chain and the signatures
    fieldkit zero close                   Zero closes the net
    fieldkit zero schedule [--apply | --remove]   the comms check every 15 min (Windows task / systemd / launchd)
    fieldkit zero doctor                  files, chain, hook, schedule, cable key, the agent rules, the clock
    fieldkit zero task ST --objective .. --area PATH[,PATH] --done ..   Zero gives a station its order and ground
    fieldkit zero release ST [--finished]  Zero closes an order; fieldkit zero orders lists them (command.py, set 2)
    fieldkit zero checkin --relieve OLD   a rotated station's relief takes its order and ground
    every verb: --as STATION (agents with no session id in their environment), --json, --epoch N (fencing)

Military term -> what it is in IT (the docs and the skill use both, so "zero" never confuses a model):
    net                     the set of agent sessions (stations) on this computer and the cable peer; a cluster
    Zero                    net control station (NCS): the coordinator / leader / primary. ONE at a time
    2IC (second in command) the standby / deputy for failover: assumes Zero when Zero is MISSING (`--assume`)
    outstation / station    a worker session (Claude Code, Gorilla OpenCode, Gemini, Antigravity, Codex, a shell)
    "Zero has been established"   leader elected and announced; NET.yaml holds it with an epoch
    epoch                   fencing token / term number: +1 on every change of Zero; a command carrying an older epoch
                            is refused, so two Zeros can never both act (split-brain refused)
    comms check / radio check   heartbeat / liveness probe; each check-in renews a lease (TTL = 2 x interval)
    SILENT / MISSING        lease expired once (flagged on the PLAN.md Board) / twice (an item in `fieldkit inbox`)
    roll call / sitrep      a broadcast status request with a fixed reply schema; answers due within --answer min
    net log                 append-only audit log, hash-chained (each line carries the sha256 of the line before),
                            signed with the cable pairing key when this computer has one
    acknowledge / ack       delivery receipt; unacked messages are retried, then listed (store-and-forward)
    net discipline          rate limit (6 messages a minute a station) + quiet hours (routine traffic held)
    FLASH                   the highest precedence: sent at once, even in quiet hours

Files (state/coordination, beside PLAN.md; git-ignored; FIELDKIT_COORDINATION_DIR overrides):
    NET.yaml            the net: open/closed, Zero {station, machine, session}, epoch, 2IC, interval, quiet hours
    net-stations.json   one lease per station, its last sitrep, what it has seen
    net-log.jsonl       the net log
Every station works offline: no cloud, no telemetry, no new port, nothing calls home. channels.py lists the doors.

Exit codes: 0 OK, 1 WARN (late / silent / clock over 2 s), 2 ALARM (missing / clock over 30 s / chain broken),
3 no net established, 4 REFUSED (not Zero, stale epoch, split-brain, net discipline). argparse uses 2 for bad usage.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import socket
import sys
import time
import uuid
from pathlib import Path

import yaml

from . import channels as ch

ROOT = Path(__file__).resolve().parents[2]
STATES = ("WORKING", "BLOCKED", "WAITING", "DONE", "IDLE")
SITREP = (("job", "job"), ("state", "state"), ("branch", "branch/PR"), ("waiting", "waiting on"),
          ("not_verified", "NOT verified"), ("shared", "shared ground"))
SITREP_MAX = 300
DEFAULTS = {"interval_min": 15, "answer_min": 10, "retries": 3, "quiet": "", "per_minute": 6}
TRAFFIC = ("open", "handover", "close", "rollcall", "msg")
COMMAND = ("open", "handover", "close", "rollcall")       # command traffic: never held by quiet hours
OK, WARN, ALARM, NONET, REFUSED = 0, 1, 2, 3, 4
RENEW_EVERY_S = 60                                        # the hook renews a lease at most once a minute
PENDING_TTL_S = 24 * 3600                                 # a rotated Zero's relief has a day to take over
HOOK_MAX_LINES = 6


class Refused(Exception):
    """A command the net refuses: not Zero, stale epoch, split-brain, net discipline."""


# ---------------------------------------------------------------- places, lock, identity

def coord():
    return Path(os.environ.get("FIELDKIT_COORDINATION_DIR") or (ROOT / "state" / "coordination"))


def machine():
    return socket.gethostname()


class _Lock:
    """One writer at a time on net.lock (msvcrt on Windows, fcntl elsewhere), as the room does."""

    def __enter__(self):
        coord().mkdir(parents=True, exist_ok=True)
        self.f = open(coord() / "net.lock", "a+b")
        deadline = time.time() + 5
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() > deadline:
                    self.f.close()
                    raise
                time.sleep(0.05)

    def __exit__(self, *a):
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        finally:
            self.f.close()


def station_id(as_name=None, env=None, host=None):
    """The station this process speaks for: --as NAME, else the Claude Code session in the environment, else a shell
    of this account. Always NAME@MACHINE, so the same session id on two computers is two stations."""
    env = os.environ if env is None else env
    host = host or machine()
    if as_name:
        return as_name if "@" in as_name else f"{as_name}@{host}"
    sid = env.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        return f"claude:{sid[:8]}@{host}"
    return f"shell:{env.get('USERNAME') or env.get('USER') or '?'}@{host}"


def hook_station(session_id, host=None):
    return f"claude:{(session_id or '?')[:8]}@{host or machine()}"


def kind_of(station):
    return station.split(":", 1)[0] if ":" in station.split("@")[0] else "agent"


def machine_of(station):
    return station.rsplit("@", 1)[1] if "@" in station else ""


# ---------------------------------------------------------------- NET.yaml and the stations

def _atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def load_net():
    p = coord() / "NET.yaml"
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, yaml.YAMLError):
        return {"broken": True}
    return d if isinstance(d, dict) else {"broken": True}


def save_net(net):
    _atomic(coord() / "NET.yaml", "# the agent net: written by fieldkit net (fieldkit/zero/net.py); do not edit by hand\n"
            + yaml.safe_dump(net, sort_keys=False, allow_unicode=True))


def is_open(net):
    return net.get("net") == "open" and bool((net.get("zero") or {}).get("station"))


def zero_of(net):
    return (net.get("zero") or {}).get("station") if is_open(net) else None


def cfg(net, key):
    v = net.get(key)
    return DEFAULTS[key] if v in (None, "") else v


def load_stations():
    p = coord() / "net-stations.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, ValueError):
        d = {}
    d.setdefault("stations", {})
    d.setdefault("meta", {})
    return d


def save_stations(s):
    _atomic(coord() / "net-stations.json", json.dumps(s, indent=1, ensure_ascii=False))


def ttl_s(net):
    return 2 * float(cfg(net, "interval_min")) * 60


def misses(rec, now):
    """0 while the lease holds; 1 once it expired (SILENT); 2+ after another TTL (MISSING). Pure, so it never
    double-counts however often it is asked."""
    lu, ttl = float(rec.get("lease_until") or 0), float(rec.get("ttl") or 1800)
    return 0 if now <= lu else 1 + int((now - lu) // ttl)


def state_of(rec, now):
    if rec.get("off"):
        return "OFF"
    m = misses(rec, now)
    return "UP" if m == 0 else "SILENT" if m == 1 else "MISSING"


def renew(stations, sid, net, now, **info):
    rec = stations["stations"].setdefault(sid, {"first": now, "kind": kind_of(sid), "machine": machine_of(sid)})
    rec.update({k: v for k, v in info.items() if v not in (None, "")})
    rec.update(last=now, ttl=ttl_s(net), lease_until=now + ttl_s(net), epoch=net.get("epoch"), off=bool(info.get("off")))
    rec.pop("logged_miss", None)
    return rec


# ---------------------------------------------------------------- the net log: append-only, hash-chained, signed

def log_path():
    return coord() / "net-log.jsonl"


def _key():
    try:
        from ..peers import link
        return link.key()
    except Exception:  # noqa: BLE001 - no key is "unsigned", said by verify, never a crash
        return None


def _canon(e):
    return json.dumps({k: e[k] for k in sorted(e) if k != "mac"}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _tail_line(path):
    if not path.is_file():
        return b""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 65536))
        lines = [x for x in f.read().split(b"\n") if x.strip()]
    return lines[-1] if lines else b""


def new_id(prefix="M"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def append(kind, frm, to="*", text="", ref=None, data=None, want_ack=False, eid=None, now=None, epoch=None):
    """One line on the net log under the lock. -> the entry (with its seq and the byte offset it starts at)."""
    now = time.time() if now is None else now
    with _Lock():
        p = log_path()
        last = _tail_line(p)
        try:
            seq = json.loads(last)["seq"] + 1 if last else 1
        except (ValueError, KeyError):
            seq = 1
        e = {"seq": seq, "ts": round(now, 3), "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
             "epoch": epoch if epoch is not None else load_net().get("epoch", 0), "kind": kind, "from": frm, "to": to,
             "id": eid or new_id({"rollcall": "R", "msg": "M"}.get(kind, "E")),
             "prev": hashlib.sha256(last).hexdigest() if last else "0" * 64}
        if ref:
            e["ref"] = ref
        if text:
            e["text"] = str(text)[:4000]
        if data:
            e["data"] = data
        if want_ack:
            e["want_ack"] = True
        k = _key()
        if k:
            e["mac"] = hmac.new(k, _canon(e), hashlib.sha256).hexdigest()
        p.parent.mkdir(parents=True, exist_ok=True)
        off = p.stat().st_size if p.is_file() else 0
        with open(p, "ab") as f:
            f.write(json.dumps(e, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    return dict(e, _off=off)


def entries(since_off=0):
    """[(offset_after_line, entry)] from a byte offset on; a torn or foreign line is skipped, never fatal."""
    p = log_path()
    if not p.is_file():
        return []
    out = []
    with open(p, "rb") as f:
        f.seek(since_off)
        pos = since_off
        for raw in f:
            pos += len(raw)
            try:
                out.append((pos, json.loads(raw)))
            except ValueError:
                continue
    return out


def log_size():
    p = log_path()
    return p.stat().st_size if p.is_file() else 0


def verify():
    """-> {"ok", "lines", "signed", "problems"}: seq +1 each line, prev = sha256 of the line before, mac valid."""
    p, k = log_path(), _key()
    problems, prev, seq, signed, n = [], b"", 0, 0, 0
    if p.is_file():
        for raw in p.read_bytes().split(b"\n"):
            if not raw.strip():
                continue
            n += 1
            try:
                e = json.loads(raw)
            except ValueError:
                problems.append(f"line {n}: not JSON")
                prev = raw
                continue
            if e.get("seq") != seq + 1:
                problems.append(f"line {n}: seq {e.get('seq')} after {seq}")
            want = hashlib.sha256(prev).hexdigest() if prev else "0" * 64
            if e.get("prev") != want:
                problems.append(f"line {n} (seq {e.get('seq')}): chain broken (a line before it was changed or removed)")
            if "mac" in e:
                signed += 1
                if k and not hmac.compare_digest(hmac.new(k, _canon(e), hashlib.sha256).hexdigest(), str(e["mac"])):
                    problems.append(f"line {n} (seq {e.get('seq')}): bad signature")
            seq, prev = e.get("seq") or seq + 1, raw
    return {"ok": not problems, "lines": n, "signed": signed, "key": bool(k), "problems": problems}


# ---------------------------------------------------------------- fencing

def require_zero(net, me, epoch=None):
    if not is_open(net):
        raise Refused("no net is established (fieldkit zero open)")
    if zero_of(net) != me:
        raise Refused(f"you are not Zero: you are {me}; Zero is {zero_of(net)} (epoch {net.get('epoch')})")
    if epoch is not None and int(epoch) != int(net.get("epoch") or 0):
        raise Refused(f"stale epoch {epoch}: the net is at epoch {net.get('epoch')} (Zero changed since)")


def _quiet(net, now):
    """True inside the quiet hours "HH:MM-HH:MM" (may cross midnight)."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", str(cfg(net, "quiet") or "").strip())
    if not m:
        return False
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    t = time.localtime(now)
    x = t.tm_hour * 60 + t.tm_min
    return (a <= x < b) if a < b else (x >= a or x < b)


def _push(entry, net, channels, now, flash=False):
    if entry["kind"] not in COMMAND and not flash and _quiet(net, now):
        return [{"channel": "all", "target": "-", "ok": False, "detail": "held: quiet hours (net discipline); "
                 "the comms check sends it when they end"}]
    out = []
    for c in channels:
        try:
            out += c.push(entry, net)
        except Exception as e:  # noqa: BLE001 - one dead channel never silences the others
            out.append({"channel": getattr(c, "name", "?"), "target": "-", "ok": False, "detail": str(e)})
    return out


def _env_info(env=None):
    env = os.environ if env is None else env
    return {"session": env.get("CLAUDE_CODE_SESSION_ID"), "host_session": env.get("CLAUDE_CODE_HOST_SESSION_ID"),
            "folder": os.getcwd()}


# ---------------------------------------------------------------- the verbs

def open_net(me, second=None, interval=None, answer=None, quiet=None, name="", channels=None, now=None, env=None):
    now = time.time() if now is None else now
    net = load_net()
    if is_open(net) and zero_of(net) != me:
        raise Refused(f"split-brain refused: Zero is already {zero_of(net)} (epoch {net.get('epoch')}). Only Zero "
                      f"hands over (fieldkit zero handover --to {me}); the 2IC assumes only when Zero is MISSING "
                      "(fieldkit zero handover --assume)")
    reopen = is_open(net)
    epoch = int(net.get("epoch") or 0) + (0 if reopen else 1)       # an epoch is never reused, even after close
    info = _env_info(env)
    new = {"net": "open",
           "zero": {"station": me, "machine": machine_of(me) or machine(), "session": info["session"],
                    "host_session": info["host_session"], "name": name or (net.get("zero") or {}).get("name") or ""},
           "epoch": epoch,
           "2ic": ({"station": second if "@" in second else f"{second}@{machine()}"} if second
                      else (net.get("2ic") if reopen else None)),
           "interval_min": interval or (net.get("interval_min") if reopen else None) or DEFAULTS["interval_min"],
           "answer_min": answer or (net.get("answer_min") if reopen else None) or DEFAULTS["answer_min"],
           "quiet": quiet if quiet is not None else (net.get("quiet") if reopen else "") or "",
           "opened": net.get("opened") if reopen else time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
           "updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))}
    with _Lock():
        save_net(new)
        st = load_stations()
        renew(st, me, new, now, name=new["zero"]["name"] or None, **info)
        save_stations(st)
    e = append("open", me, text=f"Zero has been established: {me}", data={"net": _public(new), "reopen": reopen},
               now=now, epoch=epoch)
    return {"net": new, "entry": e, "pushed": _push(e, new, channels or ch.default(), now)}


def _public(net):
    """What travels to other stations: no folders, no session ids beyond the station name."""
    z = net.get("zero") or {}
    return {"net": net.get("net"), "epoch": net.get("epoch"), "zero": {"station": z.get("station"),
            "machine": z.get("machine"), "name": z.get("name")}, "2ic": net.get("2ic"),
            "interval_min": net.get("interval_min"), "answer_min": net.get("answer_min"), "quiet": net.get("quiet")}


def handover(me, to=None, take=False, assume=False, second=None, epoch=None, channels=None, now=None, env=None):
    now = time.time() if now is None else now
    net = load_net()
    if not is_open(net):
        raise Refused("no net is established (fieldkit zero open)")
    old = zero_of(net)
    st = load_stations()
    if take:
        p = net.get("handover_pending") or {}
        if not p or now - float(p.get("ts") or 0) > PENDING_TTL_S:
            raise Refused("no handover is pending (a rotated Zero leaves one; else Zero runs handover --to YOU)")
        if int(p.get("epoch") or -1) != int(net.get("epoch") or 0):
            raise Refused(f"the pending handover was for epoch {p.get('epoch')}; the net is at {net.get('epoch')}")
        to, how = me, f"relief of rotated Zero {old}"
    elif assume:
        sec = (net.get("2ic") or {}).get("station")
        if sec != me:
            raise Refused(f"only the 2IC ({sec or 'none named'}) assumes Zero; you are {me}")
        zrec = st["stations"].get(old) or {}
        if state_of(zrec, now) != "MISSING":
            raise Refused(f"Zero {old} is {state_of(zrec, now) if zrec else 'unknown'}, not MISSING: the 2IC assumes "
                          "only when Zero missed two check-ins")
        to, how = me, f"2IC assumed: Zero {old} MISSING"
    else:
        require_zero(net, me, epoch)
        if not to:
            raise Refused("handover needs --to STATION (or --take / --assume)")
        to = to if "@" in to else f"{to}@{machine()}"
        if to == me:
            raise Refused("you are already Zero")
        how = f"handed over by {me}"
    info = _env_info(env) if to == me else {"session": None, "host_session": None}
    net = dict(net)
    net["epoch"] = int(net.get("epoch") or 0) + 1
    net["zero"] = {"station": to, "machine": machine_of(to), "session": info.get("session"),
                   "host_session": info.get("host_session"), "name": (net.get("zero") or {}).get("name") or ""}
    sec = (net.get("2ic") or {}).get("station")
    net["2ic"] = ({"station": second if "@" in second else f"{second}@{machine()}"} if second
                     else (None if sec == to else net.get("2ic")))
    net.pop("handover_pending", None)
    net["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    with _Lock():
        save_net(net)
        st = load_stations()
        if to == me:
            renew(st, me, net, now, **info)
        save_stations(st)
    if take:                                          # the rotated Zero's order and ground go with the net
        from . import command
        command.carry(old, to, now)
    e = append("handover", me, text=f"Zero is now {to} ({how})", data={"net": _public(net), "previous": old, "how": how},
               now=now, epoch=net["epoch"])
    return {"net": net, "entry": e, "previous": old, "pushed": _push(e, net, channels or ch.default(), now)}


def on_rotate(session_id, now=None):
    """Called by `fieldkit rotate handover`: when the rotated session is Zero, leave a pending handover for its
    relief; when it holds an order, leave the order for its relief (command.on_rotate). -> the lines for the fresh
    session's brief, or "". Never raises."""
    try:
        now = time.time() if now is None else now
        net = load_net()
        me = hook_station(session_id)
        if not is_open(net):
            return ""
        from . import command
        order_line = command.on_rotate(me, now)
        if zero_of(net) != me:
            return order_line
        net = load_net()
        net["handover_pending"] = {"from": me, "epoch": net.get("epoch"), "ts": now,
                                   "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))}
        with _Lock():
            save_net(net)
        append("msg", me, text=f"Zero {me} is rotating to a fresh session: its relief takes over with "
               "`fieldkit zero handover --take`", now=now)
        return (f"\nZERO: the session you continue was Zero of the agent net (epoch {net.get('epoch')}). Your FIRST "
                "command: fieldkit zero handover --take   (epoch +1, every station is told Zero is now you"
                + ("; Zero's order and its ground go with it" if order_line else "") + ")\n")
    except Exception:  # noqa: BLE001 - a rotation must never fail because of the net
        return ""


def close_net(me, epoch=None, channels=None, now=None):
    now = time.time() if now is None else now
    net = load_net()
    require_zero(net, me, epoch)
    net = dict(net, net="closed", closed=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)))
    with _Lock():
        save_net(net)
    e = append("close", me, text=f"net closed by Zero {me}", data={"net": _public(net)}, now=now)
    return {"net": net, "entry": e, "pushed": _push(e, net, channels or ch.default(), now)}


def check_sitrep(s):
    """-> the cleaned sitrep, or raise ValueError naming what is missing. The fixed format of fieldkit-sitrep's
    'Where we are now', one plain line per field, "-" for none."""
    out, bad = {}, []
    for k, label in SITREP:
        v = " ".join(str((s or {}).get(k) or "").split())
        if not v:
            bad.append(label)
            continue
        if len(v) > SITREP_MAX:
            raise ValueError(f"sitrep {label}: {len(v)} characters, at most {SITREP_MAX} (one plain line)")
        out[k] = v
    if bad:
        raise ValueError("sitrep incomplete, missing: " + ", ".join(bad) + " (use - for none)")
    out["state"] = out["state"].upper()
    if out["state"] not in STATES:
        raise ValueError(f"sitrep state {out['state']!r}: one of {', '.join(STATES)}")
    return out


def _addressed(e, me):
    return e.get("kind") in TRAFFIC and e.get("from") != me and e.get("to") in ("*", me)


def pending_for(me, rec, net, now):
    """Traffic for this station since it last looked: [(offset, entry)]."""
    off = rec.get("seen_off")
    if off is None:                                       # a new station: the open roll call, else from now on
        rc = net.get("rollcall") or {}
        off = rc.get("offset", log_size()) if now < float(rc.get("deadline") or 0) else log_size()
    off = min(int(off), log_size())
    return [(o, e) for o, e in entries(off) if _addressed(e, me)]


def _acked(ids, me):
    return {e.get("ref") for _, e in entries() if e.get("kind") == "ack" and e.get("from") == me and e.get("ref") in ids}


def _deliver(me, items, now):
    """Mark seen, ack what wants an ack. -> the ids acked."""
    want = [e["id"] for _, e in items if e.get("want_ack")]
    done = _acked(set(want), me) if want else set()
    acked = []
    for i in want:
        if i not in done:
            append("ack", me, to="*", ref=i, now=now)
            acked.append(i)
    return acked


def checkin(me, sitrep=None, ref=None, off=False, channels=None, now=None, env=None, ingest=True, cable=None,
            relieve=None):
    now = time.time() if now is None else now
    if ingest:
        ingest_cable()
    net = load_net()
    if not is_open(net):
        return {"net": net, "me": me, "established": False}
    clean = check_sitrep(sitrep) if sitrep else None
    rc = net.get("rollcall") or {}
    ref = ref or (rc.get("id") if clean else None)
    relieved = None
    if relieve:                                       # the relief of a rotated station takes its order and ground
        from . import command
        relieved = command.relieve(me, relieve, now)
    with _Lock():
        st = load_stations()
        rec = renew(st, me, net, now, off=off, sitrep=clean, sitrep_ref=ref if clean else None,
                    sitrep_at=now if clean else None, **_env_info(env))
        items = pending_for(me, rec, net, now)
        save_stations(st)
    order = (rec.get("order") or {}).get("id") if not (rec.get("order") or {}).get("closed") else None
    e = append("checkin", me, to=zero_of(net), ref=ref,
               data={k: v for k, v in {"sitrep": clean, "off": off, "order": order}.items() if v}, now=now)
    acked = _deliver(me, items, now)
    with _Lock():
        st = load_stations()
        st["stations"].setdefault(me, rec)["seen_off"] = log_size()
        save_stations(st)
    remote = []
    if machine_of(me) != (net.get("zero") or {}).get("machine"):  # Zero is on the other computer: tell it there
        remote = (cable or ch.Cable()).push(e, net)
    return {"net": net, "me": me, "established": True, "entry": e, "pending": [x for _, x in items],
            "acked": acked, "remote": remote, "rec": rec, "relieved": relieved}


def send(me, text, to="*", flash=False, want_ack=True, channels=None, now=None):
    now = time.time() if now is None else now
    net = load_net()
    if not is_open(net):
        raise Refused("no net is established (fieldkit zero open)")
    recent = [e for _, e in entries(max(0, log_size() - 262144))
              if e.get("kind") == "msg" and e.get("from") == me and now - float(e.get("ts") or 0) < 60]
    if len(recent) >= int(cfg(net, "per_minute")) and not flash:
        raise Refused(f"net discipline: {len(recent)} messages from you in the last minute (at most "
                      f"{cfg(net, 'per_minute')}); wait, or --flash if it cannot wait")
    to = to if to == "*" or "@" in to else f"{to}@{machine()}"
    st = load_stations()["stations"]
    expect = [to] if to != "*" else [s for s, r in st.items() if s != me and state_of(r, now) != "OFF"]
    e = append("msg", me, to=to, text=text, want_ack=want_ack,
               data={"expect": expect, "flash": flash} if want_ack else {"flash": flash}, now=now)
    return {"entry": e, "pushed": _push(e, net, channels or ch.default(), now, flash)}


def ack(me, ref, now=None):
    return append("ack", me, ref=ref, now=now)


def rollcall(me, epoch=None, answer=None, channels=None, now=None):
    now = time.time() if now is None else now
    ingest_cable()
    net = load_net()
    require_zero(net, me, epoch)
    st = load_stations()["stations"]
    expect = sorted(s for s, r in st.items() if s != me and state_of(r, now) != "OFF")
    deadline = now + float(answer or cfg(net, "answer_min")) * 60
    off = log_size()
    e = append("rollcall", me, text="report your sitrep", want_ack=False,
               data={"deadline": deadline, "expect": expect}, now=now)
    with _Lock():
        net = load_net()
        net["rollcall"] = {"id": e["id"], "deadline": deadline, "offset": off,
                           "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))}
        save_net(net)
    return {"entry": e, "expect": expect, "pushed": _push(e, net, channels or ch.default(), now)}


# ---------------------------------------------------------------- what is out there

def discover(now, hours=None, adapters=None, sessions=None, devices=None):
    """Stations that never checked in but are there: recent Claude Code / Antigravity sessions, live Gorilla OpenCode
    sessions, cable peers. -> [{station, kind, via, detail}]."""
    out = []
    try:
        from ..tokens import session_cost as sc
        for a in adapters if adapters is not None else sc.adapters_for("all"):
            for p in a.sessions()[:40]:
                try:
                    age = now - p.stat().st_mtime
                except OSError:
                    continue
                if age <= (hours or 1) * 3600:
                    k = "claude" if a.name == "claude-code" else a.name
                    out.append({"station": f"{k}:{p.stem[:8]}@{machine()}", "kind": k,
                                "via": "room" if k == "claude" else "pull",
                                "detail": f"transcript active {int(age // 60)} min ago"})
    except Exception:  # noqa: BLE001 - discovery is a hint; it never fails a roll call
        pass
    try:
        from ..peers import peers
        for s in (sessions or peers.sessions)():
            out.append({"station": f"opencode:{s['name']}@{machine()}", "kind": "opencode", "via": "opencode",
                        "detail": f"Gorilla OpenCode pid {s.get('pid')}"})
    except Exception:  # noqa: BLE001
        pass
    try:
        for d in (devices if devices is not None else ch.Cable().devices()):
            out.append({"station": f"*@{d}", "kind": "cable", "via": "cable", "detail": f"cable peer {d}"})
    except Exception:  # noqa: BLE001
        pass
    return out


def roll(net, now, found=None):
    """The last roll call, station by station: ANSWERED / PENDING / LATE / MISSING / UNREGISTERED."""
    rc = net.get("rollcall") or {}
    st = load_stations()["stations"]
    rows, answered = [], {}
    if rc:
        for _, e in entries(int(rc.get("offset") or 0)):
            if e.get("kind") == "checkin" and e.get("ref") == rc["id"] and (e.get("data") or {}).get("sitrep"):
                answered[e["from"]] = e
        expect = next((e.get("data", {}).get("expect", []) for _, e in entries(int(rc.get("offset") or 0))
                       if e.get("id") == rc["id"]), [])
    else:
        expect = []
    zero = zero_of(net)
    for s in sorted(set(expect) | set(answered) | {s for s, r in st.items() if state_of(r, now) != "OFF"}):
        if s == zero:
            continue
        rec = st.get(s, {})
        if s in answered:
            status = "ANSWERED"
        elif state_of(rec, now) == "MISSING" or not rec:
            status = "MISSING"
        elif rc and now < float(rc.get("deadline") or 0):
            status = "PENDING"
        elif rc:
            status = "LATE"
        else:
            status = state_of(rec, now)
        rows.append({"station": s, "status": status, "sitrep": (answered.get(s) or {}).get("data", {}).get("sitrep"),
                     "last": rec.get("last"), "kind": rec.get("kind") or kind_of(s), "host_session": rec.get("host_session"),
                     "name": rec.get("name")})
    known = set(st) | set(answered) | ({zero} if zero else set())
    devices_with_stations = {machine_of(s) for s in known}
    for f in found or []:
        if f["kind"] == "cable":
            if f["station"].split("@")[1] not in devices_with_stations:
                rows.append({"station": f["station"], "status": "NO STATION", "kind": "cable",
                             "detail": f"{f['detail']}: no station there has checked in"})
        elif f["station"] not in known:
            rows.append({"station": f["station"], "status": "UNREGISTERED", "kind": f["kind"],
                         "detail": f"{f['detail']}; " + ("pull only" if f["via"] == "pull" else f"reached via {f['via']}")})
    return {"rollcall": rc, "rows": rows}


def roll_level(rows):
    s = {r["status"] for r in rows}
    return ALARM if "MISSING" in s else WARN if s & {"LATE", "PENDING", "SILENT", "NO STATION"} else OK


# ---------------------------------------------------------------- the cable: store-and-forward from the far side

def ingest_cable(items=None, now=None):
    """Net entries that came over the cable go into this log (once, by id); a far Zero's announcement updates this
    computer's mirror of NET.yaml only with a HIGHER epoch; the same epoch with another Zero is split-brain, logged
    and refused. -> number ingested."""
    now = time.time() if now is None else now
    st = load_stations()
    since = int(st["meta"].get("cable_since") or 0)
    try:
        got = items if items is not None else ch.inbox_items(since)
    except Exception:  # noqa: BLE001 - not paired, no inbox: nothing came
        return 0
    seen = set(st["meta"].get("seen_ids") or [])
    n = 0
    for num, e in got:
        since = max(since, int(num))
        if e.get("id") in seen:
            continue
        seen.add(e["id"])
        k = e.get("kind")
        net = load_net()
        if k in ("open", "handover", "close"):
            far = (e.get("data") or {}).get("net") or {}
            far_epoch, here = int(far.get("epoch") or e.get("epoch") or 0), int(net.get("epoch") or 0)
            far_zero = (far.get("zero") or {}).get("station")
            if far_epoch > here or (k == "close" and far_epoch == here and far_zero == zero_of(net)):
                mirror = dict(far, mirror_of=e.get("from_host"),
                              updated=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)))
                with _Lock():
                    save_net(mirror)
            elif far_epoch == here and far_zero and far_zero != zero_of(net):
                append("refused", "net", text=f"split-brain: {far_zero} claims epoch {far_epoch}, Zero here is "
                       f"{zero_of(net)}", data={"far": far}, now=now)
                continue
            elif far_epoch < here:
                append("refused", "net", ref=e.get("id"), text=f"stale epoch {far_epoch} from {e.get('from')} "
                       f"(here {here})", now=now)
                continue
        if k == "checkin":
            with _Lock():
                s2 = load_stations()
                d = e.get("data") or {}
                renew(s2, e["from"], net, now, via="cable", sitrep=d.get("sitrep"),
                      sitrep_ref=e.get("ref") if d.get("sitrep") else None, off=d.get("off"))
                s2["meta"] = st["meta"]
                st = s2
                save_stations(st)
        append(k, e.get("from"), to=e.get("to", "*"), text=e.get("text", ""), ref=e.get("ref"),
               data=dict(e.get("data") or {}, relayed=e.get("from_host")), want_ack=e.get("want_ack", False),
               eid=e["id"], now=now, epoch=e.get("epoch"))
        n += 1
    with _Lock():
        s3 = load_stations()
        s3["meta"].update(cable_since=since, seen_ids=sorted(seen)[-2000:])
        save_stations(s3)
    return n


# ---------------------------------------------------------------- the comms check

def unacked(net, now):
    """Messages that want an ack, with the stations that have not acked: [{entry, missing, attempts}]."""
    all_ = [e for _, e in entries()]
    acks = {}
    for e in all_:
        if e.get("kind") == "ack":
            acks.setdefault(e.get("ref"), set()).add(e.get("from"))
    tries = {}
    for e in all_:
        if e.get("kind") == "retry":
            tries[e.get("ref")] = tries.get(e.get("ref"), 0) + 1
    out = []
    for e in all_:
        if e.get("kind") == "msg" and e.get("want_ack"):
            miss = [s for s in (e.get("data") or {}).get("expect", []) if s not in acks.get(e["id"], set())]
            if miss:
                out.append({"entry": e, "missing": miss, "attempts": 1 + tries.get(e["id"], 0)})
    return out


def check(heal=True, channels=None, now=None, reviver=None, inbox_add=None, board=True, cable=None, command_kw=None):
    now = time.time() if now is None else now
    ingest_cable()
    net = load_net()
    if not is_open(net):
        return {"established": False, "net": net, "level": NONET}
    st = load_stations()
    rows, events = [], []
    for s, rec in sorted(st["stations"].items()):
        state, m = state_of(rec, now), misses(rec, now)
        rows.append({"station": s, "state": state, "misses": m, "last": rec.get("last"), "kind": rec.get("kind")})
        if state == "OFF" or m == 0:
            continue
        if int(rec.get("logged_miss") or 0) < m:
            append("miss", "net", to=s, text=f"{s} {'SILENT' if m == 1 else 'MISSING'}: {m} missed check-in(s)",
                   data={"misses": m}, now=now)
            rec["logged_miss"] = m
            events.append(f"{s} {state}")
        if m >= 2 and rec.get("inbox_lease") != rec.get("lease_until"):
            title = f"net: station {s} MISSING (2 missed check-ins)"
            why = (f"Zero {zero_of(net)} has not heard {s} since "
                   f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(float(rec.get('last') or 0)))}. "
                   "Its session may have ended, hung, or lost the cable.")
            try:
                if inbox_add:
                    inbox_add(title, why, "fieldkit zero rollcall")
                else:
                    from ..autopilot import inbox
                    inbox.add(title, why, "fieldkit zero rollcall", kind="net")
                rec["inbox_lease"] = rec.get("lease_until")
                events.append(f"{s} -> inbox")
            except Exception as e:  # noqa: BLE001
                events.append(f"{s}: inbox not written ({e})")
    with _Lock():
        cur = load_stations()
        for s, rec in st["stations"].items():
            if s in cur["stations"]:
                for k in ("logged_miss", "inbox_lease"):
                    if k in rec:
                        cur["stations"][s][k] = rec[k]
        save_stations(cur)
    retried, undelivered = [], []
    for u in unacked(net, now):
        if u["attempts"] > int(cfg(net, "retries")):
            undelivered.append({"id": u["entry"]["id"], "missing": u["missing"], "text": u["entry"].get("text", "")})
            continue
        if now - float(u["entry"].get("ts") or 0) < 60 * float(cfg(net, "interval_min")) / 3:
            continue                                  # give it a third of an interval before the first retry
        pushed = _push(u["entry"], net, channels or ch.default(), now, (u["entry"].get("data") or {}).get("flash"))
        if any("held" in p["detail"] for p in pushed):
            continue
        append("retry", "net", ref=u["entry"]["id"], data={"missing": u["missing"]}, now=now)
        retried.append(u["entry"]["id"])
    cab = cable or ch.Cable()
    links = []
    for d in cab.devices():
        r = cab.hello(d)
        row = {"device": d, "ok": bool(r.get("ok")), "detail": "answers" if r.get("ok") else str(r.get("error"))}
        if not r.get("ok") and heal:
            try:
                if reviver:
                    rc = reviver(d)
                else:
                    from ..peers import link_heal
                    rc = link_heal.revive(d)
                row["revived"] = rc == 0
                row["detail"] += "; REVIVED by peers link heal" if rc == 0 else "; heal could not revive it (inbox)"
                append("miss" if rc else "checkin", "net", to=f"*@{d}", text=f"cable {d}: {row['detail']}", now=now)
            except Exception as e:  # noqa: BLE001
                row["detail"] += f"; heal failed: {e}"
        links.append(row)
    try:                                              # set 2: orders, drift, idle, archive, the wake (command.py)
        from . import command
        cmd = command.sweep(net, now, inbox_add=inbox_add, board=board, **(command_kw or {}))
    except Exception as e:  # noqa: BLE001 - the comms check still reports the net
        cmd = {"error": str(e), "findings": [], "new": []}
    level = (ALARM if any(r["state"] == "MISSING" for r in rows) or any(not x["ok"] and not x.get("revived") for x in links)
             else WARN if any(r["state"] == "SILENT" for r in rows) or undelivered or cmd.get("findings") else OK)
    out = {"established": True, "net": net, "rows": rows, "events": events, "retried": retried,
           "undelivered": undelivered, "links": links, "level": level, "command": cmd}
    if board:
        try:
            out["board"] = write_board(board_row(net, rows, now))
        except OSError as e:
            out["board"] = f"not written: {e}"
    return out


# ---------------------------------------------------------------- the PLAN.md Board row

BOARD_KEY = "| Net (fieldkit zero) |"


def board_row(net, rows, now):
    up = [r["station"] for r in rows if r["state"] == "UP"]
    silent = [r["station"] for r in rows if r["state"] == "SILENT"]
    missing = [r["station"] for r in rows if r["state"] == "MISSING"]
    sec = (net.get("2ic") or {}).get("station") or "none"
    state = (f"{len(up)} UP" + (f"; SILENT: {', '.join(silent)}" if silent else "")
             + (f"; MISSING: {', '.join(missing)}" if missing else ""))
    return (f"{BOARD_KEY} ZERO ESTABLISHED: Zero {zero_of(net)}, epoch {net.get('epoch')}, 2IC {sec} | {state} "
            f"(written by the comms check) | `fieldkit zero` for the roll; MISSING stations go to `fieldkit inbox` |")


def write_board(row, plan=None, key=BOARD_KEY):
    """Replace the net's own row (the one starting with `key`) on the Board, or add it under the Board's header;
    nothing else is touched. -> "written" / "unchanged" / "no board"."""
    plan = Path(plan or coord() / "PLAN.md")
    if not plan.is_file():
        return "no board"
    text = plan.read_text(encoding="utf-8")
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith(key):
            if ln == row:
                return "unchanged"
            lines[i] = row
            break
    else:
        b = next((i for i, ln in enumerate(lines) if ln.startswith("## Board")), None)
        if b is None:
            return "no board"
        sep = next((i for i in range(b, min(b + 8, len(lines))) if lines[i].startswith("|---")), None)
        if sep is None:
            return "no board"
        lines.insert(sep + 1, row)
    _atomic(plan, "\n".join(lines))
    return "written"


# ---------------------------------------------------------------- the hook rider (fieldkit/autopilot/room.py)

def hook_line(call, event, now=None):
    """Every Claude Code session on this computer: checked in by the hook (the lease renewed at most once a minute),
    shown its unseen net traffic (acked), told once who Zero is. Zero is told which stations went quiet, once per
    change. Nothing when no net is open (net discipline: silence). Never raises."""
    try:
        now = time.time() if now is None else now
        sid = call.get("session_id")
        if not sid:
            return ""
        net = load_net()
        if not is_open(net):
            return ""
        me = hook_station(sid)
        out = []
        with _Lock():
            st = load_stations()
            rec = st["stations"].get(me)
            new = rec is None
            if new or now - float(rec.get("last") or 0) >= RENEW_EVERY_S or rec.get("off"):
                rec = renew(st, me, net, now, via="hook", folder=call.get("cwd"), session=sid)
            items = pending_for(me, rec, net, now)
            if items:
                rec["seen_off"] = log_size()
            elif rec.get("seen_off") is None:
                rec["seen_off"] = log_size()
            save_stations(st)
        if new:
            append("checkin", me, to=zero_of(net), data={"by": "hook"}, now=now)
            out.append(f"NET: Zero is {zero_of(net)} (epoch {net.get('epoch')}); you are station {me}, checked in by "
                       "the hook. Owner words: 'zero', 'sitrep', 'comms check', 'roll call' mean fieldkit zero "
                       "(skill fieldkit-net).")
        for _, e in items[:HOOK_MAX_LINES]:
            out.append(ch.human(e, net))
        if len(items) > HOOK_MAX_LINES:
            out.append(f"  +{len(items) - HOOK_MAX_LINES} more: fieldkit zero checkin")
        if items:
            _deliver(me, items, now)
        if me == zero_of(net) and event in ("UserPromptSubmit", "SessionStart"):
            st = load_stations()
            quiet = sorted(f"{s} {state_of(r, now)}" for s, r in st["stations"].items()
                           if s != me and state_of(r, now) in ("SILENT", "MISSING"))
            key = hashlib.sha256("|".join(quiet).encode()).hexdigest()[:12]
            if quiet and st["meta"].get("zero_alert") != key:
                out.append("NET (you are Zero): " + "; ".join(quiet[:8]) + " - fieldkit zero rollcall")
            if st["meta"].get("zero_alert") != key:
                with _Lock():
                    s2 = load_stations()
                    s2["meta"]["zero_alert"] = key
                    save_stations(s2)
        return "\n".join(out)
    except Exception:  # noqa: BLE001 - the hook informs; it never fails a tool call
        return ""


# ---------------------------------------------------------------- status, doctor

def status(now=None):
    now = time.time() if now is None else now
    net = load_net()
    st = load_stations()["stations"]
    rows = [{"station": s, "state": state_of(r, now), "kind": r.get("kind"), "last": r.get("last"),
             "sitrep": r.get("sitrep"), "name": r.get("name"), "via": r.get("via")} for s, r in sorted(st.items())]
    return {"established": is_open(net), "net": net, "rows": rows,
            "level": NONET if not is_open(net) else ALARM if any(r["state"] == "MISSING" for r in rows)
            else WARN if any(r["state"] == "SILENT" for r in rows) else OK}


RULE_MARK = "fieldkit zero checkin"


def doctor(settings=None, gemini=None, agents=None, run=None):
    rows = []
    net = load_net()
    rows.append(("NET.yaml", not net.get("broken"),
                 "broken YAML" if net.get("broken") else ("open, epoch %s, Zero %s" % (net.get("epoch"), zero_of(net))
                                                          if is_open(net) else "no net open (fieldkit zero open)")))
    v = verify()
    rows.append(("net log", v["ok"], f"{v['lines']} lines, {v['signed']} signed, chain "
                 + ("intact" if v["ok"] else "BROKEN: " + "; ".join(v["problems"][:3]))))
    rows.append(("signing key", v["key"], "cable pairing key present: new lines are signed" if v["key"]
                 else "no pairing key: lines are hash-chained, not signed (fieldkit peers link pair)"))
    try:
        from ..autopilot import room
        r = room.doctor(settings)
        rows.append(("room hook (carries the net)", r["ok"], "wired" if r["ok"] else
                     "MISSING " + ", ".join(r["missing"]) + ": python -m fieldkit.autopilot.room doctor"))
    except Exception as e:  # noqa: BLE001
        rows.append(("room hook (carries the net)", False, str(e)))
    from . import command
    ok, detail, _snip = command.hook_doctor(settings)
    rows.append(("ground-claim hook (PreToolUse)", ok, detail))
    from . import schedule
    ok, detail = schedule.scheduled(run=run) if run else schedule.scheduled()
    rows.append(("scheduled comms check", ok, detail + ("" if ok else ": fieldkit zero schedule --apply")))
    for label, p in (("GEMINI.md rule", Path(gemini or Path.home() / ".gemini" / "GEMINI.md")),
                     ("AGENTS.md rule", Path(agents or ROOT / "AGENTS.md"))):
        try:
            has = RULE_MARK in p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            has = False
        rows.append((label, has, f"{p}: " + ("has the check-in rule" if has else f"no '{RULE_MARK}' rule")))
    return rows


# ---------------------------------------------------------------- what the person reads

def _when(ts):
    return time.strftime("%H:%M", time.localtime(float(ts))) if ts else "never"


def status_lines(r, now=None):
    net = r["net"]
    if not r["established"]:
        return ["ZERO NOT ESTABLISHED: no net is open. Establish it from the coordinating session: fieldkit zero open"
                + (f" (last: epoch {net.get('epoch')}, closed {net.get('closed')})" if net.get("epoch") else "")]
    z = net.get("zero") or {}
    sec = (net.get("2ic") or {}).get("station") or "none named"
    n = {k: sum(x["state"] == k for x in r["rows"]) for k in ("UP", "SILENT", "MISSING", "OFF")}
    out = [f"ZERO ESTABLISHED: Zero = {z.get('station')}{' (' + z['name'] + ')' if z.get('name') else ''}, "
           f"epoch {net.get('epoch')}, 2IC = {sec}" + (f", mirror of {net['mirror_of']}" if net.get("mirror_of") else ""),
           f"  check-in every {cfg(net, 'interval_min')} min (lease {int(ttl_s(net) // 60)} min), roll answers due in "
           f"{cfg(net, 'answer_min')} min" + (f", quiet hours {net['quiet']}" if net.get("quiet") else ""),
           f"  {len(r['rows'])} station(s): {n['UP']} UP, {n['SILENT']} SILENT, {n['MISSING']} MISSING, {n['OFF']} OFF"]
    for x in r["rows"]:
        tag = "ZERO" if x["station"] == z.get("station") else x["state"]
        line = f"  {tag:<8} {x['station']:<34} last {_when(x['last'])}"
        if x.get("sitrep"):
            line += f"  {x['sitrep']['state']}: {x['sitrep']['job']}"
        out.append(line)
    if net.get("handover_pending"):
        out.append(f"  HANDOVER PENDING from {net['handover_pending']['from']}: its relief runs fieldkit zero handover --take")
    return out


def roll_lines(r):
    rc = r["rollcall"]
    if not rc:
        return ["no roll call yet: fieldkit zero rollcall"]
    out = [f"ROLL CALL {rc['id']} sent {rc.get('at')}, answers due {_when(rc.get('deadline'))}"]
    for x in r["rows"]:
        out.append(f"  {x['status']:<12} {x['station']}" + (f" ({x['name']})" if x.get("name") else ""))
        if x.get("sitrep"):
            for k, label in SITREP:
                out.append(f"      {label + ':':<15}{x['sitrep'][k]}")
        elif x.get("detail"):
            out.append(f"      {x['detail']}")
        elif x["status"] in ("LATE", "PENDING", "MISSING") and x.get("kind") == "claude":
            out.append(f"      last heard {_when(x.get('last'))}; an idle Desktop session runs no hook: nudge it"
                       + (f" (send_message to {x['host_session']})" if x.get("host_session") else ""))
    return out


def pushed_lines(pushed):
    return [f"  {'sent' if p['ok'] else 'NOT SENT':<8} {p['channel']:<8} {p['target']}: {p['detail']}" for p in pushed]


# ---------------------------------------------------------------- the command

VERBS = {"status": "status", "open": "open", "establish": "open", "rollcall": "rollcall", "sitrep": "rollcall",
         "checkin": "checkin", "check": "check", "comms": "check", "time": "time", "handover": "handover",
         "send": "send", "ack": "ack", "log": "log", "close": "close", "doctor": "doctor", "schedule": "schedule",
         "task": "task", "release": "release", "orders": "orders"}


def _emit(a, data, lines):
    if a.json:
        print(json.dumps(data, indent=1, default=str, ensure_ascii=False))
    else:
        print("\n".join(lines))


def main(argv=None, prog="fieldkit zero"):
    argv = sys.argv[1:] if argv is None else list(argv)
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verb", nargs="?", default="status", choices=sorted(VERBS))
    ap.add_argument("rest", nargs="*", help="send: the text; ack: the id; log: how many lines")
    ap.add_argument("--as", dest="as_name", help="the station you speak for (agents with no session id)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--epoch", type=int, help="fencing: refuse unless the net is still at this epoch")
    ap.add_argument("--2ic", "--second", dest="second", help="open/handover: the 2IC (second in command) station")
    ap.add_argument("--interval", type=float, help="open: check-in interval in minutes (15)")
    ap.add_argument("--answer", type=float, help="open/rollcall: minutes a station has to answer a roll call (10)")
    ap.add_argument("--quiet-hours", help="open: HH:MM-HH:MM, routine traffic held")
    ap.add_argument("--name", default="", help="open: a readable name for Zero, e.g. 'command post'")
    ap.add_argument("--to", help="handover: the new Zero; send: one station (default every station)")
    ap.add_argument("--take", action="store_true", help="handover: the relief of a rotated Zero takes over")
    ap.add_argument("--assume", action="store_true", help="handover: the 2IC assumes Zero (Zero MISSING)")
    ap.add_argument("--ref", help="checkin: the roll call answered")
    for k, label in SITREP:
        ap.add_argument("--" + k.replace("_", "-"), dest=k, help=f"checkin: sitrep {label}")
    ap.add_argument("--off", action="store_true", help="checkin: this station signs off the net")
    ap.add_argument("--flash", action="store_true", help="send: FLASH precedence, never held")
    ap.add_argument("--no-ack", action="store_true", help="send: wants no ack")
    ap.add_argument("--wait", type=float, default=0, help="rollcall: wait up to N minutes for the answers")
    ap.add_argument("--status", action="store_true", help="rollcall: show the last roll call, send nothing")
    ap.add_argument("--no-heal", action="store_true", help="check: do not revive a dead cable")
    ap.add_argument("--scheduled", action="store_true", help="check: run by the schedule (prints only problems)")
    ap.add_argument("--verify", action="store_true", help="log: check the hash chain and the signatures")
    ap.add_argument("--apply", action="store_true", help="schedule: install it")
    ap.add_argument("--remove", action="store_true", help="schedule: remove it")
    ap.add_argument("--objective", help="task: what the station is to achieve")
    ap.add_argument("--area", action="append", default=[], help="task: the folder(s) it may write, PATH[,PATH]")
    ap.add_argument("--done", help="task: what done looks like")
    ap.add_argument("--drift-min", type=float, help="task: minutes without progress while WORKING = DRIFT (45)")
    ap.add_argument("--idle-min", type=float, help="task: minutes without activity = asked why idle (45)")
    ap.add_argument("--finished", action="store_true", help="release: the order is done (else released)")
    ap.add_argument("--relieve", help="checkin: take over this rotated station's order and ground")
    a = ap.parse_args(argv)
    verb = VERBS[a.verb]
    me = station_id(a.as_name)
    try:
        if verb == "status":
            r = status()
            if r["established"] and (r["net"].get("rollcall") or {}):
                rr = roll(r["net"], time.time())
                _emit(a, dict(r, roll=rr), status_lines(r) + [""] + roll_lines(rr))
            else:
                _emit(a, r, status_lines(r))
            return r["level"]
        if verb == "open":
            r = open_net(me, a.second, a.interval, a.answer, a.quiet_hours, a.name)
            _emit(a, r, [f"ZERO HAS BEEN ESTABLISHED: Zero = {me}, epoch {r['net']['epoch']}, 2IC = "
                         f"{(r['net'].get('2ic') or {}).get('station') or 'none named'}. Announced:"]
                  + pushed_lines(r["pushed"]))
            return OK
        if verb == "rollcall":
            now = time.time()
            if not a.status:
                r = rollcall(me, a.epoch, a.answer)
                if not a.json:
                    print(f"ROLL CALL {r['entry']['id']} sent to {len(r['expect'])} known station(s):")
                    print("\n".join(pushed_lines(r["pushed"])))
            end = now + a.wait * 60
            while True:
                net = load_net()
                if not is_open(net):
                    print(status_lines(status())[0])
                    return NONET
                ingest_cable()
                rr = roll(net, time.time(), discover(time.time()))
                if time.time() >= end or not any(x["status"] == "PENDING" for x in rr["rows"]):
                    break
                time.sleep(15)
            _emit(a, rr, roll_lines(rr))
            return roll_level(rr["rows"])
        if verb == "checkin":
            sit = {k: getattr(a, k) for k, _ in SITREP} if any(getattr(a, k) for k, _ in SITREP) else None
            r = checkin(me, sit, a.ref, a.off, relieve=a.relieve)
            if not r["established"]:
                _emit(a, r, status_lines(status()))
                return NONET
            lines = [f"CHECKED IN: {me} -> Zero {zero_of(r['net'])} (epoch {r['net'].get('epoch')}), lease "
                     f"{int(ttl_s(r['net']) // 60)} min" + (", sitrep recorded" if sit else "")
                     + (f" (answers {r['entry']['ref']})" if r["entry"].get("ref") else "")
                     + (" - signed off" if a.off else "")]
            if r.get("relieved"):
                o = r["relieved"]
                lines.append(f"  RELIEVED {a.relieve}: order {o['id']} is yours: {o['objective']}; ground "
                             f"{', '.join(o['area'])}; done when: {o['done']}")
            lines += [ch.human(e, r["net"]) for e in r["pending"]] or ["  no traffic for you"]
            if r["acked"]:
                lines.append(f"  acked: {', '.join(r['acked'])}")
            lines += pushed_lines(r["remote"])
            _emit(a, {k: v for k, v in r.items() if k != "rec"}, lines)
            return OK
        if verb == "check":
            r = check(heal=not a.no_heal)
            if not r["established"]:
                if not a.scheduled:
                    _emit(a, r, status_lines(status()))
                return NONET
            lines = [f"COMMS CHECK {('OK', 'WARN', 'ALARM')[r['level']]}: Zero {zero_of(r['net'])}, epoch "
                     f"{r['net'].get('epoch')}"]
            lines += [f"  {x['state']:<8} {x['station']} (last {_when(x['last'])})" for x in r["rows"]]
            lines += [f"  cable {x['device']}: {x['detail']}" for x in r["links"]] or ["  cable: not paired"]
            lines += [f"  event: {x}" for x in r["events"]]
            lines += [f"  retried {x}" for x in r["retried"]]
            lines += [f"  UNDELIVERED {x['id']} to {', '.join(x['missing'])}: {x['text'][:80]}" for x in r["undelivered"]]
            cmd = r.get("command") or {}
            lines += [f"  {f['kind']}: {f['text']}" for f in cmd.get("findings", [])]
            if cmd.get("error"):
                lines.append(f"  command sweep failed: {cmd['error']}")
            if cmd.get("woke"):
                lines.append(f"  woke {cmd['woke']} with {len(cmd.get('new', []))} new finding(s)")
            lines.append(f"  PLAN.md Board: {r.get('board')}" + (f"; command row: {cmd['board']}" if cmd.get("board") else ""))
            if not a.scheduled or r["level"]:
                _emit(a, r, lines)
            return r["level"]
        if verb == "time":
            from . import clock
            cab = ch.Cable()
            r = clock.check(cab.devices(), cab.hello)
            append("time", me, text=f"clock {r['level']}", data={"peers": [{k: p[k] for k in ("device", "skew", "level")}
                                                                            for p in r["peers"]],
                                                                 "synced": r["local"]["synced"]})
            _emit(a, r, clock.lines(r))
            return {"OK": OK, "WARN": WARN, "STOP": ALARM}[r["level"]]
        if verb == "handover":
            r = handover(me, a.to, a.take, a.assume, a.second, a.epoch)
            _emit(a, r, [f"ZERO IS NOW {zero_of(r['net'])} (epoch {r['net']['epoch']}; was {r['previous']}). Announced:"]
                  + pushed_lines(r["pushed"]))
            return OK
        if verb == "send":
            if not a.rest:
                ap.error("send needs the text")
            r = send(me, " ".join(a.rest), a.to or "*", a.flash, not a.no_ack)
            _emit(a, r, [f"SENT {r['entry']['id']}" + (" (FLASH)" if a.flash else "") + (", wants an ack" if not a.no_ack else "")]
                  + pushed_lines(r["pushed"]))
            return OK
        if verb == "ack":
            if not a.rest:
                ap.error("ack needs the message id")
            e = ack(me, a.rest[0])
            _emit(a, e, [f"ACKED {a.rest[0]} as {me} (log seq {e['seq']})"])
            return OK
        if verb == "log":
            if a.verify:
                v = verify()
                _emit(a, v, [f"NET LOG {'INTACT' if v['ok'] else 'BROKEN'}: {v['lines']} lines, {v['signed']} signed"
                             + ("" if v["key"] else " (no pairing key here: signatures not checked)")]
                      + [f"  {p}" for p in v["problems"]])
                return OK if v["ok"] else ALARM
            n = int(a.rest[0]) if a.rest else 30
            es = [e for _, e in entries()][-n:]
            _emit(a, es, [f"{e['seq']:>5} {e['at']} e{e.get('epoch')} {e['kind']:<9} {e.get('from')} -> {e.get('to')} "
                          f"{e['id']}" + (f" re {e['ref']}" if e.get("ref") else "") + (f"  {e['text'][:100]}" if e.get("text") else "")
                          for e in es] or ["net log empty"])
            return OK
        if verb == "close":
            r = close_net(me, a.epoch)
            _emit(a, r, [f"NET CLOSED by {me} (epoch {r['net']['epoch']}). Announced:"] + pushed_lines(r["pushed"]))
            return OK
        if verb == "doctor":
            rows = doctor()
            _emit(a, [{"item": i, "ok": o, "detail": d} for i, o, d in rows],
                  [f"  {'OK' if o else 'MISSING':<8} {i}: {d}" for i, o, d in rows])
            return OK if all(o for _, o, _ in rows) else WARN
        if verb in ("task", "release", "orders"):
            from . import command
            if verb == "orders":
                rows = command.orders()
                _emit(a, rows, [f"ORDERS: {sum(r['open'] for r in rows)} open"] + command.order_lines(rows))
                return OK
            if not a.rest:
                ap.error(f"{verb} needs the STATION")
            if verb == "task":
                area = [x for v in a.area for x in v.split(",")]
                r = command.task(me, a.rest[0], a.objective, area, a.done, a.epoch, a.drift_min, a.idle_min)
                _emit(a, r, [f"ORDER {r['order']['id']} -> {r['station']}: {r['order']['objective']}",
                             f"  ground: {', '.join(r['order']['area'])} (claims written: writes outside it refused)",
                             f"  done when: {r['order']['done']}"] + pushed_lines(r["sent"].get("pushed", []))
                      + ([f"  NOT SENT: {r['sent']['refused']}"] if r["sent"].get("refused") else []))
                return OK
            r = command.release(me, a.rest[0], a.finished, a.epoch)
            _emit(a, r, [f"ORDER {r['order']['id']} {r['order']['outcome'].upper()}: {r['station']}'s ground is free"])
            return OK
        if verb == "schedule":
            from . import schedule
            steps = schedule.remove_plan() if a.remove else schedule.plan(state=coord())
            if not (a.apply or a.remove):
                for s in steps:
                    print(f"would {s[0]} {s[1]}" if s[0] != "run" else f"would run: {' '.join(s[1])}")
                print("(nothing changed; --apply installs, --remove undoes)")
                return OK
            return schedule.apply(steps)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED
    return OK


if __name__ == "__main__":
    sys.exit(main())
