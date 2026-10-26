"""The load each device carries, and where a heavy job goes when this computer is full.

    fieldkit orchestrate load [--json]                    one line per device: CPU, RAM, temperature, the gate's
                                                          slots, heavy jobs running, what it can run
    fieldkit orchestrate place [--tree REPO] [--any-os] [--wait S] [--project P] [PATH ...]
                                                          the test suite of REPO's tree: here when a slot is free,
                                                          else on an idle capable device over the cable, else
                                                          here after the queue; the verdict says where it ran

Stage O4 of the household orchestrator (local/PROJECT-MANDATE-PLAN.md section 19; the Windows command post's order of
2026-10-26: who analyses the load jobs put on each computer, and who spreads the work across the cable?). Nothing here
asks a model or the owner: the rules are fixed and written below.

LOAD REPORT: `report()` is this computer's numbers, from the hog's own probes (fieldkit/hog/probe.py: RAM and the
proven CPU sensor), the admission gate (fieldkit/admission.py) and the process table. Other devices answer the link
verb `load`, read-only like hello and manifest (so not one of link.JOB_VERBS: it travels on the LAN as well).

PLACEMENT, in this order:
  1. a slot is free here now: the suite runs here;
  2. else a device is IDLE and CAPABLE: it answered `load`, has a free slot, CPU under 50 %, below its hot line, has
     python, pytest and git, and runs the same OS (unless --any-os: a suite that does not depend on the OS). The one
     with the most free slots, then the lowest CPU, gets it. The tree is the exact commit `git archive` writes for
     the tree hash testcost uses (tracked and untracked files, .gitignore respected), sent with `peers link put`;
     the job is `fieldkit admit suite` (stage O3's run job, so the worker's own gate admits it too). The result comes
     back signed by the worker's key and is recorded in testcost for that tree hash, with that OS;
  3. else the load is read again every 5 s (--seek S caps it) and the first to free takes it: a slot here or an idle
     device. When anything over the cable fails: here, after the queue. A remote failure is said, never hidden.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import admission

IDLE_CPU = 50.0
SEEK_S = 5.0                                          # place: how often the load is read again while queued
HEAVY = (                                             # (what, needles that must ALL be in one command line)
    ("full suite", ("pytest",)),
    ("mutate", ("fieldkit", "mutate")),
    ("go test", ("go", "test")),
    ("build", ("mach", "build")),
    ("build", ("cargo", "build")),
    ("build", ("make", "-j")),
    ("build", ("ninja",)),
    ("build", ("fieldkit", "build")),
)


def heavy_jobs(procs=None):
    """-> [{what, pid}] for running processes whose command line matches HEAVY (each pid once)."""
    if procs is None:
        import psutil
        procs = []
        for p in psutil.process_iter(["cmdline"]):
            try:
                procs.append((p.pid, p.info.get("cmdline") or []))
            except Exception:  # noqa: BLE001 - a process that ended while being read
                continue
    out = []
    me = os.getpid()
    for pid, cmd in procs:
        if pid == me or not cmd:
            continue
        words = [str(c).replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe") for c in cmd]
        for what, needles in HEAVY:
            if all(any(n == w or (n == "pytest" and w.startswith("pytest")) for w in words) for n in needles):
                out.append({"what": what, "pid": pid})
                break
    return out


def capabilities(which=shutil.which):
    def has_mod(m):
        import importlib.util
        try:
            return importlib.util.find_spec(m) is not None
        except (ImportError, ValueError):
            return False
    return {"python": sys.version.split()[0], "pytest": has_mod("pytest"), "git": bool(which("git")),
            "go": bool(which("go")), "opencode": bool(which("gorilla-opencode"))}


def report(cpu_fn=None, temp_fn=None, procs=None, gate=None):
    """-> this computer's load: {host, os, cores, cpu_pct, load1, ram_total, ram_free, temp_c, hot, gate, heavy,
    can, idle, at}. Read-only, under a second."""
    import psutil
    from .peers import link
    t = (temp_fn or admission.temperature)()
    g = gate or admission.state(temp_c=t)
    v = psutil.virtual_memory()
    try:
        load1 = round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        load1 = None                                  # Windows has no load average
    cpu = (cpu_fn or (lambda: psutil.cpu_percent(interval=0.5)))()
    heavy = heavy_jobs(procs)
    r = {"host": socket.gethostname(), "os": link.os_tag(), "cores": psutil.cpu_count() or 1,
         "cores_physical": psutil.cpu_count(logical=False), "cpu_pct": round(float(cpu), 1), "load1": load1,
         "ram_total": v.total, "ram_free": v.available, "temp_c": t, "hot": g["hot"],
         "gate": {k: g[k] for k in ("slots", "offered", "held", "free")} | {"waiting": len(g["waiting"])},
         "heavy": heavy, "can": capabilities(), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    r["idle"] = idle(r)
    return r


def idle(r):
    """The fixed rule: a free slot, CPU under IDLE_CPU, and below the hot line (an unknown temperature is not hot:
    the gate on that device still decides)."""
    g = r.get("gate") or {}
    hot = r.get("temp_c") is not None and r.get("hot") is not None and r["temp_c"] >= r["hot"]
    return bool(g.get("free", 0) > 0 and float(r.get("cpu_pct") or 0) < IDLE_CPU and not hot)


def capable(r, os_name, any_os=False):
    c = r.get("can") or {}
    return bool(c.get("pytest") and c.get("git") and (any_os or r.get("os") == os_name))


def load_all(call_fn=None, here=None):
    """-> [row]: this device first, then every other household device's answer to `load` (or why it gave none)."""
    from .peers import link
    call_fn = call_fn or link.call
    h = link.household()
    rows = [dict(here or report(), name=h.get("me") or socket.gethostname(), me=True, ok=True)]
    for d in link.others(h) if h else []:
        a = call_fn({"verb": "load"}, device=d["name"])
        if a.get("ok") and isinstance(a.get("load"), dict):
            rows.append(dict(a["load"], name=d["name"], me=False, ok=True))
        else:
            rows.append({"name": d["name"], "me": False, "ok": False, "os": d.get("os") or "",
                         "error": a.get("error") or "no load report (an older Fieldkit there?)"})
    return rows


