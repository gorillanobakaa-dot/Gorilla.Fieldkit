# Version: 1.0.0 · updated 26-11-12
"""`fieldkit procs check|reap`: every process tree Fieldkit started, one verdict each, Linux and Windows.

    fieldkit procs check [--json]    OK, ZOMBIES, ORPHAN or HOG per tree; exit 3 when any tree is not OK
    fieldkit procs reap  [--json]    check, then stop Fieldkit's own orphans (by PID + creation time) and reap this
                                     process's own zombies; never anything Fieldkit did not start

WHAT A TREE IS (the two registers the harness already keeps; no third one)
    liveness beacon   state/liveness/runs/*.json: a long run (runbook, emerge, goal supervisor, task runner, link job,
                      pipeline) with its container's recorded members (fieldkit/core/liveness.py, contain.py)
    hog registry      state/hog/pids.jsonl: a process a session started or a tool registered, a detached service
                      (the thermal governor, the link relay) included (fieldkit/hog/registry.py; `record` below)

THE VERDICTS (same on both systems; a number that cannot exist on one system is 0 there)
    ZOMBIES  exited children of a Fieldkit supervisor that nobody collected (Linux; Windows has none). Only the
             PARENT can collect its zombie: a supervisor of this process (the subreaper reaps in its own tick,
             contain.reap_orphans) is fixed by `reap`; a foreign live supervisor is named with its PID, because no
             other process can take its children's exit status. Not a lie: `reap` says what it could not do.
    ORPHAN   a member still alive whose owner (the beacon's process, the registering session) is gone: stopped by
             `reap` while PID and creation time both still match; a service class is never an orphan.
    HOG      a supervisor with more than 50 zombie children, or more than 30 % of a core on average for over 10 min
             (the 2026-11-12 runbook runners: 8313 zombies, 80 % of a core for 7 h).
    OK       otherwise.

WHY (owner, 2026-11-12 21:0x): five job runners burned 70-83 % of a core each for hours behind thousands of zombies;
nothing named the process tree that did it. Wired into `fieldkit hog` (check and the 5-minute watch), the task keeper's
scheduled pass (`fieldkit task watch --act --scheduled`), and a CI test (tests/test_procs.py).
"""
import argparse
import json
import os
import sys
import time

from . import contain, liveness

ZOMBIE_LIMIT = liveness.ZOMBIE_LIMIT
CPU_SHARE_LIMIT = liveness.CPU_SHARE_LIMIT
CPU_SPAN_S = liveness.CPU_SPAN_S
ZOMBIE = "ZOMBIES"
ORPHAN = "ORPHAN"
HOG = "HOG"
OK = "OK"
ORDER = {OK: 0, ZOMBIE: 1, ORPHAN: 2, HOG: 3}


# ---- the register: a detached service Fieldkit starts on purpose --------------------------------------------------
def record(pid, cls, why="", source="spawn"):
    """Write a process this harness started and lets outlive its parent (the thermal governor, the link relay) into the
    hog registry, by PID + creation time, so `procs check` sees it. cls is a thresholds.json service class (never
    stopped as an orphan). Never raises: a register is a report. -> True when recorded."""
    try:
        from ..hog import registry
        p = contain.psutil.Process(int(pid))
        return bool(registry.add([registry.entry(p, cls, source=source, why=why)]))
    except Exception:  # noqa: BLE001
        return False


# ---- the table -------------------------------------------------------------------------------------------------
def table():
    """-> {pid: (ppid, state, creation time)} for the whole machine; state 'Z' for a zombie. One lean pass."""
    scan = contain._scan_proc() if sys.platform.startswith("linux") else None
    if scan is not None:
        return {pid: (ppid, st, ct) for pid, (ppid, _pg, st, ct) in scan.items()}
    out = {}
    if contain.psutil is None:
        return out
    try:
        for p in contain.psutil.process_iter(["pid", "ppid", "create_time", "status"]):
            i = p.info
            if i.get("create_time") is None:
                continue
            z = i.get("status") == contain.psutil.STATUS_ZOMBIE
            out[i["pid"]] = (i["ppid"], "Z" if z else "S", round(i["create_time"], 3))
    except contain.psutil.Error:
        pass
    return out


