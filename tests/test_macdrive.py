# Version: 1.0.0 · updated 26-10-23-11-02
"""fieldkit mac keep-awake|open-app|screen-text|keys|wait-text: driving a Mac's screen like a person, with every lesson
of 2026-10-23 (the 2011 MacBook Air) as a rule. A fake Mac answers the ssh lines and AppleScripts."""
import pytest

from fieldkit.mac import macdrive as D

fn = lambda line: ["ssh", "mac", line]


class Mac:
    """Remote lines and AppleScripts -> canned answers; `drops` Wi-Fi failures first; a screen that a key changes."""

    def __init__(self, drops=0, screen="> \nidle", windows_seq=None, change_on_key=True):
        self.drops, self.screen, self.calls = drops, screen, []
        self.windows_seq = list(windows_seq or [])
        self.change_on_key = change_on_key

    def __call__(self, argv, stdin):
        line = argv[-1]
        self.calls.append((line, stdin))
        if self.drops:
            self.drops -= 1
            return 255, "ssh: connect to host mac port 22: Connection timed out"
        if line.startswith("osascript -") and stdin == D.TAB_SCRIPT:
            return 0, self.screen
        if line == "pbcopy":
            self.clip = stdin
        if line.startswith("osascript -") and stdin == D.KEY_SCRIPT:
            if self.change_on_key:                       # a real TUI shows what was pasted (or reacts to the key)
                self.screen += "\n> " + (getattr(self, "clip", None) or "changed")
            return 0, "SENT"
        if line.startswith("osascript -") and stdin == D.WINDOWS_SCRIPT:
            return 0, self.windows_seq.pop(0) if self.windows_seq else ""
        return 0, "ok"


def test_a_wifi_drop_is_retried_and_a_refusal_is_not():
    mac = Mac(drops=2)
    rc, out, n = D.remote(fn, "true", runner=mac, sleep=lambda s: None)
    assert (rc, n) == (0, 3)                               # the Air dropped for 10-40 s at a time, 5 times in an hour
    refused = lambda argv, stdin: (255, "Permission denied (publickey)")
    rc, out, n = D.remote(fn, "true", runner=refused, sleep=lambda s: None)
    assert n == 1 and "publickey" in out
    rc, out, n = D.remote(fn, "true", tries=3, runner=Mac(drops=9), sleep=lambda s: None)
    assert rc == 255 and n == 3 and "router" in out


def test_screen_text_reads_the_tab_by_tty_without_a_picture():
    rows, err = D.screen_text(fn, "/dev/ttys001", runner=Mac(screen="a\n\n  b  \n> "))
    assert rows == ["a", "  b", ">"]
    gone = lambda argv, stdin: (0, "NO TAB ON /dev/ttys009")
    assert D.screen_text(fn, "/dev/ttys009", runner=gone)[0] is None


def test_a_prompt_is_pasted_as_one_line():
    mac = Mac()
    code, msg = D.keys(fn, "/dev/ttys001", paste_text="line one\nline two\n  three", runner=mac, sleep=lambda s: None)
    assert code == 0
    assert ("pbcopy", "line one line two three") in mac.calls      # a newline would have submitted half of it


def test_a_key_that_did_not_land_is_reported():
    code, msg = D.keys(fn, "/dev/ttys001", key="enter", runner=Mac(change_on_key=False), sleep=lambda s: None)
    assert code == 3 and "did not change" in msg                    # 2026-10-23: "sent", and nothing happened
    assert D.keys(fn, "/dev/ttys001", key="f13", runner=Mac(), sleep=lambda s: None)[0] == 2


def test_keys_raise_the_window_after_activate_in_one_script():
    # "activate" brought Terminal's own front window and two pastes landed in an idle bash (2026-10-23)
    s = D.KEY_SCRIPT
    assert s.index("activate") < s.index("set index of w to 1") < s.index('tell application "System Events"')
    assert "tty of t is theTTY" in s


def test_only_new_idle_windows_are_closed():
    before = [(10, "/dev/ttys000", False)]                         # the owner's own window: never touched
    after = before + [(20, "/dev/ttys001", True), (21, "/dev/ttys002", False), (22, "/dev/ttys003", True)]
    assert D.extra_windows(before, after, "/dev/ttys001") == [(21, "/dev/ttys002", False)]


