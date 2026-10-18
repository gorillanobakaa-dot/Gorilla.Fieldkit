# Version: 1.0.0 · updated 26-10-18-20-00
"""How long will it take? Computed from measurements, never guessed (fieldkit eta).

Owner, 2026-10-18: "everything is computable ... it should be a simple mathematical operation". Overnight a model
said "1-2 minutes" for 8, "8-10" for 37, "~3 remaining" for 19. Hardware constants (disk MB/s, PCIe lanes) give only
a ceiling: deleting 1 PB is bound by how many FILES there are, not bytes, and the per-file rate depends on the disk
and the filesystem. So every answer here rests on one of four bases, and says which:

  plan     the remaining steps are known: their planned durations, times the overhead measured on the steps already
           done in this run (the leak gate: its scenario table, its own run folders)
  history  the same step ran before: the median of its recorded durations (state/eta/history.jsonl; build-run records
           every stage it runs)
  live     a running job with a known total: units done / elapsed = measured rate; remaining / rate
  sample   before starting: count the work (files, bytes), time a small trial of the same operation on the same disk

    fieldkit eta leakgate TASK|RUN_DIR      fieldkit eta build TASK      fieldkit eta history [STEP]
    fieldkit eta delete PATH                fieldkit eta copy SRC DEST_DIR
    fieldkit eta record STEP SECONDS
Answers: {"remaining_s", "ends", "basis", "evidence"}; no basis -> remaining_s None and the reason, never a number.
"""
import json
import os
import shutil
import statistics
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "state" / "eta" / "history.jsonl"


def answer(remaining_s, basis, evidence, now=None):
    now = now or time.time()
    if remaining_s is None:
        return {"remaining_s": None, "ends": None, "basis": basis, "evidence": evidence}
    remaining_s = max(0, int(remaining_s))
    return {"remaining_s": remaining_s, "ends": time.strftime("%H:%M", time.localtime(now + remaining_s)),
            "basis": basis, "evidence": evidence}


def human(a):
    if a["remaining_s"] is None:
        return f"ETA: unknown - {a['evidence']}"
    m = a["remaining_s"] // 60
    span = f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min" if m else f"{a['remaining_s']} s"
    return f"ETA: {span} left, ends about {a['ends']} ({a['basis']}: {a['evidence']})"


# ---- history ---------------------------------------------------------------------------------------------------
def record(step, seconds, path=None, at=None):
    p = Path(path or HISTORY)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"step": step, "seconds": round(float(seconds)), "at": at or time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")


def durations(step, path=None):
    p = Path(path or HISTORY)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("step") == step:
            out.append(e["seconds"])
    return out


def from_history(step, elapsed=0, path=None, now=None):
    ds = durations(step, path)[-7:]
    if not ds:
        return answer(None, "history", f"no recorded run of '{step}' yet")
    med = statistics.median(ds)
    left = med - elapsed
    if left < 0:
        return answer(None, "history", f"'{step}' is already {int(elapsed // 60)} min in, past every recorded run "
                                       f"(median {int(med // 60)} min of {len(ds)}): no basis for a number")
    return answer(left, "history", f"'{step}' took {', '.join(str(int(d // 60)) + ' min' for d in ds)}; "
                                   f"median {int(med // 60)} min, {int(elapsed // 60)} min done", now)


# ---- live ------------------------------------------------------------------------------------------------------
def live(done, total, started, now=None, unit="units"):
    now = now or time.time()
    el = now - started
    if done <= 0 or el <= 0:
        return answer(None, "live", f"nothing done yet: no measured rate ({done}/{total} {unit})")
    rate = done / el
    return answer((total - done) / rate, "live", f"{done}/{total} {unit} in {int(el // 60)} min = "
                                                 f"{rate * 60:.2f} {unit}/min measured", now)


# ---- plan: the leak gate -----------------------------------------------------------------------------------------
def _created(p):
    return p.stat().st_ctime          # Windows: creation time; the run's profile folder is made when it starts