def line(r):
    if not r.get("ok"):
        return f"  {r['name']:<20} {r.get('os') or '?':<8} NO REPORT: {str(r.get('error'))[:90]}"
    g = r["gate"]
    t = f"{r['temp_c']:.0f}/{r['hot']:.0f}C" if r.get("temp_c") is not None else "temp ?"
    heavy = ", ".join(sorted({h["what"] for h in r.get("heavy") or []})) or "none"
    can = ",".join(k for k in ("pytest", "git", "go", "opencode") if (r.get("can") or {}).get(k)) or "-"
    return (f"  {r['name']:<20} {r['os']:<8} {'IDLE' if r['idle'] else 'BUSY'}  cpu {r['cpu_pct']:>5.1f}%  "
            f"ram free {r['ram_free'] / 2 ** 30:4.1f}/{r['ram_total'] / 2 ** 30:.0f} GB  {t:<9} "
            f"slots {g['held']}/{g['offered']} (+{g['waiting']} waiting)  heavy: {heavy}  can: {can}")


def choose(rows, os_name, any_os=False):
    """-> the other device that gets the job, or None. Most free slots first, then the lowest CPU, then the name."""
    ok = [r for r in rows if not r.get("me") and r.get("ok") and idle(r) and capable(r, os_name, any_os)]
    ok.sort(key=lambda r: (-r["gate"]["free"], float(r["cpu_pct"]), r["name"]))
    return ok[0] if ok else None


def export_tree(repo, out_dir=None):
    """-> (folder with exactly the files of the testcost tree hash, that hash). Nothing in the repo is changed."""
    from . import testcost
    key = testcost.tree_key(repo)
    d = Path(out_dir or tempfile.mkdtemp(prefix="fk-place-")) / f"tree-{key[:12]}"
    d.mkdir(parents=True)
    tar = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", key], capture_output=True)
    if tar.returncode:
        raise RuntimeError(f"git archive {key[:12]}: {tar.stderr.decode('utf-8', 'replace')[:200]}")
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as tf:
        tf.extractall(d, filter="data")
    return d, key


