"""Zero retires only into a new Zero: `fieldkit zero retire` and the archive guard.

Owner, 2026-10-30 (after Zero epoch 12 archived itself at 17:17 when its PR merged, with no handover, and the net
was left with a dead Zero): "we can ALLOW 0 to archive itself AS LONG AS A NEW ZERO with a handover is created. That
was the whole idea... Zero must have this ability but it needs to be done properly."

A session may archive itself (or be archived) when it does not hold the seat. When it held the seat, it may go only
after all three hold:
  1. the seat moved by a handover (`fieldkit zero handover --to ST`, or the relief's `--take` after
     `fieldkit rotate handover`): the net's Zero is another station;
  2. that new Zero is UP (it checked in; a handover to a dead station leaves nobody in command);
  3. state/coordination/HANDOFF.md was written at or after the handover.
A pending rotation (`handover_pending`) is not a handover: the old Zero still holds the seat until `--take`.

    fieldkit zero retire [--json]        exit 0 = may archive; 4 (REFUSED) = not yet, with the one missing step
    pretool(call)                         the tool router's part: archive_session on Zero's session is refused
"""
import time

# net is imported inside each function: the tool router imports this module on every call, and only an archive call
# may load the net code (tests/test_zero_command.py::test_the_whole_hook_process_measured_and_loads_no_yaml)

ARCHIVE_TOOLS = ("archive_session",)


def _last_handover_from(me):
    """-> the newest handover entry that took the seat from `me`, or None."""
    from . import net
    last = None
    for _off, e in net.entries():
        if e.get("kind") == "handover" and (e.get("data") or {}).get("previous") == me:
            last = e
    return last


def verdict(me, now=None):
    """-> {"ok", "reason", "zero", "handover"} for station `me` archiving itself."""
    from . import net
    now = time.time() if now is None else now
    n = net.load_net()
    zero = net.zero_of(n)
    if zero == me:
        pend = n.get("handover_pending") or {}
        why = ("a rotation is pending: Zero stays until its relief runs `fieldkit zero handover --take`" if pend else
               "you hold the Zero seat: hand it over first (`fieldkit zero handover --to ST`, or `fieldkit rotate "
               "handover` and let the relief `--take`), write state/coordination/HANDOFF.md, then retire")
        return {"ok": False, "reason": why, "zero": zero, "handover": None}
    h = _last_handover_from(me)
    if h is None:
        return {"ok": True, "reason": "not Zero (never held the seat in this log)", "zero": zero, "handover": None}
    if not zero:
        return {"ok": False, "reason": "the net is not open: nobody holds the seat you left", "zero": zero,
                "handover": h}
    rec = net.load_stations()["stations"].get(zero) or {}
    st = net.state_of(rec, now) if rec else "unknown"
    if st != "UP":
        return {"ok": False, "reason": f"the new Zero {zero} is {st}, not UP: stay until it checks in", "zero": zero,
                "handover": h}
    f = net.coord() / "HANDOFF.md"
    if not f.is_file() or f.stat().st_mtime + 1 < float(h.get("ts") or 0):
        return {"ok": False, "reason": f"{f} is older than the handover ({h.get('at')}): write it, then retire",
                "zero": zero, "handover": h}
    return {"ok": True, "reason": f"handed over to {zero} (UP) at {h.get('at')}; HANDOFF.md written", "zero": zero,
            "handover": h}


def _target_station(call, n):
    """The station an archive_session call would end: "self" is the caller; else the Zero whose desktop session id
    matches. -> station or None (an unrelated session: not this guard's business)."""
    from . import net
    target = str((call.get("tool_input") or {}).get("session_id") or "")
    if target == "self":
        return net.hook_station(call.get("session_id"))
    z = n.get("zero") or {}
    if target and target in (z.get("host_session"), z.get("session")):
        return z.get("station")
    return None


def pretool(call):
    """PreToolUse: -> the refusal text when the call would archive the Zero session before it retired properly, else
    None. Never raises (fails open: the hook teaches, the hard lines are the safety layer)."""
    try:
        tool = str(call.get("tool_name") or "")
        if not tool.endswith(ARCHIVE_TOOLS):
            return None
        from . import net
        me = _target_station(call, net.load_net())
        if not me:
            return None
        v = verdict(me)
        return None if v["ok"] else ("ZERO RETIRE: this would archive Zero with nobody in command. "
                                     + v["reason"] + ". Check: fieldkit zero retire")
    except Exception:  # noqa: BLE001
        return None


def lines(v):
    return [("RETIRE OK: " if v["ok"] else "RETIRE NOT YET: ") + v["reason"],
            "NEXT: " + ("archive this session (archive_session \"self\")" if v["ok"] else "do the step above, then "
                        "fieldkit zero retire again")]
