"""The hog rules: pure functions from measured facts to findings. No I/O here, so every rule is tested with fake
process tables and fake `lms ps` output, and `fieldkit mutate` proves each threshold comparison is caught.

A finding: {"check", "severity": "warn"|"act", "line": one line for a person, "numbers": {...}, "action": None or
{"kind": "unload_lms"|"unload_ollama"|"kill", ...}}. Only checks that are over a threshold produce findings.
"""
import re
import time

GB = 1024 ** 3
MB = 1024 ** 2
SEVERITY = {"ok": 0, "warn": 1, "act": 2}
#: lms ps states that mean the model is in use now (never unloaded, whatever the log says)
BUSY = {"generating", "processing", "loading", "busy"}


def _f(check, severity, line, numbers=None, action=None):
    return {"check": check, "severity": severity, "line": line, "numbers": numbers or {}, "action": action}


def worst(findings):
    return max((SEVERITY[f["severity"]] for f in findings), default=0)


def _top(procs, n):
    return [f"{p['name']} {p['pid']} {p['rss'] / GB:.1f} GB" for p in sorted(procs, key=lambda p: -p["rss"])[:n]]


def ram(facts, cfg):
    r = facts.get("ram")
    if not r:
        return []
    pct = r["used"] * 100.0 / r["total"]
    sev = "act" if pct >= cfg["ram_act_pct"] else "warn" if pct >= cfg["ram_warn_pct"] else None
    if not sev:
        return []
    top = _top(facts.get("procs", []), cfg["top_n"])
    return [_f("ram", sev, f"RAM {r['used'] / GB:.1f}/{r['total'] / GB:.1f} GB ({pct:.0f}%); top: " + ", ".join(top[:3]),
               {"used": r["used"], "total": r["total"], "pct": round(pct, 1), "top": top})]


def commit(facts, cfg):
    c = facts.get("commit")
    if not c or not c.get("limit"):
        return []
    pct = c["used"] * 100.0 / c["limit"]
    sev = "act" if pct >= cfg["commit_act_pct"] else "warn" if pct >= cfg["commit_warn_pct"] else None
    if not sev:
        return []
    return [_f("commit", sev, f"commit charge {c['used'] / GB:.1f}/{c['limit'] / GB:.1f} GB ({pct:.0f}%)",
               {"used": c["used"], "limit": c["limit"], "pct": round(pct, 1)})]


def critical(facts, cfg):
    """RAM or commit at the act line: idle models then go after model_idle_min_critical instead of model_idle_min."""
    return any(f["severity"] == "act" for f in ram(facts, cfg) + commit(facts, cfg))


def idle_limit(cfg, crit):
    """Minutes a model may sit idle: model_idle_min, or model_idle_min_critical while RAM/commit is at the act line."""
    return cfg["model_idle_min_critical"] if crit else cfg["model_idle_min"]


def over(idle, limit):
    return idle > limit           # idle means MORE than the limit


def lms_models(facts, cfg, now, crit=False):
    """LM Studio: a model whose state is not busy and whose last request in the server log is older than the limit
    is unloaded. No log evidence at all = no unload (fail closed: never act on a missing measurement)."""
    lms = facts.get("lms") or {}
    act = lms.get("activity") or {}
    limit = idle_limit(cfg, crit)
    out = []
    for m in lms.get("models") or []:
        if (m.get("status") or "").lower() in BUSY:
            continue
        by = act.get("by_model") or {}
        logged = [t for t in (by.get(m.get("id")), by.get(m.get("key"))) if t] or [act.get("last_any") or 0]
        last = max(logged + [m.get("last_used") or 0]) or None     # the newest of the log and lms's lastUsedTime
        size = m.get("size") or 0
        if last is None:
            out.append(_f("model", "warn", f"LM Studio model {m['id']} ({size / GB:.1f} GB) loaded, no request in the "
                          "server log to date it: not unloaded", {"id": m["id"], "size": size}))
            continue
        idle = (now - last) / 60.0
        if over(idle, limit):
            out.append(_f("model", "act", f"LM Studio model {m['id']} ({size / GB:.1f} GB) idle {idle:.0f} min "
                          f"(limit {limit}): unload", {"id": m["id"], "size": size, "idle_min": round(idle, 1),
                                                       "limit_min": limit},
                          {"kind": "unload_lms", "id": m["id"]}))
    return out


