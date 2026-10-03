"""Layer 2, runtime: the installed build, measured from inside, on a throwaway COPY with a throwaway profile.

The build has Marionette and WebDriver BiDi cut, so the route is AUTOCONFIG, written only into the copy:

    <copy>/defaults/pref/autoconfig.js   general.config.filename = gvisual.cfg, sandbox off
    <copy>/gvisual.cfg                   chrome-privileged at startup (probe_js.CFG_JS)
    <copy>/gvisual/*.sys.mjs             a JSWindowActor (child) that measures each page, and the measurements

On the first browser window the script reads the page list from about:about's own page (falling back to the
about-module registrations about:about reads, minus the ones it hides), opens every page in a tab, waits for it to
settle, measures it, then opens the app menu and the toolbar context menu and measures them, writing JSON after
every page, and quits. The browser runs headless with -no-remote and -profile, once at layout.css.devPixelsPerPx 1
and once at 2 (HiDPI: a raster without a 2x version shows as upscaled), with a dead proxy (127.0.0.1:9) so the run
reaches nothing on the network. Only the process this module started is stopped (taskkill /PID /T), and the copy
and profiles are deleted afterwards; the JSON is kept under Fieldkit's state/visual/<stamp>/.

Rules (each per page and per DPR; FAIL and UNVERIFIABLE fail the row):
    RT-RUN        the run finished (autoconfig ran, the browser quit by itself, `complete` is true)
    RT-LIST       about:about listed pages
    RT-DPR        the page really rendered at the DPR asked for
    RT-LOAD       the page loaded (no timeout, no error page)
    RT-METRIC     the measurements were collected and not truncated
    RT-UPSCALE    a raster painted larger than its pixels (painted CSS px x DPR > natural px by more than 5 %)
    RT-BROKEN     an image that does not load (natural width 0)
    RT-ZERO       a visible element with an icon whose box is 0 wide or 0 high
    RT-CLIP       text wider than its own box (clipped, ellipsis, or spilling out)
    RT-OVERLAP    two sibling controls overlap by more than 1 px both ways
    RT-OFFSCREEN  content outside the viewport horizontally, or the page scrolls sideways
    RT-MENU-ALIGN labels (or icons) of a menu's items not on one x offset (more than 2 px from the most common)
    RT-MENU       the app menu / toolbar context menu opened and was measured
    RT-PAGE       a page with none of the above (PASS)

What this cannot see: pixels. It measures what layout reports (sizes, positions, natural sizes), not what was
drawn: an SVG that embeds a soft raster, a raster that is soft at its natural size (the static layer's ICON-002),
colour/contrast, and anything drawn by the GPU after layout. Pages that need a network, an account or a device
show their offline state.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import allow as allowmod, probe_js
from ..buildh import throwaway

PREFIX = "gvisual_"
DPRS = (1, 2)
PAGE_MS = 20000
UPSCALE_TOLERANCE = 1.05
USER_JS = {
    "browser.shell.checkDefaultBrowser": False,
    "browser.aboutwelcome.enabled": False,
    "browser.sessionstore.resume_from_crash": False,
    "browser.startup.page": 0,
    "browser.tabs.warnOnClose": False,
    "toolkit.startup.max_resumed_crashes": -1,
    "network.proxy.type": 1,                  # a dead proxy: the visual run talks to nobody
    "network.proxy.http": "127.0.0.1",
    "network.proxy.http_port": 9,
    "network.proxy.ssl": "127.0.0.1",
    "network.proxy.ssl_port": 9,
    "network.proxy.share_proxy_settings": True,
    "network.proxy.no_proxies_on": "",
    "network.proxy.allow_hijacking_localhost": True,
    "network.dns.disablePrefetch": True,
    "network.prefetch-next": False,
    "dom.disable_open_during_load": True,
}


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------- throwaway space
def make_root():
    """A fresh folder directly in the temp folder, with the harness's throwaway marker."""
    root = Path(tempfile.mkdtemp(prefix=PREFIX, dir=str(throwaway.root())))
    (root / throwaway.MARK).write_text("made by fieldkit visual; deleted after the run\n", encoding="utf-8")
    return root