def _alive(tab, pid, ct):
    row = tab.get(int(pid)) if pid is not None else None
    return row is not None and row[1] not in ("Z", "X") and ct is not None and abs(row[2] - float(ct)) <= contain.CT_TOL


def _below(tab, root):
    """All live descendants of root (pid -> creation time) by ppid, from the one table."""
    kids = {}
    for pid, (ppid, st, _ct) in tab.items():
        if st not in ("Z", "X"):
            kids.setdefault(ppid, []).append(pid)
    out, stack = {}, [root]
    while stack:
        for c in kids.get(stack.pop(), ()):
            if c not in out:
                out[c] = tab[c][2]
                stack.append(c)
    return out


def _load(pid):
    """-> (CPU share of one core since it started, age s) of a live process; (0.0, 0) when unknown."""
    try:
        p = contain.psutil.Process(int(pid))
        t = p.cpu_times()
        age = max(1.0, time.time() - p.create_time())
        return (t.user + t.system) / age, age
    except Exception:  # noqa: BLE001
        return 0.0, 0


# ---- the check -------------------------------------------------------------------------------------------------
def _roots(records=None, entries=None):
    """-> [{kind, id, pid, ct, label, source, members: {pid: ct}, service: bool, owner: (pid, ct)|None}]."""
    out = []
    for r in (liveness.runs() if records is None else records):
        out.append({"kind": r.get("kind") or "run", "id": str(r.get("id")), "pid": r.get("pid"), "ct": r.get("ctime"),
                    "label": r.get("label") or "", "source": "beacon", "service": False, "owner": None,
                    "members": {int(p): m.get("ctime") for p, m in (r.get("members") or {}).items()}})
    if entries is None:
        try:
            from ..hog import registry
            entries = registry.load()
        except Exception:  # noqa: BLE001
            entries = []
    svc = _service_classes()
    for e in entries:
        own = (e.get("owner_pid"), e.get("owner_ctime")) if e.get("owner_pid") is not None else None
        out.append({"kind": e.get("cls") or "registered", "id": str(e.get("pid")), "pid": e.get("pid"),
                    "ct": e.get("ctime"), "label": e.get("name") or "", "source": "registry", "members": {},
                    "service": (e.get("cls") in svc), "owner": own, "cap_s": e.get("cap_s")})
    return out


def _service_classes():
    try:
        from ..hog import config
        return set((config().get("service_classes") or {}))
    except Exception:  # noqa: BLE001
        return {"link-listener", "link-relay", "thermal-governor"}


def assess(root, tab, load=_load):
    """One root -> its tree record with a verdict (pure apart from `load`)."""
    pid, ct = root["pid"], root["ct"]
    live = _alive(tab, pid, ct)
    below = _below(tab, int(pid)) if live else {}
    mem = {p: c for p, c in root["members"].items() if _alive(tab, p, c)}
    members = {**mem, **below}
    members.pop(int(pid), None)
    zomb = sorted(p for p, (pp, st, _c) in tab.items() if st == "Z" and (pp == pid or pp in members))
    share, age = load(pid) if live else (0.0, 0)
    why, verdict, kill = "", OK, []
    if root["source"] == "beacon" and not live and members:
        verdict, kill = ORPHAN, sorted(members.items())
        why = f"its owner (PID {pid}) is gone, {len(members)} member(s) still run"
    elif root["source"] == "registry" and live and not root["service"]:
        own = root.get("owner")
        cap = root.get("cap_s")
        if own and own[0] is not None and not _alive(tab, own[0], own[1]):
            verdict, kill, why = ORPHAN, [(pid, ct)], "the session that started it ended"
        elif cap and age > cap:
            verdict, kill, why = ORPHAN, [(pid, ct)], f"older than its cap ({age / 3600:.1f} h)"
    if verdict == OK and live and len(zomb) > ZOMBIE_LIMIT:
        verdict = HOG
        why = f"{len(zomb)} zombie children (limit {ZOMBIE_LIMIT}): it does not collect its exited children"
    if verdict == OK and live and age >= CPU_SPAN_S and share > CPU_SHARE_LIMIT:
        verdict = HOG
        why = f"{share * 100:.0f} % of a core on average over {int(age / 60)} min (limit {int(CPU_SHARE_LIMIT * 100)} %)"
    if verdict == OK and zomb:
        verdict, why = ZOMBIE, f"{len(zomb)} uncollected zombie child(ren)"
    return {"kind": root["kind"], "id": root["id"], "pid": pid, "label": root["label"], "source": root["source"],
            "alive": live, "age_s": int(age), "cpu_share": round(share, 3), "members": len(members),
            "zombies": len(zomb), "zombie_pids": zomb[:20], "verdict": verdict, "why": why, "kill": kill,
            "mine": live and int(pid) == os.getpid()}