def leakgate_plan(repeat, quick):
    """[(run name, planned seconds)] in the gate's order, from its own scenario table."""
    from ..leakgate import gate, scenarios as sc
    out = []
    for name, _t, secs, _a, _h, watch in sc.SCENARIOS:
        s = sc.QUICK.get(name, secs) if quick else secs
        w = min(watch, 10) if quick else watch
        for rep in range(repeat):
            for mode in gate.modes_for(name, quick):
                out.append((f"{name}-r{rep}-{mode}", s + w))
    return out


def leakgate(run_dir, repeat=None, quick=None, now=None, stamp=None):
    """Planned seconds of the runs not yet started, times the overhead measured on this run's finished runs.
    Finished = a folder under runs/ (its creation time is when the run started)."""
    from ..leakgate import status as st
    d = Path(run_dir)
    if (d / "artifacts.json").is_file():
        return answer(0, "plan", f"{d.name} has finished (artifacts.json written)", now)
    pl = st.plan(d)
    repeat = repeat or pl.get("repeat") or 3
    quick = pl.get("quick") if quick is None and pl.get("quick") is not None else bool(quick)
    plan = leakgate_plan(repeat, quick)
    runs = d / "runs"
    # one profile folder per run, named profile-<scenario>-r<rep>-<mode>, made when the run starts
    started = sorted((p for p in runs.iterdir() if p.is_dir() and p.name.startswith("profile-")),
                     key=lambda p: (stamp or _created)(p)) if runs.is_dir() else []
    if not started:
        total = sum(s for _, s in plan)
        return answer(total, "plan", f"{len(plan)} runs planned, {total // 60} min of scenario time; overhead not "
                                     "measured yet (no run finished), so this is a floor", now)
    planned = dict(plan)
    names = [p.name[len("profile-"):] for p in started]
    done = names[:-1]                                     # the newest folder is the run in progress
    times = [(stamp or _created)(p) for p in started]
    actual = times[-1] - times[0]
    plan_done = sum(planned.get(n, 0) for n in done)
    factor = actual / plan_done if plan_done else 1.0
    if factor <= 0:
        return answer(None, "plan", "the finished runs have no measurable duration yet", now)
    left = [s for n, s in plan if n not in names] + [planned.get(names[-1], 0)]
    rest = sum(left) * factor
    return answer(rest, "plan", f"{len(done)}/{len(plan)} runs done; the rest are {sum(left) // 60} min of planned "
                                f"scenario time x {factor:.2f} measured overhead (start, shutdown, sensors); the final "
                                "audits after the last run are not included", now)