def test_open_app_answers_the_folder_and_closes_the_extra_window():
    class AppMac(Mac):
        def __call__(self, argv, stdin):
            line = argv[-1]
            if "choose folder" in line:
                self.calls.append((line, stdin))
                return 0, "DIALOG"
            if "pgrep -x" in line:
                self.calls.append((line, stdin))
                return 0, "2295 /dev/ttys001 /Users/u/Stranger-Test"
            return super().__call__(argv, stdin)
    mac = AppMac(windows_seq=["", "344\t/dev/ttys001\ttrue\n341\t/dev/ttys000\tfalse\n"])
    code, lines = D.open_app(fn, "~/Applications/Gorilla OpenCode.app", "~/Stranger-Test", "gorilla-opencode",
                             runner=mac, sleep=lambda s: None)
    assert code == 0, lines
    assert any("open -a \"$HOME\"/" in c[0] for c in mac.calls)    # "~" is the Mac's home, not this computer's
    assert any("id is 341" in (c[1] or "") for c in mac.calls)      # the extra idle window
    assert not any("id is 344" in (c[1] or "") for c in mac.calls)
    assert "PID 2295" in " ".join(lines)


def test_wait_text_finds_a_pattern_or_times_out():
    mac = Mac(screen="working\nPermission Required\nAllow (a)")
    assert D.wait_text(fn, "/dev/ttys001", r"permission required", runner=mac, sleep=lambda s: None)[0] == 0
    clock = {"t": 0}
    code, msg = D.wait_text(fn, "/dev/ttys001", "READY", seconds=10, runner=Mac(), now=lambda: clock["t"],
                            sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    assert code == 3


def test_keep_awake_records_its_pid_and_stop_kills_only_that():
    mac = Mac()
    D.keep_awake(fn, minutes=90, runner=mac, sleep=lambda s: None)
    assert "caffeinate -dimsu -t 5400" in mac.calls[-1][0] and "fieldkit-keep-awake.pid" in mac.calls[-1][0]
    D.keep_awake(fn, stop=True, runner=mac, sleep=lambda s: None)
    assert "kill $(cat /tmp/fieldkit-keep-awake.pid)" in mac.calls[-1][0] and "pkill" not in mac.calls[-1][0]


def test_a_paste_is_proven_by_its_text_not_by_any_change():
    class Swallow(Mac):          # the screen changes (a quota line goes), the paste itself is lost (2026-10-23)
        def __call__(self, argv, stdin):
            if argv[-1].startswith("osascript -") and stdin == D.KEY_SCRIPT:
                self.screen = "> "
                return 0, "SENT"
            return super().__call__(argv, stdin)
    code, msg = D.keys(fn, "/dev/ttys000", paste_text="I'm a new user on this Mac", runner=Swallow(screen="quota\n> "),
                       sleep=lambda s: None)
    assert code == 3 and "not on the screen" in msg
    class Takes(Mac):
        def __call__(self, argv, stdin):
            if argv[-1].startswith("osascript -") and stdin == D.KEY_SCRIPT:
                self.screen += "\n> I'm a new user on this Mac"
                return 0, "SENT"
            return super().__call__(argv, stdin)
    assert D.keys(fn, "/dev/ttys000", paste_text="I'm a new user on this Mac", runner=Takes(), sleep=lambda s: None)[0] == 0


def test_expect_refuses_to_type_into_an_unidentified_screen():
    # 2026-10-23: a wait timed out, the loop typed anyway, and a prompt landed in the "somewhere else" folder field
    mac = Mac(screen="Which folder are you working in?\n  1. /Users/u/x\n> somewhere else:")
    code, msg = D.keys(fn, "/dev/ttys000", paste_text="hello", expect=r"^model .+\| +in ", runner=mac,
                       sleep=lambda s: None)
    assert code == 3 and "refused" in msg
    assert not any(c[0] == "pbcopy" for c in mac.calls) and not any(c[1] == D.KEY_SCRIPT for c in mac.calls)


class Startup(Mac):
    """Gorilla OpenCode's start-up screens in order; each accepted key moves to the next."""
    SCREENS = [
        "Which folder are you working in?\n  1. /Users/u/x\n     where this was started\n> somewhere else:\n    >",
        "Which folder are you working in?\n> 1. /Users/u/x\n     where this was started\n  somewhere else:",
        "Choose a provider for this session\n> Google Antigravity",
        "How fast is your connection?\n> Unconstrained",
        "Keeping your model\n >\nmodel Gemini 3.8 Flash  |  in x  |  context 11K",
    ]

    def __init__(self):
        super().__init__()
        self.i = 0
        self.screen = self.SCREENS[0]
        self.keys = []

    def __call__(self, argv, stdin):
        if argv[-1].startswith("osascript -") and stdin == D.KEY_SCRIPT:
            self.keys.append(argv[-1].split()[-1])
            self.i = min(self.i + 1, len(self.SCREENS) - 1)
            self.screen = self.SCREENS[self.i]
            return 0, "SENT"
        return super().__call__(argv, stdin)


def test_tui_start_moves_the_folder_cursor_to_entry_1_before_enter_then_reaches_the_prompt():
    mac = Startup()
    code, lines = D.tui_start(fn, "/dev/ttys000", runner=mac, sleep=lambda s: None)
    assert code == 0, lines
    assert mac.keys == ["code:126", "code:36", "code:36", "code:36"]      # up, then enter on entry 1, enter, enter
    assert lines[-1] == "at the prompt: ready for text"


def test_tui_start_never_presses_on_a_screen_it_does_not_know():
    clock = {"t": 0}
    mac = Mac(screen="some new screen nobody described\n> choose")
    code, lines = D.tui_start(fn, "/dev/ttys000", seconds=20, runner=mac, now=lambda: clock["t"],
                              sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    assert code == 3 and not any(c[1] == D.KEY_SCRIPT for c in mac.calls)


def test_old_screens_above_do_not_get_a_second_key_and_the_help_bar_means_ready():
    # the tab's text keeps the provider screen above the speed screen; only the LOWEST title is current, and the
    # layout-fixed build shows only "ctrl+? help" at the bottom (2026-10-23, live on the Air)
    rows = "Choose a provider for this session\n> Google\nHow fast is your connection?\n> Unconstrained"
    mac = Mac(screen=rows)
    def after(argv, stdin, _orig=mac.__call__):
        if argv[-1].startswith("osascript -") and stdin == D.KEY_SCRIPT:
            mac.keys_sent = getattr(mac, "keys_sent", 0) + 1
            mac.screen = rows + "\nKeeping your model\n > \n ctrl+? help    Gemini 3.8 Flash"
            return 0, "SENT"
        return _orig(argv, stdin)
    code, lines = D.tui_start(fn, "/dev/ttys000", runner=after, sleep=lambda s: None)
    assert code == 0 and mac.keys_sent == 1 and lines[0].startswith("speed: enter"), lines


def test_the_v0150_help_bar_without_ctrl_also_means_ready():
    # v0.1.150 writes "? help" at the bottom, not "ctrl+? help"; the old pattern waited 90 s at a ready prompt
    for bar in (" ? help        AUTO - approving without asking  Gemini 3.8 Flash", " ctrl+? help    Gemini 3.8 Flash"):
        assert D._last(["Keeping your model", " >", bar], D.TUI_READY) == 2, bar
    assert D._last(["type ? help for nothing in particular"], D.TUI_READY) == -1


def test_mac_run_retries_only_a_wifi_drop(capsys):
    from fieldkit.mac import macssh
    seq = [(255, "", "ssh: connect to host 1.2.3.4 port 22: Connection timed out"), (0, "hello\n", "")]
    R = lambda rc, o, e: type("R", (), {"returncode": rc, "stdout": o, "stderr": e})()
    calls = []
    def runner(argv):
        calls.append(argv)
        return R(*seq[len(calls) - 1])
    assert macssh.run_retry(["ssh", "x"], runner=runner, sleep=lambda s: None) == 0
    assert len(calls) == 2 and "hello" in capsys.readouterr().out
    refused = lambda argv: R(255, "", "Permission denied (publickey)")
    assert macssh.run_retry(["ssh", "x"], runner=refused, sleep=lambda s: None) == 255


DIALOG = """┃ Bash: python3 -c "import urllib.request; print('urllib ok')"
┃ Waiting for response...
  +----------------------------------------------------------+
  | Permission Required                                      |
  |                                                          |
  | Command                                                  |
  | python3 -c "import urllib.request; print('urllib ok')"   |
  |                                                          |
  | Runs python3; it can reach the network or run a script.  |
  | Asks even with auto-approve on: this turn has already    |
  | Tool: bash   Path: /Users/gorilla/Stranger-Test          |
  | Allow for session (s) covers: this exact request         |
  |                                                          |
  |  Allow (a)    Allow for session (s)    Deny (d)          |
  +----------------------------------------------------------+
─────────────────────────────
 >
model Gemini 3.8 Flash (High) (Antigravity)  |  in Stranger-Test
tokens 40.1K in / 930 out
 ? help  Context: 41K                         AUTO  Gemini 3.8 Flash"""
FOOTER = "\n─────\n >\nmodel Gemini | in Stranger-Test\ntokens 1K\n ? help   AUTO"


def test_a_dialog_on_screen_is_read_and_an_old_one_above_is_not():
    rows = [r for r in DIALOG.splitlines() if r.strip()]
    d = D.current_dialog(rows)
    assert d["command"] == "python3 -c \"import urllib.request; print('urllib ok')\""
    assert "Asks even with auto-approve on" in d["why"] and d["tool"].startswith("Tool: bash")
    # answered: the model kept working, so the old box is now far above the footer
    later = rows + ["┃ urllib ok", "┃ Bash: ls", "┃ a", "┃ b", "┃ c", "┃ d", "┃ e", "┃ f"] + FOOTER.split("\n")[1:]
    assert D.current_dialog(later) is None


def test_watch_dialogs_answers_each_dialog_once_counts_and_stops_at_the_report(tmp_path):
    mac = Mac(screen=DIALOG)
    state = {"answered": 0}
    def runner(argv, stdin, _orig=mac.__call__):
        line = argv[-1]
        if line.startswith("osascript -") and stdin == D.KEY_SCRIPT:
            state["answered"] += 1
            mac.screen = "┃ urllib ok\n┃ working" + FOOTER     # the dialog goes away once a key lands
            return 0, "SENT"
        if line.startswith("test -f"):
            return (0, "") if state["answered"] else (1, "")
        return _orig(argv, stdin)
    log = tmp_path / "dialogs.jsonl"
    code, lines = D.watch_dialogs(fn, "/dev/ttys001", "allow", "/Users/gorilla/Stranger-Test/REPORT.txt",
                                  seconds=60, every=1, log=str(log), runner=runner, sleep=lambda s: None)
    assert code == 0 and state["answered"] == 1, lines
    assert lines[0].startswith("dialog 1 ") and "allow -> python3 -c" in lines[0]
    assert lines[-1].endswith("1 dialog(s) answered")
    assert '"answer": "allow"' in log.read_text() and "urllib" in log.read_text()


def test_watch_dialogs_refuses_an_unknown_answer():
    assert D.watch_dialogs(fn, "/dev/ttys001", "maybe", runner=Mac(), sleep=lambda s: None)[0] == 2


def test_tui_start_waits_out_a_google_sign_in_that_finishes_alone():
    # 2026-10-23 on the Air: Enter on the provider screen started a Google sign-in that finished by itself in ~60 s;
    # tui-start's 90 s ran out at a ready prompt (and an earlier run was stopped by hand, wrongly, as "needs a person")
    clock, mac = {"t": 0.0}, Mac(screen="folder: /Users/gorilla/Stranger-Test\nStarting Google sign-in (Antigravity)...")
    def runner(argv, stdin, _orig=mac.__call__):
        if clock["t"] >= 120 and "Signed in" not in mac.screen:
            mac.screen += "\nSigned in as someone@example.com.\nReady. Plan: Google AI Pro.\n >\n ? help   AUTO"
        return _orig(argv, stdin)
    code, lines = D.tui_start(fn, "/dev/ttys001", seconds=90, every=10, runner=runner,
                              now=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    assert code == 0 and lines[0].startswith("Google sign-in started") and lines[-1].startswith("at the prompt"), lines
    assert not any(c[1] == D.KEY_SCRIPT for c in mac.calls)          # nothing typed into a sign-in


def test_tui_start_names_a_sign_in_that_never_finishes():
    clock, mac = {"t": 0.0}, Mac(screen="Starting Google sign-in (Antigravity)...\nOpening your browser")
    code, lines = D.tui_start(fn, "/dev/ttys001", seconds=30, every=10, sign_in_seconds=60, runner=mac,
                              now=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    assert code == 3 and "a person must finish it in the browser" in lines[-1], lines


ERROR_END = """┃ Bash: cd /Users/gorilla/Stranger-Test/Fieldkit && python3 -m fieldkit.core.doctor --probe
┃   WAY IN: zip + lean
 🦍⚠️·⚠️·🦍 The model returned an error and produced no answer:
  Antigravity: Internal error encountered (HTTP 500)
 🦍⚠️·⚠️·🦍
 16:37:36
 Gemini 3.8 Flash (High) (Antigravity) (error)""" + FOOTER


def test_a_turn_that_ended_in_a_provider_error_is_seen_and_one_that_ran_on_is_not():
    rows = [r for r in ERROR_END.splitlines() if r.strip()]
    assert D.ended_in_error(rows)
    ran_on = rows[:-5] + ["┃ Bash: ls", "┃   Fieldkit"] + rows[-5:]
    assert not D.ended_in_error(ran_on)


def test_watch_dialogs_types_continue_after_a_provider_error_then_stops_at_the_report(tmp_path):
    mac = Mac(screen=ERROR_END)
    state = {"pasted": 0}
    def runner(argv, stdin, _orig=mac.__call__):
        line = argv[-1]
        if line.startswith("osascript -") and stdin == D.KEY_SCRIPT:
            state["pasted"] += 1
            mac.screen = "┃ > continue\n┃ Bash: sh install.sh --yes\n┃   READY" + FOOTER
            return 0, "SENT"
        if line.startswith("test -f"):
            return (0, "") if state["pasted"] else (1, "")
        return _orig(argv, stdin)
    log = tmp_path / "w.jsonl"
    code, lines = D.watch_dialogs(fn, "/dev/ttys001", until_file="/x/REPORT.txt", seconds=60, every=1,
                                  log=str(log), runner=runner, sleep=lambda s: None)
    assert code == 0 and state["pasted"] == 1, lines
    assert "HTTP 500" in lines[0] and "typed 'continue' (1 of 2" in lines[0]
    assert "continue after error" in log.read_text()


def test_watch_dialogs_stops_when_a_new_error_comes_back_after_the_nudges():
    mac = Mac(screen=ERROR_END, change_on_key=False)
    state = {"n": 0}
    def runner(argv, stdin, _orig=mac.__call__):
        if argv[-1].startswith("osascript -") and stdin == D.KEY_SCRIPT:
            state["n"] += 1                                   # the continue is taken, and Google fails again, later
            mac.screen = (ERROR_END.replace("16:37:36", f"17:0{state['n']}:00")
                          .replace("─────\n >", f"─────\n > continue {state['n']}"))
            return 0, "SENT"
        if argv[-1].startswith("test -f"):
            return 1, ""
        return _orig(argv, stdin)
    code, lines = D.watch_dialogs(fn, "/dev/ttys001", until_file="/x/REPORT.txt", seconds=600, every=1, nudges=2,
                                  runner=runner, sleep=lambda s: None)
    assert code == 3 and "ended in an error again after 2 'continue'" in lines[-1] and state["n"] == 2, lines


def test_the_old_error_still_on_screen_after_a_continue_is_not_counted_again():
    # 2026-10-23 17:18: the watcher typed a second 'continue' 9 s after the first because the SAME 16:37 error was
    # still the newest thing on screen; only an error with a new timestamp counts
    clock, mac = {"t": 0.0}, Mac(screen=ERROR_END)
    def runner(argv, stdin, _orig=mac.__call__):
        if argv[-1].startswith("osascript -") and stdin == D.KEY_SCRIPT:
            mac.screen = mac.screen.replace("─────\n >", "─────\n > continue")
            return 0, "SENT"
        return (1, "") if argv[-1].startswith("test -f") else _orig(argv, stdin)
    code, lines = D.watch_dialogs(fn, "/dev/ttys001", until_file="/x/REPORT.txt", seconds=3600, every=30,
                                  idle_seconds=300, runner=runner, now=lambda: clock["t"],
                                  sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    assert sum("typed 'continue'" in l for l in lines) == 1 and "has not changed" in lines[-1], lines


def test_keys_refuses_when_another_window_is_in_front():
    mac = Mac(screen="> \n ? help")
    def runner(argv, stdin, _orig=mac.__call__):
        if argv[-1].startswith("osascript -") and stdin == D.KEY_SCRIPT:
            return 0, "FRONT IS /dev/ttys002"
        return _orig(argv, stdin)
    code, msg = D.keys(fn, "/dev/ttys001", paste_text="continue", runner=runner, sleep=lambda s: None)
    assert code == 3 and "ttys002" in msg and "nothing was typed" in msg


def test_a_paste_is_not_proven_by_the_same_word_higher_up_in_old_text():
    mac = Mac(screen="┃     continue\n┃ code listing\n > \n ? help", change_on_key=False)
    code, msg = D.keys(fn, "/dev/ttys001", paste_text="continue", runner=mac, sleep=lambda s: None)
    assert code == 3 and "did not take it" in msg


def test_watch_dialogs_stops_on_an_idle_screen_instead_of_waiting_out_the_clock():
    clock, mac = {"t": 0.0}, Mac(screen="┃ Bash: ls\n┃   done" + FOOTER)
    def runner(argv, stdin, _orig=mac.__call__):
        return (1, "") if argv[-1].startswith("test -f") else _orig(argv, stdin)
    code, lines = D.watch_dialogs(fn, "/dev/ttys001", until_file="/x/REPORT.txt", seconds=3600, every=60,
                                  idle_seconds=300, runner=runner, now=lambda: clock["t"],
                                  sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    assert code == 3 and "has not changed for 300 s" in lines[-1] and clock["t"] < 600, (lines, clock)