def check(records=None, entries=None, tab=None, load=_load):
    """-> {"status": OK|FINDINGS, "trees": [...], "findings": [tree...]} for every tree Fieldkit started."""
    tab = table() if tab is None else tab
    trees = [assess(r, tab, load) for r in _roots(records, entries)]
    found = [t for t in trees if t["verdict"] != OK]
    return {"status": "FINDINGS" if found else "OK", "trees": trees, "findings": found,
            "zombies": sum(t["zombies"] for t in trees)}


def reap(res=None, stopper=None, own_reaper=None):
    """Collect this process's own zombies, stop Fieldkit's own orphans by PID + creation time. -> {"reaped", "stopped",
    "left": [what no other process can fix]}. Never kills the caller's own tree, a service, or anything not in a register."""
    res = check() if res is None else res
    reaped = (own_reaper or contain.reap_orphans)()
    stopped, left = [], []
    for t in res["findings"]:
        if t["verdict"] == ORPHAN:
            for pid, ct in t["kill"]:
                if int(pid) == os.getpid():
                    continue
                ok, r = (stopper or liveness.stop_pid)(pid, ct)
                a = {"kind": "procs-reap", "state": ORPHAN, "run": t["id"], "pid": pid, "ok": ok, "result": r,
                     "why": f"{t['kind']} {t['id']}: {t['why']}"}
                liveness.log_action(a)
                stopped.append(a)
        elif t["verdict"] in (ZOMBIE, HOG) and t["zombies"] and not t["mine"]:
            left.append(f"{t['kind']} {t['id']} (PID {t['pid']}): {t['zombies']} zombie(s) only that process can "
                        f"collect; it reaps in its own tick (contain.reap_orphans): restart it between jobs")
    return {"reaped": reaped, "stopped": stopped, "left": left}


def summary(res, done=None):
    f = res["findings"]
    if not f:
        return f"procs: OK ({len(res['trees'])} tree(s), 0 zombies)"
    line = "procs: " + "; ".join(f"{t['verdict']} {t['kind']} {t['id']}: {t['why']}" for t in f)
    if done:
        line += f" | reaped {done['reaped']}, stopped {len(done['stopped'])}"
    return line


def pass_line(act=False):
    """The one-line pass the hog, the keeper and the preflight run. Never raises."""
    try:
        res = check()
        done = reap(res) if act else None
        return summary(res, done)
    except Exception as e:  # noqa: BLE001
        return f"procs: error {type(e).__name__}: {e}"


def main(argv=None, prog="fieldkit procs"):
    ap = argparse.ArgumentParser(prog=prog, description="every process tree Fieldkit started: zombies, orphans, "
                                                         "CPU share, age; reap only Fieldkit's own")
    ap.add_argument("action", choices=["check", "reap"])
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    res = check()
    done = reap(res) if ns.action == "reap" else None
    if ns.json:
        print(json.dumps({**res, "done": done}, indent=1, default=str))
    else:
        print(summary(res, done))
        for t in res["trees"]:
            print(f"  {t['verdict']:<7} {t['kind']:<14} {t['id'][:24]:<24} pid {t['pid']}  age {t['age_s'] // 60} min  "
                  f"cpu {t['cpu_share'] * 100:.0f} %  members {t['members']}  zombies {t['zombies']}")
        for x in (done or {}).get("left", []):
            print(f"  LEFT: {x}")
        print("NEXT: " + ("fieldkit procs reap   # stops Fieldkit's own orphans" if res["findings"] and not done
                          else "carry on"))
    return 3 if res["findings"] and not (done and not done["left"] and all(a["ok"] for a in done["stopped"])) else 0


if __name__ == "__main__":
    sys.exit(main())