# ---- sample: delete and copy -----------------------------------------------------------------------------------
def count(path):
    """(files, bytes) under path; junctions and symlinks not followed."""
    files = size = 0
    stack = [str(path)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_symlink() or (hasattr(e, "is_junction") and e.is_junction()):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            files += 1
                            size += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return files, size


def _trial_dir(near):
    """A scratch folder on the same volume as `near` (the trial must use the same disk and filesystem)."""
    anchor = Path(near).resolve().anchor or "/"
    base = Path(anchor) / ".fieldkit-eta-trial"
    try:
        base.mkdir(exist_ok=True)
        return Path(tempfile.mkdtemp(dir=base))
    except OSError:
        return Path(tempfile.mkdtemp())


def delete_rate(near, n=2000):
    """Files deleted per second on the volume of `near`: create n small files in a scratch folder there, time
    deleting them. Only the scratch files are deleted."""
    d = _trial_dir(near)
    try:
        for i in range(n):
            (d / f"f{i}").write_bytes(b"x" * 512)
        t0 = time.perf_counter()
        for i in range(n):
            os.remove(d / f"f{i}")
        return n / max(time.perf_counter() - t0, 1e-6), str(d.anchor)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def delete(path, now=None, rate=None):
    files, size = count(path)
    if not files:
        return answer(0, "sample", f"{path}: no files")
    r, vol = rate or delete_rate(path)
    return answer(files / r, "sample", f"{files:,} files ({size / 2**30:.1f} GiB) / {r:,.0f} files/s measured by deleting "
                                       f"2,000 scratch files on {vol}; permanent delete; a recycle-bin move is a rename "
                                       "and runs at about the same rate", now)


def copy(src, dest_dir, now=None, mb=128):
    files, size = count(src)
    d = _trial_dir(dest_dir)
    try:
        blob = os.urandom(1 << 20)
        srcf = _trial_dir(src) / "big.bin"
        with open(srcf, "wb") as f:
            for _ in range(mb):
                f.write(blob)
        t0 = time.perf_counter()
        shutil.copyfile(srcf, d / "big.bin")
        bps = (mb << 20) / max(time.perf_counter() - t0, 1e-6)
        small = 300
        for i in range(small):
            (srcf.parent / f"s{i}").write_bytes(b"x" * 4096)
        t0 = time.perf_counter()
        for i in range(small):
            shutil.copyfile(srcf.parent / f"s{i}", d / f"s{i}")
        fps = small / max(time.perf_counter() - t0, 1e-6)
        shutil.rmtree(srcf.parent, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    secs = size / bps + files / fps
    return answer(secs, "sample", f"{files:,} files, {size / 2**30:.1f} GiB: {bps / 2**20:,.0f} MiB/s and {fps:,.0f} "
                                  f"small files/s measured with a {mb} MiB and {small}-file trial on the same disks "
                                  "(the operating system's cache can make a short trial faster than a long copy)", now)


# ---- build: history from the harness's own logs ---------------------------------------------------------------------
import re as _re

_ATTEMPT = _re.compile(r"^(\d\d:\d\d:\d\d)\s+(build|package) attempt \d+:")
_DONE = _re.compile(r"^(\d\d:\d\d:\d\d)\s+(build|package) OK(?:\s+\((\d+) lines\))?")
FULL_LINES = 1000          # a full compile prints ~1,800 lines; an incremental rebuild under 300


def build_log_durations(log_text):
    """[(stage, seconds)] for every stage that finished OK in a build-run log (start: its last attempt line)."""
    out, start = [], {}
    to_s = lambda hms: sum(int(x) * m for x, m in zip(hms.split(":"), (3600, 60, 1)))
    for line in log_text.splitlines():
        m = _ATTEMPT.match(line)
        if m:
            start[m.group(2)] = to_s(m.group(1))
            continue
        m = _DONE.match(line)
        if m and m.group(2) in start:
            s = to_s(m.group(1)) - start.pop(m.group(2))
            stage = m.group(2)
            if stage == "build":
                stage = "build full" if m.group(3) and int(m.group(3)) >= FULL_LINES else "build incremental"
            out.append((stage, s + 86400 if s < 0 else s))
    return out


def import_build_logs(windows_dir=None, product="firefox", path=None):
    """Seed the history from earlier build-run logs (state/windows/*build-run*.log), once per log."""
    wd = Path(windows_dir or ROOT / "state" / "windows")
    seen_p = Path(path or HISTORY).with_name("imported-logs.txt")
    seen = set(seen_p.read_text(encoding="utf-8").split("\n")) if seen_p.is_file() else set()
    n = 0
    for log in sorted(wd.glob("*build-run*.log")) + sorted(wd.glob("158-chain-*.log")):
        if log.name in seen:
            continue
        for stage, secs in build_log_durations(log.read_text(encoding="utf-8", errors="replace")):
            record(f"{product} {stage}", secs, path=path, at=log.name[:15])
            n += 1
        seen.add(log.name)
    seen_p.parent.mkdir(parents=True, exist_ok=True)
    seen_p.write_text("\n".join(sorted(seen)), encoding="utf-8")
    return n


def build_kind(objdir):
    """'full' when the object directory holds no linked browser yet (a first or clean build), else 'incremental'."""
    od = Path(objdir) if objdir else None
    xul = [od / "dist" / "bin" / n for n in ("xul.dll", "libxul.so")] if od else []
    return "incremental" if any(x.is_file() for x in xul) else "full"


def build(stage="build", elapsed=0, product="firefox", objdir=None, now=None):
    import_build_logs(product=product)
    if stage == "build":
        stage = f"build {build_kind(objdir)}"
    return from_history(f"{product} {stage}", elapsed, now=now)


def lines(a):
    return [human(a)]
