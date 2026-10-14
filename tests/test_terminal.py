"""fieldkit terminal: a window for a person, with the command ready; nothing opened in the tests."""
import subprocess

from fieldkit import terminal as T


def test_pick_prefers_the_env_then_the_desktop_then_any():
    have = {"gnome-terminal", "xterm", "konsole"}
    which = lambda n: "/usr/bin/" + n if n in have else None
    assert T.pick_linux(which, {"TERMINAL": "konsole"})[0] == "konsole"
    assert T.pick_linux(which, {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"})[0] == "gnome-terminal"
    assert T.pick_linux(lambda n: "/usr/bin/xterm" if n == "xterm" else None, {})[0] == "xterm"
    assert T.pick_linux(lambda n: None, {}) == (None, None)


def test_ask_script_waits_for_enter_and_run_script_does_not():
    ask = T.bash_script(["/opt/fk", "build-harness", "approve", "t1"], "ask", "/tmp/x.log", "Approve")
    run = T.bash_script(["/opt/fk", "doctor"], "run", "/tmp/x.log")
    assert "Press Enter to run it" in ask and "exit 130" in ask and "/opt/fk build-harness approve t1" in ask
    assert "Press Enter to run it" not in run and "Press Enter to close" in run
    assert 'tee -a "$log"' in ask and "PIPESTATUS" in ask


def test_open_refuses_without_a_desktop_and_names_the_command(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "log_path", lambda now=None: tmp_path / "t.log")
    r = T.open_window(["/opt/fk", "doctor"], env={}, platform="linux", popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    assert not r["ok"] and "DISPLAY" in r["why"] and r["command"] == ["/opt/fk", "doctor"]
    r = T.open_window(["/opt/fk", "doctor"], env={"DISPLAY": ":0"}, platform="linux", which=lambda n: None,
                      popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    assert not r["ok"] and "no terminal program" in r["why"]


def test_open_builds_the_right_argv_for_the_desktop_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "log_path", lambda now=None: tmp_path / "t.log")
    seen = {}
    class P:
        pid = 4242
    def popen(argv, **kw):
        seen["argv"] = argv; seen["kw"] = kw; return P()
    r = T.open_window(["/opt/fk", "build-harness", "approve", "t1"], mode="ask", env={"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "GNOME"},
                      platform="linux", which=lambda n: "/usr/bin/" + n if n == "gnome-terminal" else None, popen=popen)
    assert r["ok"] and r["terminal"] == "gnome-terminal" and r["pid"] == 4242
    assert seen["argv"][:5] == ["gnome-terminal", "--title", "Fieldkit: /opt/fk build-harness approve t1", "--", "bash"]
    assert seen["kw"]["start_new_session"] is True and seen["kw"]["stdin"] is subprocess.DEVNULL
    assert "Press Enter to run it" in seen["argv"][-1]


def test_windows_uses_start_and_a_kept_open_powershell(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "log_path", lambda now=None: tmp_path / "t.log")
    seen = {}
    def popen(argv, **kw):
        seen["argv"] = argv; return type("P", (), {"pid": 7})()
    r = T.open_window("C:\\fk\\fieldkit.exe doctor", mode="run", platform="win32", popen=popen)  # privacy-scan: allow
    assert r["ok"] and seen["argv"][:4] == ["cmd", "/c", "start", ""] and "-NoExit" in seen["argv"]
    assert "Read-Host" not in seen["argv"][-1]


def test_doctor_says_what_is_missing():
    d = T.doctor(which=lambda n: None, env={}, platform="linux")
    assert not d["ok"] and len(d["problems"]) == 2
    d = T.doctor(which=lambda n: "/usr/bin/xterm" if n == "xterm" else None, env={"DISPLAY": ":0"}, platform="linux")
    assert d["ok"] and d["terminal"] == "xterm"


def test_log_path_works_unstubbed_with_and_without_a_time(tmp_path, monkeypatch):
    """2026-10-14: the first real `fieldkit terminal run` crashed in log_path because strftime refuses None and
    every test had stubbed the function out. A stub is not a test of the thing it replaces."""
    import time
    monkeypatch.setattr(T.settings, "ROOT", tmp_path)
    a = T.log_path()
    b = T.log_path(time.localtime(0))
    assert a.parent == tmp_path / "state" / "terminal" and a.suffix == ".log"
    assert b.name == time.strftime("%Y%m%d-%H%M%S", time.localtime(0)) + ".log"
