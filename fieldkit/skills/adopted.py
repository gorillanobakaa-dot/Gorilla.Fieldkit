# Version: 1.0.0 · updated 26-10-26
"""Fieldkit commands for the scripts behind the adopted Anthropic skills (third_party/anthropic-skills/).

Owner, 2026-10-26: "I need tools. not suggestions for an LM." A skill is prose a model may skip; these run the
vendored, pinned, reviewed scripts the same way every time, with the doors the owner's threat model asks for:

    fieldkit webtest --server CMD --port N [--server CMD --port N ...] [--timeout S] [--log-dir D] -- COMMAND...
        webapp-testing's with_server.py job, reimplemented: start local servers, wait for their ports on localhost,
        run COMMAND (a Playwright script, pytest, anything), stop every server's whole process tree (upstream left
        the real server running on Windows). Exit code: COMMAND's; 1 when a server never opened its port.
    fieldkit prefix-diff PATH... [--json] [--scan]
        claude-api/.../prefix_diff.py: offline diff of consecutive Messages API request bodies (what broke the prompt
        prefix: cache misses, thinking-block replays), or --scan a repository for the code that usually causes it.
    fieldkit mcp-eval EVAL.xml --api-base URL [evaluation.py options]
        mcp-builder/scripts/evaluation.py: a model answers the evaluation questions through an MCP server's tools.
        It calls a model API, so it is explicit opt-in: no default endpoint (--api-base is required and becomes
        ANTHROPIC_BASE_URL), the key only from ANTHROPIC_API_KEY. Needs `pip install anthropic mcp`.
    fieldkit skills benchmark DIR [--skill-name N] [--skill-path P] [-o OUT]
        skill-creator/scripts/aggregate_benchmark.py: benchmark.json + benchmark.md from eval run results (offline).
    fieldkit skills eval --eval-set F --skill-path P --yes [run_eval.py options]
        skill-creator/scripts/run_eval.py: how often a skill's description triggers, via `claude -p` (model calls
        that cost tokens, so --yes is required).
    fieldkit skills package SKILL_DIR [OUT_DIR]
        Fieldkit's own packer (in place of package_skill.py): validates with fieldkit skills validate, then writes
        NAME.skill, a zip with the same exclusions as upstream, sorted entries and fixed timestamps, so the same
        folder always gives the same bytes.
"""
import argparse
import fnmatch
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import spec

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "third_party" / "anthropic-skills" / "skills"
PREFIX_DIFF = VENDOR / "claude-api" / "shared" / "preserved-thinking-migration" / "prefix_diff.py"
EVALUATION = VENDOR / "mcp-builder" / "scripts" / "evaluation.py"
SKILL_CREATOR = VENDOR / "skill-creator"