def ollama_models(facts, history, cfg, now, crit=False):
    """Ollama has no request log; its expires_at moves forward on every request. An expires_at that has not moved
    across samples spanning more than the limit = no request for that long."""
    limit = idle_limit(cfg, crit)
    out = []
    for m in (facts.get("ollama") or {}).get("models") or []:
        since = now
        for h in reversed(history):
            if (h.get("ollama") or {}).get(m["name"]) != m.get("expires_at"):
                break
            since = h["t"]
        idle = (now - since) / 60.0
        if over(idle, limit):
            out.append(_f("model", "act", f"Ollama model {m['name']} ({(m.get('size') or 0) / GB:.1f} GB) idle "
                          f"{idle:.0f} min (limit {limit}): unload",
                          {"name": m["name"], "size": m.get("size"), "idle_min": round(idle, 1), "limit_min": limit},
                          {"kind": "unload_ollama", "name": m["name"]}))
    return out


def _series(history, facts):
    """-> {(pid, ctime): [(t, rss, cpu_s, name)]} over the history plus the current sample, oldest first."""
    s = {}
    for h in list(history) + [{"t": facts["t"], "procs": [[p["pid"], p["ctime"], p["name"], p["rss"], p.get("cpu_s")]
                                                          for p in facts.get("procs", [])]}]:
        for pid, ctime, name, rss, cpu in h.get("procs", []):
            s.setdefault((pid, ctime), []).append((h["t"], rss, cpu, name))
    return s


def leaks(facts, history, cfg):
    """A process whose working set never dropped across >= leak_min_samples samples spanning >= 80% of the window
    and grew by >= leak_growth_mb is reported with its curve. Report only: a leak is not ours to kill."""
    now, win = facts["t"], cfg["leak_window_min"] * 60
    out = []
    for (pid, _c), pts in _series(history, facts).items():
        pts = [p for p in pts if p[0] >= now - win]
        if len(pts) < cfg["leak_min_samples"] or pts[-1][0] - pts[0][0] < 0.8 * win:
            continue
        rss = [p[1] for p in pts]
        if any(b < a for a, b in zip(rss, rss[1:])):
            continue
        growth = rss[-1] - rss[0]
        if growth >= cfg["leak_growth_mb"] * MB:
            curve = [round(x / GB, 2) for x in rss]
            out.append(_f("leak", "warn", f"leak? {pts[-1][3]} {pid} grew {growth / GB:.1f} GB in "
                          f"{(pts[-1][0] - pts[0][0]) / 60:.0f} min, never dropping: {curve} GB",
                          {"pid": pid, "name": pts[-1][3], "growth": growth, "curve_gb": curve}))
    return out


def cpu_pinned(facts, history, cfg):
    """A process at >= cpu_pinned_pct of one core in every interval of the last cpu_pinned_min minutes (and the
    samples cover them) is reported (TextInputHost's known loop). Report only."""
    now, win = facts["t"], cfg["cpu_pinned_min"] * 60
    out = []
    for (pid, _c), pts in _series(history, facts).items():
        pts = [p for p in pts if p[2] is not None]
        recent = [p for p in pts if p[0] >= now - win]
        older = [p for p in pts if p[0] < now - win]
        if older:
            recent = [older[-1]] + recent          # the interval that crosses the window edge counts too
        if len(recent) < 2 or recent[-1][0] - recent[0][0] < win:
            continue
        rates = [(b[2] - a[2]) * 100.0 / (b[0] - a[0]) for a, b in zip(recent, recent[1:]) if b[0] > a[0]]
        if rates and min(rates) >= cfg["cpu_pinned_pct"]:
            out.append(_f("cpu", "warn", f"{recent[-1][3]} {pid} has held {min(rates):.0f}%+ of a core for "
                          f"{(recent[-1][0] - recent[0][0]) / 60:.0f} min", {"pid": pid, "name": recent[-1][3],
                                                                             "min_pct": round(min(rates), 1)}))
    return out


