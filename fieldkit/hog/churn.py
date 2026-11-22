"""Process churn: who keeps starting short-lived child processes, and the gate that keeps it from coming back.

2026-11-22: the Windows laptop had 60,000 console hosts, with 1,000+ new ones an hour. The room hook ran on every hook
event and shelled out to PowerShell, netsh, typeperf, git and gh; every console child of a window-less hook gets a
conhost.exe. Three tools, no model:

  hooks   run the hook (python -m fieldkit.autopilot.room --hook) once per event with a synthetic call on stdin and
          watch its process tree every 10 ms. One line per event: EVENT rc secs children, then each child's command
          line. Budget: churn_budget in thresholds.json (0 children on every per-prompt and per-tool event, a few on
          SessionStart). Exit 1 over budget.
  trace   watch the whole machine for N seconds and catch every new conhost, powershell, git, gh, netsh, typeperf
          and wmic. Grouped by the first ancestor that is not one of those, with the Fieldkit module named when that
          ancestor is `python -m fieldkit...`, and projected to an hourly rate.
  scan    static: the AST call graph reachable from the room hook (room.hook, room.main), every subprocess.*, os.system,
          os.popen, os.spawn*/exec* call and every powershell/netsh/typeperf/wmic string on it. Each hit is keyed
          module:function:callee and must be in churn_allow.yaml with a reason; a new hit fails tests/test_hog_churn.py.

Windows and Linux: hooks and trace use psutil on both; scan is pure Python.
"""
import ast
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent                                   # .../fieldkit
ALLOW = HERE / "churn_allow.yaml"

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
DEFAULT_BUDGET = {"SessionStart": 8, "UserPromptSubmit": 0, "PreToolUse": 0, "PostToolUse": 0, "Stop": 0}
TRACE_NAMES = ("conhost", "openconsole", "powershell", "pwsh", "git", "gh", "netsh", "typeperf", "wmic", "cmd")
ENTRIES = ("fieldkit.autopilot.room:hook", "fieldkit.autopilot.room:main")

