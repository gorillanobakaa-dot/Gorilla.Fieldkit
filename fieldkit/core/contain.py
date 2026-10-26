# Version: 1.0.0 · updated 26-10-26
"""Containment: every process the harness starts runs in a container that stops the WHOLE tree, orphans included.

WHY (2026-10-26, goal windows-capabilities): the verifier ran `go test ./...` through PowerShell with
subprocess.run(capture_output, timeout). PowerShell died; go.exe (PID 2672) lived on as an ORPHAN, spinning two cores
for 80 minutes, and it held the output pipes, so the supervisor sat in communicate() with no log line while 8 jobs
waited. kill_tree() walked only LIVE children, so it could not see a grandchild whose parent was gone, and a
non-elevated session could not stop the elevated orphan. The owner had to ask.

    with Container("verify go-test", deadline_s=1800) as c:
        r = c.run(cmd, cwd=..., stdout_path=None)       -> Outcome(status ok|fail|timeout, rc, out, seconds, killed)

Per system:
  Windows  a Job Object with KILL_ON_JOB_CLOSE + DIE_ON_UNHANDLED_EXCEPTION (no Windows Error Reporting dialog to hang
           on). The child starts SUSPENDED, is assigned to the job, then resumed, so not even its first grandchild can
           start outside the job. TerminateJobObject stops every member, orphans included; closing the last handle
           (the supervisor dies) does the same. Nested jobs (Windows 8+) are fine.
  Linux    its own session/process group (killpg catches grandchildren whose parent died), every descendant seen while
           it runs is tracked by (pid, creation time), and a supervisor may become the CHILD SUBREAPER
           (PR_SET_CHILD_SUBREAPER), so orphans re-parent to it instead of init and stay findable.
  macOS    its own process group + the tracked descendants.

Output never goes through a pipe: it goes to files, so a process that outlives its parent cannot block the reader
(failure mode FM-3). The deadline is a WALL deadline; on expiry the container is killed and the Outcome says
"timeout", which is not "fail". When the main process ends, whatever it left running in the container is stopped too
and listed (`leftovers`). The elevated supervisor reaps its own children this way: no other session needs admin.
Every container reports its members to the liveness beacon (fieldkit/core/liveness.py) of the run it belongs to.
Stops by PID and creation time, never by name: nothing outside the container is ever touched.
"""
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import psutil
except ImportError:                     # pragma: no cover - psutil is a requirement; the job object needs none
    psutil = None

IS_WIN = os.name == "nt"
POLL_S = 1.0                            # how often a waiting container looks at its tree
IDLE_CPU_S = 2.0                        # CPU seconds within the idle window that still count as progress
TAIL_KEEP = 8000                        # characters of output kept on disk after a timeout or a stall
CT_TOL = 0.05


@dataclass
class Outcome:
    status: str                          # ok | fail | timeout | error
    rc: int
    out: str = ""
    err: str = ""
    seconds: float = 0.0
    pid: int = None
    killed: list = field(default_factory=list)     # [pid, ...] stopped by the container
    leftovers: list = field(default_factory=list)  # [pid, ...] still running after the main process ended
    contained: bool = True               # False: the system refused the container (reported, never hidden)
    why: str = ""
    tail_file: str = None                # timeout/stall: the last output, kept on disk (state/liveness/tails)

    @property
    def ok(self):
        return self.status == "ok"

    @property
    def timed_out(self):
        return self.status in ("timeout", "stalled")

    @property
    def stalled(self):
        return self.status == "stalled"

    def as_dict(self):
        return {"status": self.status, "rc": self.rc, "seconds": round(self.seconds, 2), "pid": self.pid,
                "killed": self.killed, "leftovers": self.leftovers, "contained": self.contained, "why": self.why}


def ctime(pid):
    """Creation time of a live PID (psutil seconds), None when it is gone or a zombie."""
    if psutil is None:
        return None
    try:
        p = psutil.Process(int(pid))
        if p.status() == psutil.STATUS_ZOMBIE:
            return None
        return round(p.create_time(), 3)
    except (psutil.Error, ValueError, OSError):
        return None


def same(pid, ct):
    """True only when PID is alive AND was created at ct: a reused number is never ours."""
    now = ctime(pid)
    return now is not None and ct is not None and abs(now - float(ct)) <= CT_TOL


