"""herald.py: the spoken herald on any platform; battery warnings, the run's result, a printed line when no voice."""
import subprocess
import os
import sys

import pytest

from fieldkit.buildh import herald


@pytest.fixture(autouse=True)
def _no_ambient_silence(monkeypatch):
    """This module's result must not depend on how the suite was started.

    2026-10-13: a full run with FIELDKIT_VOICE_SILENT=1 exported failed two
    tests here that passed without it. The switch returns early before the
    injected `run` is reached, which is correct (it is a hard gate, and the test
    below asserts nothing is run at all), so the ambient value has to go instead.
    The tests that want the switch set it themselves inside their own bodies,
    and the subprocess tests build their child's env explicitly.
    """
    monkeypatch.delenv("FIELDKIT_VOICE_SILENT", raising=False)


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
    # SILENT: this used to come out of the speakers on every full suite run
    env = dict(os.environ, FIELDKIT_VOICE_SILENT="1")
    r = subprocess.run([sys.executable, "-m", "fieldkit.buildh.herald", "--say",
                        "Claude is checking that this computer has a voice at all"], capture_output=True, text=True,
                       timeout=120, env=env)
    assert r.returncode == 0 and "herald: " in r.stdout


def test_the_herald_command_works_from_any_folder(tmp_path):
    """2026-10-10: the owner was given `python -m fieldkit.buildh.herald` and ran it from C:\\WINDOWS\\system32; a
    command the harness gives must work from anywhere: `fieldkit build-harness herald "words"`."""
    env = dict(os.environ, FIELDKIT_VOICE_SILENT="1")   # SILENT: see above
    r = subprocess.run([sys.executable, "-m", "fieldkit", "build-harness", "herald",
                        "Claude is checking the herald works from any folder"], capture_output=True,
                       text=True, timeout=120, cwd=str(tmp_path), env=env)
    assert r.returncode == 0 and "herald: " in r.stdout, r.stdout + r.stderr


def test_on_windows_the_sentence_travels_in_the_environment_not_the_command_line():
    """2026-10-10 on the owner's laptop: `powershell -Command <script> test` joins "test" into the script, the voice
    failed, and the herald blamed a missing espeak-ng (a Linux program)."""
    seen = {}
    def run(argv, env=None, **kw):
        seen.update(argv=argv, env=env)
        return type("R", (), {"returncode": 0, "stderr": b""})()
    assert herald.say("Plug your charger in.", platform="win32", run=run) == "spoken"
    assert "Plug your charger in." not in " ".join(seen["argv"]) and seen["env"]["FIELDKIT_SAY"] == "Plug your charger in."
    assert "$env:FIELDKIT_SAY" in seen["argv"][-1]
    fail = lambda argv, **kw: type("R", (), {"returncode": 1, "stderr": b"Add-Type : Cannot add type.\n"})()
    assert herald.say("x", platform="win32", run=fail, out=lambda *a, **k: None) == "printed"
    assert herald.LAST_PROBLEM["why"].startswith("Windows speech (System.Speech) failed: Add-Type")
    assert "espeak" not in herald.LAST_PROBLEM["why"]


def test_no_voice_is_checked_and_explained_in_plain_words(monkeypatch):
    linux = herald.voice_check(platform="linux", which=lambda e: None)
    assert not linux["ok"] and "espeak-ng" in linux["install"][0] and "battery" in linux["purpose"]
    text = "\n".join(herald.explain(linux))
    assert "NO VOICE ON THIS COMPUTER" in text and "Why it matters" in text and 'fieldkit build-harness herald "test"' in text
    zero = lambda argv, **kw: type("R", (), {"returncode": 0, "stdout": "0\n"})()
    win = herald.voice_check(platform="win32", run=zero)
    assert not win["ok"] and "Speech" in win["install"][0] and "espeak" not in " ".join(win["install"])
    two = lambda argv, **kw: type("R", (), {"returncode": 0, "stdout": "2\n"})()
    assert herald.voice_check(platform="win32", run=two)["ok"]
    assert herald.voice_check(platform="linux", which=lambda e: "/usr/bin/espeak-ng" if e == "espeak-ng" else None)["ok"]


def test_doctor_knows_the_herald_voice():
    import json
    from fieldkit.core import doctor
    req = json.loads(doctor.REQUIREMENTS.read_text(encoding="utf-8"))["tools"]["espeak-ng"]
    assert req["for"] == ["herald"] and req["only"] == "linux" and "battery" in req["why"]


def test_the_suite_never_makes_the_computer_talk():
    """2026-10-13: the owner asked why he kept hearing "test, a test" from his
    speakers. Two tests ran the real herald, so every full run spoke aloud.
    Same fault as a GUI test drawing on the real screen: a side effect on the
    person running the suite. It should be runnable during a phone call."""
    import subprocess as sp
    spoke = []
    env = dict(os.environ, FIELDKIT_VOICE_SILENT="1")
    real = os.environ.get("FIELDKIT_VOICE_SILENT")
    os.environ["FIELDKIT_VOICE_SILENT"] = "1"
    try:
        assert herald.silent() is True
        result = herald.say("this must not be audible",
                            which=lambda n: "/usr/bin/spd-say" if n == "spd-say" else None,
                            run=lambda *a, **k: spoke.append(a) or sp.CompletedProcess(a, 0))
        assert result == "spoken", "silent mode must still report success"
        assert not spoke, "nothing may actually be run in silent mode"
    finally:
        if real is None:
            os.environ.pop("FIELDKIT_VOICE_SILENT", None)
        else:
            os.environ["FIELDKIT_VOICE_SILENT"] = real


