"""No fieldkit code starts a child process except through fieldkit.proc (owner order 2026-11-22).

On Windows a console program started by a console-less parent (a hook, a watcher) gets its own conhost.exe; 60,000
piled up. fieldkit.proc adds DETACHED_PROCESS when the streams are redirected. A raw subprocess call outside it fails
here, unless fieldkit/proc_allow.yaml lists it with a reason."""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUB_FNS = {"run", "Popen", "check_output", "check_call", "call", "getoutput", "getstatusoutput"}
OS_FNS = {"system", "startfile"}
SKIP_OS = {"startfile"}  # opens a document in its viewer; creates no console


def scan_source(src, rel):
    """-> [(key, lineno)] for every raw process-starting call in one module's source (pure)."""
    tree = ast.parse(src)
    mods, froms = {}, {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name in ("subprocess", "os"):
                    mods[a.asname or a.name] = a.name
        elif isinstance(n, ast.ImportFrom) and n.module in ("subprocess", "os"):
            for a in n.names:
                froms[a.asname or a.name] = (n.module, a.name)
    hits = []

    def walk(node, fn):
        for ch in ast.iter_child_nodes(node):
            name = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(ch, ast.Call):
                f, callee = ch.func, None
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in mods:
                    m = mods[f.value.id]
                    if (m == "subprocess" and f.attr in SUB_FNS) or (m == "os" and f.attr in OS_FNS - SKIP_OS):
                        callee = f"{m}.{f.attr}"
                elif isinstance(f, ast.Name) and f.id in froms:
                    m, a = froms[f.id]
                    if (m == "subprocess" and a in SUB_FNS) or (m == "os" and a in OS_FNS - SKIP_OS):
                        callee = f"{m}.{a}"
                if callee:
                    hits.append((f"{rel}:{fn}:{callee}", ch.lineno))
            walk(ch, name)

    walk(tree, "<module>")
    return hits


def load_allow(path):
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {k: v for k, v in (data.get("allow") or {}).items() if str(v or "").strip()}


def scan_tree(root, allow):
    """-> (new hits, stale allow keys)."""
    new, seen = [], set()
    for p in sorted((root / "fieldkit").rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel == "fieldkit/proc.py":
            continue
        for key, line in scan_source(p.read_text(encoding="utf-8-sig"), rel):
            seen.add(key)
            if key not in allow:
                new.append(f"{key} (line {line})")
    return new, sorted(set(allow) - seen)


def test_no_raw_subprocess_in_fieldkit():
    new, stale = scan_tree(ROOT, load_allow(ROOT / "fieldkit" / "proc_allow.yaml"))
    assert not new, ("raw process start outside fieldkit.proc; use `from fieldkit import proc as fkproc` "
                     "(fkproc.run/Popen/check_output) or add KEY: REASON to fieldkit/proc_allow.yaml:\n  "
                     + "\n  ".join(new))
    assert not stale, "stale proc_allow.yaml entries (no longer called): " + ", ".join(stale)


def test_scan_can_fail(tmp_path):
    pkg = tmp_path / "fieldkit"
    pkg.mkdir()
    (pkg / "a.py").write_text("import subprocess as sp\nfrom subprocess import Popen\nimport os\n"
                              "def f():\n    sp.run(['x'])\n    Popen(['y'])\n    os.system('z')\n    os.startfile('d')\n",
                              encoding="utf-8")
    (pkg / "proc.py").write_text("import subprocess\nsubprocess.run(['ok'])\n", encoding="utf-8")
    new, _ = scan_tree(tmp_path, {})
    assert [n.split(" ")[0] for n in new] == ["fieldkit/a.py:f:subprocess.run", "fieldkit/a.py:f:subprocess.Popen",
                                              "fieldkit/a.py:f:os.system"]
    new, stale = scan_tree(tmp_path, {"fieldkit/a.py:f:subprocess.run": "r", "fieldkit/a.py:f:subprocess.Popen": "r",
                                      "fieldkit/a.py:f:os.system": "r", "fieldkit/gone.py:g:os.system": "r"})
    assert not new and stale == ["fieldkit/gone.py:g:os.system"]


def test_allow_entry_without_reason_is_ignored(tmp_path):
    f = tmp_path / "allow.yaml"
    f.write_text("allow:\n  'a.py:f:os.system': ''\n  'b.py:f:os.system': because\n", encoding="utf-8")
    assert list(load_allow(f)) == ["b.py:f:os.system"]


def test_proc_flags(monkeypatch):
    from fieldkit import proc
    monkeypatch.setattr(proc.sys, "platform", "win32")
    assert proc._prepare({"capture_output": True})["creationflags"] & proc.DETACHED_PROCESS
    assert proc._prepare({"capture_output": True})["stdin"] is subprocess.DEVNULL
    assert proc._prepare({"stdout": 1, "stderr": 2, "stdin": 3})["stdin"] == 3
    inherit = {"check": True}
    assert proc._prepare(inherit) is inherit                       # streaming keeps its console
    assert proc._prepare({"capture_output": True, "creationflags": proc.CREATE_NEW_CONSOLE}).get("creationflags") \
        == proc.CREATE_NEW_CONSOLE                                  # asked for its own console: untouched
    monkeypatch.setattr(proc.sys, "platform", "linux")
    kw = {"capture_output": True}
    assert proc._prepare(kw) is kw


def test_proc_runs_a_child():
    from fieldkit import proc
    r = proc.run([sys.executable, "-c", "print(7)"], capture_output=True, text=True)
    assert r.stdout.strip() == "7"
    assert proc.check_output([sys.executable, "-c", "print(8)"]).strip() == b"8"


def test_consoles_owners():
    from fieldkit.hog import consoles
    rows = [{"pid": 1, "name": "python.exe", "ppid": 0, "cmd": "python -m fieldkit.autopilot.room --hook"},
            {"pid": 2, "name": "conhost.exe", "ppid": 1, "cmd": ""},
            {"pid": 3, "name": "claude.exe", "ppid": 0, "cmd": r"claude C:\Scripts\Fieldkit\x"},
            {"pid": 4, "name": "conhost.exe", "ppid": 3, "cmd": ""},
            {"pid": 5, "name": "conhost.exe", "ppid": 99, "cmd": ""}]
    groups, mine = consoles.owners(rows)
    assert mine == [2] and groups == {"python.exe": [2], "claude.exe": [4], "dead parent": [5]}