SINK_CALLS = {
    "subprocess": {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
    "os": {"system", "popen", "startfile", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
           "execlpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
           "posix_spawn", "posix_spawnp"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
}
SINK_WORDS = re.compile(r"\b(powershell|pwsh|netsh|typeperf|wmic)(\.exe)?\b", re.I)
FK_MODULE = re.compile(r"-m\s+(fieldkit(?:\.[\w]+)*)(?:\s+([a-z][\w-]*))?")


def budget(cfg=None):
    out = dict(DEFAULT_BUDGET)
    out.update((cfg or {}).get("churn_budget") or {})
    return out


# ---- hooks: run the hook per event and count its children ------------------------------------------------------
def probe_call(event, cwd):
    return {"hook_event_name": event, "session_id": "probe", "tool_name": "Bash",
            "tool_input": {"command": "echo probe"}, "prompt": "probe", "cwd": str(cwd)}


def _watch(pid, seen, stop, every=0.01):
    """Poll pid's descendants every `every` s until stop is set; seen: {pid: (name, cmdline)} at first sight."""
    import psutil
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return
    while not stop.is_set():
        try:
            kids = root.children(recursive=True)
        except psutil.Error:
            kids = []
        for k in kids:
            if k.pid in seen:
                continue
            try:
                seen[k.pid] = (k.name(), " ".join(k.cmdline()) or k.name())
            except psutil.Error:
                seen[k.pid] = ("?", "?")
        time.sleep(every)


def run_hook(event, module="fieldkit.autopilot.room", args=("--hook",), python=None, cwd=None, timeout=60):
    """-> {event, rc, secs, children: [(pid, name, cmd)]}. The hook runs with no console (as Claude's pythonw does),
    so every console child it starts would get a console host, and with its room in a scratch folder so the probe
    moves no session's cursor and writes no room line."""
    cwd = Path(cwd or PKG_ROOT.parent)
    env = dict(os.environ, FIELDKIT_ROOM_DIR=tempfile.mkdtemp(prefix="churn-room-"), FIELDKIT_CHURN_PROBE="1",
               PYTHONPATH=str(PKG_ROOT.parent) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    flags = 0x00000008 if sys.platform == "win32" else 0            # DETACHED_PROCESS: no console, like pythonw
    t0 = time.monotonic()
    p = subprocess.Popen([python or sys.executable, "-m", module, *args], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(cwd), env=env, creationflags=flags)
    seen, stop = {}, threading.Event()
    w = threading.Thread(target=_watch, args=(p.pid, seen, stop), daemon=True)
    w.start()
    try:
        p.communicate(json.dumps(probe_call(event, cwd)).encode("utf-8"), timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        p.kill()                                                    # our own child, by its handle (PID)
        p.communicate()
        rc = "timeout"
    secs = time.monotonic() - t0
    stop.set()
    w.join(1)
    return {"event": event, "rc": rc, "secs": round(secs, 2),
            "children": [(pid, n, c) for pid, (n, c) in sorted(seen.items())]}


def verdict(results, cfg=None):
    """-> list of 'EVENT: N children > budget B' for every result over its budget (pure)."""
    b = budget(cfg)
    return [f"{r['event']}: {len(r['children'])} children > budget {b.get(r['event'], 0)}"
            for r in results if len(r["children"]) > b.get(r["event"], 0)]


def hooks_lines(results, cfg=None):
    b = budget(cfg)
    out = []
    for r in results:
        out.append(f"{r['event']:<17} rc {r['rc']}  secs {r['secs']:.2f}  children {len(r['children'])}"
                   f"  (budget {b.get(r['event'], 0)})")
        out += [f"    {pid} {name}: {cmd[:200]}" for pid, name, cmd in r["children"]]
    return out


# ---- trace: new console-ish processes machine-wide, grouped by who started them -----------------------------------
def _base(name):
    n = (name or "").lower()
    return n[:-4] if n.endswith(".exe") else n


def module_of(cmd):
    """'pythonw -m fieldkit.autopilot.room --hook' -> 'fieldkit.autopilot.room'; '-m fieldkit hog leaks' ->
    'fieldkit hog'; anything else -> ''."""
    m = FK_MODULE.search(cmd or "")
    if not m:
        return ""
    return m.group(1) + (" " + m.group(2) if m.group(1) == "fieldkit" and m.group(2) else "")


def group_trace(events, secs):
    """events: [{name, starter}] where starter is the command line of the first ancestor that is not a traced name.
    -> [{starter, module, names: {name: n}, count, per_hour}] most first (pure)."""
    groups = {}
    for e in events:
        g = groups.setdefault(e["starter"], {"starter": e["starter"], "module": module_of(e["starter"]),
                                             "names": {}, "count": 0})
        g["names"][e["name"]] = g["names"].get(e["name"], 0) + 1
        g["count"] += 1
    out = sorted(groups.values(), key=lambda g: -g["count"])
    for g in out:
        g["per_hour"] = int(round(g["count"] * 3600.0 / max(secs, 0.001)))
    return out


def starter_of(pid, table):
    """table: {pid: (name, ppid, cmd)}. Walk up past traced names (git under powershell under conhost...) to the first
    ancestor that is not one of them -> its cmd, or '<gone>' (pure)."""
    hops, cur = 0, table.get(pid)
    while cur and hops < 20:
        ppid = cur[1]
        parent = table.get(ppid)
        if parent is None:
            return "<gone>"
        if _base(parent[0]) not in TRACE_NAMES:
            return parent[2] or parent[0]
        cur, hops = parent, hops + 1
    return "<gone>"


def trace(secs=60, every=0.01):
    """-> {secs, events, groups}. Polls psutil.pids() (cheap) and reads only the new ones."""
    import psutil
    table = {}

    def info(pid):
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                return (p.name(), p.ppid(), " ".join(p.cmdline()) or p.name())
        except psutil.Error:
            return None

    for pid in psutil.pids():
        i = info(pid)
        if i:
            table[pid] = i
    before, events = set(table), []
    end = time.monotonic() + secs
    while time.monotonic() < end:
        new = set(psutil.pids()) - before
        for pid in new:
            before.add(pid)
            i = info(pid)
            if not i:
                continue
            table[pid] = i
            if _base(i[0]) in TRACE_NAMES:
                if i[1] not in table:                           # parent started between two polls
                    pi = info(i[1])
                    if pi:
                        table[i[1]] = pi
                events.append({"pid": pid, "name": _base(i[0]), "cmd": i[2], "starter": starter_of(pid, table)})
        time.sleep(every)
    return {"secs": secs, "events": events, "groups": group_trace(events, secs)}


def trace_lines(res):
    out = [f"churn trace: {len(res['events'])} new console-ish processes in {res['secs']} s"
           f" (~{int(round(len(res['events']) * 3600.0 / max(res['secs'], 0.001)))}/h)"]
    for g in res["groups"]:
        names = ", ".join(f"{n} {c}" for n, c in sorted(g["names"].items(), key=lambda x: -x[1]))
        out.append(f"  {g['count']:>5}  ~{g['per_hour']}/h  {g['module'] or '-':<32} {names}")
        out.append(f"         starter: {g['starter'][:180]}")
    return out


# ---- scan: the static call graph of the hook ------------------------------------------------------------------------
def _modname(path, root):
    rel = Path(path).resolve().relative_to(Path(root).resolve().parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class _Module:
    def __init__(self, name, path, tree, is_pkg):
        self.name, self.path, self.tree, self.is_pkg = name, path, tree, is_pkg
        self.funcs = {}             # qualname -> FunctionDef
        self.classes = {}           # class name -> [method qualnames]
        self.aliases = {}           # module-level name -> ("mod", modname) | ("attr", modname, name)
        self.top = []               # module-level statements (not defs/classes)


def _resolve_from(mod, node):
    """The absolute module an ImportFrom refers to."""
    if not node.level:
        return node.module or ""
    parts = mod.name.split(".")
    base = parts if mod.is_pkg else parts[:-1]
    base = base[:len(base) - (node.level - 1)] if node.level > 1 else base
    return ".".join(base + ([node.module] if node.module else []))


class Graph:
    def __init__(self, root=PKG_ROOT):
        self.root = Path(root)
        self.mods = {}
        for p in self.root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"), str(p))
            except SyntaxError:
                continue
            m = _Module(_modname(p, self.root), p, tree, p.name == "__init__.py")
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m.funcs[node.name] = node
                elif isinstance(node, ast.ClassDef):
                    meths = []
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            m.funcs[f"{node.name}.{sub.name}"] = sub
                            meths.append(f"{node.name}.{sub.name}")
                    m.classes[node.name] = meths
                    m.top.append(node)            # class bodies run at import (decorators, attributes)
                elif not _is_main_guard(node):
                    m.top.append(node)
            self._bind(m, [n for n in ast.walk(ast.Module(body=m.top, type_ignores=[]))
                           if isinstance(n, (ast.Import, ast.ImportFrom))], m.aliases)
            self.mods[m.name] = m

    def _bind(self, m, nodes, into):
        for node in nodes:
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        into[a.asname] = ("mod", a.name)
                    else:
                        into[a.name.split(".")[0]] = ("mod", a.name.split(".")[0])
            else:
                base = _resolve_from(m, node)
                for a in node.names:
                    full = f"{base}.{a.name}" if base else a.name
                    into[a.asname or a.name] = ("mod", full) if full in self.mods or self._is_mod(full) \
                        else ("attr", base, a.name)

    def _is_mod(self, name):
        if not name.startswith(self.root.name):
            return False
        rel = Path(*name.split(".")[1:]) if "." in name else Path()
        p = self.root / rel
        return p.with_suffix(".py").is_file() or (p / "__init__.py").is_file()

    def _local_aliases(self, m, fn):
        out = dict(m.aliases)
        self._bind(m, [n for n in ast.walk(fn) if isinstance(n, (ast.Import, ast.ImportFrom))], out)
        return out

    def _targets(self, m, ref, aliases):
        """A called/referenced name -> [(modname, qualname)] of functions in this package."""
        out = []
        if isinstance(ref, ast.Name):
            n = ref.id
            if n in m.funcs:
                out.append((m.name, n))
            elif n in m.classes:
                out += [(m.name, q) for q in m.classes[n]]
            elif n in aliases and aliases[n][0] == "attr":
                out += self._attr(aliases[n][1], aliases[n][2])
        elif isinstance(ref, ast.Attribute) and isinstance(ref.value, ast.Name):
            a = aliases.get(ref.value.id)
            if a and a[0] == "mod":
                out += self._attr(a[1], ref.attr)
            elif a and a[0] == "attr":                     # from pkg import mod-like-name (a class) -> Class.meth
                tm = self.mods.get(a[1])
                if tm and f"{a[2]}.{ref.attr}" in tm.funcs:
                    out.append((a[1], f"{a[2]}.{ref.attr}"))
            elif ref.value.id in m.classes and f"{ref.value.id}.{ref.attr}" in m.funcs:
                out.append((m.name, f"{ref.value.id}.{ref.attr}"))
            elif ref.value.id in ("self", "cls"):
                out += [(m.name, q) for q in m.funcs if q.endswith("." + ref.attr)]
        return out

    def _attr(self, modname, name, depth=0):
        tm = self.mods.get(modname)
        if not tm or depth > 4:
            return []
        if name in tm.funcs:
            return [(modname, name)]
        if name in tm.classes:
            return [(modname, q) for q in tm.classes[name]]
        a = tm.aliases.get(name)                            # re-exported through a package __init__
        if a and a[0] == "attr":
            return self._attr(a[1], a[2], depth + 1)
        return []

    @staticmethod
    def _sink(call, aliases):
        f = call.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            a = aliases.get(f.value.id)
            mod = a[1] if a and a[0] == "mod" else f.value.id
            if f.attr in SINK_CALLS.get(mod, ()):
                return f"{mod}.{f.attr}"
        if isinstance(f, ast.Name):
            a = aliases.get(f.id)
            if a and a[0] == "attr" and a[2] in SINK_CALLS.get(a[1], ()):
                return f"{a[1]}.{a[2]}"
        return None

    def _imported_mods(self, aliases_new):
        return {v[1] if v[0] == "mod" else v[1] for v in aliases_new.values()}

    def scan(self, entries=ENTRIES):
        """-> sorted [{key, module, function, callee, line, path}] for every sink reachable from entries."""
        todo = []
        for e in entries:
            mn, fn = e.split(":")
            todo.append((mn, fn))
            todo.append((mn, "<module>"))
        done, hits = set(), {}
        self.parent = {}
        while todo:
            mn, fn = todo.pop()
            if (mn, fn) in done or mn not in self.mods:
                continue
            done.add((mn, fn))
            m = self.mods[mn]
            if fn == "<module>":
                body = ast.Module(body=m.top, type_ignores=[])
                aliases = m.aliases
                parts = mn.split(".")                       # importing a.b.c runs a and a.b first
                todo += [(".".join(parts[:i]), "<module>") for i in range(1, len(parts))]
            else:
                body = m.funcs.get(fn)
                if body is None:
                    continue
                aliases = self._local_aliases(m, body)
            here = (mn, fn)

            def push(t, here=here):
                if t not in done and t not in self.parent:
                    self.parent[t] = here
                todo.append(t)
            for name, a in aliases.items():                 # every module this code imports runs its top level
                target = a[1]
                if a[0] == "mod" or target in self.mods:
                    push((target, "<module>"))
            for node, in_args in _walk(body, prune_defs=fn == "<module>"):
                if isinstance(node, ast.Call):
                    s = self._sink(node, aliases)
                    if s:
                        self._hit(hits, m, fn, s, node)
                    for t in self._targets(m, node.func, aliases):
                        push(t)
                    for ref in _callbacks(node):              # a function handed to a thread, map or sort key
                        for t in self._targets(m, ref, aliases):
                            push(t)
                elif in_args and isinstance(node, ast.Constant) and isinstance(node.value, str):
                    w = SINK_WORDS.search(node.value)          # a program name handed to a call, not a name list
                    if w and not _docstring(body, node):
                        self._hit(hits, m, fn, "str:" + w.group(1).lower(), node)
        return sorted(hits.values(), key=lambda h: h["key"])

    def path(self, mn, fn):
        """-> the chain of (module, function) from an entry to (mn, fn), entry first."""
        out, cur = [], (mn, fn)
        while cur and len(out) < 60:
            out.append(cur)
            cur = self.parent.get(cur)
        return out[::-1]

    def _hit(self, hits, m, fn, callee, node):
        key = f"{m.name}:{fn}:{callee}"
        if key not in hits:
            hits[key] = {"key": key, "module": m.name, "function": fn, "callee": callee, "line": node.lineno,
                         "path": str(m.path)}


CALLBACK_KW = {"target", "key", "callback", "fn", "func", "function", "default"}
CALLBACK_POS = {"map", "filter", "submit", "apply_async", "run_in_executor", "Thread", "Timer", "call_soon"}


def _callbacks(call):
    """The function references a call hands on to be called: Thread(target=f), map(f, xs), pool.submit(f). A
    function only stored (set_defaults(func=f) in a parser that is never run, a dispatch dict) is not followed:
    func= is a callback keyword, but only for those same higher-order calls."""
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
    out = []
    if name in CALLBACK_POS:
        out += [a for a in call.args if isinstance(a, (ast.Name, ast.Attribute))]
        out += [k.value for k in call.keywords if k.arg in CALLBACK_KW and isinstance(k.value, (ast.Name, ast.Attribute))]
    else:
        out += [k.value for k in call.keywords if k.arg in ("target", "key", "callback")
                and isinstance(k.value, (ast.Name, ast.Attribute))]
    return out


def _walk(body, prune_defs=False):
    """-> (node, in_args) for every node under body. in_args: the node sits inside a call's arguments (where a
    program name becomes a command line); a constant in a name list or a regex is data. prune_defs: a function
    defined at module or class level is not run by importing the module, so its body is not walked."""
    stack = [(body, False)]
    while stack:
        node, in_args = stack.pop()
        yield node, in_args
        if isinstance(node, ast.Call):
            f = node.func                                   # re.compile("...|powershell|...") is a pattern, data
            arg = not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "re")
            stack.append((node.func, in_args))
            stack += [(a, arg) for a in node.args]
            stack += [(k.value, arg) for k in node.keywords]
            continue
        for child in ast.iter_child_nodes(node):
            if prune_defs and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append((child, in_args))


def _is_main_guard(node):
    """`if __name__ == "__main__":` - not run when the module is imported."""
    t = getattr(node, "test", None)
    return (isinstance(node, ast.If) and isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
            and t.left.id == "__name__" and any(isinstance(c, ast.Constant) and c.value == "__main__"
                                                for c in t.comparators))


def _docstring(body, node):
    """True when node is the docstring of body or of a def/class inside it."""
    for n in ast.walk(body if isinstance(body, ast.AST) else ast.Module(body=body, type_ignores=[])):
        b = getattr(n, "body", None)
        if isinstance(b, list) and b and isinstance(b[0], ast.Expr) and b[0].value is node:
            return True
    return False


def load_allow(path=ALLOW):
    """churn_allow.yaml: {allow: {KEY: REASON}} -> {key: reason}. A key without a reason is not allowed."""
    import yaml
    p = Path(path)
    if not p.is_file():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {k: str(v).strip() for k, v in (data.get("allow") or {}).items() if v and str(v).strip()}


def scan(root=PKG_ROOT, allow_path=ALLOW, entries=ENTRIES):
    """-> {hits, new: [hit], stale: [key], allowed: n}."""
    hits = Graph(root).scan(entries)
    allow = load_allow(allow_path)
    keys = {h["key"] for h in hits}
    return {"hits": hits, "new": [h for h in hits if h["key"] not in allow],
            "stale": sorted(k for k in allow if k not in keys), "allowed": len(keys & set(allow))}


def scan_lines(res, root=PKG_ROOT):
    out = [f"churn scan: {len(res['hits'])} process-starting calls reachable from the room hook, "
           f"{res['allowed']} allowed, {len(res['new'])} NEW, {len(res['stale'])} stale allow entries"]
    base = Path(root).resolve().parent
    for h in res["new"]:
        try:
            where = Path(h["path"]).resolve().relative_to(base).as_posix()
        except ValueError:
            where = h["path"]
        out.append(f"  NEW {h['key']}  ({where}:{h['line']})")
    out += [f"  stale {k} (no longer reachable: remove it from churn_allow.yaml)" for k in res["stale"]]
    return out


# ---- CLI ----------------------------------------------------------------------------------------------------------
def main(ns, cfg):
    act = ns.churn_cmd or "hooks"
    if act == "hooks":
        ev = getattr(ns, "event", None)
        events = [ev] if ev else list(EVENTS)
        res = [run_hook(e, module=getattr(ns, "module", None) or "fieldkit.autopilot.room",
                        args=tuple(getattr(ns, "hook_arg", None) or ["--hook"])) for e in events]
        bad = verdict(res, cfg)
        if ns.json:
            print(json.dumps({"results": res, "over_budget": bad, "budget": budget(cfg)}, indent=1))
        else:
            print("\n".join(hooks_lines(res, cfg)))
            print("churn hooks: " + ("FAIL - " + "; ".join(bad) if bad else "PASS (every event inside its budget)"))
            if bad:
                print("NEXT: fieldkit hog churn scan names the call; replace it with an in-process read or a cache")
        return 1 if bad else 0
    if act == "trace":
        res = trace(ns.secs)
        if ns.json:
            print(json.dumps(res, indent=1))
        else:
            print("\n".join(trace_lines(res)))
            print("NEXT: " + ("fieldkit hog churn hooks shows whether a hook is the starter"
                              if res["events"] else "nothing new started"))
        return 0
    if act == "scan":
        res = scan()
        if ns.json:
            print(json.dumps(res, indent=1))
        else:
            print("\n".join(scan_lines(res)))
            print("churn scan: " + ("FAIL" if res["new"] else "PASS"))
            if res["new"]:
                print("NEXT: make the call in-process (ctypes, a file read, a cache), or add KEY: REASON to "
                      "fieldkit/hog/churn_allow.yaml when it is gated (SessionStart only, cached, rare)")
        return 1 if res["new"] else 0
    return 2


def add_parser(sub):
    ch = sub.add_parser("churn", help="process churn: hook children per event, who starts consoles, a static gate")
    cs = ch.add_subparsers(dest="churn_cmd")
    h = cs.add_parser("hooks", help="run the hook per event and count the processes it starts")
    h.add_argument("--event", choices=EVENTS, default=argparse.SUPPRESS)
    h.add_argument("--module", default="fieldkit.autopilot.room", help="the hook module (default: the room hook)")
    h.add_argument("--hook-arg", action="append", help="argument(s) to the module (default: --hook)")
    h.add_argument("--json", action="store_true")
    t = cs.add_parser("trace", help="catch new conhost/powershell/git/gh/netsh/typeperf machine-wide")
    t.add_argument("--secs", type=float, default=argparse.SUPPRESS)
    t.add_argument("--json", action="store_true")
    s = cs.add_parser("scan", help="static: process-starting calls reachable from the room hook")
    s.add_argument("--json", action="store_true")
    ch.add_argument("--event", choices=EVENTS, help="hooks: one event only (default: all five)")
    ch.add_argument("--secs", type=float, default=60, help="trace: seconds to watch (default 60)")
    ch.add_argument("--json", action="store_true")
    return ch