# ---- Windows: the job object ------------------------------------------------------------------------------------
class _Job:
    KILL_ON_JOB_CLOSE = 0x2000
    DIE_ON_UNHANDLED_EXCEPTION = 0x400
    EXTENDED_LIMIT = 9
    BASIC_ACCOUNTING = 1
    PID_LIST = 3
    CREATE_SUSPENDED = 0x4

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                ctypes.c_void_p)
        k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.k = k
        self.nt = ctypes.WinDLL("ntdll")
        self.nt.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        self.h = k.CreateJobObjectW(None, None)
        if not self.h:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        info = EXT()
        info.Basic.LimitFlags = self.KILL_ON_JOB_CLOSE | self.DIE_ON_UNHANDLED_EXCEPTION
        if not k.SetInformationJobObject(self.h, self.EXTENDED_LIMIT, ctypes.byref(info), ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            k.CloseHandle(self.h)
            raise OSError(err, "SetInformationJobObject failed")

    def assign(self, handle):
        return bool(self.k.AssignProcessToJobObject(self.h, int(handle)))

    def resume(self, handle):
        return self.nt.NtResumeProcess(int(handle)) == 0

    def pids(self):
        ct = self.ct
        n = 256
        while True:
            class LIST(ct.Structure):
                _fields_ = [("Assigned", self.wt.DWORD), ("InList", self.wt.DWORD), ("Ids", ct.c_size_t * n)]
            buf = LIST()
            if self.k.QueryInformationJobObject(self.h, self.PID_LIST, ct.byref(buf), ct.sizeof(buf), None):
                return [int(buf.Ids[i]) for i in range(buf.InList)]
            if ct.get_last_error() == 234 and n < 65536:     # ERROR_MORE_DATA
                n *= 4
                continue
            return []

    def terminate(self, code=1):
        return bool(self.k.TerminateJobObject(self.h, code))

    def close(self):
        if self.h:
            self.k.CloseHandle(self.h)          # KILL_ON_JOB_CLOSE: anything still inside dies now
            self.h = None


# ---- Linux: the child subreaper ---------------------------------------------------------------------------------
_SUBREAPER = False


def enable_subreaper():
    """Linux: make THIS process the child subreaper, so its descendants' orphans re-parent to it (not to init) and the
    container can still find and stop them. -> True when set. A no-op elsewhere. Called by every supervisor."""
    global _SUBREAPER
    if not sys.platform.startswith("linux"):
        return False
    if _SUBREAPER:
        return True
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        _SUBREAPER = libc.prctl(36, 1, 0, 0, 0) == 0          # PR_SET_CHILD_SUBREAPER
    except (OSError, AttributeError):
        _SUBREAPER = False
    return _SUBREAPER


# ---- the container ----------------------------------------------------------------------------------------------
class Container:
    """One container per started command. Use as a context manager, or call close() (which stops what is left)."""

    def __init__(self, label="", deadline_s=None, beacon=None, keep_leftovers=False):
        self.label = label
        self.deadline_s = deadline_s
        self.beacon = beacon
        self.keep_leftovers = keep_leftovers
        self.job = None
        self.contained = True
        self.why = ""
        self.root = None
        self.root_ct = None
        self.tracked = {}                 # pid -> creation time: every descendant ever seen
        self.killed = []
        self.sid = None                   # this container's step in the beacon
        self._lock = threading.Lock()

    # -- start
    def popen(self, cmd, **kw):
        """subprocess.Popen inside the container. stdin/stdout/stderr as the caller gives them (files, DEVNULL)."""
        if IS_WIN:
            try:
                self.job = self.job or _Job()
            except OSError as e:
                self.contained, self.why = False, f"no job object: {e}"
            flags = kw.pop("creationflags", 0)
            if self.job:
                flags |= _Job.CREATE_SUSPENDED
            p = subprocess.Popen(cmd, creationflags=flags, **kw)
            if self.job:
                if not self.job.assign(p._handle):
                    self.contained, self.why = False, "AssignProcessToJobObject refused; tree kill only"
                self.job.resume(p._handle)
        else:
            kw.setdefault("start_new_session", True)
            p = subprocess.Popen(cmd, **kw)
        self.root = p.pid
        self.root_ct = ctime(p.pid)
        self.tracked[p.pid] = self.root_ct
        self.track()
        return p

    # -- what is inside
    def members(self):
        """-> {pid: ctime} of the live processes in this container now."""
        out = {}
        if self.job:
            for pid in self.job.pids():
                c = ctime(pid)
                if c is not None:
                    out[pid] = c
        if psutil is not None and self.root is not None:
            if same(self.root, self.root_ct):
                try:
                    for ch in psutil.Process(self.root).children(recursive=True):
                        c = ctime(ch.pid)
                        if c is not None:
                            out[ch.pid] = c
                except psutil.Error:
                    pass
            if not IS_WIN:
                for pid in self._group_pids():
                    c = ctime(pid)
                    if c is not None:
                        out[pid] = c
            for pid, c in list(self.tracked.items()):
                if same(pid, c):
                    out[pid] = c
                    try:                  # the children of a tracked orphan are ours too
                        for ch in psutil.Process(pid).children(recursive=True):
                            cc = ctime(ch.pid)
                            if cc is not None:
                                out[ch.pid] = cc
                    except psutil.Error:
                        pass
        return out

    def _group_pids(self):
        if self.root is None or psutil is None:
            return []
        out = []
        for p in psutil.process_iter(["pid"]):
            try:
                if os.getpgid(p.pid) == self.root:
                    out.append(p.pid)
            except (OSError, psutil.Error):
                continue
        return out

    def track(self):
        """Record every live member (pid + creation time) and tell the beacon. -> the members."""
        m = self.members()
        with self._lock:
            new = {p: c for p, c in m.items() if p not in self.tracked}
            self.tracked.update(new)
        if self.beacon is not None and new:
            try:
                self.beacon.add_members(new, self.sid)
            except Exception:  # noqa: BLE001 - the beacon is a report, never a reason to fail the run
                pass
        return m

    # -- stop
    def kill(self, why="stopped"):
        """Stop every member (orphans included). -> the PIDs stopped. Never anything outside the container."""
        live = self.members()
        if self.job:
            self.job.terminate(1)
        if not IS_WIN and self.root is not None:
            try:
                os.killpg(self.root, signal.SIGKILL)   # the group id exists only while a member lives: never reused
            except OSError:
                pass
        for pid, c in live.items():
            if same(pid, c):
                try:
                    if psutil is not None:
                        psutil.Process(pid).kill()
                    else:
                        os.kill(pid, signal.SIGKILL)
                except Exception:  # noqa: BLE001 - access denied is reported by the liveness check
                    pass
        if psutil is not None and live:
            try:
                psutil.wait_procs([psutil.Process(p) for p in live if ctime(p) is not None], timeout=5)
            except psutil.Error:
                pass
        self._reap(live)
        with self._lock:
            self.killed += [p for p in live if p not in self.killed]
        return sorted(live)

    def _reap(self, pids):
        """Linux subreaper: collect the exit status of re-parented orphans we stopped (never a Popen's own child)."""
        if IS_WIN:
            return
        for pid in pids:
            if pid == self.root:
                continue
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass

    def close(self):
        left = []
        if not self.keep_leftovers:
            if self.members():
                left = self.kill("leftover")
        if self.job:
            self.job.close()
            self.job = None
        if self.beacon is not None and self.sid is not None:
            try:
                self.beacon.clear_step(self.sid)
            except Exception:  # noqa: BLE001
                pass
            self.sid = None
        return left

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def open_step(self, cmd, deadline_s):
        """Tell the beacon a step with this deadline is open, and how to stop it from inside (the watchdog)."""
        if self.beacon is not None and self.sid is None:
            try:
                self.sid = self.beacon.set_step(self.label or str(cmd)[:120], deadline_s, killer=self.kill)
            except Exception:  # noqa: BLE001
                self.sid = None

    # -- run to the end, or to the deadline
    def run(self, cmd, cwd=None, env=None, stdin_text=None, stdout_path=None, merge=False, deadline_s=None,
            on_tick=None, popen=None, keep_tail=200_000, idle_s=None):
        """Run cmd to completion or its wall deadline. Output to files, never pipes (FM-3). -> Outcome.
        idle_s (FM-9, the silent stall): no PROGRESS for that long (its output did not grow AND its processes used
        less than IDLE_CPU_S of CPU) stops the container: status "stalled", the output tail kept on disk
        (Outcome.tail_file), the stall logged, put in the ROOM and shown by `fieldkit liveness check`."""
        deadline_s = deadline_s if deadline_s is not None else self.deadline_s
        if popen is not None and popen is not subprocess.Popen:
            return _run_seam(popen, cmd, cwd, env, stdin_text, deadline_s)
        tmp = Path(tempfile.mkdtemp(prefix="fk-contain-"))
        out_p = Path(stdout_path) if stdout_path else tmp / "out.txt"
        err_p = out_p if merge else tmp / "err.txt"
        in_p = tmp / "in.txt"
        t0 = time.monotonic()
        self.open_step(cmd, deadline_s)
        try:
            if stdin_text is not None:
                in_p.write_text(stdin_text, encoding="utf-8")
            fin = open(in_p, "rb") if stdin_text is not None else subprocess.DEVNULL
            fout = open(out_p, "ab" if stdout_path else "wb")
            ferr = fout if merge else open(err_p, "wb")
            try:
                try:
                    p = self.popen([str(c) for c in cmd], cwd=cwd, env=env, stdin=fin, stdout=fout, stderr=ferr)
                except OSError as e:
                    return Outcome("error", 127, err=f"could not start: {e}", contained=self.contained,
                                   why=f"could not start: {e}")
                if on_tick:
                    on_tick()
                status, rc = None, None
                prog = _Progress(self, (out_p, err_p), idle_s)
                while True:
                    left = None if not deadline_s else deadline_s - (time.monotonic() - t0)
                    try:
                        rc = p.wait(timeout=POLL_S if left is None else max(0.0, min(POLL_S, left)))
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    self.track()
                    if on_tick:
                        try:
                            on_tick()
                        except Exception:  # noqa: BLE001
                            pass
                    if prog.stalled():
                        self.kill("stall")
                        try:
                            p.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            p.kill()
                        status, rc = "stalled", 124
                        break
                    if deadline_s and time.monotonic() - t0 >= deadline_s:
                        self.kill("deadline")
                        try:
                            p.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            p.kill()
                        status, rc = "timeout", 124
                        break
                leftovers = []
                if status is None:
                    self.track()
                    live = {k: v for k, v in self.members().items() if k != p.pid}
                    if live and not self.keep_leftovers:
                        leftovers = self.kill("leftover")
                    elif live:
                        leftovers = sorted(live)
                    status = "ok" if rc == 0 else "fail"
            finally:
                for f in {id(fout): fout, id(ferr): ferr}.values():
                    f.close()
                if fin is not subprocess.DEVNULL:
                    fin.close()
            out = _tail(out_p, keep_tail)
            err = "" if merge else _tail(err_p, keep_tail)
            why = (f"wall deadline {deadline_s:.0f} s passed: the container was stopped "
                   f"({len(self.killed)} process(es))") if status == "timeout" else ""
            if status == "stalled":
                why = (f"STALL: no progress for {idle_s:.0f} s (no output, under {IDLE_CPU_S} s CPU): the container "
                       f"was stopped ({len(self.killed)} process(es))")
            if leftovers:
                why = f"{len(leftovers)} process(es) left running by it were stopped: {leftovers}"
            o = Outcome(status, rc, out, err, time.monotonic() - t0, p.pid, list(self.killed), leftovers,
                        self.contained, why or self.why)
            if status in ("timeout", "stalled"):
                o.tail_file = keep_tail_file(self.label, out, err, why)
                if status == "stalled":
                    _report_stall(self.label, why, o.tail_file)
            return o
        finally:
            for f in (tmp / "out.txt", tmp / "err.txt", in_p):
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                tmp.rmdir()
            except OSError:
                pass


class _Progress:
    """Progress of a running container: its output files grew, or its members used CPU. -> stalled() when neither
    happened for idle_s. Also tells the beacon when progress was last seen (liveness reads it)."""

    def __init__(self, c, paths, idle_s):
        self.c, self.paths, self.idle_s = c, [Path(x) for x in dict.fromkeys(paths)], idle_s
        self.size, self.cpu = self._size(), self._cpu()
        self.at = time.monotonic()
        self.told = 0.0
        self._beacon(force=True)

    def _size(self):
        n = 0
        for x in self.paths:
            try:
                n += x.stat().st_size
            except OSError:
                pass
        return n

    def _cpu(self):
        if psutil is None:
            return 0.0
        t = 0.0
        for pid in self.c.members():
            try:
                ct = psutil.Process(pid).cpu_times()
                t += ct.user + ct.system
            except psutil.Error:
                pass
        return t

    def _beacon(self, force=False):
        b = self.c.beacon
        if b is None or self.c.sid is None or not self.idle_s:
            return
        now = time.monotonic()
        if force or now - self.told >= 30:
            self.told = now
            try:
                b.progress(self.c.sid, time.time() - (now - self.at), self.idle_s)
            except Exception:  # noqa: BLE001
                pass

    def stalled(self):
        if not self.idle_s:
            return False
        size, cpu = self._size(), self._cpu()
        if size > self.size or cpu - self.cpu >= IDLE_CPU_S:
            self.size, self.cpu, self.at = size, cpu, time.monotonic()
        self._beacon()
        return time.monotonic() - self.at >= self.idle_s


def keep_tail_file(label, out, err, why):
    """The last output of a run that timed out or stalled, kept on disk. -> its path (None when it cannot be written)."""
    try:
        from . import liveness
        d = liveness.state_dir() / "tails"
        d.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (label or "run"))[:60]
        p = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{safe}.txt"
        p.write_text(f"{why}\n--- stdout (tail) ---\n{(out or '')[-TAIL_KEEP:]}\n--- stderr (tail) ---\n"
                     f"{(err or '')[-TAIL_KEEP:]}\n", encoding="utf-8")
        return str(p)
    except OSError:
        return None


