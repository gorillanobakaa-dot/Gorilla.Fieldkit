# Version: 1.0.0 · updated 26-10-28-19-48
"""The household list (household.yaml): every device, reached by the one path it has, and which of them are up.

    fieldkit machine reach [--no-sweep] [--json]   each device of household.yaml by its own path: the cable devices
                                                   by the link's hello, the SSH-only ones by `fieldkit mac`'s lookup
                                                   and one `ssh ... true`; exit 0 all up, 3 a device down or wrongly
                                                   wired (a SSH-only device on the cable)

Each device has ONE id (l15, vaio, air) that recipes and commands use; `resolve` maps an id, an alias, the tag or the
device's household.json name to its row, and refuses any other name (plan items R6 + C1: recipes said l15 / vaio,
the cable said the host names).

Plan item C1 (owner 2026-10-27): the comms layer knew only the cable, one peer, no household list, and the MacBook Air
(which cannot take the cable) was not in it. Now the list is data and each row says its path; the comms check
(`fieldkit zero check`) reports the same rows, without the ping sweep.

States: UP (answered by its path), DOWN (its path was tried here and it did not answer), HERE (this machine),
NOT FROM HERE (the path exists only on another machine: the SSH key is there), NOT SET UP (the path is not
configured on this machine: not paired, no local/macs.json entry), WRONG PATH (household.json puts a device on a path
household.yaml forbids). Doors: none new. The cable call is the link's signed hello; SSH is the owner's own key and
alias, run in BatchMode (never a password prompt).
"""
import json
import sys
from pathlib import Path

PLAN = Path(__file__).with_name("household.yaml")
PATHS = ("cable", "ssh")
SSH_TIMEOUT = 20
GOOD = ("UP", "HERE", "NOT FROM HERE")


def load(path=None):
    """-> [device rows] of household.yaml. A missing or malformed list is an error, never an empty household."""
    import yaml
    p = Path(path or PLAN)
    if not p.is_file():
        raise ValueError(f"{p}: the household list is missing (it ships with Fieldkit; restore it from git)")
    rows = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("devices")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{p}: no `devices:` list")
    for r in rows:
        if r.get("path") not in PATHS:
            raise ValueError(f"{p}: {r.get('tag')}: path {r.get('path')!r} is not one of {', '.join(PATHS)}")
        if r["path"] in (r.get("never") or []):
            raise ValueError(f"{p}: {r.get('tag')}: path {r['path']} is also under never")
        if r["path"] == "ssh" and not r.get("mac"):
            raise ValueError(f"{p}: {r.get('tag')}: an ssh device names its `mac` (fieldkit mac list)")
        if not r.get("id"):
            raise ValueError(f"{p}: {r.get('tag')}: a device needs its `id` (the one name recipes use)")
    seen = {}
    for r in rows:
        for n in names(r):
            if seen.setdefault(n, r["id"]) != r["id"]:
                raise ValueError(f"{p}: the name {n!r} means both {seen[n]} and {r['id']}")
    return rows


def names(row):
    """-> the lower-cased names that mean this device: its id, its tag and its aliases."""
    return [str(n).lower() for n in [row.get("id"), row.get("tag"), *(row.get("aliases") or [])] if n]


def resolve(name, plan=None, h=None):
    """-> the household.yaml row `name` means: an id, an alias, a tag, or (with h, household.json) the device's name
    there, matched by its tag. Any other name is a ValueError that lists the ids: never a guess."""
    plan = load() if plan is None else plan
    key = str(name or "").strip().lower()
    for r in plan:
        if key in names(r):
            return r
    for d in (h or {}).get("devices", []):
        if str(d.get("name", "")).lower() == key and d.get("tag"):
            row = next((r for r in plan if r["tag"] == d["tag"]), None)
            if row:
                return row
    raise ValueError(f"unknown device {name!r}; the household (fieldkit/peers/household.yaml) has "
                     + ", ".join(f"{r['id']} {r['tag']}" + (f" (also {', '.join(r['aliases'])})"
                                                           if r.get("aliases") else "") for r in plan))


def cable_name(row, h):
    """-> the name of the household.json device with this row's tag (the name the link calls), or None."""
    return next((d["name"] for d in (h or {}).get("devices", []) if d.get("tag") == row["tag"] and d.get("name")),
                None)


def wrong_paths(plan, h):
    """-> {tag: why} for each household.json row put on a path its household.yaml row forbids."""
    out = {}
    for r in plan:
        for d in (h or {}).get("devices", []):
            if d.get("tag") == r["tag"] and (d.get("link") or "") in (r.get("never") or []):
                out[r["tag"]] = (f"household.json puts {d.get('name')} on the {d.get('link')}, which household.yaml "
                                 f"forbids for it (never: {', '.join(r['never'])}): fieldkit peers link devices remove "
                                 f"{d.get('name')}")
    return out


