"""One command for "is this build releasable?": every release check, in order, one verdict.

    fieldkit build-harness release-check <task>

The 157 port (2026-10-01 to 10-04) ran these checks by hand, one at a time, often in the wrong order, and each
forgotten one cost a build. This runs them all against the current tree and installed build and prints a numbered
list: what passed, what failed and the one command that fixes or explains each failure. It changes nothing.

Checks (none needs administrator rights; the leak gate and the maintainer's approvals are listed as the steps
that remain, with their exact commands):
  1. techniques     every Gorilla technique holds in the tree        (build-harness techniques)
  2. decisions      the decision register, strict                     (build-harness decisions --strict)
  3. replay         public patch set + pristine = the compiled tree   (build-harness replay)
  4. claims         0 contradicted claims, 0 failing patches          (build-harness claims)
  5. post-install   the proof rows of the installed build             (build-harness post-install)
  6. leak gate      the latest release run is of THIS build and PASS  (state/leakgate_result.json)
  7. release docs   every number and web address of the release's documents is in their sources, privacy, both
                    tracks complete (docs release --manifest <owner>/<patch set>/../release/<version>/release-docs.yaml)
"""
import json
import subprocess
import sys
from pathlib import Path

from . import task


def _run(args, timeout=7200):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "build-harness", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


def _tail(out, n=3):
    return " | ".join(l.strip() for l in out.strip().splitlines()[-n:])[:300]


def release_docs_manifest(t):
    """<owner>/<patch set root>/../release/<version>/release-docs.yaml (gorilla-patchset/release/157.0/...), or None."""
    from . import buildrun
    try:
        owner = Path(buildrun._owner_root(t))
        pol = json.loads((owner / "config" / "patch_policy.json").read_text(encoding="utf-8"))
        version = t["meta"]["upstream"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return owner / Path(pol["patchset_root"]).parent / "release" / version / "release-docs.yaml"


def run(task_id, say=print, skip_post_install=False):
    rows = []

    def row(name, ok, evidence, fix):
        rows.append({"check": name, "ok": ok, "evidence": evidence, "fix": fix})
        say(f"  {'OK  ' if ok else 'FAIL'} {name}: {evidence}")

    rc, out = _run(["techniques", task_id])
    row("techniques", rc == 0, _tail(out, 4), f"fieldkit build-harness techniques {task_id}  (apply each UNGUARDED place)")
    rc, out = _run(["decisions", task_id, "--strict"])
    row("decisions (strict)", rc == 0 and "DECISIONS OK" in out, _tail(out, 2), "the maintainer decides; a pending entry needs its check")
    rc, out = _run(["replay", task_id])
    row("replay", rc == 0 and "REPLAY OK" in out, _tail(out, 3), f"fieldkit build-harness export-hand {task_id}, then replay again")
    rc, out = _run(["claims", task_id, "--json"])
    try:
        d = json.loads(out[out.index("{"):])
        t = d["totals"]
        ok = t["CONTRADICTED"] == 0 and t["patches_failing"] == 0
        ev = (f"{t['claims']} claims: {t['CONTRADICTED']} contradicted, {t['STALE']} stale, {t['UNPROVEN']} unproven; "
              f"{t['patches']} patches, {t['patches_failing']} failing")
    except (ValueError, KeyError):
        ok, ev = False, _tail(out)
    row("claims", ok, ev, "claims/AUDIT-<version>.md lists each one with its evidence")
    if not skip_post_install:
        rc, out = _run(["post-install", task_id])
        bad = [l.strip() for l in out.splitlines() if l.strip().startswith("[FAIL]")]
        row("post-install", not bad, f"{len(bad)} row(s) failing" + (f": {bad[0][:160]}" if bad else ""),
            f"fieldkit build-harness post-install {task_id}")
    t = task.load(task_id)
    from . import buildrun
    res = Path(buildrun._owner_root(t)) / "state" / "leakgate_result.json"
    try:
        lg = json.loads(res.read_text(encoding="utf-8"))
        from . import install as inst
        ini = Path(inst.find_install() or "") / "application.ini"
        build = next((l.split("=", 1)[1].strip() for l in ini.read_text(encoding="utf-8").splitlines() if l.startswith("BuildID=")), None) \
            if ini.is_file() else None
        ok = lg.get("release_run") is True and lg.get("FINAL_RESULT") == "PASS" and str(lg.get("BUILD")) == str(build)
        ev = (f"latest {'release' if lg.get('release_run') else 'developer'} run: {lg.get('FINAL_RESULT')} on build "
              f"{lg.get('BUILD')}; installed build {build}")
    except (OSError, ValueError, StopIteration):
        ok, ev = False, "no release leak-gate result recorded"
    row("leak gate (release)", bool(ok), ev,
        "administrator, unattended: powershell -ExecutionPolicy Bypass -File <Fieldkit>\\toolbox\\leak-gate-launcher\\run-leakgate.ps1; "
        "then the maintainer, at a real terminal: build-harness leakgate-propose / leakgate-approve / leakgate-baseline")
    manifest = release_docs_manifest(t)
    if manifest and manifest.is_file():
        from ..gdocs import releasedocs as RD
        try:
            r = RD.check(**RD.load_manifest(manifest))
            bad = [f"{Path(p).name}: {len(d['findings'])}" for p, d in r["documents"].items() if d["findings"]]
            ok, ev = r["ok"], (f"{len(r['documents'])} document(s) against {r['sources']} source(s); "
                               + ("all sourced" if r["ok"] else "; ".join(r["problems"][:2] + bad)[:200]))
        except (OSError, ValueError) as e:
            ok, ev = False, f"{manifest}: {e}"
    else:
        ok, ev = False, f"no {manifest or 'release folder (the task has no owner root or version)'}: the documents are not held against their sources"
    row("release documents", ok, ev, f"fieldkit docs release --manifest {manifest}  (release-docs.yaml: sources, layman, "
                                     "developer, plain; see fieldkit/gdocs/releasedocs.py)")
    say("")
    left = [r for r in rows if not r["ok"]]
    say(f"RELEASE CHECK {'PASS' if not left else 'NOT READY'}: {len(rows) - len(left)} of {len(rows)} checks pass")
    for i, r in enumerate(left, 1):
        say(f"  {i}. {r['check']}: {r['fix']}")
    return rows
