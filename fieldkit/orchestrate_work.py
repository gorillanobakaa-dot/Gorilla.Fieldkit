"""Jobs on a received tree: the boss sends a folder and a command, a worker runs it, the artefacts come back.

    fieldkit orchestrate job run --device W --tree DIR --project REPO [--artefact GLOB ...] [--wall M] [--wait S]
                                 -- FIELDKIT ARGS ...        a Fieldkit command line, run on a copy of DIR there
    fieldkit orchestrate job run --plain --device W --tree DIR ... -- PROGRAM ARGS ...
                                                              no AI, no Fieldkit command: a build or test program
                                                              (make, go, cargo, pytest...: PROGRAMS) on a copy of DIR
    fieldkit orchestrate job agent --device W --tree DIR --project REPO --brief FILE [--model ID] [--brain URL]
                                 [--artefact GLOB ...] [--wall M] [--wait S]
                                                              Gorilla OpenCode headless with the brief: the model
                                                              is one the WORKER's Gorilla OpenCode has (default a
                                                              cheap Gemini Flash); --brain adds an OpenAI-compatible
                                                              URL for a worker with no provider of its own
    fieldkit orchestrate job status                           the ledger: every job, its result and concerns
    fieldkit orchestrate work JOB-ID                          (the worker, started by its listener: not for people)

Stage O3 of the household orchestrator (local/PROJECT-MANDATE-PLAN.md section 19). What it adds to the job protocol
of fieldkit/mandate/jobs.py (one boss, many workers; the owner's mandate is the only approval):
  - the material travels with the job: the tree goes first with `peers link put` (stage O2: streamed, sha256 per
    file), and the job names it by the digest both ends compared, so the worker runs exactly those bytes;
  - the worker runs on a COPY of the received tree (received/ is never changed), one job at a time, with a wall
    cap, awake, after a free-space and clock check. It never needs the internet: nothing reaches GitHub;
  - the job runs in a container (fieldkit/core/contain.py): a Job Object on Windows, its own process group on Linux
    and macOS, so the wall cap stops the WHOLE tree, orphans included; its output goes to job.log through a file,
    never a pipe; no output and no CPU for STALL_MINUTES is a STALL, stopped long before the wall cap with the tail
    of its output kept on disk and a ROOM line; what the job leaves running when it ends is stopped and reported.
    Before (2026-10-26): a plain subprocess.run with timeout = the wall cap, so a hung `go test` on the VAIO would
    have sat silent for the whole hour and left its children behind. A job on a worker can never hang silently;
  - `run` is a Fieldkit command line with no shell; the commands that approve, push, publish or start agents are
    refused (REFUSED). `run --plain` (HARNESS-PLAN C3 a, 2026-10-28) is a build or test program from PROGRAMS, with
    no shell and without the subcommands that publish or log in: the plain heavy work a device with no local model
    (the VAIO, the Mac) takes. Its result is checked the same way: exit code, the test count of a test job (pytest,
    go test, cargo test, ctest), every artefact's sha256 and the log tail. The tree's own build files (a Makefile)
    run as the worker's user, like any job on that tree. `agent` is a fresh Gorilla OpenCode run with the brief, on
    the model the job names, through the worker's own configured provider (owner, 2026-10-26: cheap Gemini for
    tests, never a local model for O3); the model is pinned in the tree copy's .gorilla-opencode.json, the only thing a project folder may set. With
    --brain (an OpenAI-compatible URL) the run gets a profile that knows only that endpoint, and a model it loaded
    on this computer is unloaded afterwards (owner rule: an idle 43 GB model woke him);
  - every artefact goes back to the boss with put, and its name, size and sha256 are in the result the worker
    signs with its own key; the boss checks the files it got against them (`artefacts_check`).
Jobs travel on the cable only (link.JOB_VERBS), until the mandate gate covers the LAN. Not done yet (security is
for later, section 19): an agent run has the worker's normal Gorilla OpenCode tools, so it can write outside the tree
copy as the worker's user could.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import review_verdict
from .mandate import jobs
from .peers import link

REFUSED = {"mandate", "ship", "publish", "release", "release-cycle", "autopilot", "peers", "inbox", "orchestrate",
           "agent", "elevate", "build-harness"}       # approvals, pushes, publishing, sessions, admin, the harness
CMD = re.compile(r"[a-z][a-z0-9-]{0,40}")
# run --plain: the build and test programs a job line may start, each with the first arguments refused (they publish,
# log in, install outside the tree or run any code). No shell, no git, no ssh, no curl
PROGRAMS = {
    "make": (), "gmake": (), "ninja": (), "cmake": (), "ctest": (), "meson": (), "msbuild": (),
    "go": (), "gofmt": (),
    "cargo": ("publish", "login", "logout", "owner", "yank", "install"),
    "dotnet": ("nuget", "tool"),
    "npm": ("publish", "unpublish", "login", "logout", "adduser", "owner", "token", "deprecate", "dist-tag", "exec",
            "x"),
    "pytest": (), "py.test": (),
    "python": (), "python3": (), "py": (),
}
PY_MODULES = ("pytest", "unittest", "compileall", "py_compile", "build")   # python -m: the ones a plain job may run
MAX_ARTEFACTS = 50
MAX_BRIEF = 32 * 1024
DEFAULT_MODEL = "gemini-flash-lite-latest"            # cheap Gemini, the owner's model for tests (2026-10-26)
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,120}")
SHA256 = re.compile(r"[0-9a-f]{64}")
STALL_MINUTES = 10                                    # FM-9: no output and no CPU for this long = a silent stall, stopped


def work_root():
    return link.home() / "work"


def _job_path(jid):
    return work_root() / jid / "job.json"


def receipt_for(digest):
    """-> the newest receipt of a tree that arrived here with that digest (peers link put), or None."""
    d = link.home() / "receipts"
    for p in sorted(d.glob("*.json"), reverse=True) if d.is_dir() else []:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("digest") == digest and Path(r.get("saved", "")).exists():
            return r
    return None


def check_work(req):
    """-> why this run/agent job cannot be taken, or None."""
    kind = req.get("kind")
    if not SHA256.fullmatch(str(req.get("tree_digest") or "")):
        return "a run or agent job names its tree by the 64-hex digest `peers link put` reported"
    pats = req.get("artefacts") or []
    if not isinstance(pats, list) or len(pats) > 20 or any(
            not isinstance(g, str) or link_transfer_bad(g) for g in pats):
        return "artefacts: at most 20 relative glob patterns inside the tree (no .., no absolute path)"
    try:
        wall = int(60 if req.get("wall_minutes") is None else req["wall_minutes"])
    except (TypeError, ValueError):
        wall = 0
    if not 1 <= wall <= 1440:
        return "wall_minutes: 1 to 1440"
    if kind == "run":
        argv = req.get("argv")
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) and "\x00" not in a for a in argv)):
            return "run: argv is a list of strings, the Fieldkit command line without `fieldkit`"
        if req.get("plain"):
            return check_plain(argv)
        if not CMD.fullmatch(argv[0]):
            return f"run: {argv[0]!r} is not a Fieldkit command"
        if argv[0] == "admit" and argv[1:2] != ["suite"]:
            return "run: of `fieldkit admit` only `admit suite` is a job (admit -- CMD would run any command)"
        if argv[0] in REFUSED:
            return f"run: `fieldkit {argv[0]}` is not run as a job (it approves, pushes, publishes or starts agents)"
    elif kind == "agent":
        brief = req.get("brief")
        if not isinstance(brief, str) or not brief.strip() or len(brief) > MAX_BRIEF:
            return f"agent: a brief of 1 to {MAX_BRIEF} characters"
        if req.get("brain") and not re.match(r"^https?://[^\s/]+", str(req.get("brain"))):
            return "agent: brain, when given, is the http(s) URL of an OpenAI-compatible endpoint"
        if not MODEL.fullmatch(str(req.get("model") or DEFAULT_MODEL)):
            return "agent: model is a model id the worker's Gorilla OpenCode knows (e.g. gemini-flash-lite-latest)"
    else:
        return f"{kind!r} is not a job on a tree"
    return None


def _bare(prog):
    return prog[:-4] if prog.lower().endswith(".exe") else prog


def check_plain(argv):
    """-> why this plain program line is not a job, or None (PROGRAMS, PY_MODULES)."""
    name = _bare(argv[0])
    if "/" in name or "\\" in name or name not in PROGRAMS:
        return (f"run --plain: {argv[0]!r} is not a build or test program a job may start (a bare name, one of "
                f"{', '.join(sorted(PROGRAMS))})")
    if argv[1:2] and argv[1] in PROGRAMS[name]:
        return f"run --plain: `{name} {argv[1]}` is not run as a job (it publishes, logs in or runs any code)"
    if name in ("python", "python3", "py") and (argv[1:2] != ["-m"] or argv[2:3] == [] or argv[2] not in PY_MODULES):
        return f"run --plain: python runs only `-m` with one of {', '.join(PY_MODULES)}"
    return None


def link_transfer_bad(pattern):
    from .peers import link_transfer
    return link_transfer.bad_path(pattern.replace("*", "x").replace("?", "x").replace("[", "x").replace("]", "x"))


def accept(req, mpath, lock, cap=3600, popen=subprocess.Popen):
    """The worker's side, inside jobs.handle_job after the mandate and boss checks. -> the answer to the boss."""
    why = check_work(req)
    if why:
        return {"ok": False, "error": why}
    rec = receipt_for(req["tree_digest"])
    if rec is None:
        return {"ok": False, "error": f"no tree with digest {req['tree_digest'][:12]} arrived here (send it first: "
                                      "fieldkit peers link put DIR)"}
    if jobs.busy(lock, cap):
        return {"ok": False, "error": "a job is running here already: one at a time"}
    jid = req["id"]
    d = work_root() / jid
    d.mkdir(parents=True, exist_ok=True)
    job = {k: req.get(k) for k in ("id", "kind", "repo", "tree_digest", "argv", "plain", "brief", "brain", "model",
                                   "artefacts", "wall_minutes", "from_host")}
    job.update(mandate_path=str(mpath), tree=rec["saved"], tree_kind=rec.get("kind"), lock=str(lock),
               accepted=time.strftime("%Y-%m-%d %H:%M:%S"))
    _job_path(jid).write_text(json.dumps(job, indent=1, ensure_ascii=False), encoding="utf-8")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{jid} {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    popen([sys.executable, "-P", "-m", "fieldkit", "orchestrate", "work", jid], env=link.link_env(),
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
          **({"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}))
    return {"ok": True, "started": jid}


def run_argv(args, platform=None, plain=False, path=None):
    """The command a run job starts. On Windows it always runs inside `fieldkit guard --` (owner rule: every
    compile job on the Windows laptop goes through the fan check and the build governor), whatever the boss sent.
    plain: args[0] is a program, found on `path` (the job's PATH); a `python` is the worker's own Python (the O3
    false PASS ran a /usr/bin/python3 with no pytest). Raises RuntimeError when the program is not on this worker."""
    me = [sys.executable, "-P", "-m", "fieldkit"]
    if plain:
        name = _bare(args[0])
        exe = sys.executable if name in ("python", "python3", "py") else shutil.which(args[0], path=path)
        if not exe:
            raise RuntimeError(f"{args[0]} is not installed on this worker (not on its PATH)")
        cmd = [exe, *args[1:]]
    else:
        cmd = [*me, *args]
    if (platform or sys.platform) == "win32":
        return [*me, "guard", "--", *cmd]
    return cmd


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _tail(path, n=15, chars=1500):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])[-chars:]


def evidence_tail(log, guard_log=None):
    """The command's last lines (the pytest summary) first, then the last lines of the guard's report: the guard's
    preflight once filled the whole tail and the inner output never showed (C3/I6, 2026-10-28)."""
    inner = _tail(log, 15, 1250)
    g = _tail(guard_log, 3, 450) if guard_log else ""
    return inner + (f"\n--- the guard\n{g}" if g else "")


PYTEST_SUMMARY = re.compile(r"\b(\d+ (?:passed|failed|errors?|skipped)|no tests ran)\b.* in [\d.]+s")
REGRESS_LINE = re.compile(r": (\d+) passed, (\d+) failed \((\d+) test names read; exit (-?\d+|None)\)")


GO_OK = re.compile(r"^ok\s+\S+(?!.*\[no tests to run\])")
GO_FAIL = re.compile(r"^(?:FAIL\s+\S+|--- FAIL: )")
CARGO_LINE = re.compile(r"^test result: \w+\. (\d+) passed; (\d+) failed;")
CTEST_LINE = re.compile(r"\d+% tests passed, (\d+) tests? failed out of (\d+)")


def is_test_job(argv):
    """A run job whose point is to run tests: `admit suite`, `regress record`, pytest anywhere in the line, or (a
    plain job) `go test`, `cargo test`, `ctest`."""
    argv = [str(a) for a in argv or []]
    base = [_bare(a.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]) for a in argv]
    return argv[:2] == ["admit", "suite"] or argv[:2] == ["regress", "record"] or any(
        b.startswith("pytest") for b in base) or base[:2] in (["go", "test"], ["cargo", "test"]) or base[:1] == [
        "ctest"]


def test_counts(text):
    """-> {passed, failed, errors} from the LAST pytest summary or `regress record` line in the output, or None."""
    found = None
    for ln in text.splitlines():
        m = REGRESS_LINE.search(ln)
        if m:
            p, f, n, ex = m.groups()
            found = {"passed": int(p), "failed": int(f), "errors": 0 if ex == "0" else 1, "read": int(n)}
        elif PYTEST_SUMMARY.search(ln):
            c = {k: 0 for k in ("passed", "failed", "errors")}
            for num, word in re.findall(r"(\d+) (passed|failed|errors?)\b", ln):
                c["errors" if word.startswith("error") else word] += int(num)
            found = c
    return found if found is not None else other_counts(text)


def other_counts(text):
    """-> {passed, failed, errors} summed over go test (packages), cargo test and ctest summary lines, or None."""
    c, seen = {"passed": 0, "failed": 0, "errors": 0}, False
    for ln in text.splitlines():
        cargo, ctest = CARGO_LINE.search(ln), CTEST_LINE.search(ln)
        if GO_OK.search(ln):
            c["passed"] += 1
        elif GO_FAIL.search(ln):
            c["failed"] += 1
        elif cargo:
            c["passed"] += int(cargo[1])
            c["failed"] += int(cargo[2])
        elif ctest:
            c["passed"] += int(ctest[2]) - int(ctest[1])
            c["failed"] += int(ctest[1])
        else:
            continue
        seen = True
    return c if seen else None


def judge(kind, argv, rc, output, declared):
    """The evidence rule (O3 false PASS, 2026-10-26: two `regress record ... -- python3 -m pytest` jobs said "exit 0
    in 0 s" and PASS while that python3 had no pytest and nothing was tested). -> (passes, [concern]). Fixed rules:
      - an exit code other than 0 fails;
      - a run job that printed nothing and declared no artefact fails: nothing shows the command did anything;
      - a test job (is_test_job) passes only when its output ends in a test count with at least one test passed and
        none failed or errored; "0 test names read", "no tests ran" or no count at all is invalid evidence."""
    if rc != 0:
        return False, []
    if kind != "run":
        return True, []
    if not output.strip() and not declared:
        return False, [{"text": "exit 0 with no output and no declared artefact: nothing shows the command ran",
                        "fix_offered": "declare the file the job makes with --artefact, or run a command that "
                                       "reports what it did"}]
    if is_test_job(argv):
        c = test_counts(output)
        if c is None or c["passed"] < 1:
            return False, [{"text": "the tests never ran: no test passed"
                                    + (f" ({c.get('read', 0)} test names read)" if c and "read" in c else
                                       " (no test count in the output)" if c is None else ""),
                            "fix_offered": "check the job log: pytest missing for that Python, or 0 tests "
                                           "collected"}]
        if c["failed"] or c["errors"]:
            return False, [{"text": f"{c['failed']} test(s) failed, {c['errors']} error(s)",
                            "fix_offered": "fix the failing tests"}]
    return True, []


AGENTS = ("coder", "task", "title", "summarizer")


def pin_model(tree, model):
    """The model for this run, in the tree copy's own folder settings (Gorilla OpenCode lets a project folder choose
    its models and nothing else); the worker's provider keys stay in its own config, never copied."""
    (Path(tree) / ".gorilla-opencode.json").write_text(
        json.dumps({"agents": {a: {"model": model} for a in AGENTS}}, indent=1), encoding="utf-8")


def agent_profile(d, brain):
    """--brain: a Gorilla OpenCode config that knows only that endpoint (XDG_CONFIG_HOME for the run)."""
    root = d / "profile"
    c = root / "gorilla-opencode"
    c.mkdir(parents=True, exist_ok=True)
    cfg = {"localEndpoints": [{"name": "brain", "baseURL": brain}], "extrasChoiceMade": True, "mcpServers": {}}
    (c / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    return root


def opencode_exe():
    exe = shutil.which("gorilla-opencode")
    if not exe and os.environ.get("LOCALAPPDATA"):
        cand = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Gorilla OpenCode" / "gorilla-opencode.exe"
        exe = str(cand) if cand.is_file() else None
    return exe


def unload_local_models(brain, run=subprocess.run):
    """Owner rule: a model the job loaded is unloaded when it ends. -> what was done (one line)."""
    host = re.sub(r"^https?://", "", brain).split("/")[0].split(":")[0]
    if host not in ("127.0.0.1", "localhost", "::1"):
        return f"brain {host} is another computer: its models are its owner's to unload"
    lms = shutil.which("lms") or next((str(p) for p in (Path.home() / ".lmstudio" / "bin" / "lms.exe",
                                                       Path.home() / ".lmstudio" / "bin" / "lms") if p.is_file()), None)
    if not lms:
        return "NOT UNLOADED: no lms command here (unload the model in LM Studio)"
    try:
        r = run([lms, "unload", "--all"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"NOT UNLOADED: lms unload --all failed ({e})"
    return "unloaded: lms unload --all" if r.returncode == 0 else \
        f"NOT UNLOADED: lms unload --all exit {r.returncode}: {(r.stderr or r.stdout).strip()[:160]}"


def stall_s():
    """The stall limit in seconds: FIELDKIT_IDLE_MINUTES (the same knob as autopilot claude), else STALL_MINUTES."""
    return float(os.environ.get("FIELDKIT_IDLE_MINUTES") or STALL_MINUTES) * 60


def run_contained(argv, cwd, env, log, deadline_s, idle_s, label):
    """The job's command in a container (fieldkit/core/contain.py). -> contain.Outcome.
    The container's output is APPENDED to log (stdout and stderr merged), through a file, never a pipe; the wall
    deadline and the stall limit stop the whole tree; the worker is a detached process, so it opens its own liveness
    beacon when none is current (fieldkit liveness check sees the job, its step and its members)."""
    from .core import contain, liveness
    own = liveness.beacon("job", label, label).start() if liveness.current() is None else None
    try:
        return contain.run([str(a) for a in argv], label=label, deadline_s=deadline_s, cwd=str(cwd), env=env,
                           stdout_path=str(log), merge=True, idle_s=idle_s)
    finally:
        if own:
            own.close()


def job_concerns(o, wall, idle_s):
    """What a contained run's Outcome says beyond its exit code, as concerns for the boss. -> (rc, [concern])."""
    out = []
    if o.status == "timeout":
        out.append({"text": f"stopped at the wall cap ({wall // 60} min): the container stopped "
                            f"{len(o.killed)} process(es)", "fix_offered": "send it again with a larger --wall"})
    elif o.status == "stalled":
        out.append({"text": f"STALL: no output and no CPU for {idle_s / 60:g} min; the container stopped "
                            f"{len(o.killed)} process(es); output tail kept at {o.tail_file}",
                    "fix_offered": "read the tail; a job that waits on a person or a lock here cannot run as a job"})
    if o.leftovers:
        out.append({"text": f"{len(o.leftovers)} process(es) the job left running were stopped: {o.leftovers}",
                    "fix_offered": "a job ends with its children; check what it started"})
    if not o.contained:
        out.append({"text": f"NOT CONTAINED: {o.why}; only the tree kill held",
                    "fix_offered": "fieldkit liveness doctor on the worker"})
    rc = o.rc if o.status in ("ok", "fail") else None
    return rc, out


def work(jid, run=None, put=None, deliver=None, sign=None, health_fn=None, unload=None, reviewer=None):
    """The job itself (a detached process on the worker). -> the result, delivered to the boss.
    run: the contained runner (run_contained): (argv, cwd, env, log, deadline_s, idle_s, label) -> contain.Outcome."""
    run = run or run_contained
    unload = unload or unload_local_models
    put = put or link.put
    deliver = deliver or jobs.deliver
    sign = sign or jobs.sign_result
    job = json.loads(_job_path(jid).read_text(encoding="utf-8"))
    d = work_root() / jid
    tree = d / "tree"
    log = d / "job.log"
    guard_log = d / "guard.log"
    concerns, t0, rc, verdict = [], time.time(), None, None
    boss = job.get("from_host") if link.device(link.household(), job.get("from_host")) else None
    try:
        if tree.exists():
            shutil.rmtree(tree)                       # this job's own copy from an earlier start
        src = Path(job["tree"])
        if src.is_dir():
            shutil.copytree(src, tree, symlinks=True)
        else:
            tree.mkdir(parents=True)
            shutil.copy2(src, tree / src.name)
        concerns += (health_fn or _health)()
        wall = int(job.get("wall_minutes") or 60) * 60
        env = link.link_env()
        # a `python3` in the job's own line is the worker's Python (it has pytest), never a bare system one: the
        # O3 false PASS ran /usr/bin/python3, which had no pytest
        env["PATH"] = os.pathsep.join([os.path.dirname(sys.executable), env.get("PATH", "")])
        if job["kind"] == "run":
            argv = run_argv(job["argv"], plain=bool(job.get("plain")), path=env["PATH"])
            if argv[4:6] == ["guard", "--"]:
                # the guard's own lines go to their own file: job.log is the command's output alone, so the
                # evidence tail ends in the pytest summary, not in the preflight (C3/I6, 2026-10-28)
                from .thermal.buildguard import GUARD_LOG
                guard_log.unlink(missing_ok=True)
                env[GUARD_LOG] = str(guard_log)
        else:
            exe = opencode_exe()
            if not exe:
                raise RuntimeError("gorilla-opencode is not installed on this worker")
            model = job.get("model") or DEFAULT_MODEL
            if job.get("brain"):
                env["XDG_CONFIG_HOME"] = str(agent_profile(d, job["brain"]))
                model = model if model.startswith("local.") else f"local.{model}"
            pin_model(tree, model)
            env["GORILLA_OPENCODE_HEADLESS_TIMEOUT"] = f"{max(1, wall // 60)}m"
            # -q: no spinner. With it, a worker started with no console (DETACHED_PROCESS) ended after 8 s with
            # 0xC000013A, "ended by Ctrl+C", live on Windows 2026-10-26; fieldkit buildh drive passes -q too
            argv = [exe, "-p", job["brief"], "-c", str(tree), "-q"]
        from .mandate import health
        idle_s = stall_s()
        with open(log, "w", encoding="utf-8") as out:
            out.write(f"$ {' '.join(argv[:6])}{' ...' if len(argv) > 6 else ''}\n"
                      f"# contained: wall {wall // 60} min, stall {idle_s / 60:g} min without output or CPU\n")
        with health.awake(f"fieldkit job {jid}"):
            o = run(argv, tree, env, log, wall, idle_s, f"job {jid}")
        if o.status == "error":
            raise RuntimeError(o.why or "the job's command could not start")
        rc, more = job_concerns(o, wall, idle_s)
        concerns += more
        if more:
            with open(log, "a", encoding="utf-8") as out:
                out.write("".join(f"\n=== {c['text']}" for c in more) + "\n")
        if job["kind"] == "agent" and job.get("brain"):
            said = unload(job["brain"])
            if said.startswith("NOT"):
                concerns.append({"text": said, "fix_offered": "unload the model in LM Studio"})
        arts, missing = _artefacts(tree, job.get("artefacts") or [], put, boss)
        concerns += [{"text": f"no file matched the artefact pattern {g!r}", "fix_offered": "check the pattern"}
                     for g in missing]
        bad = [a for a in arts if not a["sent"]]
        concerns += [{"text": f"artefact {a['name']} did not reach the boss: {a['error']}",
                      "fix_offered": "the file is still in the job folder; send the job again"} for a in bad]
        output = "".join(_read(log).splitlines(keepends=True)[2:])     # after the "$ command" and "# contained" lines
        ok, why = judge(job["kind"], job.get("argv"), rc, output, job.get("artefacts"))
        concerns += why
        status = "pass" if ok and not missing and not bad else "fail"
        if job["kind"] == "agent" and status == "pass":          # C3b: an AI result passes only on a reviewer's verdict
            verdict = review_verdict.review(job, rc, output, arts, reviewer)
            if verdict["verdict"] != review_verdict.PASS:
                status = "fail"
                concerns.append({"text": f"review verdict {verdict['verdict']}: {verdict['explanation']}",
                                 "fix_offered": "send the explanation back to the worker" if verdict["verdict"] ==
                                 review_verdict.SEND_BACK else "the reviewer fixes it" if verdict["verdict"] ==
                                 review_verdict.FIX_ITSELF else "wire the reviewer (I7), then send the job again"})
        evidence = (f"{o.status} exit {rc} in {time.time() - t0:.0f} s; {len(arts) - len(bad)} of {len(arts)} "
                    f"artefact(s) sent, sha256 in this result\n{evidence_tail(log, guard_log)}")
        if guard_log.is_file():
            with open(log, "a", encoding="utf-8") as out:
                out.write("=== the guard's report\n" + _read(guard_log))
    except Exception as e:  # noqa: BLE001 - every failure becomes a result the boss sees, never silence
        status, arts = "not-run", []
        evidence = f"the job could not run here: {type(e).__name__}: {e}"
    finally:
        try:
            Path(job.get("lock") or "").unlink(missing_ok=True)
        except OSError:
            pass
    r = sign({"id": jid, "kind": job["kind"], "repo": job["repo"], "commit": job["tree_digest"], "status": status,
              "evidence": evidence[:1900], "concerns": concerns, "os": link.os_tag(), "host": socket.gethostname(),
              "at": time.strftime("%Y-%m-%d %H:%M:%S"), "verdict": verdict["verdict"] if verdict else None,
              "artefacts": [{k: a[k] for k in ("name", "size", "sha256")} for a in arts]})
    (d / "result.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    deliver(r)
    return r


def _health():
    """Free temp space and the clock (health.py); never the network: a tree job needs no internet."""
    from .mandate import health
    rows = {"temp space": health.free_temp(health.MIN_FREE_GB), "clock": health.clock(subprocess.run)}
    return [{"text": f"{n}: {detail}", "fix_offered": fix} for n, (ok, detail, fix) in rows.items() if ok is False]


def _artefacts(tree, patterns, put, boss):
    out, missing = [], []
    for g in patterns:
        files = sorted(p for p in tree.glob(g) if p.is_file())[:MAX_ARTEFACTS - len(out)]
        if not files:
            missing.append(g)
        for p in files:
            res = put(p, device=boss)
            out.append({"name": p.relative_to(tree).as_posix(), "size": p.stat().st_size, "sha256": _sha(p),
                        "sent": bool(res.get("ok")), "error": "" if res.get("ok") else res.get("error", ""),
                        "boss_sha256": res.get("sha256")})
    return out, missing


# ---- the boss -------------------------------------------------------------------------------------------------------
def job_key(kind, argv=None, brief=None, brain=None, model=None, artefacts=(), plain=False):
    blob = json.dumps([kind, argv, brief, brain, model, list(artefacts)] + (["plain"] if plain else []),
                      sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:8]


def send(kind, device, tree, project, argv=None, brief=None, brain=None, model=None, artefacts=(), wall=60,
         put=None, call=None, find_mandate=None, plain=False):
    """Send the tree, then the job. -> (job id, what happened, the put answer)."""
    from .mandate import mandate as M
    put = put or link.put
    call = call or link.call
    mpath = (find_mandate or jobs.mandate_for)(project)
    if mpath is None:
        return None, f"no mandate in force here names {project}: fieldkit mandate status", None
    req = {"verb": "job", "kind": kind, "repo": project, "mandate": M._sha(mpath), "artefacts": list(artefacts),
           "wall_minutes": int(wall), "tree_digest": "0" * 64}
    if kind == "run":
        req["argv"] = list(argv or [])
        if plain:
            req["plain"] = True
    else:
        req.update(brief=brief, brain=brain, model=model or "")
    why = check_work(req)
    if why:
        return None, why, None
    t = put(tree, device=device)
    if not t.get("ok") or not t.get("digest"):
        return None, f"the tree did not arrive: {t.get('error')}", t
    jid = f"{kind}-{project}-{t['digest'][:12]}-{job_key(kind, argv, brief, brain, model, artefacts, plain)}"
    req.update(id=jid, tree_digest=t["digest"])
    led = jobs._ledger()
    if led.get(jid, {}).get("status") in ("sent", "pass"):
        return jid, f"already {led[jid]['status']} at {led[jid].get('sent')}", t
    res = call(req, device=device)
    if not res.get("ok"):
        jobs.event("job-refused", id=jid, worker=device, why=res.get("error"))
        return jid, f"not taken: {res.get('error')}", t
    led[jid] = {"kind": kind, "repo": project, "commit": t["digest"], "mandate": req["mandate"], "status": "sent",
                "sent": time.strftime("%Y-%m-%d %H:%M:%S"), "worker_name": device, "worker_said": "started"}
    jobs._save(led)
    jobs.event("job-sent", id=jid, kind=kind, repo=project, tree=t["digest"], worker=device)
    return jid, "sent", t


def artefacts_check(result, received=None):
    """-> [(name, ok, why)]: each artefact the worker signed for, against a file that arrived here with that sha256."""
    rec = received or link.home() / "receipts"
    got = {}
    for p in sorted(rec.glob("*.json")) if rec.is_dir() else []:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for _rel, sha in r.get("sha256") or []:
            got.setdefault(sha, r.get("saved"))
    out = []
    for a in result.get("artefacts") or []:
        where = got.get(a.get("sha256"))
        if not where or not Path(where).is_file():
            out.append((a.get("name"), False, "no file with that sha256 arrived here"))
        elif _sha(where) != a.get("sha256"):
            out.append((a.get("name"), False, f"{where} changed since it arrived"))
        else:
            out.append((a.get("name"), True, where))
    return out


def verified_result(jid, project, digest):
    """The stored result for that job whose signature a pinned worker key checks, or None."""
    for r in jobs.verified_results(project, digest):
        if r.get("id") == jid:
            return r
    return None


def main(argv):
    """fieldkit orchestrate job ... and fieldkit orchestrate work ID (from orchestrate.main)."""
    import argparse
    if argv[:1] == ["work"]:
        if len(argv) != 2:
            print("work JOB-ID")
            return 2
        r = work(argv[1])
        print(f"RESULT {r['id']} {r['status']}")
        return 0 if r["status"] == "pass" else 3
    rest = argv[1:] if argv[:1] == ["job"] else argv
    tail = []
    if "--" in rest:
        i = rest.index("--")
        rest, tail = rest[:i], rest[i + 1:]
    ap = argparse.ArgumentParser(prog="fieldkit orchestrate job", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["run", "agent", "status"])
    ap.add_argument("--device", help="the worker (fieldkit peers link devices)")
    ap.add_argument("--tree", help="the folder (or file) the job works on; sent first with peers link put")
    ap.add_argument("--project", default="Gorilla.Fieldkit", help="the repository the owner's mandate names")
    ap.add_argument("--artefact", action="append", default=[], help="a glob inside the tree to send back (repeat)")
    ap.add_argument("--wall", type=int, default=60, help="minutes the job may run")
    ap.add_argument("--wait", type=float, default=0, help="seconds to wait for the result here (one blocking wait)")
    ap.add_argument("--model", help=f"agent: a model id the worker's Gorilla OpenCode has ({DEFAULT_MODEL})")
    ap.add_argument("--brain", help="agent: an OpenAI-compatible URL as the WORKER reaches it, only for a worker "
                                    "with no provider of its own")
    ap.add_argument("--brief", help="agent: a file with the brief")
    ap.add_argument("--plain", action="store_true", help="run: the line after -- is a build or test program "
                                                         "(PROGRAMS), not a Fieldkit command")
    a = ap.parse_args(rest)
    if a.kind == "status":
        print("\n".join(jobs.lines()))
        return 0
    if not a.tree or not a.device:
        print("job run|agent needs --device and --tree")
        return 2
    brief = Path(a.brief).read_text(encoding="utf-8") if a.brief else None
    jid, said, t = send(a.kind, a.device, a.tree, a.project, argv=tail, brief=brief, brain=a.brain, model=a.model,
                        artefacts=a.artefact, wall=a.wall, plain=a.plain)
    if t and t.get("ok"):
        print(f"TREE  {t['files']} file(s), {t['bytes'] / 1e6:,.1f} MB, digest {t['digest'][:12]} on {a.device}")
    print(f"JOB   {jid or '-'}: {said}")
    if said != "sent":
        return 3
    if not a.wait:
        print("NEXT: fieldkit orchestrate job status, or wait: --wait SECONDS")
        return 0
    e = jobs.wait(jid, a.wait)
    if not e or e.get("status") in (None, "sent"):
        print(f"VERDICT  no result within {a.wait:g} s (the job may still run: fieldkit orchestrate job status)")
        return 3
    r = verified_result(jid, a.project, t["digest"])
    if r is None:
        print(f"VERDICT  {e.get('status')} reported, but no result signed by a pinned worker key: not trusted")
        return 3
    print(f"RESULT {r['status']} on {r['host']} ({r['os']}): {r['evidence'].splitlines()[0]}")
    for c in r.get("concerns") or []:
        print(f"  CONCERN: {c.get('text')}  FIX OFFERED: {c.get('fix_offered')}")
    checks = artefacts_check(r)
    for name, ok, why in checks:
        print(f"  {'ARTEFACT OK ' if ok else 'ARTEFACT BAD'} {name}: {why}")
    good = r["status"] == "pass" and all(ok for _, ok, _ in checks)
    print(f"VERDICT  {'PASS' if good else 'FAIL'}: {r['status']}, {sum(ok for _, ok, _ in checks)} of "
          f"{len(checks)} artefact(s) arrived with the sha256 the worker signed")
    return 0 if good else 3
