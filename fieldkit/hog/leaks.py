"""Process leaks, counted per class with an early threshold (P28, owner 2026-11-16: "worse than a memory leak, the
equivalent of a zip bomb" - 546 console hosts filled the desktop heap and no window opened, while 21 copies of the
cable listener ran). Windows and Linux. Pure classification (`classify`) over a process table, so every rule is tested
with fake tables; `snapshot` reads the real one; `sweep` stops only what is PROVABLY spare:

  console-hosts   (Windows) conhost/OpenConsole: count; EMPTY = no child, creator gone (or a newer process reusing its
                  PID), older than leak_dead_host_min_age_s (60 s; 2026-11-22: at 600 s and a sweep every 5 min a
                  churn of ~1,200 hosts an hour kept hundreds alive). Empty ones are stopped.
  duplicates      a singleton (leak_singletons: the cable listener, its rescue door, the thermal watch) running more
                  than once. One is kept: the one holding a listening socket, else the oldest; the others, older than
                  leak_dup_min_age_s, are stopped. A launcher and its own child (venv pythonw) count as one.
  orphans         shells, python, git and text tools whose creator is gone, older than leak_min_age_s: counted and
                  named, never stopped (a detached job looks the same).
  zombies         (POSIX) defunct children nobody reaped: counted with their parent (only the parent can reap them).

Every class has a WARN threshold far below the point where the machine breaks (leak_*_warn in thresholds.json).
"""
import json
import re
import sys
import time

CONSOLE_HOSTS = ("conhost.exe", "openconsole.exe")
ORPHAN_NAMES = re.compile(r"^(python[0-9.]*w?|git|grep|tail|head|sed|awk|bash|sh|cmd|powershell|pwsh|node)(\.exe)?$", re.I)

DEFAULTS = {
    "leak_console_hosts_warn": 40,
    "leak_duplicates_warn": 0,
    "leak_orphans_warn": 20,
    "leak_zombies_warn": 5,
    "leak_min_age_s": 600,
    "leak_dup_min_age_s": 60,
    "leak_dead_host_min_age_s": 60,
    "leak_singletons": {
        "link-listener": r"\bfieldkit\b.*\bpeers\s+link\s+serve\b",
        "link-rescue": r"\bfieldkit\b.*\b(link_rescue|link\s+rescue)\b.*\bserve\b",
        "thermal-watch": r"\bfieldkit\b.*\bthermal\s+watch\b.*--forever",
        "thermal-journal": r"\bfieldkit\b.*\bthermal\s+journal\b",
    },
}


def _cfg(cfg):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg or {}).items() if k.startswith("leak_")})
    return out


def _creator_gone(r, by_pid):
    p = by_pid.get(r["ppid"])
    return p is None or p["created"] > r["created"] + 1        # a newer process took the dead creator's PID


def classify(rows, now, cfg=None, listening=()):
    """rows: dicts {pid, ppid, name, cmd, created, status}. listening: pids holding a listening socket.
    -> {"console_hosts": {"count", "empty": [pid]}, "duplicates": {cls: {"keep": pid, "spare": [pid], "count"}},
        "orphans": [pid], "zombies": [(pid, ppid)]} (pure)."""
    c = _cfg(cfg)
    by_pid = {r["pid"]: r for r in rows}
    parents = {r["ppid"] for r in rows}
    hosts = [r for r in rows if (r["name"] or "").lower() in CONSOLE_HOSTS]
    empty = [r["pid"] for r in hosts if r["pid"] not in parents and now - r["created"] >= c["leak_dead_host_min_age_s"]
             and ((r["name"] or "").lower() == "openconsole.exe" or _creator_gone(r, by_pid))]
    dups = {}
    listening = set(listening)
    for cls, pat in c["leak_singletons"].items():
        rx = re.compile(pat, re.I)
        group = [r for r in rows if r["status"] != "zombie" and rx.search(r["cmd"] or "")]
        ids = {r["pid"] for r in group}
        roots = [r for r in group if r["ppid"] not in ids or _creator_gone(r, by_pid)]   # launcher + child = one
        if len(roots) < 2:
            continue

        def holds(r):
            return r["pid"] in listening or any(x["ppid"] == r["pid"] and x["pid"] in listening for x in group)
        keep = sorted(roots, key=lambda r: (not holds(r), r["created"]))[0]
        spare = [r["pid"] for r in roots if r is not keep and now - r["created"] >= c["leak_dup_min_age_s"]]
        dups[cls] = {"keep": keep["pid"], "spare": spare, "count": len(roots)}
    singles = {p for d in dups.values() for p in [d["keep"], *d["spare"]]}
    orphans = [r["pid"] for r in rows if ORPHAN_NAMES.match(r["name"] or "") and r["status"] != "zombie"
               and r["pid"] not in singles and now - r["created"] >= c["leak_min_age_s"] and r["ppid"] not in (0, 1)
               and _creator_gone(r, by_pid)]
    zombies = [(r["pid"], r["ppid"]) for r in rows if r["status"] == "zombie"]
    return {"console_hosts": {"count": len(hosts), "empty": empty}, "duplicates": dups, "orphans": orphans,
            "zombies": zombies}


