# Version: 1.0.0 · updated 26-10-22-14-40
"""How strong is this machine? Fixed workloads, measured, stored per host (fieldkit eta calibrate).

Two machines share one long goal (state/proposed/long-horizon-2026-10-22/DESIGN-MULTI-MACHINE.md): a job goes to the
peer only when (peer score / own score) saves more than the transfer costs. That needs the same yardstick on both, so
every number here comes from a FIXED workload (the same bytes, the same source text, every run, every machine):

  cpu single   compile a fixed generated Python module + zlib-compress a fixed 16 MiB buffer, one process, best of 3
  cpu multi    the same unit in one process per hardware thread at once: aggregate units/s and the scaling factor
  disk         sequential write with fsync (MB/s) and 4 KiB random writes each fsync'ed (IOPS), next to the state
               folder; reads are not measured (the file just written comes back from the cache, not the disk)
  ram          total and available, and a 256 MiB copy (GB/s)
  thermal      every thread busy until the CPU reaches the owner's cap (70 C) or --minutes pass: minutes to 70 C,
               idle and peak. Laptop rule: everything runs inside the build guard (fan check fails closed, the
               governor caps the processor above thermal.build_target + 3)

Stored in state/machines/<host>.json; a run replaces the file and keeps the previous result under "previous".
"""
import hashlib
import json
import os
import platform
import socket
import statistics
import sys
import tempfile
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from .settings import state_root as _state_root

ROOT = Path(__file__).resolve().parents[2]
MACHINES = _state_root(ROOT) / "state" / "machines"
WORKLOAD = "fk-cal-1"            # change it whenever a workload below changes: scores of different workloads never compare
CAP_C = 70.0                     # the owner's thermal cap on this laptop (memory: thermal-cap-and-build-time)
FUNCS = 400                      # functions in the generated module
BUF_MIB = 16


def _source(n=FUNCS):
    return "\n".join(f"def f{i}(a, b=({i}, '{i}')):\n    x = [a * k for k in range({i % 17})]\n"
                     f"    return {{'k': x, 's': sum(x) + len(b), 't': tuple(b)}} if a else None\n" for i in range(n))


def _buffer(mib=BUF_MIB):
    """Deterministic, half-compressible bytes: sha256 chains with repeated text in between."""
    out, h, i = bytearray(), b"fieldkit", 0
    while len(out) < mib << 20:
        h = hashlib.sha256(h).digest()
        out += h + (b"the same words again " * (i % 5))
        i += 1
    return bytes(out[: mib << 20])


def unit(scale=1.0):
    """One work unit: compile the module, compress the buffer. -> seconds. `scale` < 1 only in tests."""
    src, buf = _source(max(1, int(FUNCS * scale))), _buffer(max(1, int(BUF_MIB * scale)))
    t0 = time.perf_counter()
    compile(src, "<calibrate>", "exec")
    zlib.compress(buf, 6)
    return time.perf_counter() - t0


def cpu_single(repeat=3, scale=1.0):
    times = [unit(scale) for _ in range(repeat)]
    best = min(times)
    return {"seconds_best": round(best, 4), "seconds_all": [round(t, 4) for t in times], "units_per_s": round(1 / best, 3)}


def cpu_multi(workers=None, per_worker=2, scale=1.0, single=None):
    workers = workers or os.cpu_count() or 1
    with ProcessPoolExecutor(workers) as ex:
        list(ex.map(unit, [scale * 0.05] * workers))              # start every worker first: spawn is not work
        t0 = time.perf_counter()
        list(ex.map(unit, [scale] * (workers * per_worker)))
        wall = time.perf_counter() - t0
    ups = workers * per_worker / wall
    out = {"workers": workers, "units": workers * per_worker, "wall_s": round(wall, 3), "units_per_s": round(ups, 3)}
    if single:
        out["scaling"] = round(ups / single["units_per_s"], 2)
    return out


