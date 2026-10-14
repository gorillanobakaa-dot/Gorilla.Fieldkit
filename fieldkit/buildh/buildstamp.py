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
    out = {"buildid": None, "expected": None, "shown": None, "number": None, "idline": None, "ok": False,
           "why": "the probe reported nothing"}
    for l in lines:
        f = l.strip().split("|")
        if len(f) < 3 or f[0] != "STAMP":
            continue
        if f[1] == "verdict":
            out["ok"], out["why"] = f[2] == "ok", (f[3] if len(f) > 3 else "")
        elif f[1] in ("buildid", "expected", "shown", "number", "idline"):
            out[f[1]] = f[2]
    return out


def identity_row(app_dir, recorded):
    """The folder is the build build-verify recorded (application.ini; no browser started)."""
    bid = build_id(app_dir)
    return {"check": "stamp: this is the build the harness made and checked", "ok": bool(bid) and bid == recorded,
            "evidence": f"BuildID {bid or '(none)'}; build-verify recorded {recorded or '(nothing)'}"}


def about_rows(app_dir, recorded=None, say=print, timeout=120):
    """Rows for a built or installed browser folder: Help > About shows the stamp of THIS folder's BuildID; with
    `recorded` (the BuildID build-verify recorded), the folder is that build."""
    from . import probe
    bid = build_id(app_dir)
    rows = []
    if recorded is not None:
        rows.append(identity_row(app_dir, recorded))
    r = probe.run(app_dir, "build-stamp", wait=10, timeout=timeout, say=say)
    got = parse(r["lines"])
    ok = got["ok"] and got["buildid"] == bid and got["shown"] is not None and got["shown"].endswith(stamp(bid) or "\0")
    rows.append({"check": "stamp: Help > About shows when this build was made (D-157-38)", "ok": ok,
                 "evidence": f"shown '{got['shown']}', BuildID {bid}" + ("" if ok else f"; {got['why']}")})
    # 2026-10-09: the build number, and the BuildID itself under the line, so the owner can match an install to a report
    number = int(got["number"]) if (got["number"] or "").isdigit() else 0
    ok2 = ok and number > 0 and got["idline"] == f"Build ID {bid}"
    rows.append({"check": "stamp: Help > About shows the build number and the Build ID (D-157-38)", "ok": ok2,
                 "evidence": f"build {number or '(none)'}; line under it '{got['idline']}'"
                             + ("" if ok2 else f"; want 'Build ID {bid}' and a build number")})
    return rows


def remember(task_id, build_id, head, tree):
    """build-verify: this BuildID was made from this head and tree (state/<task>/builds.jsonl, one line per build)."""
    import json
    import time
    from . import task
    if not build_id:
        return
    p = task.STATE / task_id / "builds.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"build_id": build_id, "head": head, "tree": tree, "verified_at": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")


