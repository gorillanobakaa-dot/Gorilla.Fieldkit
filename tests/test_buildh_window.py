"""build-harness window (2026-10-08, owner: "make sure is running in screen"): a command in its own visible window,
started by Windows (WMI), not as a child of the session, logged, ending with its exit code."""
import base64
import sys
from pathlib import Path

import pytest

from fieldkit.buildh import window, task


def test_the_window_script_runs_the_command_logs_it_and_records_the_exit_code(tmp_path):
    log = tmp_path / "x.log"
    s = window.script(["build-run", "firefox-157.0-truth"], log, python="C:/py/python.exe")
    assert "& 'C:/py/python.exe' -m fieldkit build-harness 'build-run' 'firefox-157.0-truth' 2>&1 | ForEach-Object" in s
    assert "('exit ' + $code)" in s and "PYTHONUTF8" in s
    # the log is UTF-8 without a byte-order mark, written line by line; never Tee-Object (UTF-16 in PowerShell 5.1)
    assert "Tee-Object" not in s and "System.IO.StreamWriter(" in s and "UTF8Encoding($false)" in s
    assert "$log.AutoFlush = $true" in s and s.index("$log.WriteLine('exit '") < s.index("$log.Close()")
    cl = window.command_line(["build-run", "o'brien"], log, python="p")
    assert cl.startswith("powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass -EncodedCommand ")
    decoded = base64.b64decode(cl.rsplit(" ", 1)[1]).decode("utf-16-le")
    assert "'o''brien'" in decoded                                   # quotes cannot break out


def test_launch_goes_through_wmi_and_refuses_when_windows_did_not_start_it(monkeypatch, tmp_path):
    monkeypatch.setattr(task, "STATE", tmp_path / "build-harness")
    seen = {}
    class R:
        def __init__(self, out): self.stdout = out
    def run(cmd, **kw):
        seen["cmd"] = cmd
        return R("0 4242\n")
    r = window.launch(["build-run", "t"], run=run, platform="win32")
    assert r["pid"] == 4242 and r["log"].endswith(".log") and "Win32_Process -MethodName Create" in seen["cmd"][-1]
    try:
        window.launch(["build-run", "t"], run=lambda cmd, **kw: R("9 0"), platform="win32")
        assert False, "should refuse"
    except task.Refused as e:
        assert "did not start" in str(e)



def test_read_log_reads_utf8_and_the_old_utf16_logs(tmp_path):
    new = tmp_path / "new.log"
    new.write_bytes("build gate passed\nexit 0\n".encode("utf-8"))
    assert window.read_log(new) == "build gate passed\nexit 0\n"
    old = tmp_path / "old.log"      # Tee-Object's UTF-16 with Add-Content's UTF-8 exit line after it (before 2026-10-08)
    old.write_bytes("build gate passed\r\n".encode("utf-16") + b"exit 3\r\n")
    assert window.read_log(old) == "build gate passed\r\nexit 3\r\n"



def test_the_window_keeps_the_machine_awake_only_while_the_command_runs(tmp_path):
    """2026-10-09: a build sat in its gate all night while the laptop slept. The window asks Windows not to idle-sleep
    (ES_CONTINUOUS|ES_SYSTEM_REQUIRED) before the command and releases it (ES_CONTINUOUS alone) after it."""
    s = window.script(["build-run", "t"], tmp_path / "x.log", python="p")
    on, run, off = s.index("'0x80000003'"), s.index("-m fieldkit build-harness"), s.index("'0x80000000'")
    assert s.index("SetThreadExecutionState(uint esFlags)") < on < run < off < s.index("$log.Close()")


def test_awake_holds_until_the_process_exits_and_refuses_one_not_running(monkeypatch):
    class R:
        def __init__(self, out): self.stdout = out
    seen = []
    def run(cmd, **kw):
        seen.append(cmd[-1])
        return R("yes") if "Get-Process" in cmd[-1] else R("0 777")
    assert window.hold_awake(4242, run=run, platform="win32") == {"pid": 777}
    assert "Win32_Process -MethodName Create" in seen[1] and "-WindowStyle Hidden" in seen[1]
    s = window.awake_script(4242)
    assert s.index("'0x80000003'") < s.index("Wait-Process -Id 4242") < s.index("'0x80000000'")
    try:
        window.hold_awake(4242, run=lambda cmd, **kw: R(""), platform="win32")
        assert False, "should refuse"
    except task.Refused as e:
        assert "not running" in str(e)


