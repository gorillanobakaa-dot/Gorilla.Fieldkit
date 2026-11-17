"""One way to run a harness child that cannot hang the job (P29 cause 2, owner order 2026-11-16).

Firefox 160's migration lost 67 minutes to one patch.exe that never ended inside replay._apply_patch. Every child
started through here gets:
  - stdin that is never a terminal: the bytes given, a file, or nothing (DEVNULL), so a prompt reads end-of-file;
  - a wall deadline (DEADLINE_S by default);
  - a stall detector: no new output and under contain.IDLE_CPU_S of CPU for idle_s -> the whole tree is stopped;
  - output to files, never pipes, so a leftover grandchild cannot hold the call open (core/contain).
A stall or a deadline returns exit 124 and a `[fieldkit] STALL|TIMEOUT <label>` line on stderr naming the child
(the patch file), so the caller fails that step instead of waiting.

The result has subprocess.CompletedProcess's shape (args, returncode, stdout and stderr as bytes) plus .status,
.stalled and .why, so a caller swaps `subprocess.run(..., capture_output=True)` for `childrun.run(...)`.
"""
import os
import subprocess
import tempfile
from pathlib import Path

from . import contain

DEADLINE_S = 600        # the longest any one patch / git apply may take
STALL_S = 180           # no output and no CPU for this long is a hang, not work


class Result(subprocess.CompletedProcess):
    def __init__(self, args, returncode, stdout, stderr, status, why):
        super().__init__(args, returncode, stdout, stderr)
        self.status, self.why = status, why

    @property
    def stalled(self):
        return self.status in ("stalled", "timeout")


def run(cmd, label, input=None, stdin_path=None, cwd=None, env=None, deadline_s=DEADLINE_S, idle_s=STALL_S):
    """Run cmd to its end, its deadline or its stall. input: bytes for stdin; stdin_path: a file read as stdin;
    neither: DEVNULL. -> Result (bytes out). Never raises on a non-zero exit; an unstartable program is exit 127."""
    cmd = [str(c) for c in cmd]
    tmp = None
    try:
        if input is not None:
            fd, tmp = tempfile.mkstemp(prefix="fk-childrun-", suffix=".in")
            with os.fdopen(fd, "wb") as f:
                f.write(input if isinstance(input, bytes) else str(input).encode("utf-8"))
            stdin_path = tmp
        o = contain.run(cmd, label=label, deadline_s=deadline_s, cwd=None if cwd is None else str(cwd), env=env,
                        stdin_path=None if stdin_path is None else str(stdin_path), idle_s=idle_s)
    finally:
        if tmp:
            try:
                Path(tmp).unlink()
            except OSError:
                pass
    err = o.err or ""
    if o.status in ("stalled", "timeout"):
        word = "STALL" if o.status == "stalled" else "TIMEOUT"
        err += f"\n[fieldkit] {word} {label}: {o.why}; stopped, the step fails (P29)\n"
    elif o.status == "error":
        err += f"\n[fieldkit] {label}: {o.why}\n"
    return Result(cmd, o.rc, (o.out or "").encode("utf-8"), err.encode("utf-8"), o.status, o.why)
