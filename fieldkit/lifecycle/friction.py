"""Everything that can wait on a person, on this computer and on every peer over the cable. One line each, with the fix.

    card         a desktop session card nobody started (it waits for the owner's click)
    waiting      a session stopped on a question (AskUserQuestion, ExitPlanMode) or marked awaiting a person
    mode         a live session not in bypass permissions mode (it can stop on a permission prompt)
    remote       Remote Control: a pending delete/unarchive in the app's remote-control state
    link-start   this computer's link.yaml does not let the peer start a headless job (every ask becomes an inbox item)
    prereq       a machine prerequisite the harness proposes that the sealed mandate in force does not hold (its first
                 run would stop on a UAC prompt or an owner yes)
    needs-you    a [NEEDS YOU] note over the cable in the last 48 h that nobody acknowledged
    inbox        an open item in the owner inbox (fieldkit inbox)
    peer         a peer that cannot be audited (no answer, or an older Fieldkit without the friction verb)

Acknowledge a line that is handled with `fieldkit friction ack KEY` (state/lifecycle/friction-ack.json). Read-only
otherwise: no session, file or setting is changed. Zero tokens: plain code, no model.
"""
import json
import os
import re
import time
from pathlib import Path

from . import desktop

NEEDS_YOU = re.compile(r"\[\s*NEEDS YOU\s*\]", re.I)
NEEDS_YOU_HOURS = 48


