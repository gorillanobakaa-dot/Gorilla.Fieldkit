# Version: 1.1.0 · updated 26-10-28-20-30
"""Each machine of the household has one tag, [MODEL-OS] ([L15-WIN], [VAIO-LNX], [AIR-MAC]), and every session
title starts with its machine's tag, so a notification, a roll call or a Board row says at a glance which computer
spoke.

    fieldkit machine name [--json]               this machine's tag (made once from the hardware model and the OS,
                                                 then read from the household registry, never retyped)
    fieldkit machine list [--offline] [--json]   every device: tag, OS, link address, last seen (asks each peer's
                                                 hello over the link unless --offline)
    fieldkit machine reach [--no-sweep] [--json] every device of household.yaml by its own path (cable or SSH):
                                                 which are up (household.py)
    fieldkit machine hook                        a Claude Code SessionStart hook: the tag and the title rule
    fieldkit sessions tag --check [--here] [--json]
                                                 every live session, here and on each peer over the link, whose
                                                 title does not start with its machine's tag; exit 1 if any (or if
                                                 a peer could not be checked)

Owner order 2026-10-26 21:5x (verbatim): "make sure this is implemented & integrated into harness as a deterministic
set of tolls, deterministic checks and all that."

ONE registry: the link's household file (~/.fieldkit/link/household.json, fieldkit/peers/link.py). Each device row
carries `tag`, `host` (its host name), `model` (what the tag was made from) and `last_seen`. This machine's tag is
made the first time it is asked, by the fixed rules in machine_tags.yaml (data, not code), and stored in its own
row; a peer's tag comes back in the link's hello answer and is stored in the peer's row. A legacy household (only
link.json's one paired peer) is written out as household.json the first time a tag is stored.

The check is plain code on fixed data: a title is tagged when, leading spaces aside, it starts with the tag. No
model judgment anywhere in the check path. Zero tokens.
"""
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

TAG_RE = re.compile(r"^\[[A-Z0-9]{1,12}-[A-Z]{2,6}\]$")
LEADING_TAG = re.compile(r"^\s*\[[A-Z0-9]{1,12}-[A-Z]{2,6}\]\s*")
RULES = Path(__file__).with_name("machine_tags.yaml")
FIELDS = ("vendor", "product", "version", "family", "model")
DMI = {"vendor": "sys_vendor", "product": "product_name", "version": "product_version", "family": "product_family"}
WIN_BIOS = {"vendor": "SystemManufacturer", "product": "SystemProductName", "version": "SystemVersion",
            "family": "SystemFamily"}
HELLO_TIMEOUT = 8


# ---- the tag itself -----------------------------------------------------------------------------------------------
def rules(path=None):
    import yaml
    return yaml.safe_load(Path(path or RULES).read_text(encoding="utf-8")) or {}


def facts(system=None, dmi=Path("/sys/class/dmi/id"), run=subprocess.run, winreg_mod=None):
    """-> {vendor, product, version, family, model}: what the hardware says it is, read-only. Missing: ""."""
    system = system or platform.system()
    out = dict.fromkeys(FIELDS, "")
    try:
        if system == "Linux":
            for k, f in DMI.items():
                p = Path(dmi) / f
                out[k] = p.read_text(encoding="utf-8", errors="replace").strip() if p.is_file() else ""
        elif system == "Windows":
            wr = winreg_mod
            if wr is None:
                import winreg as wr
            with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as key:
                for k, v in WIN_BIOS.items():
                    try:
                        out[k] = str(wr.QueryValueEx(key, v)[0]).strip()
                    except OSError:
                        pass
        elif system == "Darwin":
            r = run(["sysctl", "-n", "hw.model"], capture_output=True, text=True, timeout=10)
            out["model"] = (r.stdout or "").strip()
    except Exception:  # noqa: BLE001 - no facts: the host name rule still makes a tag
        pass
    return out


