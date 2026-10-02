"""The allowlist. Everything a test may observe is listed here, with the spec's EXCEPTION PROCESS fields; anything
else is unexpected. Approval is the owner's alone, given at a real terminal (an agent's shell has none): a model
proposes entries, it never approves them.

Entry:
  id, kind (dest | dns | listener | process | file | udp), values (fnmatch globs: host, exe name, profile path),
  ports (optional), scenarios ("*" or [names]), component, source_file, purpose, trigger, expected_frequency,
  expected_payload, security_impact, privacy_impact, test, date, release, approval (null | {by, at, how}).
"""
import fnmatch
import json
import time
from pathlib import Path

FIELDS = ("id", "kind", "values", "scenarios", "component", "source_file", "purpose", "trigger", "expected_frequency",
          "expected_payload", "security_impact", "privacy_impact", "test", "date", "release", "approval")


def path_for(owner_root):
    return Path(owner_root) / "leakgate" / "allow.json"


def load(path):
    p = Path(path)
    if not p.is_file():
        return {"version": 1, "entries": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def problems(data):
    """Schema problems (an entry missing a field of the exception process is itself a failure)."""
    out = []
    seen = set()
    for e in data.get("entries", []):
        missing = [f for f in FIELDS if f not in e or (e[f] in ("", None, []) and f != "approval")]
        if missing:
            out.append(f"{e.get('id', '?')}: missing {missing}")
        if e.get("id") in seen:
            out.append(f"{e.get('id')}: duplicate id")
        seen.add(e.get("id"))
    return out


def match(data, kind, value, scenario, port=None):
    """-> the first entry covering this observation, or None."""
    v = (value or "").lower()
    for e in data.get("entries", []):
        if e.get("kind") != kind:
            continue
        sc = e.get("scenarios", "*")
        if sc != "*" and scenario not in sc:
            continue
        if e.get("ports") and port is not None and int(port) not in [int(x) for x in e["ports"]]:
            continue
        if any(fnmatch.fnmatch(v, g.lower()) for g in e.get("values", [])):
            return e
    return None


def verdict(data, kind, value, scenario, port=None):
    """-> ("allowed" | "pending" | "unexpected", entry id or None)."""
    e = match(data, kind, value, scenario, port)
    if e is None:
        return "unexpected", None
    return ("allowed" if e.get("approval") else "pending"), e["id"]


def approve(path, ids, say=print, terminal=None):
    """Owner only, at a real terminal: prints each entry in full, then records the approval."""
    from ..buildh import task
    if not (terminal if terminal is not None else task.owner_terminal()):
        raise task.Refused("approval is the owner's, at a real terminal; an agent's shell has none")
    data = load(path)
    done = []
    for e in data["entries"]:
        if e["id"] in ids or ("*proposed*" in ids and not e.get("approval")):
            say(json.dumps(e, indent=1, ensure_ascii=False))
            e["approval"] = {"by": "owner", "at": time.strftime("%Y-%m-%d %H:%M:%S"), "how": "terminal"}
            done.append(e["id"])
    save(path, data)
    return done


PROPOSABLE = ("process", "file", "file-system", "udp", "listener")


def propose(path, events, run_name, say=print):
    """Add UNAPPROVED entries for observations of the proposable kinds that no entry covers (destinations and DNS
    names are never proposed from observation: an unexpected host stays unexpected until the owner writes it in).
    -> ids added."""
    data = load(path)
    groups = {}
    for e in events:
        if e["kind"] not in PROPOSABLE:
            continue
        if match(data, e["kind"], e["value"], e["scenario"]):
            continue
        groups.setdefault(e["kind"], set()).add(e["value"])
    added = []
    for kind, vals in groups.items():
        eid = f"observed-{kind}-{run_name}"
        data["entries"].append({
            "id": eid, "kind": kind, "values": sorted(vals), "scenarios": "*",
            "component": "OBSERVED - owner to name", "source_file": "OBSERVED - owner to name",
            "purpose": f"OBSERVED in leakgate run {run_name}; nothing here is approved until the owner confirms it",
            "trigger": "fresh profile, scenario run", "expected_frequency": "every run", "expected_payload": "n/a (local)",
            "security_impact": "owner to assess", "privacy_impact": "owner to assess", "test": "leakgate " + kind,
            "date": time.strftime("%Y-%m-%d"), "release": "157.0", "approval": None})
        added.append(eid)
        say(f"  proposed {eid}: {len(vals)} value(s)")
    save(path, data)
    return added
