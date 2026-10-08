"""build-harness window (2026-10-08, owner: "make sure is running in screen"): a command in its own visible window,
started by Windows (WMI), not as a child of the session, logged, ending with its exit code."""
import base64

from fieldkit.buildh import window, task


def test_the_window_script_runs_the_command_logs_it_and_records_the_exit_code(tmp_path):
    log = tmp_path / "x.log"
    s = window.script(["build-run", "firefox-157.0-truth"], log, python="C:/py/python.exe")
    assert "& 'C:/py/python.exe' -m fieldkit build-harness 'build-run' 'firefox-157.0-truth' 2>&1 | Tee-Object" in s
    assert "('exit ' + $code)" in s and "PYTHONUTF8" in s
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
    r = window.launch(["build-run", "t"], run=run)
    assert r["pid"] == 4242 and r["log"].endswith(".log") and "Win32_Process -MethodName Create" in seen["cmd"][-1]
    try:
        window.launch(["build-run", "t"], run=lambda cmd, **kw: R("9 0"))
        assert False, "should refuse"
    except task.Refused as e:
        assert "did not start" in str(e)
