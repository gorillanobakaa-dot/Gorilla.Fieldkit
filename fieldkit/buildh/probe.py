"""Ask a running build a question, or try a JavaScript fix, without spending a build.

    fieldkit build-harness probe <task> js=<probe.js|name> [url=about:blank] [wait=15] [omni=<jar>:<member>=<file> ...]
                                    [sub=<old text>=><new text> ...]  (every text member of both omni archives)
                                    [tree-since=<commit|build>] [tree-until=<commit>]  (the tree's changes in that
                                    range, where packaged as-is; until defaults to HEAD: bisect a change by ranges)
                                    [file=<path in the build>=<file> ...] [add=<new path in the build>=<file> ...] [timeout=S]
                                    [--install-dir <build>]

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
remote-settings-dumps, tab-borders (active/inactive tab outline and its tokens; compare two builds), about-pages
(run it through `build-harness about-pages`).

A probe that visits a local page declares it (`// gprobe-server: echo-ua 8765`); the run starts that page on
127.0.0.1 and stops it afterwards, and every request the page received is printed among the probe's lines, in time
order (probe_servers.py; 2026-10-04 the Satellite probes needed two hand-started scripts for this).
Two saved outputs of a probe with KIND|key|...|value lines (design-tokens: TOK) are compared with
`build-harness probe-compare A B` (probe_compare.py).
"""
import re
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


def tree_since(workdir, since, install_dir, out_dir, until="HEAD"):
    """The source tree's changes since `since`, as omni replacements for a probe COPY: every member of omni.ja and
    browser/omni.ja that is byte-identical to a changed file's OLD version is replaced by its NEW version. A file that
    is preprocessed, generated or bundled at build time matches nothing and is reported, not guessed. Born 2026-10-08
    to measure the one-logo and small-icon changes (D-157-35) without a 40-minute build.
    -> ({"jar:member": local path}, [(path, why not applied)])"""
    import hashlib
    git = lambda *a: subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(workdir), *a], capture_output=True)
    index, names = {}, {}
    for jar in ("omni.ja", "browser/omni.ja"):
        with zipfile.ZipFile(Path(install_dir) / jar) as z:
            for n in z.namelist():
                if not n.endswith("/"):
                    index.setdefault(hashlib.sha256(z.read(n)).hexdigest(), []).append(f"{jar}:{n}")
                    names.setdefault(n, []).append(f"{jar}:{n}")
    out, skipped = {}, []
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    rows = git("diff", "--name-status", "--no-renames", since, until).stdout.decode("utf-8", "replace").splitlines()
    for row in rows:
        status, path = row.split("\t", 1)
        if status != "M":
            skipped.append((path, "added" if status == "A" else "deleted" if status == "D" else status))
            continue
        old, new = git("show", f"{since}:{path}").stdout, git("show", f"{until}:{path}").stdout
        members = index.get(hashlib.sha256(old).hexdigest(), [])
        if not members:
            # Fluent files ship with their comments stripped, so they never match byte for byte: the en-US source
            # <area>/locales/en-US/<rest>.ftl is the member localization/en-US/<rest>.ftl (Fluent ignores comments)
            loc = re.match(r"^[\w/.-]*?/locales/en-US/(.+\.ftl)$", path)
            members = names.get(f"localization/en-US/{loc.group(1)}", []) if loc else []
        if not members:
            skipped.append((path, "not packaged as-is (preprocessed, generated or bundled at build time)"))
            continue
        local = Path(out_dir) / path.replace("/", "__")
        local.write_bytes(new)
        for m in members:
            out[m] = local
    return out, skipped


TEXT_MEMBER = (".css", ".js", ".mjs", ".html", ".xhtml", ".json", ".svg", ".ftl", ".xml")