def ours(root):
    """True only for a folder make_root() made: directly in the temp folder, our prefix, the marker."""
    try:
        p = Path(root).resolve()
        return (p.parent == throwaway.root().resolve() and p.name.startswith(PREFIX) and p.is_dir()
                and not p.is_symlink() and (p / throwaway.MARK).is_file())
    except OSError:
        return False


def discard(root, tries=4, sleep=time.sleep):
    """Delete a throwaway root this module made. Anything else is refused (False)."""
    if not ours(root):
        return False
    for n in range(tries):
        shutil.rmtree(root, ignore_errors=True)
        if not Path(root).exists():
            return True
        if n + 1 < tries:
            sleep(2)
    return not Path(root).exists()


def _inside(path, base):
    try:
        Path(path).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False


def copy_build(install_dir, root):
    """The installed build copied into the throwaway root. The install itself is only read."""
    src, dest = Path(install_dir).resolve(), Path(root) / "browser"
    if not ours(root):
        raise Refused(f"{root} is not a throwaway root this module made")
    if not (src / "firefox.exe").is_file():
        raise Refused(f"no firefox.exe in {src}")
    if _inside(src, root) or _inside(root, src):
        raise Refused("the install and the throwaway copy overlap")
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("gvisual*", "mozilla.cfg"))
    for left in ("defaults/pref/autoconfig.js", "gvisual.cfg"):
        if (dest / left).exists():
            raise Refused(f"the install already carries {left}: refusing to run someone else's autoconfig")
    return dest


def write_probe(copy, out_json, dpr, only=(), page_ms=PAGE_MS):
    """Autoconfig + the probe, written ONLY inside the copy (refused anywhere else)."""
    copy = Path(copy)
    if not ours(copy.parent) or copy.name != "browser":
        raise Refused(f"{copy} is not a throwaway copy made by this module")
    probe = copy / "gvisual"
    probe.mkdir(exist_ok=True)
    cfg = (probe_js.CFG_JS.replace('"%OUT%"', json.dumps(str(out_json))).replace('"%DIR%"', json.dumps(str(probe)))
           .replace("%DPR%", str(int(dpr))).replace("%PAGE_MS%", str(int(page_ms))).replace("%ONLY%", json.dumps(list(only))))
    files = {copy / "defaults" / "pref" / "autoconfig.js": probe_js.AUTOCONFIG_JS, copy / "gvisual.cfg": cfg,
             probe / "GVisualChild.sys.mjs": probe_js.CHILD_MJS, probe / "GVisualMeasure.sys.mjs": probe_js.MEASURE_MJS,
             probe / "control.html": probe_js.CONTROL_HTML.replace("%PNG%", _tiny_png())}
    for p, text in files.items():
        if not _inside(p, copy):
            raise Refused(f"refusing to write {p} outside the copy")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    return probe


