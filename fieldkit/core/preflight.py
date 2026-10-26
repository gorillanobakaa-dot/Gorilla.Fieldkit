"""The ONE run guard for heavy work (a build, a compile, a test run, a mutate pass, a leak gate, an autopilot job).

Owner, 2026-10-26: "when a process like building compiling testing whatever is being managed by the fieldkit this
hog becomes activated as part of the preflight checks. it should go hand in hand with the temperature monitor,
started automatically." One wrapper, so neither the hog nor the temperature watch is ever started by hand, and both
stop when the run ends.

    from fieldkit.core import preflight
    with preflight.guard("cargo build", kind="build", say=say) as g:     # refuses (preflight.Refused) on a STOP
        ...                                                                # g.governor = gov when a governor runs
    preflight.run("leak gate", kind="gate")                                # the checks alone -> {status, lines}
    preflight.register("testcost", fn)                                     # fn(ctx) -> (status, one line)

    python -m fieldkit.core.preflight [--label L] [--kind K] [--wait S] [-- CMD ...]
        no CMD: the checks, exit 0 ok / 1 warn / 2 stop; with CMD: CMD under the guard, its exit code (2 refused)

A check is fn(ctx) -> (status, line), status "ok" | "warn" | "stop", ctx = {"label", "kind"}. Checks come from STEPS
("module:function", imported when run) and from register(). A check that raises is a WARN that says so: preflight
informs and frees; only a check's own STOP refuses (the hog's free-RAM floor), and with wait_s it re-checks every
poll_s seconds until it clears or the time is up (it says so while it waits).

During the run, one sampler thread: RAM and CPU temperature every sample_s seconds (their peaks), the hog's safe
actions every act_s seconds (idle models unloaded, dead sessions' orphans stopped), and the seconds the governor held
the CPU cap under 100%. At the end ONE line: minutes, peak RAM, peak temperature, GB reclaimed, seconds capped.

Skipped (nothing runs, nothing is said) inside pytest and with FIELDKIT_PREFLIGHT=0: a test suite must not unload the
owner's model; the pre-commit hook guards the suite from OUTSIDE pytest.
"""
import argparse
import importlib
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

OK, WARN, STOP = "ok", "warn", "stop"
RANK = {OK: 0, WARN: 1, STOP: 2}
#: in order. Add a "module:function" here (or call register()) to put a check in every guarded run.
STEPS = ["fieldkit.hog:preflight_check", "fieldkit.testcost:preflight_check", "fieldkit.scratch:preflight_check",
         "fieldkit.parity:preflight_check", "fieldkit.tokens.rotate:preflight_check"]
_REGISTERED = []
#: set while a guard runs (children inherit it): a guarded run inside a guarded run gets no second guard
NESTED = "FIELDKIT_RUN_GUARD"


class Refused(RuntimeError):
    pass


def register(name, fn):
    """Add a check for this process (fn(ctx) -> (status, line)). The same name twice replaces the first."""
    _REGISTERED[:] = [r for r in _REGISTERED if r[0] != name] + [(name, fn)]


def _checks(steps=None):
    out = []
    for s in (STEPS if steps is None else steps):
        mod, _, fn = s.partition(":")
        out.append((s, lambda ctx, mod=mod, fn=fn: getattr(importlib.import_module(mod), fn)(ctx)))
    return out + (list(_REGISTERED) if steps is None else [])


def skipped():
    return os.environ.get("FIELDKIT_PREFLIGHT") == "0" or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _once(ctx, checks):
    res = []
    for name, fn in checks:
        try:
            st, line = fn(ctx)
            st = st if st in RANK else WARN
        except Exception as e:      # a broken check informs; it never stops the run
            st, line = WARN, f"{name}: not run ({str(e)[:120]})"
        res.append((name, st, line))
    return res


def run(label="heavy work", kind="build", say=print, wait_s=0, poll_s=30, steps=None, clock=time.time,
        sleep=time.sleep):
    """-> {"status", "lines", "seconds"}. Says each non-OK line and one summary line."""
    if steps is None and skipped():
        return {"status": OK, "lines": [], "seconds": 0.0, "skipped": True}
    t0 = clock()
    ctx = {"label": label, "kind": kind}
    checks = _checks(steps)
    while True:
        res = _once(ctx, checks)
        status = max((r[1] for r in res), key=RANK.get, default=OK)
        if status == STOP and clock() - t0 < wait_s:
            say(f"  preflight {label}: STOP, waiting ({int(clock() - t0)}/{int(wait_s)} s): "
                + "; ".join(r[2] for r in res if r[1] == STOP))
            sleep(poll_s)
            continue
        break
    lines = [r[2] for r in res if r[1] != OK]
    for l in lines:
        say(f"  preflight: {l}")
    took = clock() - t0
    say(f"  preflight {label}: {status.upper()} ({took:.1f} s)")
    return {"status": status, "lines": lines, "seconds": round(took, 2), "results": res}