def test_follow_prints_milestones_as_written_and_returns_the_exit_code(tmp_path):
    from fieldkit.buildh import window as w
    log = tmp_path / "20261009-221917-build-run-x.log"
    chunks = ["build gate passed\nsome compiler noise\n", "18:46:08  build attempt 1: x\n18:48:56  build OK (161 lines)\n",
              "  [ok] stamp: fine\n  PASS  removed pages: ok\nBUILD-VERIFY: PASS", "ED\nexit 0\n"]
    log.write_text("", encoding="utf-8")
    said = []

    def sleep(s):                                   # each poll, the run writes a little more
        if chunks:
            with open(log, "a", encoding="utf-8") as f:
                f.write(chunks.pop(0))
    assert w.follow(log, say=said.append, sleep=sleep) == 0
    assert said == ["build gate passed", "18:46:08  build attempt 1: x", "18:48:56  build OK (161 lines)",
                    "BUILD-VERIFY: PASSED", "exit 0"]          # noise skipped; a half-written line waits for its end


def test_follow_reports_a_failure_and_times_out_without_an_exit(tmp_path):
    from fieldkit.buildh import window as w
    log = tmp_path / "a-post-install-x.log"
    log.write_text("  [FAIL] claims: 4 contradicted\nTraceback (most recent call last):\nexit 3\n", encoding="utf-8")
    said = []
    assert w.follow(log, say=said.append, sleep=lambda s: None) == 3 and len(said) == 3
    log2 = tmp_path / "b-build-run-x.log"
    log2.write_text("build gate passed\n", encoding="utf-8")
    t = iter(range(0, 1000, 10))
    assert w.follow(log2, say=lambda m: None, sleep=lambda s: None, timeout=30, clock=lambda: next(t)) is None
    assert w.latest_log("build-run", folder=tmp_path) == log2 and w.latest_log(folder=tmp_path) == log2


def test_the_window_starts_the_herald_on_its_log(tmp_path):
    from fieldkit.buildh import window as w
    log = tmp_path / "x.log"
    s = w.script(["build-run", "firefox-157.0-truth"], log, python="C:/py/python.exe")
    assert "herald.ps1" in s and "'the build run'" in s and str(log) in s
    assert w.HERALD.is_file()


# -- Linux (2026-10-10): a detached session, the same log and exit line, the herald, idle sleep held off ------------

def test_the_linux_script_logs_every_line_ends_with_exit_and_starts_the_herald(tmp_path):
    from fieldkit.buildh import window as w
    log = tmp_path / "x.log"
    s = w.sh_script(["build-run", "t"], log, python="/usr/bin/python3",
                    which=lambda e: "/usr/bin/systemd-inhibit" if e == "systemd-inhibit" else None)
    assert "fieldkit.buildh.herald --name 'the build run'" in s and str(log) in s
    assert "wrap=(/usr/bin/systemd-inhibit --what=idle:sleep" in s and "-m fieldkit build-harness build-run t" in s
    assert "NOT held off (systemd-inhibit could not reach logind)" in s
    assert "${PIPESTATUS[0]}" in s and 'echo "exit $code"' in s
    assert "NOT held off (no systemd-inhibit on this machine)" in w.sh_script(["build-run"], log, which=lambda e: None)
    assert "herald" not in w.sh_script(["build-run"], log, herald=False, which=lambda e: None)


@pytest.mark.skipif(sys.platform == "win32", reason="the detached POSIX session; Windows has its WMI window (tested above)")
def test_a_linux_window_really_runs_detached_and_its_log_ends_with_the_exit_code(tmp_path, monkeypatch):
    from fieldkit.buildh import task, window as w
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    r = w.launch(["no-such-command"], herald=False)
    assert r["pid"] > 0 and r["log"].startswith(str(tmp_path))
    code = w.follow(r["log"], say=lambda m: None, poll=0.2, timeout=120)
    text = w.read_log(r["log"])
    assert code == 2 and "invalid choice" in text, text[-500:]          # argparse's own refusal, and its code
    assert w.latest_log(folder=tmp_path / "windows") == Path(r["log"])


def test_linux_awake_needs_a_live_process_and_systemd_inhibit(monkeypatch):
    import os
    from fieldkit.buildh import task, window as w
    started = []
    popen = lambda cmd, **kw: started.append(cmd) or type("P", (), {"pid": 31337})()
    me = os.getpid()
    ok = lambda cmd, **kw: type("R", (), {"returncode": 0, "stderr": ""})()
    assert w.hold_awake(me, run=ok, platform="linux", popen=popen, which=lambda e: "/usr/bin/systemd-inhibit") == {"pid": 31337}
    assert started[0][:2] == ["/usr/bin/systemd-inhibit", "--what=idle:sleep"] and f"kill -0 {me}" in started[0][-1]
    no_bus = lambda cmd, **kw: type("R", (), {"returncode": 1, "stderr": "Failed to connect to bus"})()
    with pytest.raises(task.Refused, match="cannot hold idle sleep off here: Failed to connect"):
        w.hold_awake(me, run=no_bus, platform="linux", popen=popen, which=lambda e: "/usr/bin/systemd-inhibit")
    with pytest.raises(task.Refused, match="no systemd-inhibit"):
        w.hold_awake(me, platform="linux", popen=popen, which=lambda e: None)
    with pytest.raises(task.Refused, match="not running"):
        w.hold_awake(2 ** 22 + 12345, platform="linux", popen=popen, which=lambda e: "/x")