def _tiny_png():
    """An 8x8 PNG as a data: URI, for the control page (painted at 64 px it is 8x upscaled)."""
    import base64
    import io
    from PIL import Image
    im = Image.new("RGBA", (8, 8), (40, 120, 200, 255))
    for i in range(8):
        im.putpixel((i, i), (255, 255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def judge_control(dpr, control):
    """RT-CONTROL: the probe found every defect planted on the control page. A probe blind to one kind of defect
    cannot vouch for its absence on a real page, so a miss is UNVERIFIABLE for that kind, and fails the row."""
    where = f"control@{dpr}x"
    if not control or not control.get("loaded") or not isinstance(control.get("metrics"), dict):
        return [_it("RT-CONTROL", where, "FAIL", f"the control page was not measured: {(control or {}).get('error') or (control or {}).get('metric_error') or 'missing'}")]
    m = control["metrics"]
    items = []
    for key, ident in probe_js.CONTROL_EXPECT.items():
        hit = any(f"#{ident}" in json.dumps(x) for x in m.get(key) or [])
        items.append(_it("RT-CONTROL", f"{where} {key}", "PASS" if hit else "UNVERIFIABLE",
                         f"planted #{ident} {'found' if hit else 'NOT found: the probe is blind to this kind of defect'}"))
    return items


def make_profile(root, dpr):
    prof = Path(root) / f"profile-dpr{dpr}"
    prof.mkdir()
    prefs = dict(USER_JS, **{"layout.css.devPixelsPerPx": f"{float(dpr):.1f}"})
    (prof / "user.js").write_text("".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in prefs.items()),
                                  encoding="utf-8")
    return prof


def launch(copy, prof, dpr, timeout_s, popen=subprocess.Popen, kill=None, clock=time.monotonic, sleep=time.sleep):
    """Start the copy headless, wait for it to quit by itself; past the timeout stop exactly the process started
    here (with its children). -> {rc, seconds, timed_out, pid}."""
    exe = Path(copy) / "firefox.exe"
    env = dict(os.environ, MOZ_HEADLESS="1", MOZ_HEADLESS_WIDTH=str(1280 * dpr), MOZ_HEADLESS_HEIGHT=str(900 * dpr),
               MOZ_CRASHREPORTER_DISABLE="1")
    for k in ("MOZ_LOG", "MOZ_LOG_FILE"):
        env.pop(k, None)
    t0 = clock()
    proc = popen([str(exe), "-headless", "-no-remote", "-profile", str(prof), "about:blank"], env=env,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    timed_out = False
    try:
        rc = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out, rc = True, None
    if kill is None:
        def kill(pid):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    if timed_out:
        kill(proc.pid)            # still alive, so the PID is still ours; never after exit (the PID may be reused)
    sleep(1)
    left = stop_leftovers(copy) if popen is subprocess.Popen else []
    return {"rc": rc, "seconds": round(clock() - t0, 1), "timed_out": timed_out, "pid": proc.pid, "stopped_leftovers": left}


def stop_leftovers(copy):
    """Stop processes still running FROM the throwaway copy (content processes outliving their parent). The copy is
    a unique folder this module made, so a process whose executable lives in it was started by this run: nothing
    else is ever matched. -> the PIDs stopped."""
    copy = str(Path(copy).resolve()).lower()
    if os.name != "nt" or not ours(Path(copy).parent):
        return []
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath } | "
          "ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, errors="replace").stdout
    pids = []
    for line in out.splitlines():
        pid, _, exe = line.partition("|")
        if exe.strip().lower().startswith(copy + os.sep) and pid.strip().isdigit():
            pids.append(int(pid))
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    return pids


# ------------------------------------------------------------------------------------------- judgement
def _it(rule, item, verdict, evidence):
    return {"layer": "runtime", "rule": rule, "item": item, "verdict": verdict, "evidence": evidence}


def judge_metrics(where, m, dpr):
    """Items for one measured surface (a page or a menu). `where` like 'about:addons@2x'."""
    items = []
    for x in m.get("images") or []:
        items.append(_it("RT-UPSCALE", f"{where} {x.get('sel')}", "FAIL",
                         f"{x.get('kind')} {x.get('url')}: {x['natural'][0]}x{x['natural'][1]} px painted at "
                         f"{x['painted'][0]}x{x['painted'][1]} CSS px x {dpr} = {x.get('upscale')}x upscaled (blurry)"))
    for x in m.get("broken") or []:
        items.append(_it("RT-BROKEN", f"{where} {x.get('sel')}", "FAIL", f"{x.get('kind')} {x.get('url')} does not load"))
    for x in m.get("zero_size") or []:
        items.append(_it("RT-ZERO", f"{where} {x.get('sel')}", "FAIL",
                         f"visible {x.get('kind')} icon {x.get('url')} in a {x.get('rect')} box"))
    for x in m.get("clipped") or []:
        items.append(_it("RT-CLIP", f"{where} {x.get('sel')}", "FAIL",
                         f"text {x.get('how')}: {x.get('text')!r} needs {x.get('scroll')} px, has {x.get('client')}"))
    for x in m.get("overlaps") or []:
        items.append(_it("RT-OVERLAP", f"{where} {x.get('a')} | {x.get('b')}", "FAIL",
                         f"sibling controls overlap by {x.get('overlap')} px"))
    for x in m.get("outside") or []:
        items.append(_it("RT-OFFSCREEN", f"{where} {x.get('sel')}", "FAIL",
                         f"spans x {x.get('rect')} outside a {x.get('viewport')} px viewport"))
    if m.get("page_scrolls_sideways"):
        items.append(_it("RT-OFFSCREEN", f"{where} <page>", "FAIL",
                         f"the page scrolls sideways: {m['page_scrolls_sideways'][0]} px wide in {m['page_scrolls_sideways'][1]}"))
    for x in m.get("misaligned") or []:
        items.append(_it("RT-MENU-ALIGN", f"{where} {x.get('item')}", "FAIL",
                         f"{x.get('what')} at x={x.get('x')}, the menu's others at x={x.get('mode')} ({x.get('text')!r})"))
    for k in m.get("truncated") or []:
        items.append(_it("RT-METRIC", f"{where} {k}", "UNVERIFIABLE", f"more {k} than the probe records: the list is cut"))
    # the same finding on several identical elements (list rows) is one item with a count
    merged = {}
    for i in items:
        key = (i["rule"], i["item"], i["evidence"])
        if key in merged:
            merged[key]["count"] = merged[key].get("count", 1) + 1
        else:
            merged[key] = i
    for i in merged.values():
        if i.get("count"):
            i["evidence"] += f" (x{i['count']} identical elements)"
    return list(merged.values())


def judge_run(dpr, data, launch_info=None, started=False):
    """Items for one DPR run. `data` is the JSON the probe wrote (None when it wrote nothing)."""
    items = []
    run = f"dpr {dpr}"
    li = launch_info or {}
    if data is None:
        why = ("autoconfig never ran (no started marker): the route is unavailable in this build" if not started else
               "autoconfig ran but no results were written (crash or hang before the first page)")
        return [_it("RT-RUN", run, "FAIL", why + (f"; process rc {li.get('rc')}, {li.get('seconds')} s" if li else ""))]
    if li.get("timed_out"):
        items.append(_it("RT-RUN", run, "FAIL", f"the browser did not quit by itself in {li.get('seconds')} s; stopped"))
    if not data.get("complete"):
        items.append(_it("RT-RUN", run, "FAIL", f"the run stopped after {len(data.get('pages') or [])} page(s): "
                         + "; ".join(str(e)[:200] for e in data.get("errors") or []) or "no error recorded"))
    elif not li.get("timed_out"):
        items.append(_it("RT-RUN", run, "PASS", f"{len(data.get('pages') or [])} page(s) measured in {li.get('seconds', '?')} s"))
    if data.get("chrome_dpr") not in (dpr, float(dpr)):
        items.append(_it("RT-DPR", f"{run} window", "FAIL", f"the window rendered at {data.get('chrome_dpr')}, not {dpr}"))
    items += judge_control(dpr, data.get("control"))
    listed = data.get("listed") or []
    items.append(_it("RT-LIST", run, "PASS" if listed else "FAIL",
                     f"{len(listed)} page(s) from {data.get('list_source')}" if listed else "no page list"))
    for p in data.get("pages") or []:
        where = f"{p.get('url')}@{dpr}x"
        if not p.get("loaded"):
            items.append(_it("RT-LOAD", where, "FAIL", f"did not load: {p.get('error') or 'no reason recorded'}"))
            continue
        if p.get("dpr") not in (dpr, float(dpr)):
            items.append(_it("RT-DPR", where, "FAIL", f"rendered at DPR {p.get('dpr')}, not {dpr}"))
        m = p.get("metrics")
        if not isinstance(m, dict) or not m.get("elements"):
            items.append(_it("RT-METRIC", where, "UNVERIFIABLE", f"no measurements: {p.get('metric_error') or 'missing'}"))
            continue
        found = judge_metrics(where, m, dpr)
        items += found
        if not found:
            items.append(_it("RT-PAGE", where, "PASS",
                             f"{m.get('elements')} elements, {m.get('images_ok')} raster(s) at or under 1:1, "
                             f"{m.get('vector_icons')} vector icon(s); final URL {p.get('final_url')}"))
    names = {c.get("surface") for c in data.get("chrome") or []}
    for want in ("app-menu", "toolbar-context-menu"):
        if want not in names and data.get("complete"):
            items.append(_it("RT-MENU", f"{want}@{dpr}x", "FAIL", "never measured"))
    for c in data.get("chrome") or []:
        where = f"{c.get('surface')}@{dpr}x"
        if not c.get("opened") or not isinstance(c.get("metrics"), dict) or not c["metrics"].get("elements"):
            items.append(_it("RT-MENU", where, "FAIL", f"could not be opened and measured: {c.get('error')}"))
            continue
        found = judge_metrics(where, c["metrics"], dpr)
        items += found or [_it("RT-MENU", where, "PASS", f"{c['metrics'].get('elements')} elements, aligned, nothing upscaled")]
    return items


def judge(runs, dprs=DPRS):
    """runs: {dpr: {"data": dict|None, "launch": dict, "started": bool}} -> items. A DPR never run is a FAIL."""
    items = []
    for dpr in dprs:
        r = runs.get(dpr)
        if r is None:
            items.append(_it("RT-RUN", f"dpr {dpr}", "FAIL", "this DPR was never run"))
            continue
        items += judge_run(dpr, r.get("data"), r.get("launch"), r.get("started", False))
    lists = [set(r["data"].get("listed") or []) for r in runs.values() if r and r.get("data")]
    if len(lists) == 2 and lists[0] != lists[1]:
        items.append(_it("RT-LIST", "dpr 1 vs 2", "FAIL", f"the two runs listed different pages: {sorted(lists[0] ^ lists[1])[:6]}"))
    return items


# ------------------------------------------------------------------------------------------- the run
def evidence_dir(stamp=None):
    from ..core import settings
    d = settings.ROOT / "state" / "visual" / (stamp or time.strftime("%Y%m%d-%H%M%S"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def run(install_dir, only=(), page_ms=PAGE_MS, say=print, keep_dir=None, allow=None, dprs=DPRS):
    """Layer 2 end to end. -> summary (allow.summarise) plus evidence/ runs."""
    allow = allow if allow is not None else allowmod.load()
    ev = Path(keep_dir) if keep_dir else evidence_dir()
    ev.mkdir(parents=True, exist_ok=True)
    root = make_root()
    runs = {}
    try:
        say(f"  copying {install_dir} -> {root} (the install is only read)")
        copy = copy_build(install_dir, root)
        for dpr in dprs:
            out = root / f"results-dpr{dpr}.json"
            probe = write_probe(copy, out, dpr, only, page_ms)
            (probe / "started.txt").unlink(missing_ok=True)
            prof = make_profile(root, dpr)
            budget = 180 + (len(only) or 90) * (page_ms / 1000 + 4)
            say(f"  DPR {dpr}: headless run, up to {budget:.0f} s ...")
            li = launch(copy, prof, dpr, budget)
            started = (probe / "started.txt").is_file()
            data = None
            if out.is_file():
                try:
                    data = json.loads(out.read_text(encoding="utf-8"))
                except ValueError as e:
                    data = {"complete": False, "errors": [f"results file unreadable: {e}"], "pages": []}
                shutil.copy2(out, ev / out.name)
            runs[dpr] = {"data": data, "launch": li, "started": started}
            say(f"  DPR {dpr}: rc {li['rc']}, {li['seconds']} s, autoconfig {'ran' if started else 'DID NOT RUN'}, "
                f"{len((data or {}).get('pages') or [])} page(s), complete {bool((data or {}).get('complete'))}")
    except Refused as e:
        res = allowmod.summarise([_it("RT-RUN", "setup", "FAIL", f"refused: {e}")], allow, "runtime")
        res.update(evidence=str(ev), runs={})
        return res
    finally:
        gone = discard(root)
        say(f"  throwaway copy and profiles {'deleted' if gone else 'NOT deleted: ' + str(root)}")
    items = judge(runs, dprs=dprs)
    if tuple(dprs) != DPRS:
        res_note = f"only DPR {list(dprs)} run: not a proof of HiDPI and standard screens both"
    else:
        res_note = None
    res = allowmod.summarise(items, allow, "runtime")
    if only:
        res["problems"].append(f"partial run (only {len(only)} page(s)): not a proof of the whole build")
        res["ok"] = False
    if res_note:
        res["problems"].append(res_note)
        res["ok"] = False
    res.update(evidence=str(ev), runs={d: {k: v for k, v in r.items() if k != "data"} for d, r in runs.items()})
    (ev / "runtime-report.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res