def state_dir():
    from ..core import settings
    d = Path(os.environ.get("FIELDKIT_LIFECYCLE_DIR") or (settings.ROOT / "state" / "lifecycle"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def acked():
    try:
        return set(json.loads((state_dir() / "friction-ack.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def ack(keys):
    have = acked() | set(keys)
    (state_dir() / "friction-ack.json").write_text(json.dumps(sorted(have), indent=1), encoding="utf-8")
    return sorted(have)


def _row(kind, key, line, fix):
    return {"kind": kind, "key": key, "line": line, "fix": fix}


CLICKED_HOURS = 24


def desktop_rows(sessions, scan=desktop.scan, transcript=desktop.transcript, me=None, now=None):
    """Pending cards, cards the owner had to click in the last 24 h (each one a click that should not have been
    asked: the owner, 2026-10-26: "we really need to fix these problems once for all"), questions, modes."""
    now = now or time.time()
    rows = []
    for s in sessions:
        recent = s["last"] and now - s["last"] < CLICKED_HOURS * 3600
        if s["archived"] and not recent:
            continue
        name = f"{s['title'] or s['id']} ({s['id'][:14]})"
        tp = transcript(s["cli"])
        found = scan(tp) if tp else {"cards": [], "proposals": [], "waiting": ""}
        for c in found.get("proposals") or []:
            if not str(s["resolved"].get(c["id"]) or "").startswith("started"):
                continue
            if c.get("at") is not None and now - c["at"] >= CLICKED_HOURS * 3600:
                continue
            rows.append(_row("clicked", f"clicked:{c['id']}", f"card \"{c['title']}\" ({c['id']}) from {name} was "
                             "started by a click", "the session that proposed it must have used `fieldkit task start` "
                             "(fieldkit sessions rule); fix the path that made the card, then ack it"))
        if s["archived"]:
            continue
        for c in found["cards"]:
            if c["id"] in s["resolved"]:
                continue
            rows.append(_row("card", f"card:{c['id']}", f"card \"{c['title']}\" ({c['id']}) from {name} waits for a "
                             "click", f"in that session: dismiss_task {c['id']}; start the job with "
                             "`fieldkit task start BRIEF.md [--on DEVICE]`"))
        if s["id"] == me:
            continue                                  # the auditing session is busy by definition
        if found["waiting"] or s["awaiting"]:
            what = found["waiting"] or "awaiting a person"
            rows.append(_row("waiting", f"waiting:{s['id']}:{s['last']:.0f}", f"{name} is stopped on {what}",
                             "answer it, or send the session its decision (it must not ask: fieldkit sessions rule)"))
        if s["mode"] and s["mode"] != "bypassPermissions":
            rows.append(_row("mode", f"mode:{s['id']}:{s['mode']}", f"{name} runs in {s['mode']} mode: a tool call "
                             "can stop on a permission prompt", f"set_session_permission_mode {s['id']} "
                             "bypassPermissions (the hard-line hooks still hold), or run the job headless"))
    return rows


def remote_rows(dirs=None):
    rows = []
    for d in dirs if dirs is not None else desktop.app_dirs():
        p = Path(d) / "remote-control-state.json"
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for ident in (st.get("identities") or {}).values():
            for what in ("pendingDelete", "pendingUnarchive"):
                for sid in ident.get(what) or []:
                    rows.append(_row("remote", f"remote:{what}:{sid}", f"Remote Control holds a {what} for {sid}",
                                     "open the app once (it applies it), or archive the session from its own turn"))
    return rows


def link_rows(approvals=None, household=None):
    """A peer is paired but this computer would not start its jobs: each task from it becomes an inbox item."""
    try:
        from ..peers import link
        h = household() if household else link.household()
        if not h or not link.others(h):
            return []
        reg, why = approvals() if approvals else link.approvals()
    except Exception:  # noqa: BLE001
        return []
    if "claude" in (reg.get("allow_start") or []) and reg.get("cwd"):
        return []
    return [_row("link-start", f"link-start:{why}", f"a peer's `fieldkit task start --on` lands in the owner inbox "
                 f"here: {why}", "the owner, once: allow_start: [claude] + cwd in ~/.fieldkit/link/link.yaml, then "
                 "`fieldkit peers link approve` (or put it in the mandate's machine prerequisites)")]


def sealed_prereqs(mandate_mod=None):
    """The prerequisite ids of every mandate in force here. Verifying a seal takes seconds, so the answer is kept per
    (file, size, mtime) of the mandate and its seal: a changed byte in either is verified again."""
    if mandate_mod is None:
        from ..mandate import mandate as mandate_mod
    M = mandate_mod
    cache_p = state_dir() / "sealed-prereqs.json"
    try:
        cache = json.loads(cache_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    out, fresh = set(), {}
    for p in M.mandates():
        p = Path(p)
        stamp = []
        for f in (p, p.with_suffix(".seal")):
            try:
                st = f.stat()
                stamp.append(f"{st.st_size}:{st.st_mtime_ns}")
            except OSError:
                stamp.append("-")
        key, sig = str(p), "|".join(stamp)
        if cache.get(key, {}).get("sig") == sig:
            ids = cache[key]["ids"]
        else:
            ids = ([x.get("id") for x in M.load(p).get("machine_prerequisites") or []] if M.verify(p)[0] else [])
        fresh[key] = {"sig": sig, "ids": ids}
        out |= set(ids)
    if fresh != cache:
        cache_p.write_text(json.dumps(fresh, indent=1), encoding="utf-8")
    return out


def prereq_rows(proposed=None, sealed=None, platform=None):
    """Prerequisites the repository proposes (fieldkit/mandate/*.prereqs.yaml) that the sealed mandate in force lacks."""
    import sys
    import yaml
    platform = platform or ("windows" if sys.platform == "win32" else "darwin" if sys.platform == "darwin" else "linux")
    try:
        if proposed is None:
            proposed = []
            for p in sorted((Path(__file__).resolve().parents[1] / "mandate").glob("*.prereqs.yaml")):
                proposed += (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("machine_prerequisites") or []
        if sealed is None:
            sealed = sealed_prereqs()
    except Exception:  # noqa: BLE001 - no mandate module or file: nothing to compare
        return []
    rows = []
    for x in proposed:
        if x.get("id") in sealed or (x.get("os") and x["os"] != platform):
            continue
        how = "a UAC prompt" if x.get("admin") else "an owner yes"
        rows.append(_row("prereq", f"prereq:{x.get('id')}", f"prerequisite {x.get('id')} is proposed but not in the "
                         f"sealed mandate: its first use stops on {how}", "the owner's ONE list: `fieldkit mandate renew "
                         "~/.fieldkit/mandate/mandates/owner-wide.yaml --from fieldkit/mandate/owner-wide.prereqs.yaml`"))
    return rows


def needs_you_rows(items=None, now=None):
    now = now or time.time()
    try:
        if items is None:
            from ..peers import link
            items = link.inbox()
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for i in items:
        try:
            at = time.mktime(time.strptime(i.get("at", ""), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        if now - at > NEEDS_YOU_HOURS * 3600 or not NEEDS_YOU.search(i.get("text") or ""):
            continue
        first = " ".join((i.get("text") or "").split())[:120]
        rows.append(_row("needs-you", f"needs-you:{i.get('from_host')}:{i.get('n')}", f"[NEEDS YOU] #{i.get('n')} "
                         f"from {i.get('from_host')}: {first}", "do it under the mandate if it covers it, else move "
                         f"it to `fieldkit inbox`; then `fieldkit friction ack needs-you:{i.get('from_host')}:{i.get('n')}`"))
    return rows


def inbox_rows(items=None):
    try:
        if items is None:
            from ..autopilot import inbox
            items = inbox.items("open")
    except Exception:  # noqa: BLE001
        return []
    return [_row("inbox", f"inbox:{i.get('n')}", f"owner inbox #{i.get('n')}: {i.get('title')}",
                 f"the morning list: `fieldkit inbox show {i.get('n')}`, then done or decided") for i in items]


def local_rows(dirs=None, me=None):
    """Every friction line on THIS computer (also what the link verb `friction` answers a peer)."""
    me = me if me is not None else os.environ.get(desktop.HOST_ENV, "")
    rows = (desktop_rows(desktop.sessions(dirs), me=me) + remote_rows(dirs) + link_rows() + prereq_rows()
            + needs_you_rows() + inbox_rows())
    done = acked()
    return [r for r in rows if r["key"] not in done]


def peer_rows(call=None, household=None):
    """-> [(device, rows)] for every other device of the household, asked over the link."""
    try:
        from ..peers import link
        h = household() if household else link.household()
        others = link.others(h) if h else []
        call = call or link.call
    except Exception:  # noqa: BLE001
        return []
    out = []
    for d in others:
        r = call({"verb": "friction"}, timeout=30, device=d["name"])
        if r.get("ok"):
            out.append((d["name"], r.get("rows") or []))
        elif "unknown verb" in str(r.get("error")):
            out.append((d["name"], [_row("peer", f"peer:{d['name']}:old", f"{d['name']} runs a Fieldkit without the "
                                         "friction verb: not audited", f"on {d['name']}: `fieldkit sync --apply` "
                                         "after this change is merged")]))
        else:
            out.append((d["name"], [_row("peer", f"peer:{d['name']}:down", f"{d['name']} did not answer: "
                                         f"{r.get('error')}", "`fieldkit peers link heal`")]))
    return out


def audit(peers=True, dirs=None, me=None, call=None):
    import socket
    out = [(socket.gethostname(), local_rows(dirs, me))]
    if peers:
        out += peer_rows(call)
    return out


def lines(result):
    L, n = [], 0
    for where, rows in result:
        for r in rows:
            n += 1
            L.append(f"FRICTION  {where:<18} {r['kind']:<10} {r['line']}\n          fix: {r['fix']}")
    L.append(f"VERDICT  {n} thing(s) can wait on a person" if n else
             f"VERDICT  zero friction on {len(result)} computer(s)")
    return L
