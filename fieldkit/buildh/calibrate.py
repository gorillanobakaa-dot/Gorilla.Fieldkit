"""Calibration: a check is trusted only when it passes the known-good browser AND fails a copy broken in exactly the
way it guards against.

    fieldkit build-harness calibrate <task> [only=<fault id>,...]

Born 2026-10-09, item 2 of the proactive programme. Two kinds of bad check cost the project that week:
  - blind: the tab-outline probe's rule scan read no rule at all and would have passed anything;
  - false alarm: in two days the compiler never failed once, yet ten runs stopped - the sensor proof on a cool
    laptop (six times), the gate's rename check, the pre-build page check, the stamp check on a reproducible date.
Known-good is the package build-verify proved (its rows, recorded in build-result.json, must all have passed).
Known-bad is that package unpacked into a throwaway folder with ONE fault put in (pkgproof.unpack + probe
apply_change: a text substitution in the packed files), or the same check pointed at something it must report.
Each fault names the check row that must turn red. A fault the check does not catch is a blind check: the row
"calibration" fails until the check is fixed.

The result goes to state/build-harness/<task>/calibration.json with the sha256 of every file the calibrated checks
are made of (their module and probe). release-check fails when a check changed after its calibration: a new or
changed check must prove itself on both sides before it may decide a release.

Nothing leaves the machine: the copies run headless with throwaway profiles, like every probe.
"""
import datetime
import hashlib
import json
import shutil
import time
from pathlib import Path

from . import task

HERE = Path(__file__).parent


def _quiet(m):
    pass


def _tab(t, app, bundle):
    from . import tabborders
    return tabborders.rows(app, say=_quiet)


def _stamp(t, app, bundle):
    from . import buildstamp
    return buildstamp.about_rows(app, say=_quiet)


def _satellite(t, app, bundle):
    from . import satellite
    return satellite.rows(app, say=_quiet)


def _removed_page(t, app, bundle):
    """The register says a page that exists (about:license) was removed: the check must report it open."""
    import yaml
    from . import aboutpages, removedpages
    reg = yaml.safe_load(Path(aboutpages.register_for(t)).read_text(encoding="utf-8"))
    for e in reg.get("pages") or []:
        if e.get("name") == "license":
            e["verdict"] = "remove"
    tmp = bundle / "ABOUT-PAGES.calibration.yaml"
    tmp.write_text(yaml.safe_dump(reg), encoding="utf-8")
    return removedpages.rows(app, tmp, say=_quiet, files=[])


def _removed_file(t, app, bundle):
    """A file that IS packaged is listed as removed: the check must report that it opens."""
    from . import aboutpages, removedpages
    return removedpages.rows(app, aboutpages.register_for(t), say=_quiet,
                             files=removedpages.CHROME_FILES + ["chrome://global/content/license.html"])


def _timebomb(t, app, bundle):
    """The tree's dates judged ten days before the first one: the check must refuse."""
    from . import timebombs
    found = timebombs.scan(t["workdir"])
    first = min(f["expires"] for f in found) if found else datetime.date.today()
    return timebombs.judge(found, first - datetime.timedelta(days=10))


# id -> what is broken, the change (probe.apply_change keywords) or None, the runner, the row that must fail,
# and the files the check is made of (their hashes say whether the check changed since it was calibrated)
FAULTS = [
    {"id": "tab-outline-gone", "what": "every cyan 1 px outline in the packed CSS replaced by none",
     "change": {"subs": [("outline: 1px solid #00FFFF !important;", "outline: none !important;")]},
     "run": _tab, "must_fail": "tab outline: an inactive tab is outlined in cyan",
     "made_of": ["tabborders.py", "probes/tab-borders.js"]},
    {"id": "buildid-line-wrong", "what": "the About window's 'Build ID' line reworded",
     "change": {"subs": [("aboutDialog-buildid-gorilla = Build ID { $buildid }",
                          "aboutDialog-buildid-gorilla = Build { $buildid }")]},
     "run": _stamp, "must_fail": "stamp: Help > About shows the build number and the Build ID",
     "made_of": ["buildstamp.py", "probes/build-stamp.js"]},
    {"id": "call-sites-ignored", "what": "Satellite mode's call-site test removed from the identity decision",
     "change": {"subs": [("this._callSite(principal, bc.embedderElement);", "false;")]},
     "run": _satellite, "must_fail": "satellite: call sites keep their desktop version",
     "made_of": ["satellite.py", "probes/satellite-calls.js"]},
    {"id": "removed-page-detector", "what": "a page that exists (about:license) listed as removed",
     "change": None, "run": _removed_page, "must_fail": "removed pages: every removed about: page lands on",
     "made_of": ["removedpages.py", "probes/removed-pages.js"]},
    {"id": "removed-file-detector", "what": "a packaged file (chrome://global/content/license.html) listed as removed",
     "change": None, "run": _removed_file, "must_fail": "removed pages: their own chrome:// files do not open",
     "made_of": ["removedpages.py", "probes/removed-pages.js"]},
    {"id": "timebomb-close", "what": "the tree's expiry dates judged ten days before the first one",
     "change": None, "run": _timebomb, "must_fail": "time bombs:",
     "made_of": ["timebombs.py"]},
]