def _report_stall(label, why, tail_file):
    """A stall reaches the command post, not only a log: actions.jsonl + one ROOM line every session sees."""
    try:
        from . import liveness
        liveness.log_action({"kind": "stall", "run": label, "why": why, "tail": tail_file})
        liveness.room_line("stop", f"STALL {label}: {why}; tail {tail_file}")
    except Exception:  # noqa: BLE001
        pass


def _tail(p, n):
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _run_seam(popen, cmd, cwd, env, stdin_text, deadline_s):
    """A test's fake Popen: the old communicate() contract, so callers keep one code path."""
    t0 = time.monotonic()
    p = popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
              text=True, encoding="utf-8", errors="replace")
    try:
        so, se = p.communicate(stdin_text, timeout=deadline_s)
    except subprocess.TimeoutExpired:
        return Outcome("timeout", 124, "", "", time.monotonic() - t0, getattr(p, "pid", None))
    rc = p.returncode
    return Outcome("ok" if rc == 0 else "fail", rc, so or "", se or "", time.monotonic() - t0, getattr(p, "pid", None))


def run(cmd, label="", deadline_s=None, beacon=None, **kw):
    """One command in its own container. -> Outcome. The beacon defaults to this process's current run."""
    if beacon is None:
        from . import liveness
        beacon = liveness.current()
    with Container(label or str(cmd)[:120], deadline_s, beacon) as c:
        return c.run(cmd, **kw)