def findings(k, cfg=None, by_pid=None):
    """-> hog findings (rules.py shape) for every class over its threshold (pure)."""
    c = _cfg(cfg)
    by_pid = by_pid or {}
    out = []

    def f(check, line, numbers):
        out.append({"check": check, "severity": "warn", "line": line, "numbers": numbers, "action": None})
    h = k["console_hosts"]
    if h["count"] > c["leak_console_hosts_warn"]:
        f("leak-consoles", f"{h['count']} console hosts (warn over {c['leak_console_hosts_warn']}), {len(h['empty'])} "
          f"empty: fieldkit hog leaks --act", {"count": h["count"], "empty": len(h["empty"])})
    for cls, d in sorted(k["duplicates"].items()):
        if d["count"] - 1 > c["leak_duplicates_warn"]:
            f("leak-duplicates", f"{d['count']} copies of the {cls} (one allowed; keeping PID {d['keep']}): "
              f"fieldkit hog leaks --act", {"class": cls, "count": d["count"]})
    if len(k["orphans"]) > c["leak_orphans_warn"]:
        names = {}
        for p in k["orphans"]:
            n = (by_pid.get(p) or {}).get("name") or "?"
            names[n] = names.get(n, 0) + 1
        top = ", ".join(f"{n} x{v}" for n, v in sorted(names.items(), key=lambda x: -x[1])[:4])
        f("leak-orphans", f"{len(k['orphans'])} processes whose creator is gone (warn over "
          f"{c['leak_orphans_warn']}): {top}", {"count": len(k["orphans"])})
    if len(k["zombies"]) > c["leak_zombies_warn"]:
        par = {}
        for _p, pp in k["zombies"]:
            par[pp] = par.get(pp, 0) + 1
        top = ", ".join(f"{(by_pid.get(pp) or {}).get('name') or '?'} {pp} x{v}"
                        for pp, v in sorted(par.items(), key=lambda x: -x[1])[:3])
        f("leak-zombies", f"{len(k['zombies'])} zombie processes nobody reaped (warn over {c['leak_zombies_warn']}); "
          f"their parents: {top}", {"count": len(k["zombies"])})
    return out


def snapshot():
    """-> (rows, listening pids) from the live process table. Never raises."""
    rows, listening = [], set()
    try:
        import psutil
        for p in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time", "status"]):
            i = p.info
            try:
                cmd = " ".join(i["cmdline"] or [])
            except Exception:  # noqa: BLE001
                cmd = ""
            rows.append({"pid": i["pid"], "ppid": i["ppid"] or 0, "name": i["name"] or "", "cmd": cmd,
                         "created": i["create_time"] or 0, "status": i["status"] or ""})
        try:
            listening = {x.pid for x in psutil.net_connections(kind="inet") if x.status == "LISTEN" and x.pid}
        except Exception:  # noqa: BLE001 - no right to list sockets: oldest is kept
            pass
    except Exception:  # noqa: BLE001
        pass
    return rows, listening


def _stop(pid, expect_created):
    import psutil
    p = psutil.Process(pid)
    if abs(p.create_time() - expect_created) > 1:
        return False                                   # the PID was reused since the snapshot
    p.kill()
    return True


def sweep(act=False, cfg=None, now=None, snap=None, stop=None, log=None):
    """-> {"k": classification, "findings": [...], "stopped": [{pid, class, name}]}. Stops only with act=True."""
    rows, listening = snap() if snap else snapshot()
    now = now or time.time()
    k = classify(rows, now, cfg, listening)
    by_pid = {r["pid"]: r for r in rows}
    out = {"k": k, "findings": findings(k, cfg, by_pid), "stopped": []}
    if not act:
        return out
    targets = [(p, "console-host") for p in k["console_hosts"]["empty"]]
    targets += [(p, cls) for cls, d in k["duplicates"].items() for p in d["spare"]]
    for pid, cls in targets:
        r = by_pid.get(pid) or {}
        try:
            if (stop or _stop)(pid, r.get("created", 0)):
                out["stopped"].append({"pid": pid, "class": cls, "name": r.get("name")})
        except Exception:  # noqa: BLE001 - gone already
            pass
    if out["stopped"]:
        (log or _log)({"t": now, "stopped": out["stopped"], "counts": counts(k)})
    return out


def counts(k):
    return {"console_hosts": k["console_hosts"]["count"], "empty_hosts": len(k["console_hosts"]["empty"]),
            "duplicates": {c: d["count"] for c, d in k["duplicates"].items()}, "orphans": len(k["orphans"]),
            "zombies": len(k["zombies"])}


def _log(rec):
    try:
        from . import state_dir
        with open(state_dir() / "leaks.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


def line(res):
    """One summary line for a person."""
    c = counts(res["k"])
    dup = ", ".join(f"{k} x{v}" for k, v in sorted(c["duplicates"].items())) or "none"
    head = "leaks: WARN" if res["findings"] else "leaks: OK"
    s = (f"{head} - console hosts {c['console_hosts']} ({c['empty_hosts']} empty), duplicate singletons {dup}, "
         f"orphans {c['orphans']}" + (f", zombies {c['zombies']}" if sys.platform != "win32" else ""))
    if res["stopped"]:
        by = {}
        for x in res["stopped"]:
            by[x["class"]] = by.get(x["class"], 0) + 1
        s += "; stopped " + ", ".join(f"{v} {k}" for k, v in sorted(by.items()))
    return s


def check_failed(res, cfg=None):
    """`hog leaks --check`: the enforcing check (2026-11-22). -> reasons (empty = pass): any empty console host, the
    host count over leak_console_hosts_warn, any spare singleton (pure)."""
    c = _cfg(cfg)
    k, out = res["k"], []
    h = k["console_hosts"]
    if h["empty"]:
        out.append(f"{len(h['empty'])} empty console host(s) alive: {h['empty'][:10]}")
    if h["count"] > c["leak_console_hosts_warn"]:
        out.append(f"{h['count']} console hosts > leak_console_hosts_warn {c['leak_console_hosts_warn']}")
    for cls, d in k["duplicates"].items():
        if d["spare"]:
            out.append(f"{cls} runs {d['count']} times, spare {d['spare']}")
    return out