def substitute(app_dir, pairs):
    """pairs: [(old, new)] -> every text member of omni.ja and browser/omni.ja in the COPY that holds an `old` has it
    replaced by `new`. Born 2026-10-07: pointing every page of a build at ONE logo file and measuring the memory took
    a throwaway script; it is an option now (`sub=OLD=>NEW`, repeatable). -> ["jar:member", ...] rewritten."""
    done = []
    for jar in ("omni.ja", "browser/omni.ja"):
        src = Path(app_dir) / jar
        if not src.is_file():
            continue
        tmp = src.with_suffix(".sub")
        with zipfile.ZipFile(src) as zi, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
            for info in zi.infolist():
                data = zi.read(info.filename)
                if info.filename.lower().endswith(TEXT_MEMBER):
                    new = data
                    for old, rep in pairs:
                        new = new.replace(old.encode("utf-8"), rep.encode("utf-8"))
                    if new != data:
                        done.append(f"{jar}:{info.filename}")
                        data = new
                zo.writestr(info, data)
        tmp.replace(src)
    return done


PROFILE_JS = (throwaway.USER_JS + 'user_pref("browser.dom.window.dump.enabled", true);\n'
              'user_pref("devtools.console.stdout.chrome", true);\n')


def apply_change(app, omni=None, files=None, added=None, subs=None, say=print):
    """The change under test applied to an app folder (a COPY): omni members, plain files, new files, text
    substitutions -> ["jar:member" / path, ...]. Shared by the probe and the visual runtime layer (visual omni=...)."""
    patched = (patch_omni(app, omni) if omni else []) + (replace_files(app, files or {}, added) if (files or added) else [])
    if subs:
        rewritten = substitute(app, subs)
        say(f"  probe: sub= rewrote {len(rewritten)} text member(s)")
        patched += rewritten
    return patched


COPY_PREFIX = "gprobe_app_"
COPY_MARK = ".fieldkit-probe-copy"


def processes_in(folder):
    """-> [pid] of every process whose program lives inside `folder` (a probe copy: the path is unique to this run,
    so these are ours and nothing of the owner's). Windows only; [] elsewhere or when the query fails."""
    import json as _json
    import sys as _sys
    if _sys.platform != "win32":
        return []
    root = str(Path(folder).resolve()).lower()
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath } | "
                              "Select-Object ProcessId, ExecutablePath | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, timeout=60).stdout
        rows = _json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    rows = rows if isinstance(rows, list) else [rows]
    return [r["ProcessId"] for r in rows if str(r.get("ExecutablePath", "")).lower().startswith(root + "\\")]


def stop_in(folder):
    """Stop every process running from `folder`, by PID (tree) -> [pid]."""
    pids = processes_in(folder)
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    return pids


def remove_copy(copy, tries=6, sleep=time.sleep):
    """Delete a probe copy: stop what still runs from it, then retry while Windows holds files open -> True when gone."""
    copy = Path(copy)
    for i in range(tries):
        if i:
            stop_in(copy)
            sleep(2)
        shutil.rmtree(copy, ignore_errors=True)
        if not copy.exists():
            return True
    return False


def sweep_copies(older_than_s=3600, say=print, now=None):
    """Delete probe copies left by earlier runs (marked, older than an hour, nothing running from them) -> count."""
    now = now or time.time()
    gone = 0
    for d in Path(tempfile.gettempdir()).glob(COPY_PREFIX + "*"):
        try:
            if not d.is_dir() or now - d.stat().st_mtime < older_than_s:
                continue
            # marked copies, and the unmarked ones made before the mark existed (an app/firefox.exe inside)
            if not ((d / COPY_MARK).is_file() or (d / "app" / "firefox.exe").is_file()):
                continue
        except OSError:
            continue
        if processes_in(d):
            continue
        if remove_copy(d, tries=2):
            gone += 1
    if gone:
        say(f"  probe: removed {gone} copy(ies) left by earlier runs")
    return gone


