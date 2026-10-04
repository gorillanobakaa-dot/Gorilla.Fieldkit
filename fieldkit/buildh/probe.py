"""Ask a running build a question, or try a JavaScript fix, without spending a build.

    fieldkit build-harness probe <task> js=<probe.js|name> [url=about:blank] [wait=15] [omni=<jar>:<member>=<file> ...]
                                    [file=<path in the build>=<file> ...] [add=<new path in the build>=<file> ...] [--install-dir <build>]

Fastest loop for CSS, themes and JS: point --install-dir at the objdir's UNPACKED dist/bin (no omni.ja; every
chrome file is a plain file) and give file= replacements; the copy runs your change in seconds, without a build.
A change that works goes into the source tree and is recorded (build-harness record) like any hand edit.

How (2026-10-04: this is how the blank new tab and the ignored built-in data were diagnosed, and their fixes proven,
in minutes instead of builds):
  1. the installed build is copied to a throwaway folder (the install itself is only read);
  2. optionally, members of omni.ja / browser/omni.ja in the COPY are replaced by local files (a candidate JS fix);
  3. an autoconfig script in the copy runs the probe with full chrome privileges `wait` seconds after start-up;
     the probe reports with say(...) and ends with done() (or the run ends at the timeout);
  4. the copy starts headless with a fresh profile; only `GPROBE ` lines from its output are returned.
Nothing is sent anywhere by the harness; the probe is whatever JavaScript you give it, so read it first.

Ready-made probes live in fieldkit/buildh/probes/*.js (pass the name without .js): newtab-gates, search-icon,
remote-settings-dumps.
"""
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from . import throwaway

PROBES = Path(__file__).parent / "probes"
CFG = """// fieldkit build-harness probe (autoconfig): runs once, {wait} s after start-up
const {{ setTimeout: __gT }} = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
function say(...a) {{ dump("GPROBE " + a.map(x => typeof x === "string" ? x : JSON.stringify(x)).join(" ") + "\\n"); }}
function done() {{ dump("GPROBE-DONE\\n"); }}
function race(p, ms) {{
  return Promise.race([Promise.resolve(p).then(v => ({{ok: true, value: v}}), e => ({{ok: false, error: String(e)}})),
                       new Promise(r => __gT(() => r({{ok: false, error: "never (waited " + ms + " ms)"}}), ms))]);
}}
__gT(async () => {{
  try {{
{body}
  }} catch (e) {{ say("error", String(e), e && e.stack ? e.stack.split("\\n")[0] : ""); }}
  // finally: a probe that stops early with `return` (no window, nothing found) still reports DONE, instead of the
  // run waiting for its timeout and calling a finished probe TIMED OUT (2026-10-04)
  finally {{ done(); }}
}}, {wait_ms});
"""


def resolve_js(js):
    p = Path(js)
    if p.is_file():
        return p
    q = PROBES / f"{js}.js"
    if q.is_file():
        return q
    raise FileNotFoundError(f"no probe {js!r}: not a file, and not one of {[x.stem for x in PROBES.glob('*.js')]}")


def patch_omni(app_dir, patches):
    """patches: {"browser/omni.ja:modules/X.sys.mjs": Path(local)} -> replaced members of the COPY's archives."""
    by_jar = {}
    for spec, local in patches.items():
        jar, member = spec.split(":", 1)
        by_jar.setdefault(jar, {})[member] = Path(local).read_bytes()
    for jar, members in by_jar.items():
        src = Path(app_dir) / jar
        tmp = src.with_suffix(".probe")
        with zipfile.ZipFile(src) as zi, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
            missing = set(members) - set(zi.namelist())
            if missing:
                raise KeyError(f"{jar} has no member {sorted(missing)[0]}")
            for info in zi.infolist():
                zo.writestr(info, members.get(info.filename, zi.read(info.filename)))
        tmp.replace(src)
    return sorted(f"{j}:{m}" for j, ms in by_jar.items() for m in ms)


def replace_files(app_dir, files, added=None):
    """files: {"browser/chrome/browser/skin/classic/browser/master-redirect.css": Path(local)} -> copied into the COPY.
    For an UNPACKED build (the objdir's dist/bin: chrome, modules and CSS as plain files, no omni.ja), which is the
    fast way to try themes, CSS and JS: no archive to rewrite, no build (the owner's Linux workflow, 2026-10-04).
    added: the same, for files the change CREATES (a new icon, a new .ftl); their folder must already exist, so a
    typo in the path is still refused instead of silently creating a file nothing reads."""
    done = []
    for rel, local in files.items():
        dest = Path(app_dir) / rel
        if not dest.is_file():
            raise FileNotFoundError(f"{rel} is not a file of this build (packed builds keep it in omni.ja: use omni=;"
                                    f" a file the change creates: use add=)")
        dest.write_bytes(Path(local).read_bytes())
        done.append(rel)
    for rel, local in (added or {}).items():
        dest = Path(app_dir) / rel
        if dest.exists():
            raise FileExistsError(f"{rel} already exists in this build: use file= to replace it")
        if not dest.parent.is_dir():
            raise FileNotFoundError(f"{dest.parent.relative_to(app_dir)} is not a folder of this build")
        dest.write_bytes(Path(local).read_bytes())
        done.append(rel + " (new)")
    return sorted(done)


def run(install_dir, js, url="about:blank", wait=15, omni=None, timeout=90, say=print, files=None, added=None):
    """-> {"lines": [...], "done": bool, "patched": [...], "seconds": float}"""
    body = resolve_js(js).read_text(encoding="utf-8")
    copy = Path(tempfile.mkdtemp(prefix="gprobe_app_"))
    prof = throwaway.profile("gprobe_", throwaway.USER_JS + 'user_pref("browser.dom.window.dump.enabled", true);\n'
                                                           'user_pref("devtools.console.stdout.chrome", true);\n')
    try:
        app = copy / "app"
        shutil.copytree(install_dir, app)
        patched = (patch_omni(app, omni) if omni else []) + (replace_files(app, files or {}, added) if (files or added) else [])
        (app / "defaults" / "pref").mkdir(parents=True, exist_ok=True)
        (app / "defaults" / "pref" / "gprobe-autoconfig.js").write_text(
            'pref("general.config.filename", "gprobe.cfg");\npref("general.config.obscure_value", 0);\n'
            'pref("general.config.sandbox_enabled", false);\n', encoding="utf-8")
        indented = "\n".join("    " + l for l in body.splitlines())
        (app / "gprobe.cfg").write_text(CFG.format(wait=wait, wait_ms=int(wait * 1000), body=indented), encoding="utf-8")
        say(f"  probe: {Path(js).name} in a copy of {install_dir}" + (f", with {len(patched)} replaced member(s)" if patched else ""))
        t0 = time.time()
        proc = subprocess.Popen([str(app / "firefox.exe"), "-headless", "-no-remote", "-profile", str(prof), url],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        lines, finished = [], False
        import threading
        killer = threading.Timer(timeout, lambda: subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                                                 capture_output=True))
        killer.start()                                     # a silent browser cannot hang the probe
        try:
            for line in proc.stdout:
                if line.startswith("GPROBE-DONE"):
                    finished = True
                    break
                if line.startswith("GPROBE "):
                    lines.append(line[7:].rstrip())
                if time.time() - t0 > timeout:
                    break
        finally:
            killer.cancel()
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        return {"lines": lines, "done": finished, "patched": patched, "seconds": round(time.time() - t0, 1)}
    finally:
        shutil.rmtree(copy, ignore_errors=True)
        throwaway.discard(prof)