def derive(f, system, hostname, table=None):
    """-> the tag the fixed rules make from these facts. Pure: the same facts give the same tag."""
    table = table or rules()
    suffix = (table.get("os") or {}).get(system) or re.sub(r"[^A-Z]", "", str(system).upper())[:3] or "OS"
    short = ""
    for r in table.get("rules") or []:
        m = re.search(r["pattern"], str(f.get(r["field"]) or ""))
        if m:
            short = str(r["tag"]).replace("{1}", m.group(1) if m.groups() else "")
            break
    short = re.sub(r"[^A-Z0-9]", "", short.upper())[:12]
    if not short:
        short = re.sub(r"[^A-Z0-9]", "", str(hostname).upper())[:10] or "HOST"
    return f"[{short}-{suffix}]"


def valid(tag):
    return bool(TAG_RE.match(str(tag or "")))


def _link():
    from . import link
    return link


def name(h=None, save=True, facts_fn=None, system=None, hostname=None):
    """-> {tag, source: registry|derived, stored, device, registry}. Reads this machine's row; the first time, makes
    the tag and stores it there. With no household at all the tag is made and said, nothing is written (pair first)."""
    link = _link()
    h = link.household() if h is None else h
    system = system or platform.system()
    hostname = hostname or socket.gethostname()
    row = link.device(h, h.get("me")) if h else None
    if row and valid(row.get("tag")):
        return {"tag": row["tag"], "source": "registry", "stored": True, "device": row["name"],
                "registry": str(link.household_path())}
    f = (facts_fn or facts)(system=system)       # looked up at call time, so a patched facts() is the one used
    tag = derive(f, system, hostname)
    stored = False
    if row is not None and save:
        row.update(tag=tag, host=hostname, model=" / ".join(v for v in (f.get(k) for k in FIELDS) if v) or hostname,
                   os=row.get("os") or system)
        link.save_household(h)
        stored = True
    return {"tag": tag, "source": "derived", "stored": stored, "device": (row or {}).get("name") or hostname,
            "registry": str(link.household_path()),
            "note": "" if stored else "no household here yet: the tag is not stored (fieldkit peers link pair ...)"}


_CURRENT = None


def current():
    """This machine's tag for the hooks, the link's hello and the tools that stamp lines; "" when it cannot be read.
    Read-only: the stored tag, else the same rules made fresh (only `fieldkit machine name|list` writes the registry,
    so a hook or a hello never rewrites a household file). Never raises."""
    global _CURRENT
    if _CURRENT is None:
        try:
            _CURRENT = name(save=False)["tag"]
        except Exception:  # noqa: BLE001
            _CURRENT = ""
    return _CURRENT


def prefix(title, tag=None):
    """-> the title with the tag in front; a title that already starts with a tag keeps its own."""
    tag = current() if tag is None else tag
    title = " ".join(str(title or "").split())
    if not tag or LEADING_TAG.match(title):
        return title
    return f"{tag} {title}".strip()


def tagged(title, tag):
    return bool(tag) and str(title or "").lstrip().startswith(tag)


def tag_of(host, h=None):
    """-> the tag of the device with that name or host name; "" when the registry does not know it."""
    try:
        h = _link().household() if h is None else h
    except Exception:  # noqa: BLE001
        h = {}
    for d in (h or {}).get("devices", []):
        if host and host in (d.get("name"), d.get("host")) and valid(d.get("tag")):
            return d["tag"]
    return current() if host and host == socket.gethostname() else ""


# ---- every device -------------------------------------------------------------------------------------------------
def learn(h, dev_name, answer, now=None):
    """Store what a peer's hello said about itself in its row: tag, host, OS, last seen. -> the row."""
    d = _link().device(h, dev_name)
    if d is None or not answer.get("ok"):
        return d
    if valid(answer.get("tag")):
        d["tag"] = answer["tag"]
    if answer.get("host"):
        d["host"] = answer["host"]
    if answer.get("os"):
        d["os"] = str(answer["os"]).split()[0]
    d["last_seen"] = time.time() if now is None else now
    return d


def refresh(h=None, call=None, now=None):
    """Ask every other device's hello; store what came back. -> {device: "ok" | the error}."""
    link = _link()
    h = link.household() if h is None else h
    call = call or link.call
    out = {}
    for d in link.others(h) if h else []:
        try:
            r = call({"verb": "hello"}, timeout=HELLO_TIMEOUT, device=d["name"])
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "error": str(e)}
        if r.get("ok"):
            learn(h, d["name"], r, now)
            out[d["name"]] = "ok"
        else:
            out[d["name"]] = str(r.get("error") or "no answer")[:200]
    if h and "ok" in out.values():
        link.save_household(h)
    return out


