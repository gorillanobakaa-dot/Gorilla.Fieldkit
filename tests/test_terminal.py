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
    assert "PRESS ENTER to run it" in ask and "exit 130" in ask and "/opt/fk build-harness approve t1" in ask
    assert "PRESS ENTER to run it" not in run and "PRESS ENTER to close" in run
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
    assert "PRESS ENTER to run it" in seen["argv"][-1]


def test_windows_uses_start_and_a_kept_open_powershell(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "log_path", lambda now=None: tmp_path / "t.log")
    seen = {}
    def popen(argv, **kw):
        seen["argv"] = argv; return type("P", (), {"pid": 7})()
    r = T.open_window("C:\\fk\\fieldkit.exe doctor", mode="run", platform="win32", popen=popen)  # privacy-scan: allow
    assert r["ok"] and seen["argv"][:4] == ["cmd", "/c", "start", ""] and "-NoExit" in seen["argv"]
    assert "Press Enter to run it" not in seen["argv"][-1], "run mode never asks before running"


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


def test_a_title_after_the_action_is_not_run_as_a_program():
    """2026-10-14: `terminal run --title X -- CMD` ran `--title` as the program (exit 127)."""
    assert T.split_args(["--title", "Hello", "--", "/opt/fk", "doctor"]) == (["/opt/fk", "doctor"], "Hello")
    assert T.split_args(["--", "/opt/fk", "doctor"], "Kept") == (["/opt/fk", "doctor"], "Kept")
    assert T.split_args(["/opt/fk", "--", "x"]) == (["/opt/fk", "x"], None)


def test_the_persons_acts_are_logged_allowed_refused_and_closed():
    """2026-10-14, the owner: "it might have forensic value". The window writes when it opened, when the person
    allowed the run, when they closed it after, and (by a trap) when they closed it instead of answering."""
    ask = T.bash_script(["/opt/fk", "doctor"], "ask", "/tmp/x.log")
    assert "window opened at" in ask and "run allowed by the person at" in ask
    assert "closed by the person at" in ask and "without answering the prompt" in ask
    assert ask.index("trap 'echo") < ask.index("read -r _ || exit 130")
    assert ask.index("trap - EXIT") > ask.index("PRESS ENTER to close this window")
    run = T.bash_script(["/opt/fk", "doctor"], "run", "/tmp/x.log")
    assert "run allowed by the person" not in run and "closed by the person at" in run
    ps = T.powershell_script(["C:\\fk.exe", "doctor"], "ask", "C:\\x.log")  # privacy-scan: allow
    assert "run allowed by the person" in ps and "closed by the person at" in ps


def test_history_reads_each_windows_outcome(tmp_path):
    (tmp_path / "20261014-170000.log").write_text(
        "window opened at 2026-10-14 17:00:00 mode=ask command: /opt/fk doctor\n"
        "run allowed by the person at 2026-10-14 17:00:05\ncommand started at 2026-10-14 17:00:05\nhello\n"
        "command finished at 2026-10-14 17:00:06 after 1 s\nexit 0\nclosed by the person at 2026-10-14 17:00:30\n", encoding="utf-8")
    (tmp_path / "20261014-170100.log").write_text(
        "window opened at 2026-10-14 17:01:00 mode=ask command: /opt/fk install\n"
        "window closed by the person at 2026-10-14 17:01:09 without answering the prompt\n", encoding="utf-8")
    rows = T.history(root=tmp_path)
    assert [r["opened"] for r in rows] == ["2026-10-14 17:01:00", "2026-10-14 17:00:00"], "newest first"
    refused, ran = rows
    assert ran["allowed"] == "2026-10-14 17:00:05" and ran["exit"] == 0 and ran["ended"].startswith("closed by the person")
    assert refused["allowed"] is None and refused["exit"] is None and "without answering" in refused["ended"]
    text = "\n".join(T.history_lines(rows))
    assert "never ran" in text and "exit 0" in text


def test_the_window_is_shaped_for_ten_seconds():
    """The owner, 2026-10-14: "some of these only have like a 10 second window in which they can focus". The one
    thing to do is the first line on the screen, the command the second; after the run, the last thing on the
    screen is a one-line verdict and the one thing to do next; an optional spoken first line."""
    ask = T.bash_script(["/opt/fk", "doctor"], "ask", "/tmp/x.log", "Check the kit")
    body = ask.split("clear 2>/dev/null\n", 1)[1]
    first_prints = [l for l in body.splitlines() if l.startswith("printf")][:2]
    assert "PRESS ENTER to run it. Close this window to say no." in first_prints[0]
    assert "Check the kit" in first_prints[1]
    assert "DONE. It worked." in ask and "FAILED (exit $code). The harness has the log." in ask
    assert ask.rstrip().splitlines()[-4].startswith("printf") and "PRESS ENTER to close" in ask.rstrip().splitlines()[-4]
    spoken = T.bash_script(["/opt/fk", "doctor"], "ask", "/tmp/x.log", say="Oi. Look at the window.")
    assert "fieldkit.buildh.herald --say" in spoken and "Oi. Look at the window." in spoken and "herald" not in ask


def test_the_spoken_words_are_the_owners_setting_and_always_say_is_a_setting():
    always, words = T.say_settings({"terminal": {"say": "Yo. Pay attention to the terminal window.", "always_say": True}})
    assert always and words == "Yo. Pay attention to the terminal window."
    always, words = T.say_settings({})
    assert not always and words == T.DEFAULT_SAY
