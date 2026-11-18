# Version: 1.2.0 · updated 26-10-28-21-00
"""How a net message reaches a station: every channel the harness already has, and nothing new.

    room      Claude Code sessions on this computer. PULL: the room hook (fieldkit/autopilot/room.py, run by the tool
              router's PreToolUse hook + UserPromptSubmit) shows the session its unseen net traffic and checks it in. A Claude Desktop session
              that is idle runs no hook, so it stays LATE until it acts: Zero, if it is a Desktop session, nudges it
              with its own send_message tool (the CLI cannot reach the Desktop app; that door belongs to the app).
    opencode  Gorilla OpenCode sessions on this computer: PUSH, `fieldkit peers send` (its own session protocol,
              named pipe / Unix socket, same account only).
    cable     the other computer of the household: PUSH, `fieldkit peers link` (signed HMAC-SHA256 with the
              hand-carried pairing key, 120 s window, nonce, cable address only). The message is a link note whose
              text carries `NET1 {json}`; the far side's `fieldkit net checkin` ingests it, and its check-ins come
              back the same way. A station there is store-and-forward: the note waits in its link inbox.
    agent-hook  Codex and Gemini CLI stations that have checked in from their native inbox hooks.
    pull      An agent with no hook and no listener: nothing can be pushed to
              them. The rule in AGENTS.md / GEMINI.md makes them run `fieldkit net checkin` at start; they see their
              traffic then. Listed in every roll call as "pull only", never as delivered.

Who can act through these doors: only processes of this account on this computer (room, opencode, pull) and the one
paired computer on the cable (cable). No port is opened here, no address is called that the link does not already
call, nothing leaves the household.
"""
import json
import os

NET1 = "NET1 "
PUSH_MAX = 12 * 1024            # an OpenCode note is at most 16 KB, a link request 64 KB: stay well under both


