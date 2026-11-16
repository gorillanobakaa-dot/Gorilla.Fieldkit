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
