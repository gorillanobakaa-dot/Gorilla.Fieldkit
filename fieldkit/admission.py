"""One machine-wide gate for heavy jobs: at most N at once on this computer, the rest wait their turn.

    fieldkit admit [--kind K] [--label L] [--wait S] -- CMD ...   run CMD when a slot is free (queued jobs wait,
                                                                  they never ask anyone); exit = CMD's exit
    fieldkit admit suite [--wait S] [--report F] [PATH ...]       the pytest suite of the current folder under the
                                                                  gate (what a placed test job runs on a worker)
    fieldkit admit status [--json]                                the slots, who holds them, who waits

Heavy jobs are the full test suite (pre-commit), fieldkit mutate, build stages and go test ./... (stage O4 of the
household orchestrator, local/PROJECT-MANDATE-PLAN.md section 19, ordered 2026-10-26). Every worktree and every
session of one user share the gate: it lives in ~/.fieldkit/admission (FIELDKIT_ADMISSION_HOME overrides), not in a
checkout.

A slot is a file held with an OS lock (fcntl on Linux and macOS, msvcrt on Windows). The lock dies with the process,
so a crashed job never keeps its slot and there is nothing stale to clean. Beside each lock file a small JSON says who
holds it (pid, kind, label, since) for the load report; it is advice only, the lock is the truth.

N: fieldkit.local.json admission.slots when set (1 to 16), else min(2, physical cores / 2, RAM GB / 4), at least 1.
While the CPU is at or above the build governor's target + 3 (about 70 C), one slot fewer is offered (never fewer
than one), so a hot computer starts nothing new beside its running job. No model is asked anything: zero tokens.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

KINDS = ("test", "mutate", "build", "go-test", "other")
MAX_SLOTS = 16
POLL_S = 2.0
NESTED = "FIELDKIT_ADMITTED"                  # set while a slot is held; children inherit it


def home():
    return Path(os.environ.get("FIELDKIT_ADMISSION_HOME") or (Path.home() / ".fieldkit" / "admission"))


def _local():
    try:
        from .core import settings
        return settings.local_settings() or {}
    except Exception:  # noqa: BLE001 - no local settings: the defaults
        return {}


def base_slots(local=None, cores=None, ram_gb=None):
    """-> N for this computer, before the temperature rule."""
    local = _local() if local is None else local
    try:
        n = int((local.get("admission") or {}).get("slots"))
        if 1 <= n <= MAX_SLOTS:
            return n
    except (TypeError, ValueError):
        pass
    if cores is None or ram_gb is None:
        import psutil
        if cores is None:
            cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 1
        if ram_gb is None:
            ram_gb = psutil.virtual_memory().total / 2 ** 30
    return max(1, min(2, int(cores) // 2, int(ram_gb) // 4))


def hot_line(local=None):
    from .thermal import governor
    return governor.build_target(local) + 3.0


def temperature():
    """-> degrees C or None; the hog's own reading (fieldkit/hog/probe.py), never a guess."""
    try:
        from .hog import probe
        return probe.temp().get("c")
    except Exception:  # noqa: BLE001
        return None


def offered(base, temp_c, hot):
    """-> slots offered to a NEW job now: one fewer while hot, never fewer than one."""
    return max(1, base - 1) if temp_c is not None and temp_c >= hot else base


class _Slot:
    """One slot file, locked without blocking. `take` -> True when this process now holds it."""

    def __init__(self, i, root=None):
        self.i = i
        self.root = Path(root or home())
        self.path = self.root / f"slot-{i}.lock"
        self.info = self.root / f"slot-{i}.json"
        self.f = None

    def take(self):
        self.root.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self.f = f
        return True

    def release(self):
        if self.f is None:
            return
        try:
            self.info.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            if sys.platform == "win32":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        finally:
            self.f.close()
            self.f = None


