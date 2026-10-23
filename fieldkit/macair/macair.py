#!/usr/bin/env python3
# Version: 1.1.0 · updated 26-10-23-16-39
"""macair.py - build, check, install and verify Gorilla for the MacBook Air (old Intel Macs, macOS 10.13), the same
way every time.

    fieldkit macair status                              the recreate run, the Mac tree, the published series, the Air
    fieldkit macair preflight [TREE] [--json]           the Mac decisions still in the source? exit 3 if one is lost
    fieldkit macair budget [--screen 1366x768 ...]      the 11-inch layout budget, calculated (what preflight checks)
    fieldkit macair export-patches [TREE]               our Mac commits -> patches-mac.tar.xz + checksums (no fonts)
    fieldkit macair recreate [STAGE] [--route a|b]      run gorilla-firefox/macos-legacy/recreate-mac.sh
    fieldkit macair app-check ZIP                       nothing a decision removed is inside the built app (register app_absent)
    fieldkit macair install ZIP [--mac NAME]            app-check, copy, start it once on a throwaway profile, then swap
    fieldkit macair verify [--mac NAME]                 on the Mac: fonts loaded, thin text measured on a test page
    fieldkit macair heat LABEL --url URL [--minutes 3] [--pref NAME=VALUE ...]   CPU, memory, fan, temperature
                                                        (Macs Fan Control off for the run, started again after)
    fieldkit macair heat-report                         every heat run side by side (medians)

Born 2026-10-22, when the owner made the MacBook Air a maintained target ("from now on we're gonna have to maintain
and build for this old macbook ... no need to rebuild tools, procedures and relearn operations"). The public recipe
is recreate-mac.sh in gorilla-firefox/macos-legacy: this tool drives ITS stages and never keeps a second copy of the
recipe. What only the project needs (decision checks, export, install, measurement on the Air) lives here.
"""
import argparse
from shlex import quote as shlex_quote
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
DOCS = HOME / "Documents"
GORILLA_FIREFOX = Path(os.environ.get("GORILLA_FIREFOX", DOCS / "FIREFOX.WORK" / "gorilla-firefox"))
MACOS = GORILLA_FIREFOX / "macos-legacy"
TREE = Path(os.environ.get("MACAIR_TREE", HOME / "momiji-157"))
WORK = Path(os.environ.get("WORK", HOME / "gorilla-air-recreate"))
BASE = "189d295392c582c310687c6012f66b2d56d95570"           # Momiji v157.0-mj
FONTS = ("consola.ttf", "segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "SegUIVar.ttf", "YuGothR.ttc", "YuGothB.ttc")

# The MacBook Air 4,1 screen, measured 2026-10-22 on its own screenshots: 1366x768 points, menu bar 22, Dock top
# at y 733 (35 tall), window title bar 22. Another machine: fieldkit macair budget --screen WxH --dock N.
SCREEN = {"width": 1366, "height": 768, "menubar": 22, "dock": 35, "titlebar": 22, "margin": 20}
ABOUT_FIXED = {"wordmark_band": 100, "text": 40, "gap": 20, "bottom_bar": 50}


def budget(screen=None):
    """The About window budget in px: what may be tall, and the largest logo that fits."""
    s = dict(SCREEN, **(screen or {}))
    content = s["height"] - s["menubar"] - s["dock"] - s["titlebar"] - s["margin"]
    logo_max = content - sum(ABOUT_FIXED.values())
    return {"content_max": content, "logo_max": logo_max, "screen": s, "fixed": ABOUT_FIXED}


def _read(p):
    try:
        return Path(p).read_text(errors="replace")
    except OSError:
        return None


def _px(css, selector, prop):
    """The px value of prop inside the first block whose selector is exactly `selector`, or None."""
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css or "", re.S)
    if not m:
        return None
    v = re.search(rf"(?<![-\w]){re.escape(prop)}\s*:\s*(\d+)px", m.group(1))
    return int(v.group(1)) if v else None