def _call(argv, **kw):
    """subprocess.call that hands the child OUR stdout/stderr. A console-less parent (a hook, a pipe from an agent
    or CI) starts children with CREATE_NO_WINDOW (core/nowindow), and a child given no explicit handles then gets a
    fresh hidden console, so its output vanished (measured 2026-10-29: prefix-diff and webtest printed nothing)."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except (AttributeError, ValueError, OSError):
            pass
    for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
        try:
            kw[name] = stream.fileno()
        except (AttributeError, ValueError, OSError):
            pass
    return subprocess.call(argv, **kw)


def _run(argv, cwd=None, env=None):
    return _call(argv, cwd=cwd, env=env)


def _script(path, args, isolated=True):
    if not path.is_file():
        print(f"missing vendored script: {path.relative_to(ROOT)} (fieldkit skills vendor rebuilds it)")
        return 2
    return _run([sys.executable] + (["-I"] if isolated else []) + [str(path)] + list(args))


def _wait_port(port, proc, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            return f"the server exited ({proc.returncode}) before port {port} opened"
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return None
        except OSError:
            time.sleep(0.25)
    return f"port {port} did not open within {timeout:g} s"


def _stop_tree(proc):
    """Stop a server and everything it started. with_server.py terminates only the shell it started, so on Windows
    (and with any shell that forks) the real server outlived it - measured 2026-10-26: python -m http.server kept
    listening after the run. Here: Windows taskkill /T on the process tree, POSIX the process group."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _help(args):
    if args[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return True
    return False


def webtest(args):
    """with_server.py's job (start servers, wait for their ports, run COMMAND, stop them), done here so that the
    servers' whole process trees are stopped and their output goes to a log file instead of an unread pipe (a server
    that logs more than the pipe holds stops dead under with_server.py)."""
    if _help(args):
        return 0
    if "--" not in args or not args[args.index("--") + 1:]:
        print("usage: fieldkit webtest --server CMD --port N [--server CMD --port N ...] [--timeout S] "
              "[--log-dir DIR] -- COMMAND...")
        return 2
    i = args.index("--")
    opts, command = args[:i], args[i + 1:]
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit webtest", add_help=False)
    ap.add_argument("--server", action="append", default=[])
    ap.add_argument("--port", action="append", type=int, default=[])
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--log-dir", default="")
    try:
        a = ap.parse_args(opts)
    except SystemExit:
        return 2
    if not a.server or len(a.server) != len(a.port):
        print("webtest: give one --port for every --server")
        return 2
    import tempfile
    logs = Path(a.log_dir) if a.log_dir else Path(tempfile.mkdtemp(prefix="fieldkit-webtest-"))
    logs.mkdir(parents=True, exist_ok=True)
    procs, handles = [], []
    try:
        for n, (cmd, port) in enumerate(zip(a.server, a.port), 1):
            log = open(logs / f"server-{n}.log", "wb")
            handles.append(log)
            kw = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                  else {"start_new_session": True})
            p = subprocess.Popen(cmd, shell=True, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
            procs.append(p)
            print(f"webtest: server {n} started (pid {p.pid}), waiting for port {port}; log {logs / log.name}")
            err = _wait_port(port, p, a.timeout)
            if err:
                print(f"webtest: {err} - see {logs / f'server-{n}.log'}")
                return 1
        print(f"webtest: {len(procs)} server(s) ready; running: {' '.join(command)}", flush=True)
        return _call(command)
    finally:
        for p in procs:
            _stop_tree(p)
        for h in handles:
            h.close()
        print(f"webtest: {len(procs)} server(s) stopped")


def prefix_diff(args):
    if _help(args):
        return _script(PREFIX_DIFF, ["--help"])
    if not args:
        print("usage: fieldkit prefix-diff capture.jsonl | reqA.json reqB.json | DIR [--json] | --scan REPO")
        return 2
    return _script(PREFIX_DIFF, args)


def mcp_eval(args):
    if _help(args):
        return 0
    if "--api-base" not in args or args.index("--api-base") + 1 >= len(args):
        print("mcp-eval: refused - --api-base URL is required (no default endpoint: a model API is a door you "
              "open on purpose, per run)")
        return 2
    i = args.index("--api-base")
    base, rest = args[i + 1], args[:i] + args[i + 2:]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("mcp-eval: refused - ANTHROPIC_API_KEY is not set (the key is never read from a file or an argument)")
        return 2
    env = dict(os.environ, ANTHROPIC_BASE_URL=base)
    if not EVALUATION.is_file():
        print(f"missing vendored script: {EVALUATION.relative_to(ROOT)}")
        return 2
    return _run([sys.executable, str(EVALUATION)] + rest, cwd=EVALUATION.parent, env=env)


def benchmark(args):
    # run as a module from skill-creator/ (the script imports scripts.*), so not isolated
    return _run([sys.executable, "-m", "scripts.aggregate_benchmark"] + list(args), cwd=SKILL_CREATOR)


def run_eval(args):
    if "--yes" not in args:
        print("skills eval: refused without --yes - every query runs `claude -p` (model calls that cost tokens)")
        return 2
    rest = [a for a in args if a != "--yes"]
    return _run([sys.executable, "-m", "scripts.run_eval"] + rest, cwd=SKILL_CREATOR)


EXCLUDE_DIRS = {"__pycache__", "node_modules"}
EXCLUDE_GLOBS = ("*.pyc",)
EXCLUDE_FILES = {".DS_Store"}
ROOT_EXCLUDE_DIRS = {"evals"}
EPOCH = (1980, 1, 1, 0, 0, 0)
MAX_PACKAGE_FILES = 5_000
MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_PACKAGE_ENTRIES = 10_000


def _excluded(rel):
    """rel: path relative to the skill folder's parent (as upstream package_skill.py)."""
    parts = rel.parts
    if any(p in EXCLUDE_DIRS for p in parts) or (len(parts) > 1 and parts[1] in ROOT_EXCLUDE_DIRS):
        return True
    return rel.name in EXCLUDE_FILES or any(fnmatch.fnmatch(rel.name, g) for g in EXCLUDE_GLOBS)


def _package_files(src):
    files, total_bytes, visited = [], 0, 0

    def walk_error(exc):
        raise exc

    for current, directories, names in os.walk(src, topdown=True, followlinks=False, onerror=walk_error):
        current_path = Path(current)
        kept = []
        for name in sorted(directories, key=str.casefold):
            directory = current_path / name
            relative = Path(src.name) / directory.relative_to(src)
            if _excluded(relative):
                continue
            if directory.is_symlink():
                raise ValueError("skill contains a symbolic link")
            visited += 1
            if visited > MAX_PACKAGE_ENTRIES:
                raise ValueError(f"skill exceeds the {MAX_PACKAGE_ENTRIES}-entry traversal limit")
            kept.append(name)
        directories[:] = kept
        for name in sorted(names, key=str.casefold):
            path = current_path / name
            relative = Path(src.name) / path.relative_to(src)
            if _excluded(relative):
                continue
            visited += 1
            if visited > MAX_PACKAGE_ENTRIES:
                raise ValueError(f"skill exceeds the {MAX_PACKAGE_ENTRIES}-entry traversal limit")
            if path.is_symlink():
                raise ValueError("skill contains a symbolic link")
            if not path.is_file():
                continue
            if len(files) >= MAX_PACKAGE_FILES:
                raise ValueError(f"skill exceeds the {MAX_PACKAGE_FILES}-file limit")
            size = path.stat().st_size
            total_bytes += size
            if total_bytes > MAX_PACKAGE_BYTES:
                raise ValueError("skill exceeds the 100 MiB input limit")
            files.append(path)
    if not any(path == src / "SKILL.md" for path in files):
        raise ValueError("skill must contain SKILL.md at its root")
    return files


def _build_package(src):
    files = _package_files(src)
    errors = [finding for finding in spec.validate(src) if finding[1] == "error"]
    if errors:
        raise ValueError("SKILL.md frontmatter is invalid")
    import io
    data = io.BytesIO()
    total_bytes = 0
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in files:
            with path.open("rb") as source:
                contents = source.read(MAX_PACKAGE_BYTES - total_bytes + 1)
            total_bytes += len(contents)
            if total_bytes > MAX_PACKAGE_BYTES:
                raise ValueError("skill exceeds the 100 MiB input limit")
            relative = path.relative_to(src.parent)
            if _excluded(relative):
                continue
            info = zipfile.ZipInfo(relative.as_posix(), EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zipped.writestr(info, contents)
    return data.getvalue(), len(files)


def package(args):
    parser = argparse.ArgumentParser(
        prog="fieldkit skills package",
        description="Build a deterministic .skill archive from one validated skill directory.",
    )
    parser.add_argument("skill_dir", type=Path)
    parser.add_argument("output_dir", type=Path, help="directory outside the source skill")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preview", action="store_true", help="validate and report without writing")
    modes.add_argument("--verify", action="store_true", help="compare an existing archive with current source")
    try:
        args = parser.parse_args(args)
    except SystemExit as exc:
        return int(exc.code)
    try:
        src = args.skill_dir.resolve(strict=True)
        if not src.is_dir():
            raise ValueError("skill path must be a directory")
        out_dir = args.output_dir.resolve()
        try:
            out_dir.relative_to(src)
        except ValueError:
            pass
        else:
            raise ValueError("output directory must be outside the skill directory")
        archive, count = _build_package(src)
        out = out_dir / f"{src.name}.skill"
        if args.preview:
            print(f"PREVIEW: {out.name} ({count} files, {len(archive)} bytes); no files written")
            return 0
        if args.verify:
            if out.is_symlink() or not out.is_file() or out.stat().st_size > MAX_PACKAGE_BYTES:
                raise ValueError("expected archive is missing, linked, or exceeds the 100 MiB limit")
            if out.read_bytes() != archive:
                raise ValueError("existing archive does not match the current skill source")
            print(f"VERIFIED: {out.name} matches the current skill source ({count} files)")
            return 0
        out_dir.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with out.open("xb") as target:
                created = True
                target.write(archive)
        except OSError:
            if created:
                out.unlink(missing_ok=True)
            raise
        print(f"PACKAGED {out.name} ({count} files)")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}")
        print("NEXT: fix the skill or choose a new output directory")
        return 1
