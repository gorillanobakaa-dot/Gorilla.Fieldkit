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


def approve(path, ids, say=print):
    """Owner only, at a real terminal: prints each entry in full, then records the approval. There is no parameter
    that stands in for the terminal check (a `terminal=True` argument used to)."""
    from ..buildh import task
    if not task.owner_terminal():
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


def _video():
    from . import scenarios as sc
    return sc.VIDEO_COMPROMISE, sc.MOZILLA_HOSTS


def video_candidates(data, events):
    """-> {(scenario, kind): {host}} for the one exception to "hosts are never proposed": the video compromise
    (decision D-157-12). A destination or DNS name is proposable only when it was observed IN its own video scenario
    (drm-request, h264-call), matches that scenario's maker hosts (Google's Widevine hosts, Cisco's OpenH264 host),
    is not a Mozilla host and no entry covers it yet. The entry it becomes is scoped to that scenario only."""
    video, mozilla = _video()
    out = {}
    for e in events:
        if e.get("kind") not in ("dest", "sni", "dns"):
            continue
        scen, host = e.get("scenario"), (e.get("value") or "").lower()
        if scen not in video or not any(fnmatch.fnmatch(host, g) for g in video[scen]):
            continue
        if any(fnmatch.fnmatch(host, g) for g in mozilla):
            continue
        kind = "dns" if e["kind"] == "dns" else "dest"
        if match(data, kind, host, scen):
            continue
        out.setdefault((scen, kind), set()).add(host)
    return out


def scope_problems(data):
    """Allowlist entries that break the video compromise's scope (ALLOWLIST_POLICY): a Widevine/OpenH264 host allowed
    outside its own scenario, or a Mozilla host allowed in a video scenario."""
    video, mozilla = _video()
    out = []
    for e in data.get("entries", []):
        if e.get("kind") not in ("dest", "dns"):
            continue
        scen = e.get("scenarios", "*")
        for v in e.get("values", []):
            v = v.lower()
            for vs, globs in video.items():
                if any(fnmatch.fnmatch(v, g) or fnmatch.fnmatch(g, v) for g in globs) and scen != [vs]:
                    out.append(f"{e.get('id')}: {v} is a video-compromise host and may be allowed only in scenario {vs} (D-157-12), not {scen}")
            if any(fnmatch.fnmatch(v, g) or fnmatch.fnmatch(g, v) for g in mozilla) and (scen == "*" or set(scen) & set(video)):
                out.append(f"{e.get('id')}: {v} is a Mozilla host allowed in a video scenario ({scen}): the video compromise "
                           "never goes through Mozilla (D-157-12)")
    return out


def propose(path, events, run_name, say=print):
    """Add UNAPPROVED entries for observations of the proposable kinds that no entry covers (destinations and DNS
    names are never proposed from observation: an unexpected host stays unexpected until the owner writes it in),
    with one exception: the video compromise hosts seen in their own scenario (video_candidates), proposed scoped to
    that scenario and unapproved. -> ids added."""
    data = load(path)
    groups = {}
    for e in events:
        if e["kind"] not in PROPOSABLE:
            continue
        if match(data, e["kind"], e["value"], e["scenario"]):
            continue
        groups.setdefault(e["kind"], set()).add(e["value"])
    added = []
    for (scen, kind), hosts in sorted(video_candidates(data, events).items()):
        eid = f"video-{scen}-{kind}-{run_name}"
        maker = "Google (Widevine CDM)" if scen == "drm-request" else "Cisco (OpenH264)"
        data["entries"].append({
            "id": eid, "kind": kind, "values": sorted(hosts), "scenarios": [scen],
            "component": f"GMP plugin download from {maker}", "source_file": "toolkit/modules/GMPInstallManager.sys.mjs",
            "purpose": (f"OBSERVED in leakgate run {run_name}: the video compromise (decision D-157-12), the plugin fetched "
                        f"straight from {maker} because the page in scenario {scen} needed it. Scoped to {scen} only; "
                        "nothing here is approved until the maintainer confirms it at a real terminal"),
            "trigger": "a page asks for protected video (EME Widevine) or an H.264 call", "expected_frequency": "once, when first needed",
            "expected_payload": "plugin archive download (no user data)", "security_impact": "maintainer to assess: third-party binary, checksum-verified",
            "privacy_impact": f"{maker} sees the user's IP address once", "test": f"leakgate {scen}",
            "date": time.strftime("%Y-%m-%d"), "release": "157.0", "approval": None})
        added.append(eid)
        say(f"  proposed {eid} (scenario {scen} only): {sorted(hosts)}")
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
