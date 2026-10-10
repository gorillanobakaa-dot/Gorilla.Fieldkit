"""herald.py: the spoken herald on any platform; battery warnings, the run's result, a printed line when no voice."""
import subprocess
import sys

from fieldkit.buildh import herald


def test_the_verdict_comes_from_the_log():
    assert herald.verdict('{"exit_code": 0}') == "has finished, and it passed"
    assert "failed" in herald.verdict('{"exit_code": 2}')
    assert "passed" in herald.verdict('"FINAL_RESULT": "PASS"') and "failed" in herald.verdict("FINAL_RESULT=FAIL")
    assert herald.verdict("build\nexit 1\nretry\nexit 0\n") == "has finished, and it passed"
    assert herald.verdict("exit 3\n") == "has stopped with code 3. Have a look."
    assert herald.verdict("no result here") == "has finished"


def test_battery_warnings_once_per_threshold_and_again_after_the_charger():
    warned = set()
    on_bat = lambda p: {"mains": False, "percent": p, "battery": True}
    assert herald.battery_warning(on_bat(50), warned, "x") is None
    assert "19 per cent" in herald.battery_warning(on_bat(19), warned, "the build run")
    assert herald.battery_warning(on_bat(18), warned, "x") is None
    assert "9 per cent" in herald.battery_warning(on_bat(9), warned, "x")
    assert herald.battery_warning({"mains": True, "percent": 9, "battery": True}, warned, "x") is None and not warned
    assert herald.battery_warning(on_bat(9), warned, "x")                      # pulled out again: said again


def test_watch_speaks_the_warning_then_the_result(tmp_path):
    log = tmp_path / "run.log"
    said, ticks = [], iter([True, True, False])
    def alive(pid):
        return next(ticks)
    def sleep(s):
        log.write_text("line\nexit 0\n", encoding="utf-8")
    last = herald.watch("the build run", pid=42, log=str(log), status=lambda: {"mains": False, "percent": 4, "battery": True},
                        speak=said.append, sleep=sleep, alive=alive)
    assert last == "the build run has finished, and it passed." and said[-1] == last
    assert "4 per cent" in said[0]


def test_watch_on_a_log_alone_ends_at_its_exit_line(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("working\n", encoding="utf-8")
    said = []
    herald.watch("the post install", log=str(log), status=lambda: {"mains": True, "percent": None, "battery": False},
                 speak=said.append, sleep=lambda s: log.write_text("working\nexit 4\n", encoding="utf-8"))
    assert said == ["the post install has stopped with code 4. Have a look."]


def test_the_voice_is_offline_and_a_missing_one_prints():
    assert herald.voice("win32")[0] == "powershell"
    assert herald.voice("linux", which=lambda e: "/usr/bin/espeak-ng" if e == "espeak-ng" else None) == ["/usr/bin/espeak-ng"]
    assert herald.voice("linux", which=lambda e: None) is None
    out = []
    assert herald.say("hello", platform="linux", which=lambda e: None, out=lambda s, **k: out.append(s)) == "printed"
    assert out == ["HERALD: hello"]
    failing = lambda cmd, **k: type("R", (), {"returncode": 1})()
    assert herald.say("hi", platform="linux", which=lambda e: "/x/espeak", run=failing, out=lambda s, **k: None) == "printed"


def test_the_command_line_says_something_on_this_machine():
    r = subprocess.run([sys.executable, "-m", "fieldkit.buildh.herald", "--say", "test"], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0 and "herald: " in r.stdout