def try_take(n, kind="other", label="", root=None):
    """-> the slot this process now holds, or None when all `n` are taken."""
    for i in range(n):
        s = _Slot(i, root)
        if s.take():
            s.info.write_text(json.dumps({"pid": os.getpid(), "kind": kind, "label": label[:120],
                                          "since": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
            return s
    return None


def holders(root=None):
    """-> [{slot, pid, kind, label, since}] for every slot whose lock is held now (read without changing anything
    held: a free slot is taken for an instant and given back)."""
    root = Path(root or home())
    out = []
    for p in sorted(root.glob("slot-*.lock")) if root.is_dir() else []:
        try:
            i = int(p.stem.split("-", 1)[1])
        except ValueError:
            continue
        s = _Slot(i, root)
        if s.take():
            s.f.close()                       # free: close without unlinking anything, the lock goes with it
            s.f = None
            continue
        try:
            info = json.loads(s.info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        out.append(dict(info, slot=i))
    return out


def _waiting_path(root=None):
    return Path(root or home()) / "waiting"


def _mark_waiting(kind, label, root=None):
    d = _waiting_path(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{os.getpid()}.json"
    p.write_text(json.dumps({"pid": os.getpid(), "kind": kind, "label": label[:120],
                             "since": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
    return p


def waiting(root=None, alive=None):
    """-> the jobs queued now; a marker whose process is gone is not counted (and is removed)."""
    import psutil
    alive = alive or psutil.pid_exists
    out = []
    d = _waiting_path(root)
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            w = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if alive(int(w.get("pid") or 0)):
            out.append(w)
        else:
            p.unlink(missing_ok=True)
    return out


def state(root=None, local=None, temp_c=None, base=None):
    """-> {slots, offered, held, free, holders, waiting, temp_c, hot}: what the load report says about the gate."""
    local = _local() if local is None else local
    base = base_slots(local) if base is None else base
    hot = hot_line(local)
    h = holders(root)
    off = offered(base, temp_c, hot)
    return {"slots": base, "offered": off, "held": len(h), "free": max(0, off - len(h)), "holders": h,
            "waiting": waiting(root), "temp_c": temp_c, "hot": hot}


@contextmanager
def admitted(kind="other", label="", wait=None, root=None, say=lambda s: print(s, flush=True), sleep=time.sleep, now=time.monotonic,
             temp_fn=temperature, local=None):
    """Hold one slot for the body. Queues (one line said, then silence) until a slot is offered; `wait` seconds
    at most (None: no limit), then TimeoutError. Never asks anyone. Inside a job that already holds a slot (NESTED in
    its environment: `fieldkit guard` around `admit suite`, a build stage inside a guarded build) nothing more is
    taken, so a job never waits on itself."""
    if os.environ.get(NESTED):
        yield None
        return
    local = _local() if local is None else local
    base, hot = base_slots(local), hot_line(local)
    t0, marker, slot = now(), None, None
    try:
        while True:
            slot = try_take(offered(base, temp_fn(), hot), kind, label, root)
            if slot:
                break
            if marker is None:
                marker = _mark_waiting(kind, label, root)
                busy = ", ".join(f"{h.get('kind')} {h.get('label') or ''}".strip() for h in holders(root))
                say(f"ADMISSION  queued: {kind} {label}".rstrip() + f" waits for a slot (held: {busy or 'hot CPU'})")
            if wait is not None and now() - t0 >= wait:
                raise TimeoutError(f"no slot within {wait:g} s")
            sleep(POLL_S)
        if marker is not None:
            marker.unlink(missing_ok=True)
            marker = None
            say(f"ADMISSION  admitted after {now() - t0:.0f} s in the queue")
        os.environ[NESTED] = f"{os.getpid()}:{slot.i}"
        yield slot
    finally:
        if marker is not None:
            marker.unlink(missing_ok=True)
        if slot:
            os.environ.pop(NESTED, None)
            slot.release()


def run(cmd, kind="other", label="", wait=None, root=None, call=subprocess.call, cwd=None):
    """-> CMD's exit code, run inside one slot; 75 (EX_TEMPFAIL) when `wait` ran out with no slot."""
    try:
        with admitted(kind, label or " ".join(cmd)[:80], wait, root):
            return call(cmd, cwd=cwd) if cwd else call(cmd)
    except TimeoutError as e:
        print(f"ADMISSION  NOT RUN: {e}; nothing was started")
        return 75


def suite_cmd(paths=(), basetemp=None, report=None):
    """The pytest suite of the current folder (a placed job's tree copy). Paths are relative and inside it. report: a
    JUnit XML file pytest writes (relative, inside the tree), the evidence an offloaded suite sends back."""
    for p in [*paths, *([report] if report else [])]:
        if os.path.isabs(p) or ".." in Path(p).parts or p.startswith("-"):
            raise ValueError(f"{p!r}: a suite path is relative, inside the tree, and not an option")
    bt = basetemp or tempfile.mkdtemp(prefix="fk-admit-suite-")
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp", bt, "-rf",
            *(["--junitxml", report] if report else []), *paths]


def lines(st):
    out = [f"ADMISSION  {st['held']} of {st['offered']} slot(s) held ({st['slots']} on this computer"
           + (f"; {st['temp_c']:.0f} C >= {st['hot']:.0f} C, one fewer offered" if st["offered"] < st["slots"] else "")
           + f"), {len(st['waiting'])} waiting"]
    out += [f"  slot {h['slot']}: {h.get('kind', '?')} {h.get('label', '')} (pid {h.get('pid')}, since {h.get('since')})"
            for h in st["holders"]]
    out += [f"  waiting: {w.get('kind')} {w.get('label', '')} (pid {w.get('pid')}, since {w.get('since')})"
            for w in st["waiting"]]
    return out


def main(argv=None):
    import argparse
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog="fieldkit admit", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", choices=["status", "suite"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--kind", choices=KINDS, default="other")
    ap.add_argument("--label", default="")
    ap.add_argument("--wait", type=float, help="seconds to wait for a slot at most (default: as long as it takes)")
    ap.add_argument("--report", help="suite: a JUnit XML report pytest writes (relative, inside the folder)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "status":
        st = state(temp_c=temperature())
        print(json.dumps(st, indent=1) if a.json else "\n".join(lines(st)))
        return 0
    if a.action == "suite":
        try:
            cmd = suite_cmd(a.paths, report=a.report)
        except ValueError as e:
            print(f"ADMISSION  NOT RUN: {e}")
            return 2
        return run(cmd, "test", a.label or f"suite {os.path.basename(os.getcwd())}", a.wait)
    if not cmd:
        print("usage: fieldkit admit [--kind K] [--label L] -- CMD ...   |   fieldkit admit suite|status")
        return 2
    return run(cmd, a.kind, a.label, a.wait)


if __name__ == "__main__":
    sys.exit(main())