def made_of_hashes(faults=FAULTS):
    """{relative file: sha256} of every file the calibrated checks are made of."""
    out = {}
    for f in faults:
        for rel in f["made_of"]:
            p = HERE / rel
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    return out


def judge_fault(fault, rows):
    """-> (caught, evidence): the named row must be there and failing."""
    named = [r for r in rows if r["check"].startswith(fault["must_fail"])]
    if not named:
        return False, f"the row '{fault['must_fail']}' never appeared ({len(rows)} rows): the check did not run as expected"
    bad = [r for r in named if not r["ok"]]
    if bad:
        return True, f"caught: {bad[0]['evidence'][:150]}"
    return False, f"BLIND: still passes on the broken copy: {named[0]['evidence'][:150]}"


def known_good(task_id):
    """The package build-verify proved: every recorded row passed -> (ok, evidence, zip path)."""
    res = task.STATE / task_id / "build-result.json"
    if not res.is_file():
        return False, "no build-result.json: run build-verify first", None
    rec = json.loads(res.read_text(encoding="utf-8"))
    proven = (rec.get("package") or {}).get("proven") or {}
    rows = [r for g in proven.values() for r in g]
    z = (rec.get("artifacts") or {}).get("zip", {}).get("file")
    if not rows or not z or not Path(z).is_file():
        return False, "build-verify recorded no package proof (or its zip is gone): run build-verify", None
    bad = [r["check"] for r in rows if not r["ok"]]
    return not bad, (f"{len(rows)} rows of build {rec.get('build_id')} passed on the package" if not bad else
                     f"the known-good is not good: {bad[:3]}"), z


def run(task_id, only=None, say=print, faults=FAULTS):
    """-> {"rows", "results"}; writes calibration.json."""
    from . import pkgproof, probe
    t = task.load(task_id)
    rows = []
    ok, evidence, z = known_good(task_id)
    rows.append({"check": "calibration: the known-good browser passes every check", "ok": ok, "evidence": evidence})
    results = []
    if ok:
        for f in faults:
            if only and f["id"] not in only:
                continue
            t0 = time.time()
            tmp, app = pkgproof.unpack(z)
            try:
                if f["change"]:
                    probe.apply_change(app, say=_quiet, **f["change"])
                got = f["run"](t, app, tmp)
                caught, ev = judge_fault(f, got)
            except Exception as e:  # noqa: BLE001 - a fault that cannot be put in is reported, never passed
                caught, ev = False, f"the fault could not be put in or run: {type(e).__name__}: {e}"[:200]
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            secs = round(time.time() - t0)
            results.append({"id": f["id"], "caught": caught, "evidence": ev, "seconds": secs})
            say(f"  [{'ok' if caught else 'FAIL'}] {f['id']} ({secs} s): {ev}")
            rows.append({"check": f"calibration: {f['id']} ({f['what']}) is caught", "ok": caught, "evidence": ev})
    out = task.STATE / task_id / "calibration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    merged = {r["id"]: r for r in prev.get("results", [])} if only else {}
    merged.update({r["id"]: r for r in results})
    out.write_text(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "known_good": evidence,
                               "made_of": made_of_hashes(faults), "results": list(merged.values())}, indent=1),
                   encoding="utf-8")
    return {"rows": rows, "results": results}


def status_row(task_id, faults=FAULTS):
    """release-check: every fault caught, and no calibrated check changed since -> row."""
    p = task.STATE / task_id / "calibration.json"
    if not p.is_file():
        return {"check": "calibration", "ok": False, "evidence": "never calibrated: build-harness calibrate"}
    rec = json.loads(p.read_text(encoding="utf-8"))
    got = {r["id"]: r for r in rec.get("results", [])}
    missing = [f["id"] for f in faults if f["id"] not in got]
    blind = [i for i, r in got.items() if not r["caught"]]
    now = made_of_hashes(faults)
    changed = sorted(k for k, v in now.items() if rec.get("made_of", {}).get(k) != v)
    ok = not (missing or blind or changed)
    ev = (f"{len(got)} fault(s) caught ({rec.get('at')})" if ok else
          "; ".join(x for x in (f"not calibrated: {missing}" if missing else "", f"blind: {blind}" if blind else "",
                                f"changed since calibration: {changed}" if changed else "") if x))
    return {"check": "calibration", "ok": ok, "evidence": ev}