def preflight(tree=TREE, mozconfig=None, screen=None):
    """[(ok, decision, what)] for the Mac decisions, read from the SOURCE (before a two-hour build)."""
    tree = Path(tree)
    mozconfig = Path(mozconfig or MACOS / "mozconfig.air")
    rows = []

    def need(ok, dec, what):
        rows.append((bool(ok), dec, what))

    mc = _read(mozconfig) or ""
    need(mc, "build", f"Air build settings at {mozconfig}")
    need("-march=sandybridge" in mc and not re.search(r"^[^#]*-march=native", mc, re.M), "CPU",
         "built for the Air's Sandy Bridge by name, never -march=native (the build host's CPU would crash the Air)")
    for opt, dec in (("--enable-bundled-fonts", "D-158-12 fonts"), ("--without-onnx-runtime", "10.13 (onnx needs 10.15)"),
                     ("--without-wasm-sandboxed-libraries", "D-158-20 RLBox off"), ("--target=x86_64-apple-darwin", "Intel Mac")):
        need(re.search(rf"^\s*ac_add_options {re.escape(opt)}", mc, re.M), dec, f"mozconfig: {opt}")
    fjs = (_read(tree / "browser/app/profile/firefox.js") or "") + (_read(tree / "modules/libpref/init/all.js") or "")
    for name, val, dec in (("font.name.sans-serif.x-western", "Segoe UI", "D-158-12"),
                           ("font.name.monospace.x-western", "Consolas", "D-158-21"),
                           ("gorilla.ui.font-family", "Segoe UI", "Mac UI font"),
                           ("gorilla.mac.thin-text", "true", "Mac thin text")):
        vals = re.findall(rf'pref\("{re.escape(name)}",\s*"?([^")]*)"?\)', fjs)
        bad = [v for v in vals if v not in ("", val)]
        need(val in vals and not bad, dec, f"{name} = {val}" + (f" (also set to {bad})" if bad else ""))
    need("gorilla.ui.font-family" in (_read(tree / "widget/cocoa/nsLookAndFeel.mm") or ""), "Mac UI font",
         "nsLookAndFeel.mm reads gorilla.ui.font-family (the browser's own pages in Segoe UI)")
    ua = _read(tree / "layout/style/res/ua.css") or ""
    need('-moz-pref("gorilla.mac.thin-text")' in ua and "-moz-osx-font-smoothing: grayscale" in ua, "Mac thin text",
         "ua.css: greyscale smoothing under gorilla.mac.thin-text")
    fm = _read(tree / "browser/fonts/moz.build") or ""
    need('"cocoa"' in fm and all(f in fm for f in FONTS), "D-158-12 fonts", "browser/fonts/moz.build: cocoa + the 7 fonts")
    pm = _read(tree / "browser/installer/package-manifest.in") or ""
    need(re.search(r"XP_MACOSX\)?\n@RESPATH@/fonts/\*", pm), "D-158-12 fonts", "package-manifest: fonts packaged on the Mac")
    b = budget(screen)
    css = _read(tree / "browser/base/content/aboutDialog.css") or ""
    mh, lw, lh = _px(css, "#aboutDialogContainer", "min-height"), _px(css, "#leftBox", "width"), _px(css, "#leftBox", "height")
    need(mh is not None and mh <= b["content_max"], "11-inch layout",
         f"About window min-height {mh} px <= {b['content_max']} (768-22 menu-35 Dock-22 title-20)")
    need(lw and lh and max(lw, lh) <= b["logo_max"], "11-inch layout", f"About logo {lw}x{lh} px <= {b['logo_max']}")
    cs = _read(tree / "toolkit/themes/shared/in-content/common-shared.css") or ""
    need(re.search(r"@media \(max-height: \d+px\) \{\s*#content \{\s*background-size: min\(", cs), "11-inch layout",
         "about:addons logo capped by page height (the -moz-platform query never matched)")
    return rows


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_patches(tree=TREE, out=None, macos=MACOS, run=subprocess.run):
    """Our commits on top of Momiji -> patches-mac.tar.xz (Microsoft fonts excluded) + patches-mac.sha256 +
    PATCHES.sha256 in macos-legacy. -> (tarball, count)."""
    import tarfile
    import tempfile
    out = Path(out or DOCS / "Firefox.158.Macbook.air.Sierra.Legacy" / "release-mac" / "patches-mac.tar.xz")
    with tempfile.TemporaryDirectory() as d:
        r = run(["git", "-C", str(tree), "format-patch", "-q", "--binary", "-o", d, f"{BASE}..HEAD", "--", ".",
                 ":(exclude)*.ttf", ":(exclude)*.ttc"], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"REFUSED: git format-patch failed: {r.stderr.strip()[:300]}")
        pats = sorted(Path(d).glob("*.patch"))
        if not pats:
            raise SystemExit(f"REFUSED: no commits on top of Momiji {BASE[:10]} in {tree}")
        for p in pats:
            if re.search(rb"^diff --git a/\S+\.tt[fc] ", p.read_bytes(), re.M):
                raise SystemExit(f"REFUSED: {p.name} carries a Microsoft font file; the series must not")
        out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out, "w:xz", preset=9) as t:
            for p in pats:
                t.add(p, arcname=p.name)
        Path(macos).mkdir(parents=True, exist_ok=True)
        (Path(macos) / "PATCHES.sha256").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in pats))
    (Path(macos) / "patches-mac.sha256").write_text(f"{sha256(out)}  {out.name}\n")
    return out, len(pats)


