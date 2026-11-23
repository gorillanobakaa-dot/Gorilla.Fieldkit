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


WT_NAMES = {"wt", "wt.exe", "windowsterminal", "windowsterminal.exe"}


def _new_console_ref(node):
    """True for a node that asks Windows for a visible console window of its own: the name or attribute
    CREATE_NEW_CONSOLE (also as getattr's string), or a Windows Terminal launch (wt.exe as argv[0]). 2026-11-23: one
    Windows Terminal held 802 windows; a window of its own is allowed only where proc_allow.yaml says why."""
    if isinstance(node, ast.Name) and node.id == "CREATE_NEW_CONSOLE":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "CREATE_NEW_CONSOLE":
        return True
    if isinstance(node, ast.Constant) and node.value == "CREATE_NEW_CONSOLE":
        return True
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts and isinstance(node.elts[0], ast.Constant):
        v = str(node.elts[0].value).replace("\\", "/").rsplit("/", 1)[-1].lower()
        return v in WT_NAMES
    return False


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
    call_funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    # `run is subprocess.run` only compares which function a caller passed; it starts nothing
    compared = {id(o) for n in ast.walk(tree) if isinstance(n, ast.Compare)
                and all(isinstance(op, (ast.Is, ast.IsNot)) for op in n.ops) for o in [n.left, *n.comparators]}

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
            if (isinstance(ch, ast.Attribute) and id(ch) not in call_funcs and id(ch) not in compared and isinstance(ch.value, ast.Name)
                    and mods.get(ch.value.id) == "subprocess" and ch.attr in SUB_FNS):
                # `run=subprocess.run` as a default or argument starts the same raw child later (A5, 2026-11-23)
                hits.append((f"{rel}:{fn}:subprocess.{ch.attr}", ch.lineno))
            if _new_console_ref(ch):
                hits.append((f"{rel}:{fn}:CREATE_NEW_CONSOLE", ch.lineno))
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
    (pkg / "b.py").write_text("import subprocess\ndef g():\n    x = subprocess.CREATE_NEW_CONSOLE\n"
                              "    y = getattr(subprocess, 'CREATE_NEW_CONSOLE', 16)\n    z = ['wt.exe', '-w', '0']\n",
                              encoding="utf-8")
    new_b = [n.split(" ")[0] for n in scan_tree(tmp_path, {})[0] if n.startswith("fieldkit/b.py")]
    assert new_b == ["fieldkit/b.py:g:CREATE_NEW_CONSOLE"] * 3
    (pkg / "b.py").unlink()
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
    for shell in (["powershell", "-c", "x"], ["C:/Windows/System32/cmd.exe", "/c", "x"], ["C:\\t\\run.bat"], "x.ps1"):
        f = proc._prepare({"capture_output": True}, (shell,))["creationflags"]
        assert f == proc.CREATE_NO_WINDOW, shell                    # a shell needs a console of its own, hidden
    assert proc._prepare({"capture_output": True, "shell": True}, ("dir",))["creationflags"] == proc.CREATE_NO_WINDOW
    assert proc._prepare({"capture_output": True}, (["git", "status"],))["creationflags"] == proc.DETACHED_PROCESS
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


def test_terminal_floods():
    from fieldkit.hog import consoles
    rows = [{"pid": 10, "name": "WindowsTerminal.exe", "ppid": 1, "created": 0},       # 802 dead windows
            {"pid": 11, "name": "WindowsTerminal.exe", "ppid": 1, "created": 0},       # the owner's 3 tabs
            {"pid": 12, "name": "WindowsTerminal.exe", "ppid": 1, "created": 0},       # 30 windows, a live shell
            {"pid": 13, "name": "pwsh.exe", "ppid": 12, "created": 0},
            {"pid": 14, "name": "WindowsTerminal.exe", "ppid": 1, "created": 990},     # flood, but brand new
            {"pid": 15, "name": "explorer.exe", "ppid": 1, "created": 0}]
    wins = {10: 802, 11: 3, 12: 30, 14: 40, 15: 99}
    assert consoles.terminal_windows(rows, wins) == {10: 802, 11: 3, 12: 30, 14: 40}
    assert consoles.floods(rows, wins, now=1000) == [10]
    res = {"fieldkit_owned": [], "terminal_windows": 875}
    assert consoles.failures(res) == ["875 terminal windows open (limit 19)"]
    assert consoles.failures(dict(res, terminal_windows=3)) == []


def test_scoop_shim_gets_a_hidden_console(tmp_path, monkeypatch):
    """`gh` is a scoop shim that starts the real gh with default flags: with no console of its own that is a flashing
    Windows Terminal window per call (owner 2026-11-23, ship ci-wait). A shim must get CREATE_NO_WINDOW."""
    import sys
    from fieldkit import proc
    if sys.platform != "win32":
        return
    (tmp_path / "tool.exe").write_bytes(b"")
    (tmp_path / "tool.shim").write_text("path = x", encoding="utf-8")
    (tmp_path / "plain.exe").write_bytes(b"")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert proc._needs_console((["tool"],), {})
    assert not proc._needs_console((["plain"],), {})
