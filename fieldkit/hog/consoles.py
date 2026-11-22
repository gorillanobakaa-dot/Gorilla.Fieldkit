"""Console hosts nothing uses (owner, 2026-11-16: 265 empty conhost/OpenConsole processes left by session shells filled the
desktop heap, so no window opened any more). A host is EMPTY when no process has it as its parent and, for a classic
conhost, its creator is gone; it is old enough that a console being created is never taken. Windows only. Never raises."""
import sys

HOSTS = ("conhost.exe", "openconsole.exe")


def empty_hosts(rows, now, min_age_s=600):
    """rows: dicts {pid, name, ppid, created}. -> pids of the empty hosts (pure, unit-tested)."""
    alive = {r["pid"] for r in rows}
    parents = {r["ppid"] for r in rows}
    out = []
    for r in rows:
        n = (r["name"] or "").lower()
        if n not in HOSTS or r["pid"] in parents or now - r["created"] < min_age_s:
            continue
        if n == "conhost.exe" and r["ppid"] in alive:
            continue                      # a live creator: a live shell's console
        out.append(r["pid"])
    return out


def sweep(dry=False, now=None):
    """Stop the empty console hosts. -> (found, stopped). Windows only (elsewhere (0, 0))."""
    if sys.platform != "win32":
        return 0, 0
    try:
        import time
        import psutil
        rows = []
        for p in psutil.process_iter(["pid", "name", "ppid", "create_time"]):
            i = p.info
            rows.append({"pid": i["pid"], "name": i["name"], "ppid": i["ppid"], "created": i["create_time"] or 0})
        pids = empty_hosts(rows, now or time.time())
        if dry:
            return len(pids), 0
        stopped = 0
        for pid in pids:
            try:
                psutil.Process(pid).kill()
                stopped += 1
            except Exception:  # noqa: BLE001 - one already gone is fine
                pass
        return len(pids), stopped
    except Exception:  # noqa: BLE001 - the hog's own pass never fails on it
        return 0, 0


def _is_fieldkit(proc):
    """A python or fieldkit program running the package (not any program whose path merely contains the word)."""
    n, cmd = (proc["name"] or "").lower(), (proc.get("cmd") or "").lower()
    if n.startswith("fieldkit"):
        return True
    return n.startswith(("python", "pythonw")) and ("-m fieldkit" in cmd or "fieldkit\\" in cmd or "fieldkit/" in cmd)


def owners(rows):
    """rows: dicts {pid, name, ppid, cmd}. -> {parent label: [host pids]} and the hosts a fieldkit parent owns (pure).
    A host's owner is its parent when that is alive, else 'dead parent'. A fieldkit owner is a process whose command
    line names the fieldkit package (python -m fieldkit..., fieldkit.exe); it should start no console at all."""
    by = {r["pid"]: r for r in rows}
    groups, mine = {}, []
    for r in rows:
        if (r["name"] or "").lower() not in HOSTS:
            continue
        par = by.get(r["ppid"])
        label = f'{par["name"]}' if par else "dead parent"
        groups.setdefault(label, []).append(r["pid"])
        if par and _is_fieldkit(par):
            mine.append(r["pid"])
    return groups, mine


def audit(reap=False):
    """Live audit: -> {hosts, groups, fieldkit_owned, empty, stopped}. Windows only (elsewhere all zero)."""
    out = {"hosts": 0, "groups": {}, "fieldkit_owned": [], "empty": 0, "stopped": 0}
    if sys.platform != "win32":
        return out
    try:
        import psutil
        rows = []
        for p in psutil.process_iter(["pid", "name", "ppid", "cmdline", "create_time"]):
            i = p.info
            rows.append({"pid": i["pid"], "name": i["name"], "ppid": i["ppid"], "created": i["create_time"] or 0,
                         "cmd": " ".join(i["cmdline"] or [])})
        groups, mine = owners(rows)
        out.update(hosts=sum(len(v) for v in groups.values()), groups=groups, fieldkit_owned=mine)
        out["empty"], out["stopped"] = sweep(dry=not reap)
    except Exception:  # noqa: BLE001 - an audit never raises
        pass
    return out


def main(ns):
    import json
    res = audit(reap=bool(getattr(ns, "reap", False)))
    if getattr(ns, "json", False):
        print(json.dumps(res))
    else:
        print(f'console hosts: {res["hosts"]}  empty: {res["empty"]}  stopped: {res["stopped"]}')
        for k, v in sorted(res["groups"].items(), key=lambda x: -len(x[1])):
            print(f"  {len(v):5d}  {k}")
        print("consoles check: " + (f'FAIL - {len(res["fieldkit_owned"])} owned by a fieldkit process'
                                    if res["fieldkit_owned"] else "PASS"))
        if res["fieldkit_owned"]:
            print("NEXT: fieldkit hog churn scan; move the starter onto fieldkit.proc (tests/test_no_console_spawn.py)")
    return 1 if res["fieldkit_owned"] else 0