REGISTER = GORILLA_FIREFOX / "decisions" / "PRODUCT-DECISIONS.yaml"


def app_absent(register=None):
    """{decision id: [globs]} from the register's app_absent lists: paths inside the built app's omni.ja archives
    that a decision removed. The one list lives in the register, never here."""
    import yaml
    data = yaml.safe_load(Path(register or REGISTER).read_text(encoding="utf-8")) or {}
    return {e["id"]: list(e["app_absent"]) for e in data.get("decisions") or [] if e.get("app_absent")}


def app_check(zip_path, register=None):
    """-> [(decision, path inside the app)] for every removed path still shipped in a built Mac app (.zip)."""
    import fnmatch
    import io
    import zipfile
    rules = app_absent(register)
    z = zipfile.ZipFile(zip_path)
    found = []
    for name in z.namelist():
        if not name.endswith("omni.ja"):
            continue
        inner = zipfile.ZipFile(io.BytesIO(z.read(name)))
        for f in inner.namelist():
            for did, globs in rules.items():
                if any(fnmatch.fnmatch(f, g) for g in globs):
                    found.append((did, f"{name.split('Contents/', 1)[-1]}!{f}"))
                    break
    return found


def _mac():
    from fieldkit.mac import macssh
    return macssh


def mac_target(name):
    m = _mac()
    mac = m.get(m.load(), name)
    address, how = m.find(mac)
    if not address:
        raise SystemExit(f"NOT FOUND: {how}")
    return m, mac, address


def install(zip_path, name="macair"):
    zip_path = Path(zip_path)
    bad = app_check(zip_path)                       # before anything reaches the Mac
    if bad:
        return 3, (f"REFUSED: {len(bad)} removed file(s) are inside the app, first: {bad[0][1]} ({bad[0][0]}); "
                   f"build clean with recreate-mac.sh (fieldkit macair app-check {zip_path})")
    m, mac, address = mac_target(name)
    want = sha256(zip_path)
    remote = f"/Users/{mac['user']}/Downloads/{zip_path.name}"
    r = subprocess.run(m.scp_argv(mac, address, remote, "x")[:-2] + [str(zip_path), f"{mac['user']}@{address}:{remote}"])
    if r.returncode != 0:
        return 1, "copy failed"
    line = (f'set -e; [ "$(shasum -a 256 "{remote}" | cut -d" " -f1)" = "{want}" ] || {{ echo "CHECKSUM MISMATCH"; exit 3; }}; '
            # start the NEW app once, on a throwaway profile, BEFORE it replaces anything: on 2026-10-22 a build that
            # crashed at start (test5) went straight onto the owner's profile and left a Troubleshoot Mode question.
            f'N=/tmp/macair-new-$$; rm -rf "$N"; mkdir -p "$N/prof"; ditto -x -k "{remote}" "$N"; '
            f'( "$N/Gorilla Unleashed.app/Contents/MacOS/firefox" -no-remote -profile "$N/prof" > "$N/start.log" 2>&1 & '
            f'P=$!; sleep 25; if ps -p $P >/dev/null; then kill $P; sleep 2; echo START-OK; else echo START-FAILED; fi ) | tee "$N/verdict"; '
            f'grep -q START-OK "$N/verdict" || {{ echo "REFUSED: the new app did not stay up 25 s on a throwaway profile; '
            f'nothing replaced. Its output: $N/start.log"; head -5 "$N/start.log"; exit 5; }}; '
            f'pgrep -x firefox >/dev/null && {{ echo "Gorilla is running: quit it first"; exit 4; }}; '
            f'T=/Users/{mac["user"]}/Agents.Work.Trash-user/gorilla-app-$(date +%y-%m-%d-%H-%M); mkdir -p "$T"; '
            f'[ -d "/Applications/Gorilla Unleashed.app" ] && mv "/Applications/Gorilla Unleashed.app" "$T/"; '
            f'mv "$N/Gorilla Unleashed.app" /Applications/; ls "/Applications/Gorilla Unleashed.app/Contents/Resources/fonts" | wc -l; '
            f'echo "previous app kept in $T"')
    rc, out = m._run(m.ssh_argv(mac, address, line), timeout=600)
    return rc, out


