"""The build guard (fieldkit/thermal/buildguard.py): a build starts only while the fan control is cooling. Fake status
files only; the real fan, sensor and processor cap are never touched."""
import os
import sys
import time

import pytest

from fieldkit.thermal import buildguard

pytestmark = pytest.mark.real_build_guard


def status(tmp_path, mode=5, age=0.0, fan="0x07"):
    p = tmp_path / "status.txt"
    p.write_text(f"ts=1;mode={mode};cpu=55;max=60;load=40;fan={fan};rpm=3480;want=0x07;why=busy2;t=55;prev=54;n=9\r\n",
                 encoding="ascii")
    if age:
        old = time.time() - age
        os.utime(p, (old, old))
    return p


def test_compile_or_gorilla_mode_may_build(tmp_path):
    for mode, name in ((5, "Compile"), (4, "Gorilla")):
        ok, msg = buildguard.fan_check(status(tmp_path, mode), platform="win32")
        assert ok and name in msg and "fan 0x07" in msg


@pytest.mark.parametrize("mode,word", [(1, "BIOS"), (2, "Smart"), (3, "Manual")])
def test_a_mode_that_does_not_cool_refuses_and_says_the_click(tmp_path, mode, word):
    ok, msg = buildguard.fan_check(status(tmp_path, mode), platform="win32")
    assert not ok and word in msg and "pick Compile" in msg


def test_no_fan_control_refuses(tmp_path):
    ok, msg = buildguard.fan_check(tmp_path / "missing.txt", platform="win32")
    assert not ok and "not running" in msg


def test_a_stale_status_refuses(tmp_path):
    ok, msg = buildguard.fan_check(status(tmp_path, 5, age=120), platform="win32")
    assert not ok and "has not changed" in msg


def test_linux_has_thermald_and_no_fan_control(tmp_path):
    ok, msg = buildguard.fan_check(tmp_path / "missing.txt", platform="linux")
    assert ok and "thermald" in msg


def test_a_refused_build_never_starts_the_command(tmp_path, monkeypatch):
    marker = tmp_path / "ran.txt"
    monkeypatch.setattr(buildguard, "fan_check", lambda *a, **k: (False, "fan check: test refusal"))
    said = []
    rc = buildguard.run([sys.executable, "-c", f"open(r'{marker}', 'w').write('x')"], say=said.append)
    assert rc == 2 and not marker.exists() and any("REFUSED" in s for s in said)


def test_the_guard_s_own_lines_never_reach_the_command_s_stdout(tmp_path, monkeypatch, capfd):
    # R4 (2026-10-28): `guard -- token-audit --json` gave invalid JSON because the guard printed to stdout
    import contextlib

    @contextlib.contextmanager
    def guard(say, **k):                  # the real one needs a CPU temperature source on Windows (CI has none)
        say("  fan check: test pass")
        yield None
    monkeypatch.setattr(buildguard, "guard", guard)
    rc = buildguard.run([sys.executable, "-c", "print('{\"ok\": true}')"], check_fan=True)
    out, err = capfd.readouterr()
    assert rc == 0 and out.replace("\r\n", "\n") == '{"ok": true}\n'
    assert "fan check: test pass" in err


def test_with_a_guard_log_the_guard_s_lines_go_to_that_file_and_not_to_the_command(tmp_path, monkeypatch, capfd):
    # C3/I6 (2026-10-28): on the L15 worker the guard's preflight filled the job log's tail, never the pytest summary
    import contextlib

    @contextlib.contextmanager
    def guard(say, **k):
        say("  preflight: rotate: test line")
        yield None
    monkeypatch.setattr(buildguard, "guard", guard)
    glog = tmp_path / "guard.log"
    monkeypatch.setenv(buildguard.GUARD_LOG, str(glog))
    code = f"import os; print('inner', os.environ.get({buildguard.GUARD_LOG!r}, 'unset'))"
    rc = buildguard.run([sys.executable, "-c", code])
    out, err = capfd.readouterr()
    assert rc == 0 and out.strip() == "inner unset"            # the command never sees the guard's file
    assert "preflight" not in err and glog.read_text(encoding="utf-8") == "  preflight: rotate: test line\n"