def main(argv=None):
    """python -m fieldkit.core.contain [--deadline S] [--label L] -- CMD ...: the command in a container, its own
    console output kept (no capture). Exit = the command's code, 124 on the deadline."""
    import argparse
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--" not in argv:
        print("usage: python -m fieldkit.core.contain [--deadline S] [--label L] -- CMD ...")
        return 2
    i = argv.index("--")
    ap = argparse.ArgumentParser(prog="python -m fieldkit.core.contain")
    ap.add_argument("--deadline", type=float, default=None)
    ap.add_argument("--label", default="")
    a = ap.parse_args(argv[:i])
    return wait_inherit(argv[i + 1:], a.label, a.deadline)


def wait_inherit(cmd, label="", deadline_s=None, on_tick=None):
    """Run cmd in a container with the caller's console (stdin/stdout/stderr inherited). -> exit code; 124 on the
    deadline, after the whole tree was stopped."""
    from . import liveness
    with Container(label or " ".join(map(str, cmd))[:120], deadline_s, liveness.current()) as c:
        c.open_step(cmd, deadline_s)
        try:
            p = c.popen([str(x) for x in cmd])
        except OSError as e:
            print(f"[fieldkit] could not start {cmd[0]}: {e}", file=sys.stderr)
            return 127
        t0 = time.monotonic()
        while True:
            try:
                rc = p.wait(timeout=POLL_S)
                return rc
            except subprocess.TimeoutExpired:
                c.track()
                if on_tick:
                    on_tick()
                if deadline_s and time.monotonic() >= t0 + deadline_s:
                    killed = c.kill("deadline")
                    print(f"[fieldkit] TIMEOUT: {label or cmd[0]} passed its wall deadline of {deadline_s:.0f} s; "
                          f"stopped {len(killed)} process(es) {killed}", file=sys.stderr)
                    try:
                        p.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                    return 124
            except KeyboardInterrupt:
                c.kill("interrupted")
                raise


if __name__ == "__main__":
    sys.exit(main())