def disk(near=None, mib=256, seconds=3.0):
    Path(near or MACHINES).mkdir(parents=True, exist_ok=True)       # a first run on a machine has no folder yet
    d = Path(tempfile.mkdtemp(prefix="fk-calibrate-", dir=str(near or MACHINES)))
    p = d / "trial.bin"
    block = os.urandom(1 << 20)
    try:
        t0 = time.perf_counter()
        with open(p, "wb") as f:
            for _ in range(mib):
                f.write(block)
            f.flush()
            os.fsync(f.fileno())
        seq = mib / (time.perf_counter() - t0)
        n, small = 0, os.urandom(4096)
        fd = os.open(str(p), os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            end, k = time.perf_counter() + seconds, 1
            t0 = time.perf_counter()
            while time.perf_counter() < end:
                k = (k * 1103515245 + 12345) % (mib * 256)        # deterministic 4 KiB slots across the file
                os.lseek(fd, k * 4096, os.SEEK_SET)
                os.write(fd, small)
                os.fsync(fd)
                n += 1
            iops = n / (time.perf_counter() - t0)
        finally:
            os.close(fd)
    finally:
        p.unlink(missing_ok=True)
        d.rmdir()
    return {"folder": str(Path(near or MACHINES)), "seq_write_mb_s": round(seq, 1), "rand4k_sync_write_iops": round(iops),
            "reads": "not measured (the page cache would answer, not the disk)"}


def memory_totals():
    if sys.platform == "win32":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys, m.ullAvailPhys
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        info[k] = int(v.split()[0]) * 1024
    return info.get("MemTotal"), info.get("MemAvailable")


def ram(mib=256):
    total, avail = memory_totals()
    src = bytearray(mib << 20)
    best = min(_copy_time(src) for _ in range(3))
    return {"total_gb": round(total / 2**30, 2) if total else None, "available_gb": round(avail / 2**30, 2) if avail else None,
            "copy_gb_s": round(mib / 1024 / best, 2)}


def _copy_time(src):
    t0 = time.perf_counter()
    bytes(src)
    return time.perf_counter() - t0


def minutes_to(samples, target=CAP_C):
    """[(seconds since load began, C)] -> minutes until the first reading >= target, or None."""
    for t, c in samples:
        if c is not None and c >= target:
            return round(t / 60, 2)
    return None


START_C = 45.0                   # the thermal load starts from here, so two runs (and two machines) start alike
COOL_MAX_S = 600


def thermal(sensor, minutes=10.0, target=CAP_C, interval=2.0, load=None, clock=time.monotonic, sleep=time.sleep,
            start_c=START_C):
    """Wait until the CPU is at or under START_C (2026-10-22: the first run began at 52 C, straight after the CPU
    tests, and said 0.43 min), then every thread busy until the sensor reads TARGET or MINUTES pass; the load stops
    at once either way."""
    from ..thermal import sensors
    t_cool = clock()
    idle = sensor()
    while idle is not None and idle > start_c and clock() - t_cool < COOL_MAX_S:
        sleep(10)
        idle = sensor()
    if idle is not None and idle > start_c:
        return {"target_c": target, "idle_c": idle, "start_c": start_c, "minutes_to_target": None, "samples": [],
                "verdict": f"not measured: the CPU stayed at {idle} C, over {start_c:.0f} C, for {COOL_MAX_S // 60} min"}
    procs = (load or sensors._load)(os.cpu_count() or 1)
    samples, t0 = [], clock()
    try:
        while clock() - t0 < minutes * 60:
            c = sensor()
            samples.append((round(clock() - t0, 1), c))
            if c is not None and c >= target:
                break
            sleep(interval)
    finally:
        for p in procs:
            p.kill()
    vals = [c for _, c in samples if c is not None]
    m = minutes_to(samples, target)
    return {"target_c": target, "idle_c": idle, "start_c": start_c, "cooled_s": round(t0 - t_cool),
            "peak_c": max(vals) if vals else None, "minutes_to_target": m,
            "loaded_minutes": round(samples[-1][0] / 60, 2) if samples else 0,
            "verdict": (f"{target:.0f} C after {m} min of every thread busy, from {idle} C" if m is not None else
                        f"{target:.0f} C not reached in {minutes:g} min (peak {max(vals) if vals else '?'} C)"),
            "samples": samples}


def cpu_name():
    if sys.platform == "win32":
        import subprocess
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
                           capture_output=True, text=True)
        return r.stdout.strip() or platform.processor()
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def store(result, host=None, folder=None):
    folder = Path(folder or MACHINES)
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{host or socket.gethostname()}.json"
    if p.is_file():
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
            old.pop("previous", None)
            result["previous"] = old
        except ValueError:
            pass
    p.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return p


