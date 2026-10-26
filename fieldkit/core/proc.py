"""Run programs the same way every time, and stop only what we started.

- Output goes to a log file as well as being returned, so a failure an hour
  into a build is still readable afterwards.
- Coding-agent environment variables are stripped by default: mozbuild (and
  possibly other tools) hides its own output when it sees them, which cost a
  Firefox build its real error message (Gorilla.firefox triage, 2026-09).
- Every process started is recorded with its PID AND its creation time.
  `stop()` refuses a PID we did not start, and refuses a PID whose creation
  time no longer matches (the number was reused by somebody else's program):
  the owner runs the same programs, so stopping by name or by a stale number
  kills their work too. Entries for processes that have exited are pruned.
- A timeout stops the whole tree the runner started (the child and its
  descendants), never anything outside that tree.
"""
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

try:                                    # optional: better process facts when installed
    import psutil
except ImportError:                     # pragma: no cover - depends on the machine
    psutil = None

AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED",
             "GEMINI_CLI", "OPENCODE", "CURSOR_AGENT", "AIDER")
CT_TOLERANCE = 0.05                     # seconds; same method on both sides, so only float noise


@dataclass
class Result:
    cmd: list
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    log: str = None
    pid: int = None
    extra: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.returncode == 0

    def as_dict(self, tail=40):
        return {"cmd": self.cmd, "returncode": self.returncode, "ok": self.ok,
                "seconds": round(self.seconds, 2), "log": self.log, "pid": self.pid,
                "stdout_tail": self.stdout.splitlines()[-tail:],
                "stderr_tail": self.stderr.splitlines()[-tail:]}


def clean_env(extra=None, strip_agent=True):
    env = dict(os.environ)
    if strip_agent:
        for k in AGENT_ENV:
            env.pop(k, None)
    env.update(extra or {})
    return env


# -- process identity: PID + creation time ---------------------------------------
def _ct_psutil(pid):
    try:
        p = psutil.Process(int(pid))
        if p.status() == psutil.STATUS_ZOMBIE:
            return None
        return float(p.create_time())
    except (psutil.Error, ValueError, OSError):
        return None


def _ct_windows(pid):
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    h = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
            return None
        c, e, k, u = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
            return None
        return ((c.dwHighDateTime << 32) | c.dwLowDateTime) / 1e7    # seconds since 1601
    finally:
        k32.CloseHandle(h)


def _ct_linux(pid):
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError):
        return None
    rest = raw[raw.rfind(")") + 2:].split()      # fields from 3 (state) onward
    if not rest or rest[0] in ("Z", "X"):
        return None
    try:
        return float(rest[19])                   # field 22: starttime in clock ticks since boot
    except (IndexError, ValueError):
        return None


def _method():
    if psutil is not None:
        return "psutil"
    return "windows" if os.name == "nt" else "linux"


def create_time(pid, how=None):
    """-> creation time of a running PID by `how` (default: best available), None if not running."""
    how = how or _method()
    if how == "psutil":
        return _ct_psutil(pid) if psutil is not None else None
    if how == "windows":
        return _ct_windows(pid) if os.name == "nt" else None
    if how == "linux":
        return _ct_linux(pid)
    return None


def _same_process(pid, entry):
    """True only if PID is alive and was created at the recorded time (not a reused number)."""
    if not isinstance(entry, dict) or entry.get("create_time") is None or not entry.get("how"):
        return False
    now = create_time(pid, entry["how"])
    return now is not None and abs(now - float(entry["create_time"])) <= CT_TOLERANCE


def _kill_tree(pid):
    """Kill PID and every descendant of it. Callers must have proved PID is ours first."""
    pid = int(pid)
    if psutil is not None:
        try:
            root = psutil.Process(pid)
            kids = root.children(recursive=True)
        except psutil.Error:
            root, kids = None, []
        for p in kids + ([root] if root else []):
            try:
                p.kill()
            except psutil.Error:
                pass
    elif os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    if os.name != "nt":
        # the runner starts each child as its own process group (start_new_session), so the
        # group also catches grandchildren whose parent already exited; only if it is that group
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


class Runner:
    """Starts processes, logs them, remembers their PIDs and creation times."""

    def __init__(self, state_dir, log_dir=None):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = Path(log_dir) if log_dir else self.state_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = self.state_dir / "started-pids.json"

    def _write(self, pids):
        self.pid_file.write_text(json.dumps(pids, indent=1), encoding="utf-8")

    def _record_pid(self, pid, cmd):
        pids = self.prune()
        how = _method()
        pids[str(pid)] = {"cmd": cmd, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "create_time": create_time(pid, how), "how": how}
        self._write(pids)

    def _forget(self, pid):
        pids = self.started()
        if pids.pop(str(pid), None) is not None:
            self._write(pids)

    def started(self):
        try:
            data = json.loads(self.pid_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def prune(self):
        """Drop entries whose process has exited or whose PID now belongs to another process."""
        pids = self.started()
        kept = {k: v for k, v in pids.items() if _same_process(k, v)}
        if kept != pids:
            self._write(kept)
        return kept

    def run(self, cmd, cwd=None, env=None, timeout=None, name=None, strip_agent=True, check_input=None):
        """Run to completion. Never raises on a non-zero exit; read Result.ok."""
        cmd = [str(c) for c in cmd]
        log = self.log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{name or Path(cmd[0]).stem}.log"
        t0 = time.monotonic()
        # in a container (contain.py): a timeout stops the whole tree, orphans whose parent died included; output
        # goes to files, so a leftover child can never hold a pipe open; what the stage leaves running is stopped
        from . import contain, liveness
        started = []

        def _rec():
            if c.root is not None and not started:
                started.append(c.root)
                self._record_pid(c.root, cmd)
        c = contain.Container(name or Path(cmd[0]).stem, timeout, liveness.current())
        try:
            o = c.run(cmd, cwd=cwd, env=clean_env(env, strip_agent), stdin_text=check_input, deadline_s=timeout,
                      on_tick=_rec)
        except BaseException:                          # Ctrl+C etc.: do not leave our tree running
            c.kill("interrupted")
            raise
        finally:
            c.close()
            for pid in started:
                self._forget(pid)
        if o.status == "error":
            return Result(cmd, 127, "", o.why, 0.0)
        out, err = o.out, o.err
        if o.timed_out:
            err = (err or "") + (f"\n[fieldkit] timed out after {timeout} s; it and its child processes were stopped "
                                 f"({len(o.killed)})")
        elif o.leftovers:
            err = (err or "") + f"\n[fieldkit] {o.why}"
        secs = time.monotonic() - t0
        log.write_text(f"$ {' '.join(cmd)}\n# cwd={cwd} rc={o.rc} {secs:.1f}s\n"
                       f"--- stdout ---\n{out}\n--- stderr ---\n{err}\n", encoding="utf-8")
        return Result(cmd, o.rc, out, err, secs, str(log), o.pid)

    def stop(self, pid):
        """Stop a process (and its descendants) only if this Runner started it and it is still that process.

        -> True when stopped, False when it had already exited. Raises PermissionError otherwise."""
        entry = self.started().get(str(pid))
        if entry is None:
            raise PermissionError(f"PID {pid} was not started by fieldkit; refusing to stop it")
        if not _same_process(pid, entry):
            self._forget(pid)
            how = entry.get("how") if isinstance(entry, dict) else None
            if create_time(pid, how) is None:
                return False                             # ours, and already gone
            raise PermissionError(f"PID {pid} is now a different process (creation time differs); "
                                  "refusing to stop it")
        _kill_tree(pid)
        self._forget(pid)
        return True