def head_of(task_id, bid, journal=None):
    """The source commit a BuildID was built from -> head or None: builds.jsonl first, else the journal's last
    build-start up to 15 minutes before the BuildID (the BuildID is set seconds after build-start)."""
    import json
    from . import task
    p = task.STATE / task_id / "builds.jsonl"
    if p.is_file():
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("build_id") == bid and d.get("head"):
                return d["head"]
    t = when(bid)
    j = Path(journal) if journal else task.STATE / task_id / "journal.jsonl"
    if not t or not j.is_file():
        return None
    best = None
    for line in j.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("event") != "build-start" or not d.get("head"):
            continue
        try:
            at = datetime.datetime.strptime(d["t"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError):
            continue
        if t - datetime.timedelta(minutes=15) <= at <= t:
            best = d["head"]
    return best


def pinned_build_date(owner_root):
    """The owner's reproducible-build pin (config/versions.lock.json build.moz_build_date) -> str or None. The owner's
    build reuses it while the source fingerprint is unchanged, so an unchanged source keeps its BuildID."""
    import json
    try:
        d = json.loads((Path(owner_root) / "config" / "versions.lock.json").read_text(encoding="utf-8"))
        v = (d.get("build") or {}).get("moz_build_date")
        return str(v) if v else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def built_rows(objdir, gate_at, say=print, pinned=None, about=True):
    """build-verify rows for dist/bin: a BuildID from THIS build, and the About line showing it -> (rows, BuildID).
    From this build = newer than the gate, or (2026-10-09) the reproducible pin the owner's build reused for an
    unchanged source (`pinned`, see pinned_build_date) with the package made after the gate: a build attempt stopped
    before compiling had pinned 10:34, the compile of the same source ran at 12:51, and the row called it stale."""
    app = Path(objdir) / "dist" / "bin"
    bid = build_id(app)
    t = when(bid)
    gate = datetime.datetime.fromtimestamp(gate_at) if gate_at else None
    # BuildID is set when the build starts its configure step, after the gate: allow two minutes of clock slack
    fresh = bool(t and gate and t >= gate - datetime.timedelta(minutes=2))
    why = f"BuildID {bid or '(none)'} ({t or 'unreadable'}); gate at {gate:%Y-%m-%d %H:%M:%S}" if gate else \
        f"BuildID {bid or '(none)'}; no gate time"
    if not fresh and gate and bid and pinned and bid == str(pinned):
        made = max((f.stat().st_mtime for f in (Path(objdir) / "dist").glob("*.zip")), default=0)
        if made >= gate_at:
            fresh = True
            why += (f"; the reproducible pin for this unchanged source (config/versions.lock.json), packaged "
                    f"{datetime.datetime.fromtimestamp(made):%H:%M:%S}, after the gate")
        else:
            why += "; it is the pin, but no package newer than the gate: stale"
    rows = [{"check": "stamp: the built BuildID is from THIS build", "ok": fresh, "evidence": why}]
    if about:                 # build-verify reads the About window on the package instead (pkgproof.py, 2026-10-09)
        rows += about_rows(app, say=say)
    return rows, bid


# -- the source change, as code the harness applies (D-157-38 part 2) ------------------------------------------------
#
# 2026-10-15, Linux 158: part 2 (2026-10-09, "build N" in the version line and "Build ID <14 digits>" under it) was
# made by hand in the Windows tree and never exported to the shared patch set; only the register knew its shape. So
# the change itself lives here, anchored on text, never on line numbers, so any later Firefox gets it the same way:
#   apply_source(tree, number)     edit the four files; idempotent; refuses (and writes nothing) when an anchor is gone
#   source_problems(tree)          what of it is missing, in words (the technique scan and the register check this too)
# Files and what goes where:
#   browser/branding/gorilla/pref/firefox-branding.js   pref("gorilla.build.number", N): raised once per candidate
#   browser/locales/en-US/browser/aboutDialog.ftl       the four -gorilla version lines say "build { $build }, built ..."
#                                                       and aboutDialog-buildid-gorilla = Build ID { $buildid }
#   browser/base/content/aboutDialog.xhtml              <label id="gorilla-buildid" ... hidden="true"/> under #version
#   browser/base/content/aboutDialog.js                 passes $build, fills and shows #gorilla-buildid

BRANDING = "browser/branding/gorilla/pref/firefox-branding.js"
FTL = "browser/locales/en-US/browser/aboutDialog.ftl"
XHTML = "browser/base/content/aboutDialog.xhtml"
JS = "browser/base/content/aboutDialog.js"
ID_LABEL = '<label id="gorilla-buildid" class="text-blurb" hidden="true"/>'
ID_MESSAGE = "aboutDialog-buildid-gorilla = Build ID { $buildid }"
PART1_JS = "GORILLA D-157-38 (2026-10-08): every version line also says when this build was made"
NUMBER_JS = 'versionAttributes.build = Services.prefs.getIntPref("gorilla.build.number", 0);'
SHOW_JS_MARK = "GORILLA D-157-38 part 2"


def _edits(number):
    """-> [(file, anchor, replacement, already_done_marker)]; each anchor must occur exactly once."""
    show = (
        "  // " + SHOW_JS_MARK + " (2026-10-09): under the version line, the build's own BuildID in full, the string\n"
        "  // the harness reports, so the owner can tell which build is installed.\n"
        '  let gorillaBuildId = document.getElementById("gorilla-buildid");\n'
        "  if (gorillaBuildId && /^\\d{14}$/.test(buildStamp)) {\n"
        '    document.l10n.setAttributes(gorillaBuildId, "aboutDialog-buildid-gorilla", {\n'
        "      buildid: buildStamp,\n"
        "    });\n"
        "    gorillaBuildId.hidden = false;\n"
        "  }\n\n")
    out = [
        (BRANDING, None, f"\n// GORILLA D-157-38 part 2 (2026-10-09): the build number Help > About shows (\"build N\"); "
                         f"raised once per release candidate.\npref(\"gorilla.build.number\", {int(number)});\n",
         'pref("gorilla.build.number", '),
        (JS, '    versionIdKey += "-gorilla";\n',
         '    versionIdKey += "-gorilla";\n    ' + NUMBER_JS + "\n", NUMBER_JS),
        (JS, "  // Show a release notes link if we have a URL.\n", show + "  // Show a release notes link if we have a URL.\n",
         SHOW_JS_MARK),
        (XHTML, '<label id="version"/>\n', '<label id="version"/>\n          ' + ID_LABEL + "\n", 'id="gorilla-buildid"'),
        (FTL, "aboutDialog-version-gorilla = { $version } ({ $bits }-bit) built { $built }\n",
         "aboutDialog-version-gorilla = { $version } ({ $bits }-bit) build { $build }, built { $built }\n" + ID_MESSAGE + "\n",
         ID_MESSAGE),
        (FTL, "aboutDialog-version-nightly-gorilla = { $version } ({ $isodate }) ({ $bits }-bit) built { $built }\n",
         "aboutDialog-version-nightly-gorilla = { $version } ({ $isodate }) ({ $bits }-bit) build { $build }, built { $built }\n",
         "({ $bits }-bit) build { $build }, built { $built }\naboutdialog"),
        (FTL, "aboutdialog-version-arch-gorilla = { $version } ({ $arch }) built { $built }\n",
         "aboutdialog-version-arch-gorilla = { $version } ({ $arch }) build { $build }, built { $built }\n",
         "aboutdialog-version-arch-gorilla = { $version } ({ $arch }) build { $build }"),
        (FTL, "aboutdialog-version-arch-nightly-gorilla = { $version } ({ $isodate }) ({ $arch }) built { $built }\n",
         "aboutdialog-version-arch-nightly-gorilla = { $version } ({ $isodate }) ({ $arch }) build { $build }, built { $built }\n",
         "aboutdialog-version-arch-nightly-gorilla = { $version } ({ $isodate }) ({ $arch }) build { $build }"),
    ]
    return out


def source_problems(tree):
    """What of D-157-38 (part 1 and 2) the tree lacks, in words. [] = all there."""
    t = Path(tree)
    want = [(JS, PART1_JS), (JS, NUMBER_JS), (JS, SHOW_JS_MARK), (XHTML, ID_LABEL), (FTL, ID_MESSAGE),
            (FTL, "aboutDialog-version-gorilla = { $version } ({ $bits }-bit) build { $build }, built { $built }"),
            (BRANDING, 'pref("gorilla.build.number", ')]
    out = []
    for rel, text in want:
        p = t / rel
        if not p.is_file() or text not in p.read_text(encoding="utf-8", errors="replace"):
            out.append(f"{rel} lacks: {text[:80]}")
    return out


def apply_source(tree, number):
    """Put D-157-38 part 2 into a Firefox tree that has part 1. -> {"changed": [files], "already": n}.
    Refuses with ValueError, writing nothing, when part 1 is missing or an anchor is not there exactly once."""
    t = Path(tree)
    if int(number) < 1:
        raise ValueError("the build number starts at 1")
    js = t / JS
    if not js.is_file() or PART1_JS not in js.read_text(encoding="utf-8"):
        raise ValueError(f"part 1 (the 'built' stamp) is not in {JS}: port D-157-38 part 1 first")
    texts = {}
    plan = []
    for rel, anchor, repl, done in _edits(number):
        text = texts.setdefault(rel, (t / rel).read_text(encoding="utf-8"))
        if done in text:
            continue                                        # already applied: idempotent
        if anchor is None:
            plan.append((rel, None, repl))
            continue
        n = text.count(anchor)
        if n != 1:
            raise ValueError(f"{rel}: anchor found {n} times, expected once: {anchor.strip()[:70]}")
        plan.append((rel, anchor, repl))
    for rel, anchor, repl in plan:
        texts[rel] = texts[rel].rstrip("\n") + "\n" + repl if anchor is None else texts[rel].replace(anchor, repl, 1)
    for rel in {r for r, _, _ in plan}:
        (t / rel).write_text(texts[rel], encoding="utf-8")
    return {"changed": sorted({r for r, _, _ in plan}), "already": len(_edits(number)) - len(plan)}
