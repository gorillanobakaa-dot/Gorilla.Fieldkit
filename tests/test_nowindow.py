"""Open item 38: no console window flashes from the hooks (count of console-creating starts per 20 tool calls)."""
import io
import json
import subprocess
import sys

from fieldkit.autopilot import room, toolrouter
from fieldkit.core import nowindow


def test_windowless_picks_pythonw_beside_python_exe_only_on_windows():
    ex = lambda p: True
    assert nowindow.windowless(r"C:\py\python.exe", "win32", ex) == r"C:\py\pythonw.exe"
    assert nowindow.windowless(r"C:\py\python.exe", "win32", lambda p: False) == r"C:\py\python.exe"
    assert nowindow.windowless("/usr/bin/python3", "linux", ex) == "/usr/bin/python3"
    assert nowindow.flags(0, "linux") == 0 and nowindow.flags(0, "win32") == nowindow.NO_WINDOW


def test_children_of_a_console_less_process_start_with_no_window(monkeypatch):
    """20 tool calls' worth of git/gh starts: before, 0 of 20 hidden; after, 20 of 20."""
    seen = []
    monkeypatch.setattr(subprocess.Popen, "__init__", lambda self, *a, **kw: seen.append(kw.get("creationflags", 0)))
    monkeypatch.setattr(nowindow, "_installed", False)
    assert nowindow.install(force=True, platform="win32")
    for _ in range(20):
        subprocess.Popen(["git", "status"])
    subprocess.Popen(["x"], creationflags=0x10)          # asked for its own console: left alone
    assert [f & nowindow.NO_WINDOW for f in seen[:20]] == [nowindow.NO_WINDOW] * 20
    assert not seen[20] & nowindow.NO_WINDOW
    monkeypatch.setattr(nowindow, "_installed", False)


def test_install_does_nothing_off_windows():
    assert nowindow.install(force=True, platform="linux") is False


def test_doctor_apply_rewrites_a_console_python_hook_to_pythonw(tmp_path, monkeypatch):
    monkeypatch.setattr(nowindow, "windowless", lambda p, *a, **k: p.replace("python.exe", "pythonw.exe"))
    py = r"C:\py\python.exe"
    cmds = {"PreToolUse": room.ROUTER_CMD, "UserPromptSubmit": room.HOOK_CMD}
    hooks = {ev: [{"matcher": "*" if ev != "PreToolUse" else room.TOOL_MATCH,
                   "hooks": [{"type": "command", "command": c.format(py=py)}]}] for ev, c in cmds.items()}
    hooks["Stop"] = [{"hooks": [{"type": "command", "command": f'"{py}" -m fieldkit.autopilot.ownercmd --stop-hook'}]}]
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    r = room.doctor(s, python=py)
    assert not r["ok"] and sorted(r["stale"]) == ["console:PreToolUse", "console:Stop", "console:UserPromptSubmit"]
    code, _ = room.apply(s, python=py, keep_dir=tmp_path / "keep")
    assert code == 0
    new = json.loads(s.read_text(encoding="utf-8"))["hooks"]
    for ev in ("PreToolUse", "UserPromptSubmit", "Stop"):
        assert new[ev][0]["hooks"][0]["command"].startswith(r'"C:\py\pythonw.exe" -m fieldkit.')
    assert room.doctor(s, python=py)["ok"]


def test_a_hook_without_stdout_still_prints_its_deny(monkeypatch):
    """pythonw gives sys.stdout None: ensure_stdio must hand the deny a real stream (exit code is 0 either way)."""
    monkeypatch.setattr(sys, "stdout", None)
    nowindow.ensure_stdio()
    assert sys.stdout is not None
    print("x", file=sys.stdout)
