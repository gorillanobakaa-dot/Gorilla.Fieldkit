# Version: 1.0.0 · updated 26-11-03
"""The open-PR guard and the ci-wait wake (owner, 2026-11-03).

    A session that ends its turn while a pull request it worked on is still OPEN, with no `fieldkit ship ci-wait` running
    for that PR, FAILS the end-of-turn check (`fieldkit rotate endcheck --transcript PATH`, exit 3): it must start the
    wait in the background first, or say why the PR is not its to wait for.
    `fieldkit ship ci-wait PR` writes a marker while it runs (its pid and the session that started it) and, when CI
    ends, leaves a wake line for THAT session. The room hook (rotate.hook_line) hands the wake to the session at its
    next prompt or tool call, so the session is woken by the finish, not by a poll.

A wait that ENDS "unproven" (red not proven this branch's) leaves state/ci-waits/handed/pr-N.json: the PR is handed to
ci-wait's verdict, not open work of the session, and the end-of-turn check passes it (I-16: the session looped every
turn). A new wait for the PR removes it.

Files: state/ci-waits/pr-N.json (the marker, removed at the end), state/ci-waits/handed/pr-N.json and
state/ci-waits/wake/SESSION.txt (the wake lines).
Writes nothing else; never calls GitHub itself (the caller passes the PR state in).
"""
import json
import os
import subprocess
import time
from pathlib import Path

from ..core.settings import state_root as _state_root


def waits_dir():
    d = Path(os.environ.get("FIELDKIT_CIWAIT_DIR") or (_state_root(Path(__file__).resolve().parents[2]) / "state" / "ci-waits"))
    return d


def _safe(session):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session or "?"))[:80]


def pid_alive(pid):
    """True when the process exists. Unknown is False: the guard then fails closed."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "nt":                                   # os.kill(pid, 0) is Ctrl+C on Windows: never used there
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=15)
            return str(pid) in r.stdout
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def begin(pr, session, pid=None, now=None):
    """The marker for a running wait: this process, for this PR, started by this session."""
    d = waits_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "handed" / f"pr-{int(pr)}.json").unlink(missing_ok=True)
    (d / f"pr-{int(pr)}.json").write_text(json.dumps({
        "pr": int(pr), "pid": int(pid or os.getpid()), "session": str(session or "?"),
        "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() if now is None else now))}),
        encoding="utf-8")


def end(pr, session, result, now=None):
    """The wait is over: remove the marker and leave the wake line for the session that started it."""
    (waits_dir() / f"pr-{int(pr)}.json").unlink(missing_ok=True)
    wake = waits_dir() / "wake"
    wake.mkdir(parents=True, exist_ok=True)
    state = str(result.get("state") or "?").upper()
    if state == "UNPROVEN":
        (waits_dir() / "handed").mkdir(parents=True, exist_ok=True)
        (waits_dir() / "handed" / f"pr-{int(pr)}.json").write_text(
            json.dumps({"pr": int(pr), "session": str(session or "?")}), encoding="utf-8")
    why = " ".join(str(result.get("why") or "").split())[:300]
    nxt = " ".join(str(result.get("next") or "").split())[:300]
    line = f"CI #{int(pr)} finished: {state}" + (f" - {why}" if why else "") + (f". NEXT: {nxt}" if nxt else "")
    with open(wake / f"{_safe(session)}.txt", "a", encoding="utf-8") as f:
        f.write(line.replace("\n", " ") + "\n")
    return line


def take(session):
    """-> the wake lines for this session, read once (the file is removed)."""
    f = waits_dir() / "wake" / f"{_safe(session)}.txt"
    try:
        lines = [x for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        f.unlink()
    except OSError:
        return []
    return lines


def running(pr, session=None, alive=pid_alive):
    """True when a ci-wait for this PR is running (its marker's process is alive)."""
    try:
        m = json.loads((waits_dir() / f"pr-{int(pr)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(alive(m.get("pid")))


def check(sessions_prs, state_fn, session=None, alive=pid_alive):
    """-> [reason] for each OPEN PR the session mentioned with no ci-wait running. [] when the turn may end.
    state_fn(url) -> OPEN / MERGED / CLOSED / None (None = unknown: it counts as open, the guard fails closed)."""
    fails = []
    for url in sessions_prs:
        st = state_fn(url)
        if st in ("MERGED", "CLOSED"):
            continue
        num = url.rstrip("/").rsplit("/", 1)[-1]
        if not num.isdigit():
            continue
        if running(num, session, alive) or (waits_dir() / "handed" / f"pr-{num}.json").is_file():
            continue
        fails.append(f"{url} is {st or 'not checked'} and no `fieldkit ship ci-wait {num}` is running for it: start it "
                     f"in the background (it wakes this session when CI ends), or say why this session does not wait")
    return fails