def ratio(a, b, key=("cpu_multi", "units_per_s")):
    """How many times faster machine A is than B on one score (for the delegation rule). None without both."""
    try:
        x, y = a[key[0]][key[1]], b[key[0]][key[1]]
        return round(x / y, 2) if x and y else None
    except (KeyError, TypeError):
        return None


def calibrate(minutes=10.0, quick=False, say=print, guard=None, sensor=None):
    """Run every measurement (inside the build guard on Windows) and store it. -> (result, path)."""
    res = {"host": socket.gethostname(), "os": f"{platform.system()} {platform.release()}", "cpu": cpu_name(),
           "threads": os.cpu_count(), "python": platform.python_version(), "workload": WORKLOAD,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if guard is None:
        from ..thermal import buildguard
        guard = buildguard.guard
    if sensor is None:
        sensor = _sensor(say)                                     # the guard needs it on Windows even when --quick
    with guard(say, label="calibrate", sensor=sensor) as gov:
        say("  cpu single ...")
        res["cpu_single"] = cpu_single()
        say("  cpu multi ...")
        res["cpu_multi"] = cpu_multi(single=res["cpu_single"])
        say("  disk ...")
        res["disk"] = disk()
        say("  ram ...")
        res["ram"] = ram()
        if quick or sensor is None:
            res["thermal"] = {"verdict": "not measured" + (" (--quick)" if quick else " (no CPU sensor proven)")}
        else:
            say(f"  thermal: every thread busy until {CAP_C:.0f} C or {minutes:g} min ...")
            res["thermal"] = thermal(sensor, minutes)
        if gov is not None:
            res["governor"] = {"peak_c": gov.peak, "cap_pct_end": gov.cap, "verdict": gov.verdict}
    return res, store(res)


def _sensor(say):
    if sys.platform == "win32":
        from ..buildh import buildrun
        _, fn, _ = buildrun.thermal_sensor(say)
        return fn
    from ..thermal import sensors
    _, fn, detail = sensors.best()
    say(f"  thermal source: {detail}")
    return fn


def lines(r, path=None):
    out = [f"{r['host']}: {r['cpu']}, {r['threads']} threads, {r['os']} (workload {r['workload']})",
           f"  cpu single  {r['cpu_single']['units_per_s']} units/s (best {r['cpu_single']['seconds_best']} s)",
           f"  cpu multi   {r['cpu_multi']['units_per_s']} units/s on {r['cpu_multi']['workers']} workers "
           f"(x{r['cpu_multi'].get('scaling')} of one)",
           f"  disk        {r['disk']['seq_write_mb_s']} MB/s sequential write, {r['disk']['rand4k_sync_write_iops']} "
           f"random 4 KiB sync writes/s",
           f"  ram         {r['ram']['total_gb']} GB total, {r['ram']['available_gb']} GB free, copy {r['ram']['copy_gb_s']} GB/s",
           f"  thermal     {r['thermal']['verdict']}"]
    if r.get("governor"):
        g = r["governor"]
        out.append(f"  guard       peak {g['peak_c']} C, cap {g['cap_pct_end']} %" + (f", STOPPED: {g['verdict']}" if g["verdict"] else ""))
    if path:
        out.append(f"VERDICT  CALIBRATED (stored: {path})")
    return out