def envelope(entry):
    return NET1 + json.dumps(entry, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def parse_envelope(text):
    """-> the entry inside a link note's text, or None. The sending link stamps "[FROM WINDOWS #N] " in front."""
    i = (text or "").find(NET1 + "{")
    if i < 0:
        return None
    try:
        e = json.loads(text[i + len(NET1):].strip())
    except ValueError:
        return None
    return e if isinstance(e, dict) and e.get("kind") and e.get("id") else None


def human(entry, net):
    """The text a station reads: one line, then what to run."""
    k, ref = entry.get("kind"), entry.get("id")
    who = (net.get("zero") or {}).get("station", "?")
    head = {"open": f"NET OPEN: Zero has been established. Zero = {who}, epoch {net.get('epoch')}.",
            "handover": f"NET: Zero is now {who} (epoch {net.get('epoch')}).",
            "close": f"NET CLOSED by Zero {who} (epoch {net.get('epoch')}).",
            "rollcall": f"NET ROLL CALL {ref} from Zero {who}: report your sitrep.",
            "msg": f"NET MESSAGE {ref} from {entry.get('from')}: {entry.get('text', '')}"}.get(k, f"NET {k} {ref}")
    if k == "rollcall":
        return (head + f" Run: fieldkit net checkin --ref {ref} --job \"...\" --state WORKING|BLOCKED|WAITING|DONE|IDLE "
                "--branch \"...\" --waiting \"...\" --not-verified \"...\" --shared \"...\" (use - for none)")
    if entry.get("want_ack"):
        return head + f" Ack: fieldkit net ack {ref}"
    return head


class Room:
    name, mode = "room", "pull"

    def push(self, entry, net):
        return [{"channel": self.name, "target": "Claude Code sessions here", "ok": True,
                 "detail": "delivered by the room hook at each session's next prompt or tool call"}]


def _local_antigravity_ears(ear_state_fn=None):
    """-> [(station_id, rec, state_dict)] for local antigravity:* stations in net-stations.json or ears/."""
    from ..peers import link, link_relay
    from . import net as zero_net
    ear_state_fn = ear_state_fn or link_relay.ear_state
    found = {}
    here = zero_net.machine()
    try:
        for sid, rec in zero_net.load_stations().get("stations", {}).items():
            if (rec.get("kind") or zero_net.kind_of(sid)) == "antigravity" and zero_net.machine_of(sid) in ("", here):
                found[sid] = rec
    except Exception:  # noqa: BLE001
        pass
    try:
        ears_dir = link.home() / "ears"
        if ears_dir.is_dir():
            for p in ears_dir.glob("antigravity_*.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                sid = d.get("station") if isinstance(d, dict) else None
                if sid and zero_net.machine_of(sid) in ("", here):
                    found.setdefault(sid, {})
    except Exception:  # noqa: BLE001
        pass
    out = []
    for sid, rec in sorted(found.items()):
        try:
            es = ear_state_fn(sid)
        except Exception:  # noqa: BLE001
            es = {"state": "never"}
        out.append((sid, rec, es))
    return out


def _antigravity_hook_ok(hook_doctor_fn=None):
    try:
        if hook_doctor_fn is None:
            from ..autopilot import room
            hook_doctor_fn = room.antigravity_doctor
        return bool(hook_doctor_fn().get("ok"))
    except Exception:  # noqa: BLE001
        return False


def antigravity_channels(station=None, ear_state_fn=None, hook_doctor_fn=None):
    """-> (active_rows, fallback_rows) describing active Antigravity ear / room hook channels."""
    from ..peers import link_relay
    ear_state_fn = ear_state_fn or link_relay.ear_state
    hook_ok = _antigravity_hook_ok(hook_doctor_fn)
    active, fallback = [], []
    if station:
        try:
            es = ear_state_fn(station)
        except Exception:  # noqa: BLE001
            es = {"state": "never"}
        via_hook = False
        try:
            from . import net as zero_net
            rec = zero_net.load_stations().get("stations", {}).get(station) or {}
            via_hook = rec.get("via") == "hook"
        except Exception:  # noqa: BLE001
            pass
        if es.get("state") == "running":
            active.append({"channel": "antigravity", "target": station, "ok": True,
                           "detail": "active ear running (wakes on delivery)"})
        elif via_hook or hook_ok:
            active.append({"channel": "antigravity", "target": station, "ok": True,
                           "detail": "delivered by the project room hook at each prompt or tool call"})
        else:
            fallback.append({"channel": "pull", "target": station, "ok": True,
                             "detail": "pull only (no active Antigravity ear or project room hook)"})
        return active, fallback

    for sid, rec, es in _local_antigravity_ears(ear_state_fn):
        if es.get("state") == "running":
            active.append({"channel": "antigravity", "target": sid, "ok": True,
                           "detail": "active ear running (wakes on delivery)"})
        elif rec.get("via") == "hook" or hook_ok:
            active.append({"channel": "antigravity", "target": sid, "ok": True,
                           "detail": "delivered by the project room hook at each prompt or tool call"})
    if not active and hook_ok:
        active.append({"channel": "antigravity", "target": "Antigravity GUI hook", "ok": True,
                       "detail": "delivered by the project room hook at each prompt or tool call"})
    return active, fallback


def antigravity_via(ear_state_fn=None, hook_doctor_fn=None):
    """-> 'ear' | 'room' | 'pull' for Antigravity station discovery."""
    for _sid, _rec, es in _local_antigravity_ears(ear_state_fn):
        if es.get("state") == "running":
            return "ear"
    if _antigravity_hook_ok(hook_doctor_fn):
        return "room"
    return "pull"


class Antigravity:
    name, mode = "antigravity", "push"

    def __init__(self, station=None, ear_state=None, hook_doctor=None):
        self._station, self._ear_state, self._hook_doctor = station, ear_state, hook_doctor

    def push(self, entry, net):
        to = entry.get("to") or "*"
        if self._station:
            active, fallback = antigravity_channels(self._station, self._ear_state, self._hook_doctor)
            return active or fallback
        if to != "*" and not str(to).startswith("antigravity:"):
            return []
        active, _ = antigravity_channels(to if to != "*" else None, self._ear_state, self._hook_doctor)
        return active


class Pull:
    name, mode = "pull", "pull"

    def __init__(self, ear_state=None, hook_doctor=None):
        self._ear_state, self._hook_doctor = ear_state, hook_doctor

    def push(self, entry, net):
        to = entry.get("to") or "*"
        if to != "*" and _hook_stations(to):
            return []
        active, _ = antigravity_channels(to if str(to).startswith("antigravity:") else None,
                                         self._ear_state, self._hook_doctor)
        if str(to).startswith("antigravity:") and active:
            return []
        if active:
            return [{"channel": self.name, "target": "Gemini / Codex", "ok": True,
                     "detail": "pull only (Antigravity reached via active ear/hook); startup rule makes others check in"}]
        return [{"channel": self.name, "target": "Gemini / Antigravity / Codex", "ok": True,
                 "detail": "pull only: the AGENTS.md / GEMINI.md rule makes them run `fieldkit net checkin` at start"}]


def _hook_stations(target):
    from . import net as zero_net

    return [sid for sid, rec in zero_net.load_stations()["stations"].items()
            if zero_net.kind_of(sid) in ("codex", "gemini")
            and zero_net.machine_of(sid) == zero_net.machine()
            and (target == "*" or target == sid)
            and rec.get("via") == "hook" and not rec.get("off")]


class AgentHooks:
    name, mode = "agent-hook", "pull"

    def push(self, entry, net):
        to = entry.get("to") or "*"
        if to != "*" and not to.startswith(("codex:", "gemini:")):
            return []
        return [{"channel": self.name, "target": sid, "ok": True,
                 "detail": "queued for the next native inbox hook; receipt pending"}
                for sid in _hook_stations(to)]


class OpenCode:
    name, mode = "opencode", "push"

    def __init__(self, sessions=None, send=None):
        self._sessions, self._send = sessions, send

    def push(self, entry, net):
        from ..peers import peers
        sessions = self._sessions or peers.sessions
        send = self._send or peers.send
        text = human(entry, net)[:PUSH_MAX]
        out = []
        to = entry.get("to") or "*"
        if to != "*" and not str(to).startswith("opencode:"):
            # 2026-11-15: a message to Zero (a Claude Code station) was pushed to every OpenCode session as input.
            # Owner, same day: he WANTS to see Zero's traffic in OpenCode, just not mixed with his own text and not
            # as the model's input. So it goes as a CC: flagged, shown to the person only (OpenCode type "cc").
            return self._copy(entry, net, text, sessions, send)
        try:
            live = sessions()
        except Exception as e:  # noqa: BLE001 - a broken register must not stop the other channels
            return [{"channel": self.name, "target": "-", "ok": False, "detail": f"register unreadable: {e}"}]
        if to != "*":
            want = to.split(":", 1)[1].split("@", 1)[0]
            live = [s for s in live if s.get("name") == want]
        told = _told_load() if entry.get("kind") == "open" else {}
        epoch_key = str(net.get("epoch"))
        for s in live:
            name = s.get("name")
            if entry.get("kind") == "open" and name in told.get(epoch_key, []):
                continue  # NET OPEN once per station per epoch (a repeat check-in is not news)
            try:
                r = send(name, text + f" (as station: --as opencode:{name})", as_name="fieldkit-zero")
                out.append({"channel": self.name, "target": name, "ok": bool(r.get("ok")),
                            "detail": "sent" if r.get("ok") else str(r.get("error"))})
                if r.get("ok") and entry.get("kind") == "open":
                    told.setdefault(epoch_key, []).append(name)
            except Exception as e:  # noqa: BLE001
                out.append({"channel": self.name, "target": name, "ok": False, "detail": str(e)})
        if entry.get("kind") == "open":
            _told_save({epoch_key: told.get(epoch_key, [])})
        return out


    def _copy(self, entry, net, text, sessions, send):
        """A display-only copy of Zero's traffic (sent to Zero or by Zero) to every OpenCode session. Never input to a
        model: OpenCode shows a "cc" request and never queues it. A setting (fieldkit.local.json
        zero.mirror_to_opencode, default true, owner 2026-11-15) turns it off."""
        if not mirror_on():
            return [{"channel": self.name, "target": "-", "ok": True, "detail": "mirror to OpenCode is off"}]
        zero = (net.get("zero") or {}).get("station")
        if entry.get("kind") != "msg" or not zero or zero not in (entry.get("from"), entry.get("to")):
            return [{"channel": self.name, "target": "-", "ok": True,
                     "detail": f"not for OpenCode (addressed to {entry.get('to')}); not pushed"}]
        try:
            live = sessions()
        except Exception as e:  # noqa: BLE001
            return [{"channel": self.name, "target": "-", "ok": False, "detail": f"register unreadable: {e}"}]
        out = []
        for s in live:
            name = s.get("name")
            try:
                r = send(name, f"from {entry.get('from')} to {entry.get('to')}: {entry.get('text', '')}"[:PUSH_MAX],
                         as_name="fieldkit-zero", kind="cc")
                out.append({"channel": self.name, "target": name, "ok": bool(r.get("ok")),
                            "detail": "copy sent (display only)" if r.get("ok") else str(r.get("error"))})
            except Exception as e:  # noqa: BLE001
                out.append({"channel": self.name, "target": name, "ok": False, "detail": str(e)})
        return out


def mirror_on():
    from ..core import settings
    try:
        v = (settings.local_settings().get("zero") or {}).get("mirror_to_opencode", True)
    except Exception:  # noqa: BLE001 - an unreadable setting keeps the default
        return True
    return v is not False


def _told_path():
    from . import net
    return net.coord() / "net-open-told.json"


def _told_load():
    try:
        return json.loads(_told_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _told_save(d):
    try:
        _told_path().write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def fake_cable_call(req, device=None, timeout=None):
    """The cable in tests (FIELDKIT_FAKE_CABLE=PATH, set by tests/conftest.py and inherited by every child): each
    request is appended to PATH as one JSON line and nothing is sent (2026-10-28: a Zero test's child hook process
    sent fixture check-ins over the real cable)."""
    with open(os.environ["FIELDKIT_FAKE_CABLE"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"device": device, "req": req}, ensure_ascii=False) + "\n")
    return {"ok": False, "error": "fake cable (tests): nothing sent"}


def link_call():
    """-> the function a Cable sends with: the fake cable in tests, else the link's signed call."""
    if os.environ.get("FIELDKIT_FAKE_CABLE"):
        return fake_cable_call
    from ..peers import link
    return link.call


class Cable:
    name, mode = "cable", "push"

    def __init__(self, household=None, call=None, timeout=15):
        self._household, self._call, self.timeout = household, call, timeout

    def devices(self):
        from ..peers import link
        try:
            h = (self._household or link.household)()
        except Exception:  # noqa: BLE001 - an unreadable household is "not paired", said, not raised
            return []
        return [d.get("name") for d in link.others(h)] if h else []

    def push(self, entry, net):
        call = self._call or link_call()
        devs = self.devices()
        if not devs:
            return [{"channel": self.name, "target": "-", "ok": False, "detail": "not paired (fieldkit peers link)"}]
        out = []
        for d in devs:
            try:
                r = call({"verb": "message", "text": envelope(entry)}, device=d, timeout=self.timeout)
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": str(e)}
            out.append({"channel": self.name, "target": d, "ok": bool(r.get("ok")),
                        "detail": "stored there" if r.get("ok") else str(r.get("error"))})
        return out

    def hello(self, device):
        call = self._call or link_call()
        try:
            return call({"verb": "hello"}, device=device)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}


def default():
    return [Room(), OpenCode(), Cable(), Antigravity(), AgentHooks(), Pull()]


def inbox_items(since, inbox=None):
    """Net entries that arrived over the cable after link inbox number `since`: [(n, entry)]."""
    from ..peers import link
    items = (inbox or link.inbox)(0, since)
    out = []
    for it in items:
        e = parse_envelope(it.get("text", ""))
        if e:
            out.append((it["n"], dict(e, via="cable", from_host=it.get("from_host"))))
    return out