@pytest.mark.skipif(sys.platform != "win32" or not os.path.isfile(os.path.join(os.path.dirname(sys.executable),
                                                                              "pythonw.exe")),
                    reason="pythonw.exe exists on Windows only")
def test_under_pythonw_the_guarded_command_s_output_is_kept(tmp_path):
    # C3/I6: the link worker is pythonw.exe; a child it started with inherited handles had sys.stdout None,
    # died on its first print with exit 1, and wrote nothing (every L15 run job, 2026-10-28)
    import subprocess
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    root = os.path.dirname(os.path.dirname(os.path.abspath(buildguard.__file__)))
    script = ("import contextlib, sys\n"
              "from fieldkit.thermal import buildguard as B\n"
              "B.guard = lambda say, **k: contextlib.nullcontext()\n"
              "sys.exit(B.run([sys.executable, '-c', 'print(\"inner said it\")']))\n")
    out = tmp_path / "out.txt"
    env = dict(os.environ, PYTHONPATH=os.path.dirname(root))
    with open(out, "wb") as f:
        rc = subprocess.call([pyw, "-c", script], stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                             env=env, timeout=120)
    assert rc == 0 and "inner said it" in out.read_text(encoding="utf-8", errors="replace")


def test_linux_guard_registers_a_hold_so_the_brake_can_pause_the_job(tmp_path, monkeypatch):
    """R11: on Linux the guard returned before brake.hold, so a hot machine never paused a guarded job."""
    from fieldkit.thermal import brake, keeper
    monkeypatch.setattr(buildguard.sys, "platform", "linux")
    monkeypatch.setattr(brake, "_dir", lambda: tmp_path)
    monkeypatch.setattr(keeper, "refusal", lambda *a, **k: "")
    with buildguard._fan_and_governor(lambda *_: None, "t", None, False, None, None):
        assert [h["pid"] for h in brake.holds(tmp_path, alive=lambda p: True)] == [os.getpid()]
    assert brake.holds(tmp_path, alive=lambda p: True) == []


@pytest.mark.parametrize("cmd,commit", [
    (["git", "commit", "-m", "x"], True), (["git.exe", "-C", "repo", "commit", "-q"], True),
    (["git", "-c", "a.b=c", "commit"], True), (["git", "status"], False), (["git", "log", "commit"], False),
    (["cargo", "build"], False), ([], False)])
def test_only_a_git_commit_is_a_commit(cmd, commit):
    assert buildguard.is_commit(cmd) is commit


def test_a_guarded_commit_takes_no_slot_so_its_hook_can_not_queue_behind_it(tmp_path, monkeypatch):
    # I-18 (2026-11-03, 2026-11-08): `guard -- git commit` held the slot while hooks/pre-commit waited for one
    import contextlib
    seen = []

    @contextlib.contextmanager
    def guard(say, **k):
        seen.append(k.get("slot"))
        yield None
    monkeypatch.setattr(buildguard, "guard", guard)
    ok = [sys.executable, "-c", "pass"]
    assert buildguard.run(ok) == 0 and seen == [True]
    monkeypatch.setattr(buildguard.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    assert buildguard.run(["git", "commit", "-m", "x"]) == 0 and seen == [True, False]


def test_without_a_slot_the_guard_does_not_wait_when_every_slot_is_held(tmp_path, monkeypatch):
    from fieldkit import admission
    from fieldkit.core import preflight
    monkeypatch.setenv("FIELDKIT_ADMISSION_HOME", str(tmp_path / "adm"))
    monkeypatch.setattr(admission, "base_slots", lambda *a, **k: 1)
    monkeypatch.setattr(admission, "temperature", lambda: None)
    monkeypatch.setattr(preflight, "skipped", lambda: True)
    held = admission.try_take(1, "build", "parent")
    assert held is not None
    try:
        with buildguard.guard(lambda s: None, check_fan=False, slot=False) as gov:
            assert held.f is not None             # reached the body without waiting for the held slot
        with pytest.raises(TimeoutError):
            with admission.admitted("build", "child", wait=0, say=lambda s: None):
                pass
    finally:
        held.release()
