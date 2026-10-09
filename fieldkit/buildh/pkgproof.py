"""Prove once, on the package that ships; carry the proof to the install only when the install is that package.

Born 2026-10-09. The owner: "running pointless tests is not going to improve what we already improved, proved and
tweaked ... we could use the time we save for EXTRA NEW tests". On build 29 post-install took 31 minutes (19:19 to
19:50) and ran again, on the installed copy, the probes build-verify had run 20 minutes before on dist/bin (about:
pages, removed pages, the About stamp, Satellite, the tab outline). The install is an unzip of the package: measured
the same evening, 50 of 50 files identical, the only extra the harness's own marker (gorilla-install.json).

How:
  - build-verify unpacks the zip it verified into a throwaway folder and runs the PACKAGE checks there, not on
    dist/bin: dist/bin is the unpackaged objdir and held stale files that the packager shipped (D-157-40, 2026-10-09).
    It records each check's rows, its seconds and the sha256 of every file in the zip in build-result.json.
  - post-install first hashes the installed folder (about 2 s). Every recorded file present and identical, nothing
    extra but the install marker: the package checks' rows are carried, each marked, after one row that says why.
    Anything different: that row FAILS (the install is not the package that was proven) and every check runs on
    the install, as before.
  - Checks that depend on the machine, the network, the profile or a register edited after the build always run
    after the install: profiles, prefs, excised, startup, egress, adblock, leaks, decisions, claims, the installed
    BuildID, and the owner's scripts.
"""
import hashlib
import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

# post-install row groups that read nothing but the browser's own files (and the source tree)
PACKAGE_GROUPS = ("visual", "ui", "about", "stamp", "satellite")
MARKER = "gorilla-install.json"          # written by install after the files (install.py MARKER)
CARRIED = "carried: "


def _sha(fobj):
    h = hashlib.sha256()
    for b in iter(lambda: fobj.read(1 << 20), b""):
        h.update(b)
    return h.hexdigest()


def _rel(name):
    """A zip member name without the package's top folder (firefox/...)."""
    return name.split("/", 1)[1] if "/" in name else name


def zip_files(zip_path):
    """-> {relative path: sha256} of every file in the zip."""
    out = {}
    with zipfile.ZipFile(zip_path) as zf:
        for i in zf.infolist():
            if not i.is_dir():
                with zf.open(i) as f:
                    out[_rel(i.filename)] = _sha(f)
    return out


def folder_files(folder):
    """-> {relative path: sha256} of every file under `folder`."""
    out = {}
    for p in Path(folder).rglob("*"):
        if p.is_file():
            with open(p, "rb") as f:
                out[p.relative_to(folder).as_posix()] = _sha(f)
    return out


def unpack(zip_path):
    """The zip into a new throwaway folder -> (folder to discard, the browser folder inside it)."""
    tmp = Path(tempfile.mkdtemp(prefix="gpkg_"))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp)
    tops = [p for p in tmp.iterdir() if p.is_dir()]
    app = tops[0] if len(tops) == 1 and (tops[0] / "application.ini").is_file() else tmp
    return tmp, app


def compare(recorded, have, extra_ok=(MARKER,)):
    """Recorded package files vs the installed ones -> (same, evidence)."""
    differ = sorted(k for k in recorded if k in have and have[k] != recorded[k])
    missing = sorted(k for k in recorded if k not in have)
    extra = sorted(k for k in have if k not in recorded and k not in extra_ok)
    same = bool(recorded) and not (differ or missing or extra)
    if same:
        return True, f"{len(recorded)} file(s), every one byte-identical (sha256) to the package build-verify proved"
    parts = [f"{len(differ)} differ ({', '.join(differ[:4])})" if differ else "",
             f"{len(missing)} missing ({', '.join(missing[:4])})" if missing else "",
             f"{len(extra)} extra ({', '.join(extra[:4])})" if extra else "",
             "nothing recorded" if not recorded else ""]
    return False, "; ".join(p for p in parts if p)


def run_group(name, t, app, say=print):
    """One package group's rows, run on the browser folder `app` (the same functions post-install uses)."""
    from . import install as inst
    from . import buildstamp
    if name == "visual":
        return inst._visual_row(t, app, say)
    if name == "ui":
        return inst._ui_rows(t, app, say)
    if name == "about":
        return inst._about_rows(t, app, say)
    if name == "stamp":                       # the About window; the installed BuildID is checked after every install
        return buildstamp.about_rows(app, say=say)
    if name == "satellite":
        return inst._satellite_rows(app, say)
    raise ValueError(name)


def prove(t, zip_path, say=print, groups=PACKAGE_GROUPS, run=run_group):
    """build-verify: every package group on the unpacked zip -> {"rows", "proven", "seconds", "files"}."""
    files = zip_files(zip_path)
    tmp, app = unpack(zip_path)
    proven, seconds, rows = {}, {}, []
    try:
        for g in groups:
            t0 = time.time()
            got = run(g, t, app, say)
            seconds[g] = round(time.time() - t0)
            proven[g] = got
            rows.extend(got)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"rows": rows, "proven": proven, "seconds": seconds, "files": files}


def carried(state_dir, target, want=PACKAGE_GROUPS):
    """post-install: -> (row or None, {group: rows to carry} or {}). The row is None when nothing was recorded (an
    older build-result): then every check runs, as before."""
    res = Path(state_dir) / "build-result.json"
    if not res.is_file():
        return None, {}
    rec = json.loads(res.read_text(encoding="utf-8"))
    pkg = rec.get("package") or {}
    if not pkg.get("files"):
        return None, {}
    same, evidence = compare(pkg["files"], folder_files(target))
    row = {"check": "carried: the installed files are the package build-verify proved", "ok": same,
           "evidence": evidence + (f"; carried from build-verify ({rec.get('verified_at')}): {', '.join(g for g in want if g in pkg.get('proven', {}))}"
                                   if same else "; every check runs on the install")}
    if not same:
        return row, {}
    out = {}
    for g in want:
        if g in (pkg.get("proven") or {}):
            out[g] = [{**r, "evidence": CARRIED + r["evidence"]} for r in pkg["proven"][g]]
    return row, out
