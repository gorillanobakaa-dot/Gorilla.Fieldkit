"""Run programs the same way every time, and stop only what we started.

- Output goes to a log file as well as being returned, so a failure an hour
  into a build is still readable afterwards.
- Coding-agent environment variables are stripped by default: mozbuild (and
  possibly other tools) hides its own output when it sees them, which cost a
  Firefox build its real error message (Gorilla.firefox triage, 2026-09).
- Every process started is recorded with its PID. `stop()` refuses a PID we
  did not start: the owner runs the same programs, so stopping by name kills
  their work too.
"""
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED",
             "GEMINI_CLI", "OPENCODE", "CURSOR_AGENT", "AIDER")


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


class Runner:
    """Starts processes, logs them, remembers their PIDs."""

    def __init__(self, state_dir, log_dir=None):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = Path(log_dir) if log_dir else self.state_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = self.state_dir / "started-pids.json"

    def _record_pid(self, pid, cmd):
        pids = self.started()
        pids[str(pid)] = {"cmd": cmd, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.pid_file.write_text(json.dumps(pids, indent=1), encoding="utf-8")

    def started(self):
        try:
            return json.loads(self.pid_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def run(self, cmd, cwd=None, env=None, timeout=None, name=None, strip_agent=True, check_input=None):
        """Run to completion. Never raises on a non-zero exit; read Result.ok."""
        cmd = [str(c) for c in cmd]
        log = self.log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{name or Path(cmd[0]).stem}.log"
        t0 = time.monotonic()
        try:
            p = subprocess.Popen(cmd, cwd=cwd, env=clean_env(env, strip_agent),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 stdin=subprocess.PIPE if check_input is not None else subprocess.DEVNULL,
                                 text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError as e:
            return Result(cmd, 127, "", str(e), 0.0)
        self._record_pid(p.pid, cmd)
        try:
            out, err = p.communicate(input=check_input, timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
            err += f"\n[fieldkit] timed out after {timeout} s and was stopped"
            p.returncode = 124
        secs = time.monotonic() - t0
        log.write_text(f"$ {' '.join(cmd)}\n# cwd={cwd} rc={p.returncode} {secs:.1f}s\n"
                       f"--- stdout ---\n{out}\n--- stderr ---\n{err}\n", encoding="utf-8")
        return Result(cmd, p.returncode, out, err, secs, str(log), p.pid)

    def stop(self, pid):
        """Stop a process only if this Runner started it."""
        if str(pid) not in self.started():
            raise PermissionError(f"PID {pid} was not started by fieldkit; refusing to stop it")
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        return True