def _ssh_probe(mac, sweep=True, runner=None):
    """-> (up, detail): find the Mac (mDNS, the neighbour table, with sweep a ping sweep), then one `true` over SSH."""
    from ..mac import macssh
    if sweep:
        addr, how = macssh.find(mac, runner=runner)
    else:
        addr = macssh.resolve_mdns(mac.get("mdns"), runner)
        how = f"mDNS name {mac.get('mdns')}"
        if not addr and mac.get("hw_addr"):
            addr = macssh.neighbours(runner).get(mac["hw_addr"].lower())
            how = "the neighbour table"
        if not addr:
            how = f"{mac.get('mdns') or 'no mDNS name'} did not resolve and it is not in the neighbour table (no sweep)"
    if not addr:
        return False, how
    rc, out = macssh._run(macssh.ssh_argv(mac, addr, "true"), timeout=SSH_TIMEOUT, runner=runner)
    return (True, f"ssh answered at {addr} (found by {how})") if rc == 0 else \
        (False, f"found at {addr} by {how}, but ssh failed: {(out.splitlines() or [str(rc)])[-1][:160]}")


def reach(plan=None, h=None, me=None, call=None, macs=None, ssh_probe=None, sweep=True, now=None, only=None):
    """-> [{tag, what, path, state, device, detail}], one per device of household.yaml, each tried by its own path
    (`only`: these paths only; the comms check asks for "ssh", having said the cable itself)."""
    from . import link, machine
    plan = load() if plan is None else plan
    if only:
        plan = [r for r in plan if r["path"] in only]
    if h is None:
        try:
            h = link.household()
        except Exception:  # noqa: BLE001 - an unreadable household is "not paired", said per device
            h = {}
    me = machine.current() if me is None else me
    cable = any(r["path"] == "cable" for r in plan)
    reached = machine.refresh(h, call=call, now=now) if h and cable else {}   # each cable device's hello; tags learned
    by_tag = {d.get("tag"): d for d in (h or {}).get("devices", []) if d.get("tag")}
    wrong = wrong_paths(plan, h)
    ssh_probe = ssh_probe or _ssh_probe
    rows = []
    for r in plan:
        row = {"tag": r["tag"], "what": r.get("what", ""), "path": r["path"], "device": "", "state": "", "detail": ""}
        rows.append(row)
        if r["tag"] in wrong:
            row.update(state="WRONG PATH", detail=wrong[r["tag"]])
            continue
        if r["tag"] == me:
            row.update(state="HERE", detail="this machine", device=(by_tag.get(me) or {}).get("name", ""))
            continue
        if r["path"] == "cable":
            d = by_tag.get(r["tag"])
            if not h:
                row.update(state="NOT SET UP", detail="this machine is not paired (fieldkit peers link pair)")
            elif d is None:
                unknown = [x["name"] for x in link.others(h) if not x.get("tag")]
                row.update(state="DOWN" if unknown else "NOT SET UP",
                           detail=(f"no cable device answered as {r['tag']} (did not answer: {', '.join(unknown)})"
                                   if unknown else f"no cable device of household.json is {r['tag']}"))
            else:
                ok = reached.get(d["name"]) == "ok"
                row.update(device=d["name"], state="UP" if ok else "DOWN",
                           detail="answered the link's hello" if ok else str(reached.get(d["name"]) or "no answer"))
            continue
        allowed = r.get("from") or []
        if allowed and me not in allowed:
            row.update(state="NOT FROM HERE", detail=f"reached over SSH from {', '.join(allowed)} only")
            continue
        try:
            if macs is None:
                from ..mac import macssh
                macs = json.loads(macssh.CONFIG.read_text(encoding="utf-8")).get("macs", {}) \
                    if macssh.CONFIG.is_file() else {}
        except (OSError, ValueError) as e:
            macs = {}
            row["detail"] = f"local/macs.json unreadable: {e}; "
        mac = macs.get(r["mac"])
        if not mac:
            row.update(state="NOT SET UP", detail=row["detail"] + f"no Mac named {r['mac']} on this machine "
                       f"(fieldkit mac add {r['mac']} ...)")
            continue
        up, why = ssh_probe(mac, sweep=sweep)
        row.update(device=r["mac"], state="UP" if up else "DOWN", detail=why)
    return rows


def code(rows):
    return 0 if all(r["state"] in GOOD for r in rows) else 3


def lines(rows):
    L = [f"HOUSEHOLD  {len(rows)} device(s) (fieldkit/peers/household.yaml), each by its own path"]
    for r in rows:
        L.append(f"  {r['state']:<13} {r['tag']:<12} {r['path']:<6} {r['device'] or '-':<20} {r['detail']}")
    up = sum(r["state"] in ("UP", "HERE") for r in rows)
    L.append(f"VERDICT  {up} of {len(rows)} up" + ("" if code(rows) == 0 else
             "; " + ", ".join(f"{r['tag']} {r['state']}" for r in rows if r["state"] not in GOOD)))
    L.append("NEXT: " + ("nothing; every device reachable from here is up" if code(rows) == 0 else
                         "the DETAIL of each row says what to do (a DOWN cable device: fieldkit peers link heal)"))
    return L


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit machine reach", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-sweep", action="store_true", help="an SSH device: no ping sweep when its name does not resolve")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        rows = reach(sweep=not a.no_sweep)
    except ValueError as e:
        print(f"ERROR  {e}")
        return 1
    print(json.dumps({"devices": rows, "code": code(rows)}, indent=1, ensure_ascii=False) if a.json
          else "\n".join(lines(rows)))
    return code(rows)


if __name__ == "__main__":
    sys.exit(main())