def place(repo=".", paths=(), any_os=False, wait=3600.0, project="Gorilla.Fieldkit", rows=None, send=None,
          wait_fn=None, result_fn=None, run_here=None, record=None, say=None, load_fn=None, sleep=time.sleep,
          now=time.monotonic, seek_s=SEEK_S, seek_max=None):
    """-> {where, how, status, rc, note}. The rules are in this module's docstring. While nothing is free, the load
    is read again every `seek_s` seconds and whichever frees first takes the suite, a slot here or an idle device
    (`seek_max` seconds at most, None: until one does; then the local queue)."""
    from .peers import link
    say = say or (lambda s: print(s, flush=True))     # before the suite's own output, in order
    name_here = Path(repo).resolve().name
    run_here = run_here or (lambda: admission.run(admission.suite_cmd(paths), "test", f"suite {name_here}",
                                                  cwd=str(repo)))
    me = link.os_tag()
    t0, said_seek = now(), False
    while True:
        slot = admission.try_take(admission.state()["offered"], "test", f"place {name_here}")
        if slot:                                      # rule 1: room here now
            slot.release()
            say("PLACE  a slot is free here: the suite runs on this computer")
            rc = run_here()
            return {"where": "here", "how": "free slot" if not said_seek else "slot freed here first",
                    "status": "pass" if rc == 0 else "fail", "rc": rc, "note": ""}
        rows = (load_fn or load_all)() if rows is None else rows
        pick = choose(rows, me, any_os)
        if pick is not None:                          # rule 2: an idle capable device
            r, note = _send_to(pick, repo, paths, wait, project, send, wait_fn, result_fn, record, say, me)
            if r is not None:
                return r
            break
        note = "no other device is idle and capable"
        if seek_max is not None and now() - t0 >= seek_max:
            break
        if not said_seek:
            say(f"PLACE  {note} yet: reading the load again every {seek_s:g} s, the first free slot or idle "
                "device takes it")
            said_seek = True
        sleep(seek_s)
        rows = None
    say(f"PLACE  {note}: the suite runs here after the queue")
    rc = run_here()
    return {"where": "here", "how": "queued here", "status": "pass" if rc == 0 else "fail", "rc": rc, "note": note}


def _send_to(pick, repo, paths, wait, project, send, wait_fn, result_fn, record, say, me):
    """-> (result row, '') when the device ran it (pass or fail), else (None, why it did not): fail closed."""
    from . import orchestrate_work as W
    from . import testcost
    from .mandate import jobs
    record = record or testcost.record
    name = pick["name"]
    try:
        tree, key = export_tree(repo)
        t0 = time.time()
        jid, said, t = (send or W.send)("run", name, str(tree), project, argv=["admit", "suite", *paths],
                                         wall=max(1, int(wait // 60) or 1))
        shutil.rmtree(tree.parent, ignore_errors=True)
        if said != "sent":
            return None, f"{name} did not take it: {said}"
        say(f"PLACE  sent to {name} ({pick['os']}): job {jid}")
        e = (wait_fn or jobs.wait)(jid, wait)
        r = (result_fn or W.verified_result)(jid, project, t["digest"]) if e else None
        if r is None:
            return None, f"{name}: no signed result within {wait:g} s"
        if r["status"] not in ("pass", "fail"):
            return None, f"{name} could not run it: {r['evidence'].splitlines()[0][:120]}"
        check = "full-suite" if not paths else "placed-suite"
        if r.get("os") and r["os"] != me:
            check += f"@{r['os']}"                     # another OS's pass never lets this OS skip its own suite
        record(check, r["status"], time.time() - t0, key, f"placed on {name} ({r.get('os')}), job {jid}")
        first = r["evidence"].splitlines()[0] if r.get("evidence") else ""
        return {"where": name, "how": "placed", "status": r["status"], "rc": 0 if r["status"] == "pass" else 1,
                "note": first, "job": jid, "key": key, "os": r.get("os")}, ""
    except Exception as e:  # noqa: BLE001 - fail closed: anything over the cable going wrong runs it here
        return None, f"placing on {name} failed: {type(e).__name__}: {e}"


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit orchestrate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["load", "place"])
    ap.add_argument("paths", nargs="*", help="place: test paths inside the tree (default: the whole suite)")
    ap.add_argument("--tree", default=".", help="place: the git checkout whose tree is tested")
    ap.add_argument("--any-os", action="store_true", help="place: the suite does not depend on the OS")
    ap.add_argument("--wait", type=float, default=3600.0, help="place: seconds to wait for a remote result")
    ap.add_argument("--project", default="Gorilla.Fieldkit", help="place: the repository the mandate names")
    ap.add_argument("--seek", type=float, help="place: seconds at most to keep reading the load for an idle device "
                                               "(default: until a slot here or a device frees)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "load":
        rows = load_all()
        if a.json:
            print(json.dumps(rows, indent=1, ensure_ascii=False))
            return 0
        print(f"LOAD  {len(rows)} device(s)")
        print("\n".join(line(r) for r in rows))
        bad = [r for r in rows if not r.get("ok")]
        idle_n = sum(1 for r in rows if r.get("ok") and r["idle"])
        print(f"VERDICT  {idle_n} idle, {len(rows) - idle_n - len(bad)} busy" + (f", {len(bad)} no report" if bad
                                                                                 else ""))
        return 3 if bad else 0
    r = place(a.tree, a.paths, a.any_os, a.wait, a.project, seek_max=a.seek)
    print(f"VERDICT  {r['status'].upper()} on {r['where']} ({r['how']})" + (f": {r['note']}" if r["note"] else ""))
    return r["rc"] if isinstance(r["rc"], int) and r["rc"] < 256 else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
