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


def _stream(cmd, cwd, log_path, say):
    """Run `cmd`, append its output to log_path, print a heartbeat. -> (rc, lines)."""
    lines, last_beat, started = [], time.time(), time.time()
    with open(log_path, "a", encoding="utf-8", newline="\n") as lf:
        lf.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} $ {' '.join(cmd)}\n")
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
        for line in proc.stdout:
            lf.write(line)
            lines.append(line.rstrip("\n"))
            if time.time() - last_beat >= HEARTBEAT_S:
                say(f"  ... {int((time.time() - started) // 60)} min, {len(lines)} lines, last: {line.strip()[:110]}")
                last_beat = time.time()
        rc = proc.wait()
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


MISSING_TOOLCHAIN = re.compile(r"does not exist: .*?[/\\]\.mozbuild[/\\]([\w.-]+)[/\\]")
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
    have fetched (Firefox 157 added winappsdk-x86_64-pc-windows-msvc). Fetched with
    `mach artifact toolchain --from-build <job>`, the job found by its alias in taskcluster/kinds/toolchain."""
    text = "\n".join(lines)
    m = MISSING_TOOLCHAIN.search(text)
    if not m:
        return False, "no ~/.mozbuild/<toolchain>/ path in the error"
    alias = m.group(1)
    src = Path(t["workdir"])
    job = toolchain_job(src, alias)
    if not job:
        return False, f"no toolchain job carries the alias {alias}"
    if not MOZBUILD_BASH.is_file():
        return False, f"no {MOZBUILD_BASH}"
    env = _env()
    env["MOZILLABUILD"] = r"C:\mozilla-build"
    from .compile import mozconfig_path
    env["MOZCONFIG"] = str(mozconfig_path(root))
    msys = "/" + str(src).replace(":", "").replace("\\", "/")
    r = subprocess.run([str(MOZBUILD_BASH), "-l", "-c", f"cd {msys} && ./mach artifact toolchain --from-build {job}"],
                       cwd=str(src), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    have = (Path.home() / ".mozbuild" / alias).is_dir()
    say(f"  mach artifact toolchain --from-build {job}: exit {r.returncode}; ~/.mozbuild/{alias} {'present' if have else 'STILL MISSING'}")
    return have, f"toolchain {alias} via {job}: exit {r.returncode}"


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


#: (regex over the stage output, name, fix or None). Order matters: first match wins.
STOPS = [
    (r"\.mozbuild[/\\][\w.-]+[/\\].* does not exist|does not exist: .*\.mozbuild", "missing-toolchain", fix_toolchain),
    (r"Automatic clobber was not requested|clobber is required|requires a clobber|please clobber|CLOBBER file (was|has been) updated",
     "clobber-required", fix_clobber),
    (r"Refusing to start a build that cannot succeed", "owner-preflight-blocks", None),
    (r"duplicate\s+(message|term|attribute)|Duplicate (message|term)|is defined twice", "fluent-duplicate", fix_fluent),
    (r"Cannot find the target C compiler|clang-cl STILL not on PATH", "clang-cl-missing", None),
    (r"No space left on device|not enough space|ENOSPC", "disk-full", None),
    (r"Temperature stayed above", "thermal-abort", None),
]


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
    if bad:
        say("BUILD GATE FAILED: " + "; ".join(f"{r['check']}: {r['evidence']}" for r in bad[:3]))
        return {"ok": False, "why": "build gate", "rows": rows}
    say("build gate passed")
    may, needs_force, blockers = owner_preflight_ok(root, say)
    if not may:
        return {"ok": False, "why": "the owner's preflight reports a blocker a build cannot resolve", "blockers": blockers}
    if needs_force and not force:
        return {"ok": False, "why": "only build-dependent owner blockers remain; run again with --force to pass them on", "blockers": blockers}
    task.journal(t, "build-start", head=cg._git(Path(t["workdir"]), "rev-parse", "HEAD"), log=str(log_path),
                 forced=[b["name"] for b in blockers] if needs_force else [])
    for stage in stages:
        for attempt in range(1, RETRIES + 2):
            cmd = [sys.executable, str(Path(root) / "harness" / "gorilla_build.py"), stage] + (["--force"] if stage == "build" and needs_force else [])
            say(f"{time.strftime('%H:%M:%S')}  {stage} attempt {attempt}: {' '.join(cmd[1:])}")
            power = active_power_scheme()
            rc, lines = _stream(cmd, root, log_path, say)
            if restore_power_scheme(power, say):
                task.journal(t, "power-scheme-restored", stage=stage, attempt=attempt, to=power[1])
            if rc == 0:
                say(f"{time.strftime('%H:%M:%S')}  {stage} OK ({len(lines)} lines)")
                task.journal(t, "build-stage-done", stage=stage, attempt=attempt, lines=len(lines))
                break
            name, fix = classify(lines)
            errs = error_lines(lines)
            stop = {"stage": stage, "attempt": attempt, "rc": rc, "signature": name, "errors": errs}
            stops.append(stop)
            task.journal(t, "build-stop", **stop)
            say(f"{time.strftime('%H:%M:%S')}  {stage} STOPPED rc={rc}: {name}")
            for e in errs[:5]:
                say("    " + e)
            if fix is None or attempt > RETRIES:
                say("  no known fix: the signature and the first errors are in the journal; write the tool, add it to STOPS, run again")
                return {"ok": False, "stops": stops, "log": str(log_path)}
            ok, what = fix(t, root, say, lines) if fix in (fix_fluent, fix_toolchain) else fix(t, root, say)
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
