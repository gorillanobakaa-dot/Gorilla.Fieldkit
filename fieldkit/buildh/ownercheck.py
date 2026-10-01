"""The owner's own preflight (Gorilla.firefox/harness/gorilla_build.py preflight) as a harness step.

The owner wrote ~40 checks after real build failures (lazy getters after an excision, an unresolvable .ftl, a package
manifest entry for a DLL that is not built, a stale objdir). On 2026-10-01 the first run against the ported 157 tree
found four BLOCKERS the port itself could not see. So the harness runs that preflight at the end of every port and
turns each BLOCKER into an owner step with the owner's own fix text: nothing is guessed, nothing is skipped, and
the build gate stays closed until each is done or dropped on purpose.
"""
import re
import subprocess
import sys
from pathlib import Path

BLOCKER = re.compile(r"^\[-\]\s+(.+?)\s+\(BLOCKER\):\s*(.*)$")
#: checks that can only pass once an objdir exists: measured AFTER the build (build-verify), never a reason to
#: hold the gate before it. The build loop passes --force to the owner's stage for these, and only these.
BUILD_DEPENDENT = ("Package manifest resolves", "Localization resources resolve", "Bundled extensions are visible",
                   "Objdir vs CLOBBER")


def post_build(name):
    return any(name.startswith(k) for k in BUILD_DEPENDENT)
FIX = re.compile(r"^\[\*\]\s+fix\s*:\s*(.*)$")


def parse(text):
    """-> [{name, detail, fix}] from the preflight's output."""
    out, cur = [], None
    for line in text.splitlines():
        m = BLOCKER.match(line.strip())
        if m:
            cur = {"name": m.group(1).strip(), "detail": m.group(2).strip()[:300], "fix": ""}
            out.append(cur)
            continue
        m = FIX.match(line.strip())
        if m and cur is not None and not cur["fix"]:
            cur["fix"] = m.group(1).strip()[:300]
    return out


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50]


def run_preflight(owner_root, python=None):
    """Run the owner's preflight (read-only on the tree; it writes its own log). -> (returncode, text)."""
    script = Path(owner_root) / "harness" / "gorilla_build.py"
    if not script.is_file():
        return None, f"no owner preflight at {script}"
    r = subprocess.run([python or sys.executable, str(script), "preflight"], cwd=str(owner_root), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=1800)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _owner_root(t):
    """The owner's repo (holds harness/gorilla_build.py): the task's harness root itself, or, for a snapshot-derived
    root, the parent of the live tree the snapshot was taken from. Never a global setting: a unit test must never
    run the owner's real preflight (it did, once: 151 s and four real blockers inside a test)."""
    import json
    hr = t.get("meta", {}).get("harness_root")
    if not hr:
        return None
    cands = [Path(hr)]
    pol = Path(hr) / "config" / "patch_policy.json"
    try:
        snap = json.loads(pol.read_text(encoding="utf-8")).get("_snapshot") or {}
        if snap.get("live_tree"):
            cands.append(Path(snap["live_tree"]).parent)
    except (OSError, ValueError):
        pass
    for c in cands:
        if (c / "harness" / "gorilla_build.py").is_file():
            return c
    return None


def step_owner_preflight(t, owner_root=None, **kw):
    """Harness step: the owner's preflight; every BLOCKER becomes an owner step after this one."""
    root = owner_root or _owner_root(t)
    if root is None:
        return {"ok": True, "summary": "no owner harness beside this task's harness root: nothing to run"}
    rc, text = run_preflight(root)
    if rc is None:
        return {"ok": True, "summary": text}
    blockers = parse(text)
    have = {s["id"] for s in t["steps"]}
    # a blocker this run no longer reports is resolved: its owner step closes as obsolete with that reason (live run
    # 16: four steps from the first run stayed 'blocked' after the port fixed or the clobber removed their cause)
    now_ids = {f"owner-preflight-{slug(b['name'])}" for b in blockers}
    cleared = []
    for s in t["steps"]:
        if s["id"].startswith("owner-preflight-") and s.get("kind") == "owner" and s.get("status") not in ("done", "obsolete")                 and s["id"] not in now_ids:
            s["status"], s["last_why"] = "obsolete", ["the owner's preflight no longer reports this blocker"]
            cleared.append(s["id"])
    steps = []
    for b in blockers:
        sid = f"owner-preflight-{slug(b['name'])}"
        if sid in have:
            if post_build(b["name"]):                   # an earlier run parked it as a blocker: it is a post-build check
                for s in t["steps"]:
                    if s["id"] == sid and s.get("status") not in ("done", "obsolete"):
                        s["status"], s["post_build"] = "deferred", True
                        s["last_why"] = [f"{b['name']} can only pass once an objdir exists: measured by build-verify"]
            continue
        step = {"id": sid, "kind": "owner",
                "title": f"the owner's preflight blocks the build: {b['name']}: {b['detail']}"
                         + (f" | fix: {b['fix']}" if b["fix"] else "")}
        if post_build(b["name"]):
            step.update({"post_build": True, "status": "deferred",
                         "last_why": [f"{b['name']} can only pass once an objdir exists: measured by build-verify"],
                         "title": f"after the build, the owner's preflight must pass: {b['name']}: {b['detail']}"
                                  + (f" | fix: {b['fix']}" if b["fix"] else "")})
        steps.append(step)
    return {"ok": True, "add_steps": steps, "blockers": [b["name"] for b in blockers], "cleared": cleared,
            "summary": f"owner preflight exit {rc}: {len(blockers)} blocker(s)" + (" -> owner steps" if steps else "")
                       + (f"; {len(cleared)} earlier blocker(s) no longer reported -> obsolete" if cleared else "")}
