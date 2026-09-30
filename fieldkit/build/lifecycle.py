"""lifecycle - install, check, (upgrade, check,) uninstall, and report what was left behind.

Harvested from the install / upgrade / uninstall test scripts written for Gorilla
TPFanControl (settings backed up, service stopped, nothing left behind), a
Windows tweaks framework's stale-install scan, and an agent-evaluation snapshot tool.

A lifecycle spec (YAML):

    name: myapp
    watch:                      # what the snapshot records
      paths: ["${ENV:ProgramFiles}/MyApp", "${HOME}/AppData/Roaming/MyApp"]
      parts: [programs, services, tasks]        # optional system parts
    install:   {cmd: [...], verify: [{files_exist: [...]}, {output_contains: RE}]}
    upgrade:   {cmd: [...], verify: [...]}      # optional
    uninstall: {cmd: [...], verify: [...]}
    allowed_leftovers: ["*.log"]                # things the uninstaller may keep, by design

    fieldkit lifecycle SPEC --approve           # installs software: the owner's decision

Report: each step's result and verify lines, then LEFTOVERS - everything that
exists after uninstall that did not exist before install, minus allowed ones.
A clean lifecycle has no leftovers and every verify passed.
"""
import fnmatch
import re
from pathlib import Path

from ..core import settings, snapshot
from ..core.proc import Runner


def _verify(checks, output):
    msgs, ok = [], True
    for chk in checks or []:
        (kind, arg), = chk.items()
        if kind == "files_exist":
            good = all(Path(p).exists() for p in (arg if isinstance(arg, list) else [arg]))
        elif kind == "files_absent":
            good = not any(Path(p).exists() for p in (arg if isinstance(arg, list) else [arg]))
        elif kind == "output_contains":
            good = re.search(arg, output or "", re.M) is not None
        else:
            good = False
        msgs.append(f"{'ok  ' if good else 'FAIL'} {kind}: {arg}")
        ok &= good
    return ok, msgs


def _take(spec, label, state):
    snap = {"name": label, "files": snapshot.files([p for p in spec["watch"].get("paths", []) if Path(p).exists()])}
    for part in spec["watch"].get("parts", []):
        snap[part] = snapshot.PARTS[part]()
    state[label] = snap
    return snap


def run(spec_path, approve=False, state_dir=None):
    if not approve:
        raise PermissionError("a lifecycle run installs and removes software: it needs --approve from the owner")
    spec = settings.load(spec_path)
    runner = Runner(state_dir or settings.ROOT / "state" / "lifecycle" / spec["name"])
    snaps, steps, ok = {}, [], True
    _take(spec, "before", snaps)
    for step in ("install", "upgrade", "uninstall"):
        if step not in spec:
            continue
        r = runner.run(spec[step]["cmd"], name=f"{spec['name']}-{step}", timeout=spec[step].get("timeout", 1800))
        out = r.stdout + r.stderr
        vok, vmsgs = _verify(spec[step].get("verify"), out)
        steps.append({"step": step, "exit": r.returncode, "ok": r.ok and vok, "verify": vmsgs, "log": r.log})
        ok &= r.ok and vok
        _take(spec, f"after-{step}", snaps)
        if not (r.ok and vok) and step != "uninstall":
            break                                   # do not uninstall something that never installed properly
    leftovers = {}
    if "after-uninstall" in snaps:
        d = snapshot.diff(snaps["before"], snaps["after-uninstall"])
        allowed = spec.get("allowed_leftovers") or []
        for part, ch in d.items():
            left = [k for k in ch["added"] if not any(fnmatch.fnmatch(k, pat) or fnmatch.fnmatch(Path(k).name, pat)
                                                     for pat in allowed)]
            changed = list(ch["changed"])
            if left or changed:
                leftovers[part] = {"added": left, "changed": changed}
    clean = ok and not leftovers and "after-uninstall" in snaps
    return {"name": spec["name"], "steps": steps, "leftovers": leftovers, "clean": clean,
            "verdict": "CLEAN: installed, verified, removed, nothing left behind" if clean else
            "NOT CLEAN: see failed steps and leftovers"}


def lines(r):
    out = [f"{r['name']}: {r['verdict']}"]
    for s in r["steps"]:
        out.append(f"  {'ok  ' if s['ok'] else 'FAIL'} {s['step']} (exit {s['exit']})")
        out += [f"       {v}" for v in s["verify"]]
    for part, ch in r["leftovers"].items():
        out.append(f"  LEFTOVERS in {part}:")
        out += [f"       + {k}" for k in ch["added"][:20]] + [f"       ~ {k}" for k in ch["changed"][:20]]
    return out
