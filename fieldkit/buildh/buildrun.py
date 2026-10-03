"""The build loop: our gate -> the owner's preflight -> the owner's build stage under watch -> every stop recorded,
fixed when the fix is known, handed back with its signature when it is not -> package -> build-verify.

Owner's order (2026-10-01): "I do not start the compile. You start it, monitor it in real time, log, fix
automatically, and for every stop write a deterministic tool and add it to the harness." So:

  * nothing starts while our build gate fails (compile.gate), and nothing starts while the owner's preflight reports
    a blocker that is not BUILD-DEPENDENT (a check that can only pass once an objdir exists: package manifest, l10n
    resources, bundled extensions, objdir-vs-CLOBBER). Those four are the only reasons `--force` is ever passed on,
    and each one is named in the journal;
  * the owner's stage runs exactly as the owner runs it (their script, their env, their thermal governor), its
    output is streamed to a log under state/, and a heartbeat line is printed so a watcher sees progress;
  * a non-zero exit is matched against STOPS. A known stop runs its fix and the build is retried (at most RETRIES);
    an unknown stop ends the loop with the first error lines and the owner's own triage output, for a person or a
    stronger model to turn into the next entry of STOPS. The same stop never costs a second investigation.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import task

from .ownercheck import BUILD_DEPENDENT, post_build  # noqa: E402

RETRIES = 4
SURFACE_CAP = 80       # processor cap while the only proven sensor is a surface/skin reading, not the die
HEARTBEAT_S = 300
ERROR_LINE = re.compile(r"^\s*\d*:?\d*\.?\d*\s*(?:E|ERROR|error)\b|error:|Error:|FAILED|fatal error|: error |\bError \d+\b", re.I)


def _owner_root(t):
    from .ownercheck import _owner_root
    return _owner_root(t)


def _env():
    env = dict(os.environ)
    for marker in ("CLAUDECODE", "CODEX_SANDBOX", "GEMINI_CLI", "OPENCODE"):
        env.pop(marker, None)                               # the owner's stage strips these too; mozbuild goes quiet otherwise
    return env


TELEMETRY_LINE = re.compile(r"Telemetry -> (logs[/\\]thermal-[\w.-]+\.csv)")
DEAD_SAMPLES, DEAD_PERF, HARD_CEILING_C = 36, 50.0, 95.0   # 3 min at the owner's 5 s interval


def thermal_verdict(csv_path):
    """Read the owner's telemetry CSV: -> None when fine, else the reason the build must stop. The owner's stage has
    the same guard; this one is Fieldkit's own, in case the stage's thread is the thing that died."""
    try:
        rows = [l.split(",") for l in Path(csv_path).read_text(encoding="utf-8", errors="replace").splitlines()[1:]]
    except OSError:
        return None
    temps = [(float(r[1]), float(r[2])) for r in rows if len(r) >= 3 and r[1] and r[2]]
    if not temps:
        return None
    if temps[-1][0] >= HARD_CEILING_C:
        return f"temperature {temps[-1][0]:.1f} C at or above the hard ceiling {HARD_CEILING_C:.0f} C"
    tail = temps[-DEAD_SAMPLES:]
    if len(tail) == DEAD_SAMPLES and all(p > DEAD_PERF for _, p in tail) and len({t for t, _ in temps}) == 1:
        return f"temperature source stuck at {tail[-1][0]:.2f} C since the start, {DEAD_SAMPLES} busy samples: not a live sensor"
    return None


def _stream(cmd, cwd, log_path, say, watch_thermal=True, governor=None):
    """Run `cmd`, append its output to log_path, print a heartbeat, watch the owner's telemetry CSV and kill the
    process tree on a thermal verdict (2026-10-02: a stuck sensor and a flag nobody acted on cooked the laptop).
    -> (rc, lines)."""
    lines, last_beat, started, csv_path, last_check = [], time.time(), time.time(), None, time.time()
    with open(log_path, "a", encoding="utf-8", newline="\n") as lf:
        lf.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} $ {' '.join(cmd)}\n")
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
        if governor is not None:                         # the thermald: moves the cap, kills this tree on its verdict
            governor.kill = lambda why, p=proc: subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            governor.start()
        for line in proc.stdout:
            lf.write(line)
            lines.append(line.rstrip("\n"))
            m = TELEMETRY_LINE.search(line)
            if m:
                csv_path = Path(cwd) / m.group(1)
            now = time.time()
            if watch_thermal and csv_path and now - last_check >= 30:
                last_check = now
                why = thermal_verdict(csv_path)
                if why:
                    say(f"  THERMAL WATCHDOG: {why} - killing the build")
                    lines.append(f"FIELDKIT THERMAL WATCHDOG: {why}")
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            if now - last_beat >= HEARTBEAT_S:
                say(f"  ... {int((now - started) // 60)} min, {len(lines)} lines, last: {line.strip()[:110]}")
                last_beat = now
        rc = proc.wait()
        if governor is not None:
            governor.stop()
            governor.join(timeout=15)
    return rc, lines


def error_lines(lines, n=8):
    """The first n lines that look like errors, mach's ' E ' prefix included."""
    out = []
    for l in lines:
        if ERROR_LINE.search(l) and "0 errors" not in l and "error_" not in l.lower():
            out.append(l.strip()[:220])
            if len(out) >= n:
                break
    return out