def devices(h=None, now=None):
    """-> [{name, tag, os, link, address, host, last_seen, me}], one per device of the household."""
    link = _link()
    h = link.household() if h is None else h
    now = time.time() if now is None else now
    rows = []
    for d in (h or {}).get("devices", []):
        me = d.get("name") == h.get("me")
        rows.append({"name": d.get("name"), "tag": d.get("tag") or "", "os": d.get("os") or "",
                     "link": d.get("link") or "", "address": list(d.get("address") or []),
                     "host": d.get("host") or "", "last_seen": now if me else d.get("last_seen"), "me": me})
    return rows


def _when(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts))) if ts else "never"


def device_lines(rows, reached=None):
    L = [f"MACHINES  {len(rows)} device(s) in the household"]
    for r in rows:
        L.append(f"  {'*' if r['me'] else ' '} {r['tag'] or '[?]':<12} {r['name']:<22} {r['os'] or '?':<8} "
                 f"{r['link'] or '-':<6} {','.join(r['address']) or '-':<24} last seen "
                 + ("now (this machine)" if r["me"] else _when(r["last_seen"]))
                 + (f"  (now: {reached[r['name']]})" if reached and reached.get(r["name"], "ok") != "ok" else ""))
    if not rows:
        L.append("  none: this machine is not paired (fieldkit peers link pair init | import)")
    return L


# ---- the session-title check --------------------------------------------------------------------------------------
def untagged(sessions, tag, where):
    """-> [{where, tag, id, title}] for every live session whose title does not start with `tag`."""
    return [{"where": where, "tag": tag, "id": s["id"], "title": s.get("title") or ""}
            for s in sessions if not s.get("archived") and not tagged(s.get("title"), tag)]


def local_check(sessions=None, tag=None, host=None):
    """This machine: {tag, host, sessions, rows}. The link verb `tags` answers a peer with this."""
    if sessions is None:
        from ..lifecycle import desktop
        sessions = desktop.sessions()
    tag = current() if tag is None else tag
    host = host or socket.gethostname()
    live = [s for s in sessions if not s.get("archived")]
    return {"tag": tag, "host": host, "sessions": len(live), "rows": untagged(live, tag, host)}


def check(peers=True, sessions=None, tag=None, call=None, h=None, host=None):
    """-> {machines: [{where, tag, sessions, state: checked|not checked, why}], rows: [untagged session]}."""
    here = local_check(sessions, tag, host)
    machines = [{"where": here["host"], "tag": here["tag"], "sessions": here["sessions"], "state": "checked",
                 "why": ""}]
    rows = list(here["rows"])
    others = []
    if peers:
        try:
            link = _link()
            h = link.household() if h is None else h
            others = link.others(h) if h else []
            call = call or link.call
        except Exception:  # noqa: BLE001
            others = []
    for d in others:
        try:
            r = call({"verb": "tags"}, timeout=30, device=d["name"])
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "error": str(e)}
        if r.get("ok"):
            machines.append({"where": d["name"], "tag": r.get("tag") or "", "sessions": r.get("sessions", 0),
                             "state": "checked", "why": ""})
            rows += [dict(x, where=d["name"]) for x in r.get("rows") or []]
        else:
            err = str(r.get("error") or "no answer")
            why = ("runs a Fieldkit without the tags verb: on it, `fieldkit sync --apply` after this change is "
                   "merged" if "unknown verb" in err else f"did not answer: {err[:160]}")
            machines.append({"where": d["name"], "tag": d.get("tag") or "", "sessions": None,
                             "state": "not checked", "why": why})
    return {"machines": machines, "rows": rows}