def temp(facts, cfg):
    t = (facts.get("temp") or {}).get("c")
    if t is None:
        return []
    sev = "act" if t >= cfg["temp_act_c"] else "warn" if t >= cfg["temp_warn_c"] else None
    if not sev:
        return []
    return [_f("temp", sev, f"CPU {t:.0f} C ({facts['temp'].get('sensor')}); fieldkit thermal monitor caps it",
               {"c": t, "sensor": facts["temp"].get("sensor")})]


def disk(facts, cfg):
    out = []
    for d in facts.get("disks") or []:
        pct = d["free"] * 100.0 / d["total"] if d["total"] else 100.0
        sev = "act" if pct < cfg["disk_act_free_pct"] else "warn" if pct < cfg["disk_warn_free_pct"] else None
        if sev:
            out.append(_f("disk", sev, f"disk {d['path']} {d['free'] / GB:.1f} GB free ({pct:.0f}%); "
                          f"fieldkit size {d['path']} --top shows where it went",
                          {"path": d["path"], "free": d["free"], "total": d["total"], "pct_free": round(pct, 1)}))
    return out


def orphan_class(name, cmd, classes):
    """-> the class name a process belongs to, or None. Matched on 'name' and on 'cmdline', lower case."""
    for cls, rx in classes.items():
        if re.search(rx, name.lower()) or re.search(rx, cmd.lower()):
            return cls
    return None


def orphans(facts, registry, cfg, now):
    """A registered process (same pid AND creation time, so a reused number is never touched) whose session process
    is gone, or that is older than its cap, is killed. Nothing outside the registry is ever a candidate. A service
    class (thresholds.json service_classes: the link listener, the relay) outlives its session by design: never."""
    alive = {(p[0], p[1]) for p in facts.get("alive") or []}
    svc = cfg.get("service_classes") or {}
    out = []
    for e in registry:
        if (e["pid"], e["ctime"]) not in alive or e.get("cls") in svc:
            continue
        owner = (e.get("owner_pid"), e.get("owner_ctime"))
        gone = owner[0] is not None and owner not in alive
        # an entry found in a live session's tree has no default cap: an agent's own MCP servers (node under cmd /c)
        # sit in that tree for as long as the session is open
        cap = e.get("cap_s") or (None if e.get("source") == "tree" else cfg["orphan_cap_h"] * 3600)
        age = now - e["ctime"]
        if gone or (cap is not None and age > cap):
            why = "its session ended" if gone else f"past its cap ({age / 3600:.1f} h > {cap / 3600:.1f} h)"
            out.append(_f("orphan", "act", f"orphan {e.get('name')} {e['pid']} ({e.get('cls')}, started by "
                          f"{e.get('session', '?')[:8]}): {why}: stop",
                          {"pid": e["pid"], "name": e.get("name"), "cls": e.get("cls"), "age_h": round(age / 3600, 2),
                           "session": e.get("session"), "why": why},
                          {"kind": "kill", "pid": e["pid"], "ctime": e["ctime"], "name": e.get("name")}))
    return out


def services(facts, registry, cfg):
    """A registered service (thresholds.json service_classes) that is no longer alive, with no live registered one of
    its class left, is reported with its restart line. Report only: the hog never starts or stops a service."""
    alive = {(p[0], p[1]) for p in facts.get("alive") or []}
    svc = cfg.get("service_classes") or {}
    live = {e.get("cls") for e in registry if (e["pid"], e["ctime"]) in alive}
    out = []
    for e in registry:
        cls = e.get("cls")
        if cls not in svc or cls in live or (e["pid"], e["ctime"]) in alive:
            continue
        out.append(_f("service", "warn", f"{cls} {e.get('name')} {e['pid']} (started by {(e.get('session') or '?')[:8]})"
                      f" is gone: restart: {svc[cls]}",
                      {"pid": e["pid"], "name": e.get("name"), "cls": cls, "session": e.get("session"),
                       "restart": svc[cls]}))
    return out