def prepare_copy(install_dir, js, wait=15, omni=None, files=None, added=None, subs=None, say=print, body=None):
    """One throwaway copy of the install with the change applied and the probe wired in -> (copy dir, app dir,
    [patched]). Launch it as often as needed with launch(); remove the copy dir afterwards. (Split out of run() on
    2026-10-08 so build-harness weigh can open every page in a fresh browser without copying the build each time.)
    `body`: the probe text to use instead of the file's (run() passes it with its local page ports rewritten)."""
    body = body if body is not None else resolve_js(js).read_text(encoding="utf-8")
    sweep_copies(say=say)
    copy = Path(tempfile.mkdtemp(prefix=COPY_PREFIX))
    (copy / COPY_MARK).write_text("made by fieldkit build-harness probe; deleted after the run\n", encoding="utf-8")
    app = copy / "app"
    shutil.copytree(install_dir, app)
    patched = apply_change(app, omni=omni, files=files, added=added, subs=subs, say=say)
    (app / "defaults" / "pref").mkdir(parents=True, exist_ok=True)
    (app / "defaults" / "pref" / "gprobe-autoconfig.js").write_text(
        'pref("general.config.filename", "gprobe.cfg");\npref("general.config.obscure_value", 0);\n'
        'pref("general.config.sandbox_enabled", false);\n', encoding="utf-8")
    indented = "\n".join("    " + l for l in body.splitlines())
    (app / "gprobe.cfg").write_text(CFG.format(wait=wait, wait_ms=int(wait * 1000), body=indented), encoding="utf-8")
    return copy, app, patched


def launch(app, url="about:blank", timeout=90, headless=True, on_line=None):
    """One headless run of a prepared copy on a FRESH throwaway profile -> {"lines", "times" (the clock time each
    line arrived), "done", "seconds"}. Only the process this started is stopped (taskkill /PID /T).
    headless=False opens a normal window (the owner watches it: about-pages walk=1); on_line(line) is called with
    each probe line as it arrives, for a live view."""
    prof = throwaway.profile("gprobe_", PROFILE_JS)
    try:
        t0 = time.time()
        # -wait-for-browser: on Windows firefox.exe is a launcher that starts the real browser and exits; with it the
        # launcher stays until the browser ends, so the PID this run stops is the browser's parent (2026-10-08: two
        # visible walk browsers outlived their runs)
        proc = subprocess.Popen([str(Path(app) / "firefox.exe")] + (["-headless"] if headless else [])
                                + ["-wait-for-browser", "-no-remote", "-profile", str(prof), url],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace")
        lines, times, finished = [], [], False
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
                    times.append(time.time())
                    if on_line:
                        on_line(lines[-1])
                if time.time() - t0 > timeout:
                    break
        finally:
            killer.cancel()
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            stop_in(Path(app))                     # and anything else still running from this copy, by PID
        return {"lines": lines, "times": times, "done": finished, "seconds": round(time.time() - t0, 1)}
    finally:
        throwaway.discard(prof)


def timeline(r, reqs):
    """The probe's lines and the local pages' requests in one list, in time order -> ["line" | "SERVER ..."]."""
    from . import probe_servers
    rows = [(t, 0, l) for t, l in zip(r.get("times") or [], r["lines"])]
    rows += [(q["t"], 1, "SERVER " + probe_servers.request_line(q)) for q in reqs]
    return [text for _, _, text in sorted(rows, key=lambda x: (x[0], x[1]))]


def run(install_dir, js, url="about:blank", wait=15, omni=None, timeout=90, say=print, files=None, added=None, subs=None,
        headless=True, on_line=None):
    """-> {"lines": [...], "done": bool, "patched": [...], "seconds": float, "requests": [...], "timeline": [...]}
    The local pages the probe declares run only while the browser does (probe_servers.py)."""
    from . import probe_servers
    body = resolve_js(js).read_text(encoding="utf-8")
    with probe_servers.serving(body, say=say) as (body, pages):
        copy, app, patched = prepare_copy(install_dir, js, wait=wait, omni=omni, files=files, added=added, subs=subs,
                                          say=say, body=body)
        try:
            say(f"  probe: {Path(js).name} in a copy of {install_dir}" + (f", with {len(patched)} replaced member(s)" if patched else ""))
            r = launch(app, url=url, timeout=timeout, headless=headless, on_line=on_line)
        finally:
            if not remove_copy(copy):
                say(f"  probe: could not remove {copy} (files still held); the next run removes it")
        reqs = probe_servers.requests(pages)
    return {**r, "patched": patched, "requests": reqs, "timeline": timeline(r, reqs)}


