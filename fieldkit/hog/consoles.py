"""Console hosts nothing uses (owner, 2026-11-16: 265 empty conhost/OpenConsole processes left by session shells filled the
desktop heap, so no window opened any more; 2026-11-23: one Windows Terminal process held 802 windows). A host is EMPTY when no process has it as its parent and, for a classic
conhost, its creator is gone; it is old enough that a console being created is never taken. Windows only. Never raises."""
import sys

HOSTS = ("conhost.exe", "openconsole.exe")
TERMINALS = frozenset({"windowsterminal.exe", "openconsole.exe", "conhost.exe"})   # a set of names, never an argv
FLOOD = 20      # visible windows: no person keeps 20 terminal windows open (owner, 2026-11-23: one WT held 802)


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


def window_counts():
    """-> {pid: visible top-level windows} for every process. Windows only (elsewhere {}). Never raises.
    Counting conhost.exe misses Windows Terminal: there one process owns every window (2026-11-23: 802 in one)."""
    if sys.platform != "win32":
        return {}
    try:
        import ctypes
        from ctypes import wintypes as w
        u = ctypes.WinDLL("user32")
        per = {}
        cb_t = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

        def cb(h, _):
            if u.IsWindowVisible(h):
                pid = w.DWORD()
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                per[pid.value] = per.get(pid.value, 0) + 1
            return True
        u.EnumWindows(cb_t(cb), 0)
        return per
    except Exception:  # noqa: BLE001
        return {}


def terminal_windows(rows, wins):
    """rows: dicts {pid, name}; wins: {pid: windows}. -> {pid: windows} of terminal processes showing any (pure)."""
    return {r["pid"]: wins[r["pid"]] for r in rows
            if (r["name"] or "").lower() in TERMINALS and wins.get(r["pid"], 0) > 0}


def floods(rows, wins, now, flood=FLOOD, min_age_s=600):
    """-> pids of terminal processes holding at least `flood` visible windows, no live child process, older than
    min_age_s (pure). Those windows are leftovers of programs that already exited ("process exited with code N"):
    stopping the terminal closes them; nothing running is lost because nothing runs under it."""
    parents = {r["ppid"] for r in rows}
    return [r["pid"] for r in rows
            if (r["name"] or "").lower() in TERMINALS and wins.get(r["pid"], 0) >= flood
            and r["pid"] not in parents and now - r["created"] >= min_age_s]


def reap_floods(act=True, now=None, flood=FLOOD):
    """Live: find (with act, stop by PID) the terminal floods. -> {windows, floods, stopped}. Never raises."""
    out = {"windows": 0, "floods": [], "stopped": 0}
    if sys.platform != "win32":
        return out
    try:
        import time
        import psutil
        rows = [{"pid": p.info["pid"], "name": p.info["name"], "ppid": p.info["ppid"],
                 "created": p.info["create_time"] or 0}
                for p in psutil.process_iter(["pid", "name", "ppid", "create_time"])]
        wins = window_counts()
        out["windows"] = sum(terminal_windows(rows, wins).values())
        out["floods"] = floods(rows, wins, now or time.time(), flood)
        if act:
            for pid in out["floods"]:
                try:
                    psutil.Process(pid).kill()
                    out["stopped"] += 1
                except Exception:  # noqa: BLE001 - one already gone is fine
                    pass
    except Exception:  # noqa: BLE001 - the hog's own pass never fails on it
        pass
    return out


def flood_line(res):
    """One line for the 5-minute hog watch log."""
    return f'terminal windows: {res["windows"]}' + (f', flood stopped {res["stopped"]}' if res["stopped"] else "")


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
    """Live audit: -> {hosts, groups, fieldkit_owned, empty, stopped, terminal_windows, floods, floods_stopped}. Windows only (elsewhere all zero)."""
    out = {"hosts": 0, "groups": {}, "fieldkit_owned": [], "empty": 0, "stopped": 0,
           "terminal_windows": 0, "floods": [], "floods_stopped": 0}
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
        fl = reap_floods(act=reap)
        out.update(terminal_windows=fl["windows"], floods=fl["floods"], floods_stopped=fl["stopped"])
    except Exception:  # noqa: BLE001 - an audit never raises
        pass
    return out


def failures(res, flood=FLOOD):
    """-> reasons the consoles check fails (pure)."""
    bad = []
    if res["fieldkit_owned"]:
        bad.append(f'{len(res["fieldkit_owned"])} owned by a fieldkit process')
    if res["terminal_windows"] >= flood:
        bad.append(f'{res["terminal_windows"]} terminal windows open (limit {flood - 1})')
    return bad


def main(ns):
    import json
    res = audit(reap=bool(getattr(ns, "reap", False)))
    if res["floods_stopped"]:
        res["terminal_windows"] = reap_floods(act=False)["windows"]
    bad = failures(res)
    if getattr(ns, "json", False):
        print(json.dumps(dict(res, failures=bad)))
        return 1 if bad else 0
    print(f'console hosts: {res["hosts"]}  empty: {res["empty"]}  stopped: {res["stopped"]}')
    print(f'terminal windows: {res["terminal_windows"]}  floods: {len(res["floods"])}  '
          f'stopped: {res["floods_stopped"]}')
    for k, v in sorted(res["groups"].items(), key=lambda x: -len(x[1])):
        print(f"  {len(v):5d}  {k}")
    print("consoles check: " + ("FAIL - " + "; ".join(bad) if bad else "PASS"))
    if res["fieldkit_owned"]:
        print("NEXT: fieldkit hog churn scan; move the starter onto fieldkit.proc (tests/test_no_console_spawn.py)")
    elif bad:
        print(f"NEXT: fieldkit hog consoles --reap (stops a terminal holding >= {FLOOD} windows and no live program)")
    return 1 if bad else 0