#: what a person calls a class's children in the pile-up line
NOUN = {"conhost": "consoles", "bash": "bash shells", "python": "python processes", "node": "node processes",
        "powershell": "PowerShell shells", "cmd": "cmd shells"}


def _fix(parent, cfg):
    """-> (label, fix) for a parent: the first pileup_fixes entry whose regex matches its name, else a plain one."""
    for rx, label, fix in cfg.get("pileup_fixes") or []:
        if re.search(rx, parent[3].lower()):
            return label, fix.format(pid=parent[0], name=parent[3])
    return parent[3], f"close or restart {parent[3]} when nothing it runs is working"


def pileup(facts, cfg):
    """A live parent holding more than pileup_max_children children of one class, or >= pileup_stale_min of them
    started more than pileup_stale_h hours before its newest one (left behind, never closed), is reported with its
    own parent, the count, their working set and the fix. Report only: the hog never stops what it did not start.
    facts["table"] rows: [pid, ppid, ctime, name, rss]. A parent that is gone, or whose number now belongs to a
    process younger than the child (PID reused), holds nothing."""
    table = facts.get("table") or []
    byid = {r[0]: r for r in table}
    classes = cfg["pileup_classes"]
    groups = {}
    for pid, ppid, ctime, name, rss in table:
        cls = next((c for c, rx in classes.items() if re.search(rx, name.lower())), None)
        par = byid.get(ppid)
        if cls is None or par is None or par[2] > ctime:
            continue
        groups.setdefault((ppid, cls), []).append((ctime, rss))
    out = []
    for (ppid, cls), kids in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0][0])):
        newest = max(k[0] for k in kids)
        stale = [k for k in kids if newest - k[0] > cfg["pileup_stale_h"] * 3600]
        many = len(kids) > cfg["pileup_max_children"]
        if not many and len(stale) < cfg["pileup_stale_min"]:
            continue
        par = byid[ppid]
        gp = byid.get(par[1])
        label, fix = _fix(par, cfg)
        noun = NOUN.get(cls, f"{cls} processes")
        held = f"{len(kids)} {noun}" if many else \
            f"{len(stale)} {noun} started over {cfg['pileup_stale_h']} h before its newest (of {len(kids)})"
        rss = sum(k[1] for k in kids)
        out.append(_f("pileup", "warn", f"{label} ({par[3]} {ppid}, started {stamp(par[2])}, parent "
                      f"{f'{gp[3]} {gp[0]}' if gp else 'gone'}) holds {held} since {stamp(min(k[0] for k in kids))}, "
                      f"{rss / MB:.0f} MB: {fix}",
                      {"pid": ppid, "name": par[3], "cls": cls, "count": len(kids), "stale": len(stale),
                       "rss": rss, "parent_pid": par[1], "parent_name": gp[3] if gp else None}))
    return out


def evaluate(facts, history, registry, cfg, now=None):
    now = facts.get("t") if now is None else now
    crit = critical(facts, cfg)
    return (ram(facts, cfg) + commit(facts, cfg) + lms_models(facts, cfg, now, crit)
            + ollama_models(facts, history, cfg, now, crit) + leaks(facts, history, cfg)
            + cpu_pinned(facts, history, cfg) + temp(facts, cfg) + disk(facts, cfg)
            + orphans(facts, registry, cfg, now) + services(facts, registry, cfg) + pileup(facts, cfg))


def summary(findings, now=None):
    if not findings:
        return "hog: OK"
    sev = {0: "OK", 1: "WARN", 2: "ACT"}[worst(findings)]
    return f"hog: {sev} - " + "; ".join(f["line"] for f in sorted(findings, key=lambda f: -SEVERITY[f["severity"]]))


def stamp(t=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