def check_lines(r):
    L = []
    for m in r["machines"]:
        L.append(f"{'CHECKED' if m['state'] == 'checked' else 'NOT CHECKED':<12} {m['where']:<22} "
                 f"{m['tag'] or '[?]':<12} " + (f"{m['sessions']} live session(s)" if m["sessions"] is not None
                                                 else m["why"]))
    for x in r["rows"]:
        L.append(f"UNTAGGED     {x['where']:<22} {x['tag'] or '[?]':<12} {x['id'][:24]:<24} "
                 f"\"{x['title'][:60]}\"\n             fix: rename it \"{prefix(x['title'], x['tag'])}\"")
    blind = [m for m in r["machines"] if m["state"] != "checked"]
    if r["rows"]:
        L.append(f"VERDICT  {len(r['rows'])} session(s) without their machine's tag")
    elif blind:
        L.append(f"VERDICT  no untagged session found, but {len(blind)} machine(s) NOT CHECKED")
    else:
        L.append(f"VERDICT  every live session carries its machine's tag ({len(r['machines'])} machine(s))")
    return L


def check_code(r):
    """1 when a session is untagged or a machine could not be checked (silence is not success), else 0."""
    return 1 if r["rows"] or any(m["state"] != "checked" for m in r["machines"]) else 0


# ---- the hook line ------------------------------------------------------------------------------------------------
def _said_dir():
    from ..tokens.rotate import state_dir
    return state_dir() / "said-machine"


def hook_line(call, event):
    """The tag and the title rule: at SessionStart, and at a session's first prompt (the room hook is wired to
    UserPromptSubmit where no SessionStart hook is), once per session. Never raises."""
    try:
        if event not in ("SessionStart", "UserPromptSubmit"):
            return ""
        tag = current()
        if not tag:
            return ""
        sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(call.get("session_id") or ""))
        if sid:
            mark = _said_dir() / f"{sid}.json"
            if event == "UserPromptSubmit" and mark.is_file():
                return ""
            mark.parent.mkdir(parents=True, exist_ok=True)
            mark.write_text(json.dumps({"tag": tag, "event": event, "t": time.time()}), encoding="utf-8")
        line = (f"MACHINE: this machine is {tag}: title yourself {tag} <job> (the session title starts with the "
                "tag; `fieldkit sessions tag --check` lists every session that does not).")
        if event == "SessionStart":                    # advisory: the owner chooses interactive models
            from ..lifecycle import models
            line += " " + models.session_line(call.get("model") or os.environ.get("ANTHROPIC_MODEL"))
        return line.rstrip()
    except Exception:  # noqa: BLE001 - a courtesy line must never break the session's hook
        return ""


def hook(stdin=None):
    """Standalone Claude Code SessionStart hook: prints additionalContext. Never fails the session."""
    try:
        call = json.loads((stdin or sys.stdin).read() or "{}")
        event = call.get("hook_event_name") or "SessionStart"
        line = hook_line(call, event)
        if line:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": line}}))
    except Exception:  # noqa: BLE001
        pass
    return 0


# ---- the command --------------------------------------------------------------------------------------------------
def main(argv=None):
    import argparse
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["reach"]:
        from . import household
        return household.main(argv[1:])
    ap = argparse.ArgumentParser(prog="fieldkit machine", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["name", "list", "reach", "hook"])
    ap.add_argument("--offline", action="store_true", help="list: the registry only, no hello over the link")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "hook":
        return hook()
    if a.action == "name":
        r = name()
        print(json.dumps(r, indent=1, ensure_ascii=False) if a.json else
              r["tag"] + (f"\n{r['note']}" if r.get("note") else ""))
        return 0
    link = _link()
    h = link.household()
    if h:
        name(h)                               # this machine's own row carries its tag before it is listed
    reached = {} if a.offline or not h else refresh(h)
    rows = devices(h)
    print(json.dumps({"devices": rows, "reached": reached, "registry": str(link.household_path())}, indent=1,
                     ensure_ascii=False) if a.json else "\n".join(device_lines(rows, reached)))
    return 0


def sessions_main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit sessions tag")
    ap.add_argument("--check", action="store_true", help="the check (the default; kept so the command reads)")
    ap.add_argument("--here", action="store_true", help="this computer only (no peers)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = check(peers=not a.here)
    print(json.dumps(r, indent=1, ensure_ascii=False) if a.json else "\n".join(check_lines(r)))
    return check_code(r)


if __name__ == "__main__":
    sys.exit(main())