VERIFY_PAGE = ("<!doctype html><title>macair verify</title><body style='margin:0;background:#2a2a2a'>"
               "<div id=v style='background:#2a2a2a;color:#fbfbfe;padding:30px;font:15px sans-serif'>"
               "This is a list of about Gorilla pages for your convenience. Some of them might be confusing.</div>")


def locate(img, min_share=0.25):
    """The bounding box (x, y, w, h) of the test page in a screenshot, or None. The page is a flat NEUTRAL grey
    (#2a2a2a): on a coloured page white text blends into coloured pixels and the LCD-smoothing count means nothing
    (a blue page read 28.7 %, 2026-10-22). macOS colour management shifts the grey slightly on its way to the screen,
    so it is found, not assumed: the most common colour with red, green and blue within 8 of each other, between 20
    and 90, covering at least a quarter of the screen (the browser's own black chrome is excluded by the range)."""
    from collections import Counter
    im = img.convert("RGB")
    W, H = im.size
    small = im.resize((W // 4, H // 4))
    data = list(small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata())
    for colour, count in Counter(data).most_common(5):
        if count >= min_share * len(data) and max(colour) - min(colour) <= 8 and 20 <= colour[0] <= 90:
            break
    else:
        return None
    near = lambda p: all(abs(a - b) <= 3 for a, b in zip(p, colour))
    xs = [i % small.width for i, p in enumerate(data) if near(p)]
    ys = [i // small.width for i, p in enumerate(data) if near(p)]
    x0, y0 = min(xs) * 4, min(ys) * 4
    return x0, y0, max(xs) * 4 - x0, min(140, max(ys) * 4 - y0)        # the band with the sentence


def verify(name="macair", out_dir=None, thin_max=2.5, colour_max=5.0):
    """On the Mac: the bundled fonts held open by the browser, and the stroke of a known sentence on a test page."""
    from PIL import Image

    from fieldkit.shot import look
    m, mac, address = mac_target(name)
    rows = []
    line = ("P=$(pgrep -x firefox | head -1); [ -n \"$P\" ] || { echo NOT-RUNNING; exit 4; }; "
            "lsof -p $P 2>/dev/null | grep -c 'Resources/fonts/'")
    rc, outp = m._run(m.ssh_argv(mac, address, line), timeout=60)
    n = int(outp.split()[-1]) if rc == 0 and outp.split() and outp.split()[-1].isdigit() else 0
    rows.append((n >= 7, f"bundled fonts open in the browser: {n} (7 or more expected)" if rc == 0 else f"browser: {outp}"))
    page = f"/tmp/macair-verify.html"
    cmd = (f"cat > {page} <<'EOF'\n{VERIFY_PAGE}\nEOF\n"
           f"osascript -e 'tell application \"Gorilla Unleashed\" to open POSIX file \"{page}\"' >/dev/null; sleep 6")
    m._run(m.ssh_argv(mac, address, cmd), timeout=60)
    out_dir = Path(out_dir or WORK)
    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / "macair-verify.png"
    if m.take_shot(mac, address, "/tmp/macair-verify-shot.png", str(shot)) != 0:
        rows.append((False, "screenshot failed"))
        return rows
    img = Image.open(shot)
    box = locate(img)
    if not box:
        rows.append((False, "the test page is not on screen (no flat blue page area found)"))
        return rows
    met = look.text_metrics(img, box)
    rows.append((met["stroke_px"] <= thin_max, f"text stroke {met['stroke_px']} px (<= {thin_max}; Linux reference 2.12)"))
    rows.append((met["colour_pct"] <= colour_max, f"coloured LCD smoothing {met['colour_pct']} % (<= {colour_max}; thin text is greyscale)"))
    return rows


# The sensor reader is sensors.gorilla's macOS side (SMC through IOKit, no password): one source for the dashboard a
# person reads and for this harness. It replaced reading Macs Fan Control's menu-bar text with tesseract (2026-10-23),
# which returned nothing once the owner ruled that tests run with Macs Fan Control OFF, "as on a normal MacBook Air".
SENSORS = Path(__file__).resolve().parents[2] / "toolbox" / "sensors-gorilla" / "sensors.gorilla"
MAC_PYTHON = "/usr/local/bin/python3"          # not on the SSH PATH of the Air (2026-10-22)
TOO_HOT_C = 95.0                               # CPU die: stop the run, macOS throttles near 100 on the i5-2467M
MFC = "Macs Fan Control"


def _median(v):
    v = sorted(x for x in v if x is not None)
    return v[len(v) // 2] if v else None


HEAT_LOG = WORK / "heat-runs.jsonl"


def heat(label, url, minutes=3.0, prefs=(), name="macair", warmup=45, every=15, out_dir=None, keep_fan_control=False):
    """One measured run of the browser on the Mac, with Macs Fan Control off unless keep_fan_control. Test prefs go
    into the profile's user.js for this run only. The browser copies every user.js pref into prefs.js on start, so
    both files are copied first and both are put back after. -> summary dict, also appended to heat-runs.jsonl."""
    import time
    m, mac, address = mac_target(name)
    out_dir = Path(out_dir or WORK)
    out_dir.mkdir(parents=True, exist_ok=True)
    rc, prof = m._run(m.ssh_argv(mac, address, "P=$(pgrep -x firefox | head -1); [ -n \"$P\" ] && lsof -p $P 2>/dev/null "
                                 "| grep -o '/Users/[^ ]*/Profiles/[^/]*' | head -1"), timeout=60)
    prof = prof.strip().splitlines()[-1] if rc == 0 and prof.strip() else ""
    if not prof.startswith("/Users/"):
        raise SystemExit("REFUSED: start Gorilla on the Mac first (the profile is read from the running browser): "
                         "ssh macair \"open -a '/Applications/Gorilla Unleashed.app'\"")
    q = shlex_quote
    lines = "".join(f'user_pref("{k}", {v});\n' for k, v in prefs)
    stamp = time.strftime("%y-%m-%d-%H-%M-%S")
    # `open -a App about:blank` is read as a FILE name: the browser was quit and never came back (2026-10-23), so the
    # idle run measured nothing and the next run was refused. An empty local page stands in for "blank"; any other
    # non-web address is refused.
    if url in ("blank", "about:blank"):
        open_page = ("printf '<!doctype html><title>blank</title>' > /tmp/heat-blank.html; "
                     "open -a '/Applications/Gorilla Unleashed.app' /tmp/heat-blank.html")
    elif url.startswith(("http://", "https://")):
        open_page = f"open -a '/Applications/Gorilla Unleashed.app' {q(url)}"
    else:
        raise SystemExit(f"REFUSED: --url must be http(s) or 'blank' (macOS opens {url!r} as a file name)")
    setup = (f"osascript -e 'tell application \"Gorilla Unleashed\" to quit'; "
             f"for i in $(seq 30); do pgrep -x firefox >/dev/null || break; sleep 1; done; "
             + (f"cp -p {q(prof + '/prefs.js')} {q(prof + '/prefs.js.heat-' + stamp)}; " if lines else "")
             + f"cp -p {q(prof + '/user.js')} {q(prof + '/user.js.heat-' + stamp)} 2>/dev/null; "
             + (f"printf '%b' {q(lines)} >> {q(prof + '/user.js')}; " if lines else "")
             + open_page)
    if not SENSORS.exists():
        raise SystemExit(f"REFUSED: the sensor reader is missing: {SENSORS}")
    rc, _ = m._run(m.scp_argv(mac, address, "/tmp/sensors.gorilla", str(SENSORS), upload=True), timeout=60)
    if rc != 0:
        raise SystemExit("REFUSED: could not copy sensors.gorilla to the Mac")
    # Macs Fan Control OFF for the measured run (owner, 2026-10-23): "quit" from AppleScript is cancelled by the app
    # itself (error -128), so it gets a plain TERM, and it is started again afterwards whatever happens.
    rc, o = m._run(m.ssh_argv(mac, address, f"pgrep -x {q(MFC)} || true"), timeout=30)
    mfc_was_on = rc == 0 and o.strip().isdigit()
    if mfc_was_on and not keep_fan_control:
        m._run(m.ssh_argv(mac, address, f"pkill -TERM -x {q(MFC)}; sleep 5; ! pgrep -x {q(MFC)}"), timeout=60)
    samples, stopped = [], ""
    try:
        m._run(m.ssh_argv(mac, address, setup), timeout=120)
        time.sleep(warmup)
        end = time.time() + minutes * 60
        while time.time() < end:
            t0 = time.time()
            rc, o = m._run(m.ssh_argv(mac, address, "ps -A -o %cpu=,rss=,comm= | awk '/firefox|plugin-container/"
                                      "{c+=$1; r+=$2} END{printf \"%.1f %d\\n\", c, r/1024}'; "
                                      f"{MAC_PYTHON} /tmp/sensors.gorilla --json"), timeout=40)
            cpu = mem = rpm = temp = die = None
            ctl = ""
            if rc == 0:
                first, _, rest = o.partition("\n")
                if len(first.split()) >= 2:
                    cpu, mem = float(first.split()[-2]), int(first.split()[-1])
                try:
                    r = json.loads(rest.strip().splitlines()[-1])
                    temp, die, ctl = r.get("cpu_c"), r.get("cpu_die_c"), r.get("fan_control", "")
                    rpm = (r.get("fans") or [{}])[0].get("rpm")
                except (ValueError, IndexError):
                    pass
            samples.append({"t": round(time.time() - end + minutes * 60), "cpu": cpu, "mem_mb": mem, "rpm": rpm,
                            "temp": temp, "die": die, "fan_control": ctl})
            if die is not None and die >= TOO_HOT_C:
                stopped = f"stopped early: CPU die {die} C (limit {TOO_HOT_C})"
                break
            time.sleep(max(0, every - (time.time() - t0)))
    finally:
        if mfc_was_on and not keep_fan_control:
            m._run(m.ssh_argv(mac, address, f"open -a {q(MFC)}"), timeout=60)
    # Without the prefs.js copy the test prefs stayed in the owner's profile after every run (found 2026-10-23:
    # four Stage D test prefs still set, so later "baseline" runs were not baselines).
    restore = (f"osascript -e 'tell application \"Gorilla Unleashed\" to quit'; "
               f"for i in $(seq 30); do pgrep -x firefox >/dev/null || break; sleep 1; done; "
               f"mv {q(prof + '/prefs.js.heat-' + stamp)} {q(prof + '/prefs.js')}; "
               f"if [ -f {q(prof + '/user.js.heat-' + stamp)} ]; then mv {q(prof + '/user.js.heat-' + stamp)} {q(prof + '/user.js')}; "
               f"else mkdir -p ~/Agents.Work.Trash-user && mv {q(prof + '/user.js')} ~/Agents.Work.Trash-user/user.js.heat-{stamp} 2>/dev/null; fi; "
               f"open -a '/Applications/Gorilla Unleashed.app'")
    if prefs:
        m._run(m.ssh_argv(mac, address, restore), timeout=120)
    res = {"label": label, "url": url, "prefs": [f"{k}={v}" for k, v in prefs], "minutes": minutes, "when": stamp,
           "samples": len(samples), "cpu_median": _median([x["cpu"] for x in samples]),
           "mem_mb_median": _median([x["mem_mb"] for x in samples]), "rpm_median": _median([x["rpm"] for x in samples]),
           "temp_median": _median([x["temp"] for x in samples]), "temp_max": max((x["temp"] for x in samples if x["temp"]), default=None),
           "die_max": max((x["die"] for x in samples if x["die"]), default=None),
           "fan_control": fan_control_summary(samples), "stopped": stopped, "raw": samples}
    with open(HEAT_LOG if out_dir == WORK else out_dir / "heat-runs.jsonl", "a") as f:
        f.write(json.dumps(res) + "\n")
    return res


def fan_control_summary(samples):
    """'off' when every sample saw macOS's own fan control, else what was seen (a run with Macs Fan Control on does
    not say what the browser does to a normal Air)."""
    seen = {x.get("fan_control") for x in samples if x.get("fan_control")}     # a dropped read says nothing
    return "off" if seen == {"macOS (automatic)"} else ", ".join(sorted(seen)) or "unknown"


def heat_report(path=None):
    p = Path(path or HEAT_LOG)
    if not p.exists():
        return ["no heat runs yet"]
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    out = [f"{'label':<22} {'when':<17} {'cpu %':>6} {'mem MB':>7} {'rpm':>6} {'temp C':>7} {'max C':>6} "
           f"{'fan ctl':<8} prefs"]
    for r in rows:
        out.append(f"{r['label']:<22} {r['when']:<17} {r['cpu_median'] or '-':>6} {r['mem_mb_median'] or '-':>7} "
                   f"{r['rpm_median'] or '-':>6} {r['temp_median'] or '-':>7} {r['temp_max'] or '-':>6} "
                   f"{r.get('fan_control', 'on (MFC)'):<8} {' '.join(r['prefs'])}")
    return out


def status():
    lines = []
    logs = sorted(WORK.glob("recreate-run*.log"), key=lambda p: p.stat().st_mtime)
    log = logs[-1] if logs else WORK / "recreate-run.log"
    if log.exists():
        tail = [l for l in log.read_text(errors="replace").splitlines() if l.strip()][-3:]
        lines.append(f"recreate run ({log}): " + " | ".join(tail))
    zips = sorted(WORK.glob("gorilla-air-*.zip"))
    lines.append(f"latest build: {zips[-1]}" if zips else "latest build: none in " + str(WORK))
    if (TREE / ".git").exists():
        head = subprocess.run(["git", "-C", str(TREE), "log", "--oneline", "-1"], capture_output=True, text=True).stdout.strip()
        n = subprocess.run(["git", "-C", str(TREE), "rev-list", "--count", f"{BASE}..HEAD"], capture_output=True, text=True).stdout.strip()
        lines.append(f"Mac tree {TREE}: {n} commits on Momiji, HEAD {head[:90]}")
    pub = MACOS / "PATCHES.sha256"
    lines.append(f"published series: {len(pub.read_text().splitlines())} patches listed in {pub}" if pub.exists()
                 else "published series: none")
    bad = [r for r in preflight() if not r[0]] if (TREE / ".git").exists() else []
    lines.append("preflight: " + ("all Mac decisions in the source" if not bad else f"{len(bad)} LOST: " + "; ".join(r[2] for r in bad)))
    return lines


def self_cmd():
    try:
        from fieldkit.core.settings import self_cmd as sc
        return sc()
    except ImportError:
        return f"{sys.executable} -m fieldkit"


def main(argv=None, prog="fieldkit macair"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0],
                                 epilog="\n".join(__doc__.split("\n\n")[1].splitlines()),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("status")
    p = sp.add_parser("preflight"); p.add_argument("tree", nargs="?", default=str(TREE)); p.add_argument("--json", action="store_true")
    b = sp.add_parser("budget"); b.add_argument("--screen", default="1366x768"); b.add_argument("--dock", type=int, default=SCREEN["dock"])
    e = sp.add_parser("export-patches"); e.add_argument("tree", nargs="?", default=str(TREE)); e.add_argument("--out")
    r = sp.add_parser("recreate"); r.add_argument("stage", nargs="?", default="all"); r.add_argument("--route", default="a")
    i = sp.add_parser("install"); i.add_argument("zip"); i.add_argument("--mac", default="macair")
    ac = sp.add_parser("app-check"); ac.add_argument("zip")
    v = sp.add_parser("verify"); v.add_argument("--mac", default="macair")
    h = sp.add_parser("heat"); h.add_argument("label"); h.add_argument("--url", required=True)
    h.add_argument("--minutes", type=float, default=3.0); h.add_argument("--pref", action="append", default=[])
    h.add_argument("--warmup", type=int, default=45); h.add_argument("--mac", default="macair")
    h.add_argument("--keep-fan-control", action="store_true", help="leave Macs Fan Control running (not a normal Air)")
    sp.add_parser("heat-report")
    o = ap.parse_args(argv)
    if o.cmd == "status":
        print("\n".join(status()))
        return 0
    if o.cmd == "budget":
        w, h = (int(x) for x in o.screen.lower().split("x"))
        bd = budget({"width": w, "height": h, "dock": o.dock})
        print(f"screen {w}x{h}: content up to {bd['content_max']} px tall; About logo up to {bd['logo_max']} px "
              f"(fixed parts {bd['fixed']})")
        return 0
    if o.cmd == "preflight":
        rows = preflight(o.tree)
        if o.json:
            print(json.dumps([{"ok": a, "decision": b_, "what": c} for a, b_, c in rows], indent=1))
        else:
            for ok, dec, what in rows:
                print(f"  {'ok  ' if ok else 'LOST'} [{dec}] {what}")
            bad = sum(not r[0] for r in rows)
            print(f"VERDICT  {'READY TO BUILD' if not bad else f'{bad} MAC DECISION(S) LOST: do not build'}")
        return 3 if any(not r[0] for r in rows) else 0
    if o.cmd == "export-patches":
        out, n = export_patches(o.tree, o.out)
        print(f"{n} patches -> {out} ({out.stat().st_size // 1024} KB); checksums in {MACOS}")
        return 0
    if o.cmd == "recreate":
        return subprocess.run(["bash", str(MACOS / "recreate-mac.sh"), o.stage, "--route", o.route]).returncode
    if o.cmd == "app-check":
        bad = app_check(o.zip)
        for did, path in bad[:40]:
            print(f"  REMOVED BUT SHIPPED [{did}] {path}")
        if len(bad) > 40:
            print(f"  ... and {len(bad) - 40} more")
        print(f"VERDICT  {'CLEAN: nothing a decision removed is in the app' if not bad else f'{len(bad)} removed file(s) shipped'}")
        return 3 if bad else 0
    if o.cmd == "install":
        rc, out = install(o.zip, o.mac)
        print(out)
        return rc
    if o.cmd == "heat":
        prefs = []
        for p in o.pref:
            if "=" not in p:
                print(f"REFUSED: --pref {p!r} is not NAME=VALUE")
                return 2
            k, v = p.split("=", 1)
            prefs.append((k, v if v in ("true", "false") or v.lstrip("-").isdigit() else json.dumps(v)))
        r = heat(o.label, o.url, o.minutes, prefs, o.mac, o.warmup, keep_fan_control=o.keep_fan_control)
        print(f"{o.label}: {r['samples']} samples; median CPU {r['cpu_median']} %, memory {r['mem_mb_median']} MB, "
              f"fan {r['rpm_median']} rpm, temperature {r['temp_median']} C (max {r['temp_max']}, die max "
              f"{r['die_max']}); Macs Fan Control {r['fan_control']}" + (f"; {r['stopped']}" if r["stopped"] else ""))
        return 0 if r["samples"] else 1
    if o.cmd == "heat-report":
        print("\n".join(heat_report()))
        return 0
    if o.cmd == "verify":
        rows = verify(o.mac)
        for ok, what in rows:
            print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        print(f"VERDICT  {'VERIFIED' if all(r[0] for r in rows) else 'NOT VERIFIED'}")
        return 0 if all(r[0] for r in rows) else 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
