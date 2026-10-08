"""The build stamp: Help > About says when the build was made, and every stage of the harness holds it to the same
number (D-157-38).

Born 2026-10-08. The owner: "from now on, overtime when you go to Help and report--> About Gorilla Unleashed the part
where it shows the version number also carries a built YY:MM:DD:HH:SS timestamp so we the users can keep track of
what build is and when it was actually done ... make sure that as well become part of the harness and it is
integrated and synced with the rest of it".

One number, everywhere: the build's own BuildID (application.ini, YYYYMMDDHHMMSS, the moment the build was made).
  - the browser: aboutDialog.js shows "157.0 (64-bit) built YY:MM:DD:HH:MM:SS" from Services.appinfo.appBuildID;
  - before the build: decision D-157-38 checks the source (record / check-change, build gate);
  - after the build (build-verify): the built BuildID is newer than the gate, the About window of the built browser
    shows it, and build-result.json records it;
  - after the install (post-install "stamp"): the installed BuildID is the one build-verify recorded (the install
    is the build the harness made and checked), and the About window shows it.
The About line is read by probe build-stamp in a throwaway copy, headless; the owner's browser is never touched.
"""
import configparser
import datetime
from pathlib import Path


def build_id(app_dir):
    """The BuildID of an installed or built browser folder (application.ini) -> str or None."""
    ini = Path(app_dir) / "application.ini"
    if not ini.is_file():
        return None
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(ini, encoding="utf-8")
    return cp.get("App", "BuildID", fallback=None)


def stamp(bid):
    """BuildID -> the text Help > About ends with: "built YY:MM:DD:HH:MM:SS" (None when it is not 14 digits)."""
    if not bid or len(bid) != 14 or not bid.isdigit():
        return None
    return "built " + ":".join((bid[2:4], bid[4:6], bid[6:8], bid[8:10], bid[10:12], bid[12:14]))


def when(bid):
    """BuildID -> local datetime, or None."""
    try:
        return datetime.datetime.strptime(bid, "%Y%m%d%H%M%S")
    except (TypeError, ValueError):
        return None


def parse(lines):
    """Probe build-stamp lines -> {"buildid", "expected", "shown", "ok", "why"}."""
    out = {"buildid": None, "expected": None, "shown": None, "ok": False, "why": "the probe reported nothing"}
    for l in lines:
        f = l.strip().split("|")
        if len(f) < 3 or f[0] != "STAMP":
            continue
        if f[1] == "verdict":
            out["ok"], out["why"] = f[2] == "ok", (f[3] if len(f) > 3 else "")
        elif f[1] in ("buildid", "expected", "shown"):
            out[f[1]] = f[2]
    return out


def about_rows(app_dir, recorded=None, say=print, timeout=120):
    """Rows for a built or installed browser folder: Help > About shows the stamp of THIS folder's BuildID; with
    `recorded` (the BuildID build-verify recorded), the folder is that build."""
    from . import probe
    bid = build_id(app_dir)
    rows = []
    if recorded is not None:
        rows.append({"check": "stamp: this is the build the harness made and checked", "ok": bool(bid) and bid == recorded,
                     "evidence": f"BuildID {bid or '(none)'}; build-verify recorded {recorded or '(nothing)'}"})
    r = probe.run(app_dir, "build-stamp", wait=10, timeout=timeout, say=say)
    got = parse(r["lines"])
    ok = got["ok"] and got["buildid"] == bid and got["shown"] is not None and got["shown"].endswith(stamp(bid) or "\0")
    rows.append({"check": "stamp: Help > About shows when this build was made (D-157-38)", "ok": ok,
                 "evidence": f"shown '{got['shown']}', BuildID {bid}" + ("" if ok else f"; {got['why']}")})
    return rows


def built_rows(objdir, gate_at, say=print):
    """build-verify rows for dist/bin: a BuildID newer than the gate, and the About line showing it -> (rows, BuildID)."""
    app = Path(objdir) / "dist" / "bin"
    bid = build_id(app)
    t = when(bid)
    gate = datetime.datetime.fromtimestamp(gate_at) if gate_at else None
    # BuildID is set when the build starts its configure step, after the gate: allow two minutes of clock slack
    fresh = bool(t and gate and t >= gate - datetime.timedelta(minutes=2))
    rows = [{"check": "stamp: the built BuildID is from THIS build", "ok": fresh,
             "evidence": f"BuildID {bid or '(none)'} ({t or 'unreadable'}); gate at {gate:%Y-%m-%d %H:%M:%S}" if gate else
                         f"BuildID {bid or '(none)'}; no gate time"}]
    rows += about_rows(app, say=say)
    return rows, bid