def test_silent_mode_is_off_by_default_so_alerts_still_work():
    """The watchdog's whole purpose is to speak when the laptop is offline."""
    real = os.environ.pop("FIELDKIT_VOICE_SILENT", None)
    try:
        assert herald.silent() is False
    finally:
        if real is not None:
            os.environ["FIELDKIT_VOICE_SILENT"] = real


def test_an_explicit_off_value_is_not_treated_as_on():
    for value in ("0", "false", "no", ""):
        os.environ["FIELDKIT_VOICE_SILENT"] = value
        assert herald.silent() is False, value
    os.environ.pop("FIELDKIT_VOICE_SILENT", None)


# ------------------------------------------- what the suite says out loud
def test_the_suite_announces_the_real_numbers_not_a_vague_word():
    """2026-10-13, the owner's call: keep the voice, because an eight-minute
    run that announces itself keeps you honest, but make it say the actual
    thing. "test, a test" was ambiguous enough that he had to ask what it was."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent))
    import conftest as c

    for template in c._STARTING:
        line = template.format(n=1985)
        assert "1985" in line, "the count must be spoken, not implied"
    for template in c._ALL_PASSED:
        assert "1985" in template.format(p=1985)
    for template in c._SOME_FAILED:
        line = template.format(f=3, t=1985)
        assert "3" in line and "1985" in line, "say how many failed AND out of what"


def test_the_suite_is_silent_for_everybody_else_by_default():
    """Somebody cloning this repo must not have their computer start talking,
    and a build server must never talk at all."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent))
    import conftest as c

    real = os.environ.pop("FIELDKIT_VOICE_TESTS", None)
    try:
        assert c._voice_tests_on() is False
        for off in ("0", "false", "no", ""):
            os.environ["FIELDKIT_VOICE_TESTS"] = off
            assert c._voice_tests_on() is False, off
        os.environ["FIELDKIT_VOICE_TESTS"] = "1"
        assert c._voice_tests_on() is True
    finally:
        os.environ.pop("FIELDKIT_VOICE_TESTS", None)
        if real is not None:
            os.environ["FIELDKIT_VOICE_TESTS"] = real


def test_a_broken_voice_can_never_break_the_test_run():
    """The suite's verdict must not depend on whether the speakers work."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent))
    import conftest as c
    # SILENT. The first version of this test said "this will be fine even if
    # nothing can say it" out of the speakers: a sentence that means nothing to
    # the person hearing it, spoken to prove the voice cannot crash the suite.
    real = os.environ.get("FIELDKIT_VOICE_SILENT")
    os.environ["FIELDKIT_VOICE_SILENT"] = "1"
    try:
        c._announce("checking the announcer cannot crash the suite")   # must not raise
    finally:
        if real is None:
            os.environ.pop("FIELDKIT_VOICE_SILENT", None)
        else:
            os.environ["FIELDKIT_VOICE_SILENT"] = real


def test_no_spoken_line_is_too_long_to_follow():
    """2026-10-13, the owner listening to it: "on long sentences the voice gets
    a bit weird and hard to understand". The worst line was 88 characters with a
    four-digit number read out longhand in the middle, so the number was gone by
    the end. A spoken line has no punctuation you can see and no second reading."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent))
    import conftest as c

    for group in (c._STARTING, c._ALL_PASSED, c._SOME_FAILED):
        for template in group:
            line = template.format(n=1985, p=1985, f=3, t=1985)
            assert len(line) <= c.SPOKEN_LIMIT, f"{len(line)} chars, too long to follow: {line}"


def test_no_spoken_line_contains_a_word_that_sounds_like_another():
    """A spoken line has no spelling. 2026-10-13: a test sentence said "the full
    Fieldkit suite" and the owner heard "sweet" — correctly, because that is how
    the word is pronounced. The synthesiser was right and the word was wrong."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent))
    import conftest as c

    # words whose homophone would change or destroy the meaning HERE, in lines
    # that are mostly numbers. Not a general English homophone list.
    banned = {"suite": "sweet", "won": "one", "ate": "eight", "for": "four",
              "to": "two", "weight": "wait", "sum": "some"}
    for group in (c._STARTING, c._ALL_PASSED, c._SOME_FAILED):
        for template in group:
            words = template.format(n=1985, p=1985, f=3, t=1985).lower()
            words = [w.strip(".,:") for w in words.split()]
            for word, sounds_like in banned.items():
                assert word not in words, (
                    f"{word!r} sounds like {sounds_like!r} and this line is full of numbers: "
                    f"{template}")
