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
    pull      Gemini CLI, Antigravity, Codex and any agent with no hook and no listener: nothing can be pushed to
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


class Pull:
    name, mode = "pull", "pull"

    def push(self, entry, net):
        return [{"channel": self.name, "target": "Gemini / Antigravity / Codex", "ok": True,
                 "detail": "pull only: the AGENTS.md / GEMINI.md rule makes them run `fieldkit net checkin` at start"}]


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
        try:
            live = sessions()
        except Exception as e:  # noqa: BLE001 - a broken register must not stop the other channels
            return [{"channel": self.name, "target": "-", "ok": False, "detail": f"register unreadable: {e}"}]
        for s in live:
            name = s.get("name")
            try:
                r = send(name, text + f" (as station: --as opencode:{name})", as_name="fieldkit-zero")
                out.append({"channel": self.name, "target": name, "ok": bool(r.get("ok")),
                            "detail": "sent" if r.get("ok") else str(r.get("error"))})
            except Exception as e:  # noqa: BLE001
                out.append({"channel": self.name, "target": name, "ok": False, "detail": str(e)})
        return out


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
    return [Room(), OpenCode(), Cable(), Pull()]


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