# -- known stops and their fixes ------------------------------------------------------------------------------

def fix_clobber(t, root, say):
    """mach refused because the tree's CLOBBER changed: run the owner's clobber script the way the owner's stage
    runs builds (MozillaBuild bash, MOZILLABUILD set from Python, not from a Git Bash prefix)."""
    bash = Path(r"C:\mozilla-build\msys2\usr\bin\bash.exe")
    script = Path(root) / "state" / "clobber.sh"
    if not bash.is_file() or not script.is_file():
        return False, f"no {bash} or {script}"
    env = _env()
    env["MOZILLABUILD"] = r"C:\mozilla-build"
    from .compile import mozconfig_path
    env["MOZCONFIG"] = str(mozconfig_path(root))
    r = subprocess.run([str(bash), "-l", "/" + str(script).replace(":", "").replace("\\", "/")], cwd=str(root), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    say(f"  clobber exit {r.returncode}: {(r.stdout or r.stderr).strip()[-200:]}")
    return r.returncode == 0, f"clobber exit {r.returncode}"


def fix_fluent(t, root, say, lines=()):
    """A Fluent file with a doubled or lost message: reconcile it against the owner's tree (fluent.step_dedupe)."""
    from . import fluent, verify as vf
    files = sorted({m.group(1) for l in lines for m in [re.search(r"([\w/.-]+\.ftl)", l)] if m})
    truth = vf._truth_root(t["meta"].get("harness_root") or "", t["workdir"])
    if not files or not truth:
        return False, "no .ftl named in the error, or no owner tree to reconcile against"
    done = []
    for rel in files:
        if (Path(t["workdir"]) / rel).is_file():
            r = fluent.step_dedupe(t, rel, str(truth))
            done.append(r.get("summary", rel))
    say("  " + "; ".join(done))
    return bool(done), "; ".join(done)


MISSING_TOOLCHAIN = re.compile(r"does not exist: .*?[/\\]\.mozbuild[/\\]([\w.-]+)[/\\]([\w.-]+)")
MOZBUILD_BASH = Path(r"C:\mozilla-build\msys2\usr\bin\bash.exe")


def toolchain_job(src, alias):
    """The toolchain job whose `toolchain-alias` is `alias`, from taskcluster/kinds/toolchain/*.yml, or None."""
    import glob
    for yml in glob.glob(str(Path(src) / "taskcluster" / "kinds" / "toolchain" / "*.yml")):
        job = None
        for line in Path(yml).read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^([\w.-]+):\s*$", line)
            if m:
                job = m.group(1)
            elif re.match(rf"^\s+toolchain-alias:\s*{re.escape(alias)}\s*$", line) and job:
                return job
    return None


def fix_toolchain(t, root, say, lines=()):
    """A moz.build lists a file under ~/.mozbuild/<toolchain>/ that is not there: a toolchain `mach bootstrap` would
    have fetched (Firefox 157 added two DLLs to winappsdk-x86_64-pc-windows-msvc). When the toolchain folder exists
    from an older bootstrap, `mach artifact toolchain` says done and changes nothing (23:18-23:26: five identical
    stops), so a folder that lacks the named file is moved aside first; success is that FILE existing afterwards."""
    text = "\n".join(lines)
    m = MISSING_TOOLCHAIN.search(text)
    if not m:
        return False, "no ~/.mozbuild/<toolchain>/<file> path in the error"
    alias, missing = m.group(1), m.group(2)
    src = Path(t["workdir"])
    job = toolchain_job(src, alias)
    if not job:
        return False, f"no toolchain job carries the alias {alias}"
    if not MOZBUILD_BASH.is_file():
        return False, f"no {MOZBUILD_BASH}"
    folder = Path.home() / ".mozbuild" / alias
    if folder.is_dir() and not (folder / missing).exists():
        stale = folder.with_name(f"{alias}.stale-{time.strftime('%Y%m%d-%H%M%S')}")
        folder.rename(stale)
        say(f"  ~/.mozbuild/{alias} is from an older bootstrap (no {missing}): moved to {stale.name}")
    env = _env()
    env["MOZILLABUILD"] = r"C:\mozilla-build"
    from .compile import mozconfig_path
    env["MOZCONFIG"] = str(mozconfig_path(root))
    # `mach artifact toolchain` unpacks into the CURRENT directory (the first run left the folder inside the source
    # tree, where the gate then saw an untracked folder): run it from ~/.mozbuild, where bootstrap puts toolchains
    msys = lambda p: "/" + str(p).replace(":", "").replace("\\", "/")
    r = subprocess.run([str(MOZBUILD_BASH), "-l", "-c",
                        f"cd {msys(folder.parent)} && {msys(src)}/mach artifact toolchain --from-build {job}"],
                       cwd=str(folder.parent), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    have = (folder / missing).exists()
    say(f"  mach artifact toolchain --from-build {job}: exit {r.returncode}; ~/.mozbuild/{alias}/{missing} "
        f"{'present' if have else 'STILL MISSING: ' + (r.stdout or r.stderr).strip()[-160:]}")
    return have, f"toolchain {alias} via {job}: exit {r.returncode}, {missing} {'present' if have else 'missing'}"


def active_power_scheme():
    r = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, errors="replace")
    m = re.search(r"GUID:\s*([0-9a-f-]{36})\s*\((.*?)\)", r.stdout or "")
    return (m.group(1), m.group(2)) if m else (None, (r.stdout or r.stderr).strip()[:80])


def restore_power_scheme(before, say):
    """The owner's stage switches to its build power scheme and restores on exit; when the stage crashes in its
    finally (it did, 2026-10-01: a flag named `_stop` shadowed Thread._stop) the laptop stays capped. The loop puts the
    scheme it found back whenever the stage left another one active."""
    guid, name = active_power_scheme()
    if before[0] and guid and guid != before[0]:
        subprocess.run(["powercfg", "/setactive", before[0]], capture_output=True)
        now = active_power_scheme()
        say(f"  power scheme was left on '{name}': put back to '{before[1]}' -> now '{now[1]}'")
        return True
    return False


def fix_creep_include(t, root, say, lines=()):
    """A compile stop on a header from an excised component: remove the include and the self-contained uses, record
    the edit as hand-port steps (handedit.record) so the gate and the verifier judge it; leave anything else to a
    person, listed. -> (ok, what)."""
    from . import creepfix, handedit, firefox
    w = Path(t["workdir"])
    hr = t["meta"].get("harness_root")
    excised = set()
    if hr:
        excised.update(Path(x).parent.as_posix() for x in firefox.manifest_deletions(hr))
        excised.update(firefox.excised_symbols(hr))
    done, what = [], []
    for src, hdr in creepfix.missing_headers(lines):
        rel = creepfix.rel_to_tree(src, w)
        p = creepfix.inside_tree(rel, w)
        if p is None:
            what.append(f"{rel}: outside the working copy {w}: refused, nothing written")
            continue
        rel = p.relative_to(w.resolve()).as_posix()
        if not p.is_file() or not creepfix.excised_header(hdr, excised):
            what.append(f"{rel}: {hdr} is not from an excised component: a person decides")
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        nl = "\r\n" if "\r\n" in raw else "\n"
        new, removed, leftover = creepfix.excise(raw.split(nl), hdr)
        if leftover or not creepfix.balanced(new):
            what.append(f"{rel}: uses of {hdr} at lines {leftover} are not self-contained: a person decides")
            continue
        p.write_text(nl.join(new), encoding="utf-8", newline="")
        ids = handedit.record(t["id"], [rel], f"excision creep: {hdr} belongs to a removed component; include and "
                                              f"{len(removed) - 1} self-contained use(s) excised by creepfix", group="creep")
        done.append(rel)
        what.append(f"{rel}: excised {hdr} + {len(removed) - 1} use(s) -> {ids}")
    say("  " + "; ".join(what)[:300])
    return bool(done) and len(done) == len(creepfix.missing_headers(lines)), "; ".join(what)


OBJ_MAGIC = (b"\x64\x86", b"\x4c\x01", b"\x00\x00\xff\xff", b"BC", b"!<ar", b"\x00asm")   # COFF x64/x86, anon COFF, bitcode, archive, wasm


def sweep_objects(objdir, say=print):
    """Delete object files a killed or reset build left half-written: zero bytes or no known header. mach rebuilds
    them; lld-link does not ("vp9itxfm.obj: unknown file type", 2026-10-02 07:56, a 0-byte object written at 07:37
    when the stage killed the tree mid-write). -> [paths removed]. ~1 s over 4,000 objects."""
    import os
    removed = []
    for dp, dn, fn in os.walk(str(objdir)):
        if "sccache" in dp or os.sep + ".git" in dp:
            continue
        for name in fn:
            if not name.endswith((".obj", ".o")):
                continue
            path = os.path.join(dp, name)
            try:
                size = os.path.getsize(path)
                with open(path, "rb") as fh:
                    head = fh.read(4)
            except OSError:
                continue
            if size == 0 or not any(head.startswith(m) for m in OBJ_MAGIC):
                os.remove(path)
                removed.append(path)
    if removed:
        say(f"  objdir sweep: removed {len(removed)} half-written object(s), e.g. {removed[0]}")
    return removed


def fix_corrupt_object(t, root, say, lines=()):
    from .compile import mozconfig_path, objdir as _objdir
    od = _objdir(mozconfig_path(root))
    if not od or not Path(od).is_dir():
        return False, "no objdir from the mozconfig"
    removed = sweep_objects(od, say)
    return True, f"removed {len(removed)} object(s) with no valid header; mach rebuilds them"


#: (regex over the stage output, name, fix or None). Order matters: first match wins.
JARMN_FILE = re.compile(r"The error occurred while processing the following file.*?\n\s*\n\s*(\S+moz\.build)", re.S)


def fix_jar_manifest(t, root, say, lines):
    """mozbuild refuses a jar.mn that exists in a directory whose moz.build no longer declares it (build 5, 02 Oct:
    the ml excision dropped `JAR_MANIFESTS += ["jar.mn"]`). The excision keeps: declare it again and empty the
    manifest (comment only), so nothing it listed is packaged. Recorded as a hand step."""
    from . import handedit
    m = JARMN_FILE.search("\n".join(lines))
    if not m:
        return False, "the moz.build path is not in the error text"
    mb = Path(m.group(1).replace("/", os.sep))
    w = Path(t["workdir"])
    try:
        rel = mb.resolve().relative_to(w.resolve()).as_posix()
    except ValueError:
        return False, f"{mb} is not inside the tree"
    jar = w / Path(rel).parent / "jar.mn"
    text = (w / rel).read_text(encoding="utf-8", errors="replace")
    if "JAR_MANIFESTS" not in text or re.search(r"^\s*#.*JAR_MANIFESTS", text, re.M):
        text = re.sub(r"^\s*#[^\n]*JAR_MANIFESTS[^\n]*\n", "", text, count=1, flags=re.M)
        text = text.rstrip("\n") + '\nJAR_MANIFESTS += ["jar.mn"]  # GORILLA: emptied, not dropped (mozbuild refuses an undeclared jar.mn)\n'
        (w / rel).write_text(text, encoding="utf-8")
    jar.write_text("# GORILLA excised: every entry (the directory's chrome content is not packaged).\n"
                   "# The file stays because mozbuild refuses a jar.mn that exists without a JAR_MANIFESTS declaration.\n", encoding="utf-8")
    ids = handedit.record(t["id"], [rel, (Path(rel).parent / "jar.mn").as_posix()],
                          "build stop jar-manifest-undeclared: declaration restored, manifest emptied")
    say(f"  {rel}: JAR_MANIFESTS declared again, jar.mn emptied ({len(ids)} hand step(s))")
    return True, f"{rel}: declared + emptied jar.mn"


def sweep_excised_dist(dist_bin, say):
    """Delete, under dist/bin, every path the proof's EXCISED_PACKAGED prefixes name. -> [removed]."""
    from .proof import EXCISED_PACKAGED
    import shutil
    removed = []
    for pre in EXCISED_PACKAGED:
        rel = pre.replace("chrome/toolkit/content/global/", "chrome/toolkit/content/global/").rstrip("/")
        # omni.ja entries live under dist/bin, browser/omni.ja entries under dist/bin/browser (2026-10-03: the
        # removed Alpenglow theme was packaged again from dist/bin/browser because only dist/bin was swept)
        for base in (Path(dist_bin), Path(dist_bin) / "browser"):
            p = base / rel
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                removed.append(str(p.relative_to(dist_bin)).replace("\\", "/") + "/")
            elif p.is_file():
                p.unlink()
                removed.append(str(p.relative_to(dist_bin)).replace("\\", "/"))
    if removed:
        say(f"  dist/bin sweep: {len(removed)} stale excised path(s) removed before packaging: {removed[:3]}")
    return removed


def fix_mozbuild_empty(t, root, say, lines):
    """mach: "Variable X assigned an empty value" -> the statement removed, recorded, retried."""
    from . import handedit
    from .mozbuild_rules import from_log, fix_empty_assignments
    path, var = from_log(lines)
    if not path:
        return False, "the moz.build path is not in the error text"
    w = Path(t["workdir"])
    try:
        rel = Path(path.replace("/", os.sep)).resolve().relative_to(w.resolve()).as_posix()
    except ValueError:
        return False, f"{path} is not inside the tree"
    fixed = fix_empty_assignments(w / rel)
    if not fixed:
        return False, f"{rel}: no empty assignment found for {var}"
    handedit.record(t["id"], [rel], f"build stop mozbuild-empty-assignment: {fixed} removed", kind="port")
    say(f"  {rel}: empty {fixed} assignment removed")
    return True, f"{rel}: {fixed}"


def fix_retry(t, root, say):
    say("  the stage was interrupted from the console (Ctrl+C / window closed): running it again")
    return True, "retry after a console interrupt"


def fix_thermal_cooldown(t, root, say, sleep=time.sleep):
    """2026-10-03 04:15: build 12 hit 79 C in 90 s because a 50%-CPU job ran beside it (idle was already 71 C), and
    the loop gave up with "no known fix". The cause is load and heat, not the tree: wait for the machine to go idle
    (other work finished), let it cool, then run the stage again. The governor still guards the retry."""
    busy = wait_for_idle(say, busy_max=20.0, timeout=3600, sleep=sleep)
    say(f"  thermal stop: machine at {busy if busy is None else round(busy)}% CPU; cooling for 5 minutes before the retry")
    sleep(300)
    return True, "retry after the machine went idle and cooled"


STOPS = [
    (r"lld-link: error: .*: unknown file type|error: .*\.obj: (file too small|invalid|corrupt)", "corrupt-object", fix_corrupt_object),
    (r"fatal error: '[^']+' file not found", "excision-creep-include", fix_creep_include),
    (r"\.mozbuild[/\\][\w.-]+[/\\].* does not exist|does not exist: .*\.mozbuild", "missing-toolchain", fix_toolchain),
    (r"Automatic clobber was not requested|clobber is required|requires a clobber|please clobber|CLOBBER file (was|has been) updated",
     "clobber-required", fix_clobber),
    (r"Refusing to start a build that cannot succeed", "owner-preflight-blocks", None),
    (r"duplicate\s+(message|term|attribute)|Duplicate (message|term)|is defined twice", "fluent-duplicate", fix_fluent),
    (r"Build failed with 3221225794|0xC0000142|STATUS_DLL_INIT_FAILED", "host-killed", None),   # the process tree was torn down from outside
    # 0xC000013A = STATUS_CONTROL_C_EXIT: a Ctrl+C / Ctrl+Break / console-window close reached the whole console
    # process group (2026-10-02, package stage: the build's console window was closed; the harness parent died with
    # it, silently). A retry is the right fix; the window is now hidden so there is nothing to close.
    (r"failed with 3221225786|0xC000013A|STATUS_CONTROL_C_EXIT", "console-interrupt", fix_retry),
    (r"A jar\.mn exists but it\s+is not referenced in the moz\.build file", "jar-manifest-undeclared", fix_jar_manifest),
    (r"Variable \w+ assigned an empty value", "mozbuild-empty-assignment", fix_mozbuild_empty),
    (r"Cannot find the target C compiler|clang-cl STILL not on PATH", "clang-cl-missing", None),
    (r"No space left on device|not enough space|ENOSPC", "disk-full", None),
    (r"Temperature stayed above|THERMAL ABORT|THERMAL WATCHDOG|THERMAL GOVERNOR|temperature source went static|does not respond to load", "thermal", fix_thermal_cooldown),
]


def stop_guidance(fix, attempt, repeat):
    """Why the loop gives up on a stop, always followed by what to do next (an unknown stop used to print only
    "no known fix": the guidance was bound to the other branch of a conditional expression)."""
    if fix is None:
        why = "no known fix"
    elif repeat:
        why = "the same stop again after its fix: no progress"
    else:
        why = f"still stopping after {attempt - 1} fix attempt(s)"
    return f"  {why}; the signature and the first errors are in the journal; write the tool, add it to STOPS, run again"


def classify(lines):
    text = "\n".join(lines)
    for rx, name, fix in STOPS:
        if re.search(rx, text, re.I):
            return name, fix
    return "unknown", None


# -- the loop ---------------------------------------------------------------------------------------------------

def owner_preflight_ok(root, say):
    """-> (may start, force needed, blockers). May start only when every blocker is build-dependent."""
    from .ownercheck import parse, run_preflight
    rc, text = run_preflight(root)
    blockers = parse(text) if rc is not None else []
    stale = [b for b in blockers if post_build(b["name"])]
    hard = [b for b in blockers if b not in stale]
    for b in blockers:
        say(f"  owner preflight: {b['name']}: {b['detail'][:90]}" + ("  (needs an objdir: --force)" if b in stale else "  (HARD: no build)"))
    return not hard, bool(stale), blockers


def cpu_busy_percent(sample=2.0):
    """System-wide CPU use over `sample` seconds (Windows GetSystemTimes), or None where unavailable."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        FT = ctypes.c_ulonglong
        def times():
            i, kk, u = FT(), FT(), FT()
            k.GetSystemTimes(ctypes.byref(i), ctypes.byref(kk), ctypes.byref(u))
            return i.value, kk.value + u.value
        i0, t0 = times()
        time.sleep(sample)
        i1, t1 = times()
        return 100.0 * (1 - (i1 - i0) / max(1, t1 - t0))
    except Exception:
        return None


def wait_for_idle(say, busy_max=35.0, timeout=600, sleep=time.sleep):
    """The proof loads the CPU and expects the sensor to rise; on a machine already busy (02 Oct 11:40: the test
    suite ran beside the gate) it cannot rise and the sensor reads as dead. Wait for the machine first, up to
    `timeout` s. -> busy percent at the end."""
    t0 = time.time()
    while True:
        busy = cpu_busy_percent()
        if busy is None or busy <= busy_max or time.time() - t0 > timeout:
            return busy
        say(f"  thermal proof: machine busy ({busy:.0f}% CPU) - waiting for it to settle before loading it")
        sleep(20)


PROOF_CAP = 60          # % max processor state while the sensor proof loads the cores (it runs before the governor)
PROOF_REUSE_S = 12 * 3600


def _proof_cache():
    return task.STATE / "thermal-proof.json"


def _boot_time():
    try:
        import psutil
        return int(psutil.boot_time())
    except Exception:
        return None


def thermal_sensor(say, retry=True):
    """The first CPU sensor proven against load (fieldkit.thermal.sensors.best). -> (name, fn) or (None, None).
    2026-10-03 14:24: the proof itself (every core at 100%, uncapped, 80 s, a lagging surface sensor) reset the laptop.
    Now: a sensor proven this boot is reused for 12 h; a new proof runs with the processor capped and stops at a ceiling."""
    from ..thermal import sensors, governor
    import json as _json
    c = _proof_cache()
    try:
        prev = _json.loads(c.read_text(encoding="utf-8"))
    except Exception:
        prev = None
    fns = dict(sensors.PROVIDERS)
    if prev and prev.get("boot") == _boot_time() and time.time() - prev.get("when", 0) < PROOF_REUSE_S and prev.get("name") in fns \
            and fns[prev["name"]]() is not None:
        name, fn, detail = prev["name"], fns[prev["name"]], prev["detail"] + " (proven earlier this boot; not re-stressed)"
        say(f"  thermal source: {name} - {detail[:200]}")
        surface = "[surface sensor]" in detail
        if surface:
            say(f"  only a surface sensor: the compile runs with the cap held at {SURFACE_CAP}% of base (no die reading on this machine)")
        return name, fn, surface
    busy = wait_for_idle(say)
    before = governor.read_cap()
    governor.set_cap(PROOF_CAP)
    try:
        name, fn, detail = sensors.best(prove=True, settle=8, samples=6, interval=2.0)
        if name is None and retry:
            # one more try after a minute: a plateau from earlier load needs time to fall before a rise can show
            say(f"  thermal proof failed once (busy {busy if busy is None else round(busy)}%); waiting 60 s and proving again")
            time.sleep(60)
            name, fn, detail = sensors.best(prove=True, settle=8, samples=6, interval=2.0)
    finally:
        governor.set_cap(before if before else 100)
    if name:
        try:
            c.write_text(_json.dumps({"boot": _boot_time(), "when": time.time(), "name": name, "detail": detail}), encoding="utf-8")
        except OSError:
            pass
    say(f"  thermal source: {name or 'NONE'} - {detail[:200]}")
    surface = "[surface sensor]" in (detail or "")
    if surface:
        say(f"  only a surface sensor: the compile runs with the cap held at {SURFACE_CAP}% of base (no die reading on this machine)")
    return name, fn, surface


def run(task_id, force=False, say=print, stages=("build", "package")):
    """-> {"ok", "stops": [...], "log"}. `force` only allows build-dependent owner blockers through; a hard one stops."""
    from . import compile as cg
    t = task.load(task_id)
    root = _owner_root(t)
    if not root:
        return {"ok": False, "why": "no owner harness beside this task"}
    log_path = task.STATE / task_id / f"build-{time.strftime('%Y%m%d-%H%M%S')}.log"
    stops = []
    rows = cg.gate(task_id, harness_root=None, write=True)
    bad = [r for r in rows if not r["ok"]]
    stale = lambda r: r["check"].startswith("final checks passed") or (r["check"] == "every step is done" and "final-checks" in r["evidence"])
    if bad and all(stale(r) for r in bad):
        # the only thing missing is a final re-check after the last change: run it (a script step, no model)
        say("gate: final checks are stale - re-running them")
        task.unblock(task_id, "final-checks", "retry")
        task.advance(task_id)
        rows = cg.gate(task_id, harness_root=None, write=True)
        bad = [r for r in rows if not r["ok"]]
    repairable = lambda r: "JS file parses" in r["check"] or "UPPER_CASE member" in r["check"]
    if bad and all(repairable(r) for r in bad):
        # the two port failures of 02 Oct have a deterministic repair: run it once, then judge the gate again
        from . import repair
        say("gate: a JS module does not parse or reads a moved member - running repair")
        rep = repair.run(task_id, say=say)
        task.journal(task.load(task_id), "gate-repair", repaired=rep["repaired"][:10], refused=rep["refused"][:10])
        rows = cg.gate(task_id, harness_root=None, write=True)
        bad = [r for r in rows if not r["ok"]]
    if bad:
        say("BUILD GATE FAILED: " + "; ".join(f"{r['check']}: {r['evidence']}" for r in bad[:3]))
        return {"ok": False, "why": "build gate", "rows": rows}
    say("build gate passed")
    may, needs_force, blockers = owner_preflight_ok(root, say)
    if not may:
        return {"ok": False, "why": "the owner's preflight reports a blocker a build cannot resolve", "blockers": blockers}
    if needs_force and not force:
        return {"ok": False, "why": "only build-dependent owner blockers remain; run again with --force to pass them on", "blockers": blockers}
    # 2026-10-02: no build without a CPU sensor proven to move under load (the laptop reset with a blind governor)
    sensor_name, sensor, surface = thermal_sensor(say)
    if sensor is None:
        task.journal(t, "build-refused", why=["no CPU temperature source responds to load: see fieldkit thermal prove"])
        return {"ok": False, "why": "no CPU temperature source responds to load; the build would run blind (fieldkit thermal prove)"}
    task.journal(t, "build-start", head=cg._git(Path(t["workdir"]), "rev-parse", "HEAD"), log=str(log_path),
                 forced=[b["name"] for b in blockers] if needs_force else [], thermal_source=sensor_name)
    try:
        return _stages(t, task_id, root, stages, needs_force, sensor_name, sensor, surface, stops, log_path, say)
    except KeyboardInterrupt:
        task.journal(task.load(task_id), "interrupted", why=["console interrupt (Ctrl+C / Ctrl+Break / window closed) reached the harness"])
        say("INTERRUPTED from the console; journaled")
        return {"ok": False, "why": "interrupted from the console", "stops": stops, "log": str(log_path)}


def _stages(t, task_id, root, stages, needs_force, sensor_name, sensor, surface, stops, log_path, say):
    from . import compile as cg
    for stage in stages:
        if stage == "package":
            # a jar.mn that loses entries leaves the files it once installed in dist/bin, and the packager ships
            # them (build 8, 02 Oct: 26 ML modules in omni.ja with an empty ml/jar.mn). Sweep the excised prefixes.
            from .compile import mozconfig_path, objdir as _objdir
            od = _objdir(mozconfig_path(root))
            if od:
                swept = sweep_excised_dist(Path(od) / "dist" / "bin", say)
                if swept:
                    task.journal(t, "dist-sweep", removed=swept[:20])
            # the installer's outer icon lives in a vendored 7-Zip stub consumed at package time: brand it first
            from . import icons
            ok, text = icons.brand_installer_stub(root, say)
            say(f"  installer stub icon: {'ok' if ok else 'NOT branded' if ok is False else 'tool missing'}")
            task.journal(t, "installer-stub", ok=ok, what=(text or "")[-300:])
        for attempt in range(1, RETRIES + 2):
            cmd = [sys.executable, str(Path(root) / "harness" / "gorilla_build.py"), stage] + (["--force"] if stage == "build" and needs_force else [])
            say(f"{time.strftime('%H:%M:%S')}  {stage} attempt {attempt}: {' '.join(cmd[1:])}")
            if stage == "build":
                from .compile import mozconfig_path, objdir as _objdir
                od = _objdir(mozconfig_path(root))
                if od and Path(od).is_dir():
                    removed = sweep_objects(od, say)
                    if removed:
                        task.journal(t, "objdir-sweep", removed=len(removed), first=removed[0])
            power = active_power_scheme()
            from ..thermal import governor as gv
            gov = gv.Governor(sensor, target_c=75.0, interval=3.0, kill=lambda why: None, on_event=say,
                              csv_path=task.STATE / task_id / f"thermal-{stage}-{time.strftime('%Y%m%d-%H%M%S')}.csv",
                              max_cap=SURFACE_CAP if surface else None, dead_check=not surface)
            rc, lines = _stream(cmd, root, log_path, say, governor=gov, watch_thermal=not surface)
            if gov.verdict:
                lines.append(f"FIELDKIT THERMAL GOVERNOR: {gov.verdict}")
                task.journal(t, "thermal-kill", stage=stage, attempt=attempt, why=[gov.verdict], peak=gov.peak)
            else:
                task.journal(t, "thermal", stage=stage, attempt=attempt, source=sensor_name, peak=gov.peak, samples=gov.samples,
                             floor_cap=min([gov.cap or 100, gov.start_cap or 100]))
            if restore_power_scheme(power, say):
                task.journal(t, "power-scheme-restored", stage=stage, attempt=attempt, to=power[1])
            if rc == 0:
                say(f"{time.strftime('%H:%M:%S')}  {stage} OK ({len(lines)} lines)")
                task.journal(t, "build-stage-done", stage=stage, attempt=attempt, lines=len(lines))
                break
            name, fix = classify(lines)
            errs = error_lines(lines)
            stop = {"stage": stage, "attempt": attempt, "rc": rc, "signature": name, "errors": errs}
            repeat = bool(stops) and stops[-1]["signature"] == name and stops[-1]["stage"] == stage
            stops.append(stop)
            task.journal(t, "build-stop", **stop)
            say(f"{time.strftime('%H:%M:%S')}  {stage} STOPPED rc={rc}: {name}")
            for e in errs[:5]:
                say("    " + e)
            if fix is None or attempt > RETRIES or repeat:
                say(stop_guidance(fix, attempt, repeat))
                return {"ok": False, "stops": stops, "log": str(log_path)}
            ok, what = fix(t, root, say, lines) if fix in (fix_fluent, fix_toolchain, fix_creep_include, fix_corrupt_object, fix_jar_manifest, fix_mozbuild_empty) else fix(t, root, say)
            t = task.load(task_id)                      # a fix may have recorded steps and checkpointed
            task.journal(t, "build-fix", stage=stage, signature=name, ok=ok, what=what)
            if not ok:
                return {"ok": False, "stops": stops, "log": str(log_path)}
        else:
            return {"ok": False, "stops": stops, "log": str(log_path)}
    rows = cg.verify(task_id)
    for r in rows:
        say(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['check']}: {r['evidence']}")
    task.journal(t, "build-verified", ok=all(r["ok"] for r in rows))
    return {"ok": all(r["ok"] for r in rows), "stops": stops, "log": str(log_path), "rows": rows}