class Sampler(threading.Thread):
    """RAM and temperature peaks, the hog's safe actions, the governor's capped seconds, while the run runs."""

    def __init__(self, sample_s=15, act_s=300, ram=None, temp=None, act=None, clock=time.time):
        super().__init__(daemon=True, name="fieldkit-run-guard")
        self.sample_s, self.act_s, self.clock = sample_s, act_s, clock
        self._ram, self._temp, self._act = ram or _ram, temp, act
        self._halt = threading.Event()
        self.t0 = clock()
        self.peak_ram, self.total_ram, self.peak_temp = 0, 0, None
        self.reclaimed, self.capped_s, self.governor = 0, 0.0, None
        self._last_act = self.t0

    def tick(self, dt=0.0):
        used, total = self._ram()
        self.peak_ram, self.total_ram = max(self.peak_ram, used), total
        if self._temp:
            try:
                c = self._temp()
                if c is not None:
                    self.peak_temp = max(self.peak_temp or c, c)
            except Exception:
                pass
        g = self.governor
        # capped = the governor holds the cap under where it started (the scheme's own cap, 100% on most machines)
        if g is not None and getattr(g, "cap", None) is not None and g.cap < (getattr(g, "start_cap", None) or 100):
            self.capped_s += dt
        if self._act and self.clock() - self._last_act >= self.act_s:
            self._last_act = self.clock()
            try:
                self.reclaimed += self._act() or 0
            except Exception:
                pass

    def run(self):
        last = self.clock()
        while not self._halt.wait(self.sample_s):
            now = self.clock()
            self.tick(now - last)
            last = now

    def stop(self):
        self._halt.set()
        if self.is_alive():
            self.join(timeout=5)
        self.tick(0.0)

    def line(self, label):
        gb = 2 ** 30
        t = f"{self.peak_temp:.0f} C" if self.peak_temp is not None else "not readable"
        cap = f"{self.capped_s:.0f} s capped" if self.governor is not None else "no governor"
        return (f"  run guard {label}: {(self.clock() - self.t0) / 60:.1f} min, peak RAM {self.peak_ram / gb:.1f}/"
                f"{self.total_ram / gb:.1f} GB, peak CPU {t}, reclaimed {self.reclaimed / gb:.1f} GB, {cap}")


def _ram():
    import psutil
    v = psutil.virtual_memory()
    return v.total - v.available, v.total


def _temp_fn():
    try:
        from ..thermal import sensors
        _n, fn, _w = sensors.best(prove=False)
        return fn
    except Exception:
        return None


def _hog_act():
    from .. import hog
    return sum(max(0, d["ram_before"] - d["ram_after"]) for d in hog.act() if d.get("ok"))


@contextmanager
def guard(label="heavy work", kind="build", say=print, wait_s=0, sample_s=15, act_s=300, sampler=None, steps=None):
    """Preflight (a STOP raises Refused), then the sampler for the length of the block, then the one end line."""
    if (steps is None and skipped()) or os.environ.get(NESTED):
        yield None                  # inside an outer guard (a build stage of a guarded pipeline): one guard per run
        return
    r = run(label, kind, say, wait_s=wait_s, steps=steps)
    if r["status"] == STOP:
        raise Refused("; ".join(r["lines"]) or "a preflight check said stop")
    s = sampler or Sampler(sample_s, act_s, temp=_temp_fn(), act=_hog_act)
    s.tick(0.0)
    s.start()
    os.environ[NESTED] = label
    try:
        yield s
    finally:
        os.environ.pop(NESTED, None)
        s.stop()
        say(s.line(label))


def call(label, kind, fn, *args, say=print, wait_s=0, refused=None, **kw):
    """fn(*args, **kw) under the guard: the one line a call site needs. On a STOP: `refused(why)` when it is
    callable (it may raise the caller's own error), else say why and return `refused`."""
    try:
        with guard(label, kind, say, wait_s=wait_s):
            return fn(*args, **kw)
    except Refused as e:
        if callable(refused):
            return refused(str(e))
        say(f"REFUSED by the preflight ({label}): {e}")
        return refused


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog="python -m fieldkit.core.preflight", description=__doc__.split("\n\n")[0])
    ap.add_argument("--label", default="heavy work")
    ap.add_argument("--kind", default="build", help="build, test, gate, job (the checks read it)")
    ap.add_argument("--wait", type=float, default=0, help="on STOP, re-check for up to this many seconds")
    a = ap.parse_args(argv)
    if not cmd:
        return RANK[run(a.label, a.kind, wait_s=a.wait)["status"]]
    try:
        with guard(a.label, a.kind, wait_s=a.wait):
            return subprocess.call(cmd)
    except Refused as e:
        print(f"REFUSED by the preflight: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
