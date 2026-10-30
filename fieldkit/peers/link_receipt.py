# Version: 1.0.0 · updated 26-10-28-18-21
"""Whether a note over the cable was heard: a receipt at send time, and the notes nobody read after N minutes.

Seen 2026-10-28 (plan C7): `fieldkit peers link send` stored Zero's phase-3 hand-off on the VAIO, but the VAIO session
was idle and no relay listened, so the note sat unread for an hour and the sender was told only "ok". Two facts now
travel back instead:

    at send time   the receiver says whether anything can hear the note: a session named with --to took it, or a
                   relay listener runs (link_relay.py); else `heard: false` and the reason, so the sender uses
                   another channel at once (`fieldkit task start BRIEF --on DEVICE` starts a headless job there)
    later          a note no relay and no `link inbox` read within UNREAD_MINUTES (default 10;
                   FIELDKIT_LINK_UNREAD_MINUTES) is one `unread` row in that computer's `fieldkit friction`, which the
                   sender's `fieldkit friction` reads over the cable (the read-only friction verb)

Read means: the relay cursor (relay.json) or the `link inbox` cursor (inbox-read.json) reached its number, or serve
handed it to the session it was addressed to. Plain code, no model.
"""
import json
import os
import time

from . import link

UNREAD_MINUTES = 10
WINDOW_HOURS = 48


def minutes():
    try:
        return max(1, int(os.environ.get("FIELDKIT_LINK_UNREAD_MINUTES") or UNREAD_MINUTES))
    except ValueError:
        return UNREAD_MINUTES


def _seen_path():
    return link.home() / "inbox-read.json"


def mark_seen(n):
    """`fieldkit peers link inbox` printed every note up to n: a session read them."""
    if n and n > seen_upto():
        _seen_path().write_text(json.dumps({"last": n, "at": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")


def seen_upto():
    try:
        return int(json.loads(_seen_path().read_text(encoding="utf-8")).get("last", 0))
    except (OSError, ValueError, AttributeError):
        return 0


def read_upto():
    from . import link_relay
    return max(link_relay.last_read(), seen_upto())


def mark_handed(n, to):
    p = link.home() / "inbox" / f"{n:04d}-message.json"
    try:
        item = json.loads(p.read_text(encoding="utf-8"))
        item["handed"] = to
        p.write_text(json.dumps(item, indent=1, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass


def heard(handed_to="", listener=None):
    """-> (heard, why) for a note just stored. `listener` is the relay's PID or None (link_relay.other_listener)."""
    if handed_to:
        return True, f"handed to session {handed_to}"
    if listener is None:
        from . import link_relay
        listener = link_relay.other_listener()
    if listener:
        return True, f"a relay listens (PID {listener})"
    return False, ("stored, but nothing here hears it: no session took it and no relay listens; it waits until a "
                   "session runs `fieldkit peers link inbox`. For an order: `fieldkit task start BRIEF --on DEVICE`")


def unread(items, now=None, upto=None, wait_minutes=None):
    """-> the message notes from another computer that nobody read within the wait, newest window only."""
    now = now or time.time()
    upto = read_upto() if upto is None else upto
    wait = (wait_minutes or minutes()) * 60
    out = []
    for i in items:
        if i.get("kind") != "message" or i.get("handed") or int(i.get("n") or 0) <= upto:
            continue
        try:
            at = time.mktime(time.strptime(i.get("at", ""), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        if wait <= now - at <= WINDOW_HOURS * 3600:
            out.append(dict(i, age_minutes=int((now - at) // 60)))
    return out
