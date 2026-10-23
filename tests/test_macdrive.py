# Version: 1.0.0 · updated 26-10-23-01-35
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
        if line.startswith("osascript -") and stdin == D.KEY_SCRIPT:
            if self.change_on_key:
                self.screen += "\nchanged"
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
