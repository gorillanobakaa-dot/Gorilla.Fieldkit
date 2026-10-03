"""snapshot: names cannot escape the snapshot folder; a failed query is "not captured", never "all removed";
Linux services record the active state and the enabled state separately. All system queries are faked."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from fieldkit.core import snapshot

WIN = {"is_windows": True, "system": "Windows"}
LINUX = {"is_windows": False, "system": "Linux"}


def _fake_run(answers):
    """answers: callable(cmd) -> (rc, stdout) or an exception instance to raise."""
    def run(cmd, **kw):
        got = answers(cmd)
        if isinstance(got, BaseException):
            raise got
        rc, out = got
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")
    return run


@pytest.mark.parametrize("bad", ["../../x", "..\\..\\x", "a/b", "a\\b", "..", ".", "", "a b", "C:x", "x\x00y",
                                 "abc\n", "/etc/passwd"])
def test_unsafe_snapshot_names_are_refused(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path / "snaps")
    with pytest.raises(ValueError, match="refused"):
        snapshot.take(bad, paths=[tmp_path])
    assert not list(tmp_path.rglob("*.json"))                 # nothing written anywhere


def test_safe_snapshot_names_work(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path / "snaps")
    f = tmp_path / "f.txt"
    f.write_text("x")
    out = snapshot.take("before-1.2_x", paths=[f])
    assert out == tmp_path / "snaps" / "before-1.2_x.json"
    assert snapshot.load("before-1.2_x")["name"] == "before-1.2_x"


def test_load_refuses_traversal_names_but_reads_an_explicit_json_file(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path / "a" / "snaps")
    (tmp_path / "x.json").write_text('{"name": "outside"}')
    with pytest.raises(ValueError):
        snapshot.load("../../x")                               # would have been snaps/../../x.json
    assert snapshot.load(str(tmp_path / "x.json"))["name"] == "outside"


def test_powershell_timeout_is_not_captured(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path / "snaps")
    monkeypatch.setattr(snapshot, "host", lambda: WIN)
    monkeypatch.setattr(snapshot.subprocess, "run",
                        _fake_run(lambda cmd: subprocess.TimeoutExpired(cmd, 180)))
    got = snapshot.PARTS["services"]()
    assert "timed out" in got[snapshot.NOT_CAPTURED]
    snap = json.loads(snapshot.take("t", parts=["services", "programs"]).read_text())
    assert snapshot.not_captured(snap["services"]) and snapshot.not_captured(snap["programs"])


@pytest.mark.parametrize("answer", [(0, "WARNING: something odd\n{not json"), (1, ""), (0, "[1, 2]")])
def test_unreadable_powershell_answer_is_not_captured(monkeypatch, answer):
    monkeypatch.setattr(snapshot, "host", lambda: WIN)
    monkeypatch.setattr(snapshot.subprocess, "run", _fake_run(lambda cmd: answer))
    assert snapshot.NOT_CAPTURED in snapshot.capture("services")


def test_good_powershell_answer_is_read(monkeypatch):
    monkeypatch.setattr(snapshot, "host", lambda: WIN)
    rows = [{"Name": "Spooler", "State": "Running", "StartMode": "Auto"}]
    monkeypatch.setattr(snapshot.subprocess, "run", _fake_run(lambda cmd: (0, json.dumps(rows))))
    assert snapshot.capture("services") == {"Spooler": {"state": "Running", "start": "Auto"}}


def test_diff_refuses_a_part_not_captured_on_either_side():
    real = {"Spooler": {"state": "Running", "start": "Auto"}, "Dnscache": {"state": "Running", "start": "Auto"}}
    failed = {snapshot.NOT_CAPTURED: "powershell timed out after 180 s"}
    for a, b, side in ((real, failed, "after"), (failed, real, "before")):
        d = snapshot.diff({"services": a}, {"services": b})
        ch = d["services"]
        assert ch["removed"] == [] and ch["added"] == [] and ch["changed"] == {}
        assert ch["not_compared"].startswith(side) and "timed out" in ch["not_compared"]
        assert snapshot.summary_lines(d)[0].startswith("services: NOT COMPARED")
    assert snapshot.diff({"services": failed}, {"services": failed})["services"]["not_compared"]


LIST_UNITS = "\n".join([
    "ssh.service             loaded active   running OpenBSD Secure Shell server",
    "foo.service             loaded inactive dead    Foo daemon",
    "getty@tty1.service      loaded active   running Getty on tty1",  # privacy-scan: allow (a systemd unit name, not an email)
])
UNIT_FILES = "\n".join([
    "ssh.service          enabled  enabled",
    "foo.service          disabled enabled",
    "bar.service          masked   enabled",
    "getty@.service       enabled  enabled",
])


def _systemctl(cmd):
    if cmd[:2] == ["systemctl", "list-units"]:
        return 0, LIST_UNITS
    if cmd[:2] == ["systemctl", "list-unit-files"]:
        return 0, UNIT_FILES
    if cmd[:2] == ["systemctl", "is-enabled"]:
        return (0, "enabled\n") if cmd[2] == "getty@tty1.service" else (1, "")  # privacy-scan: allow (fake)
    raise AssertionError(f"unexpected command {cmd}")


def test_linux_services_record_active_and_enabled_separately(monkeypatch):
    monkeypatch.setattr(snapshot, "host", lambda: LINUX)
    monkeypatch.setattr(snapshot.subprocess, "run", _fake_run(_systemctl))
    s = snapshot.capture("services")
    assert s["ssh.service"] == {"state": "active", "sub": "running", "start": "enabled"}
    assert s["foo.service"] == {"state": "inactive", "sub": "dead", "start": "disabled"}
    assert s["getty@tty1.service"]["start"] == "enabled"                # asked with is-enabled  # privacy-scan: allow (fake)
    assert s["bar.service"] == {"state": "not-loaded", "sub": "-", "start": "masked"}


def test_linux_enable_without_start_is_seen_as_a_change(monkeypatch):
    """Before the fix 'start' held the active state, so enabling a stopped unit was invisible."""
    monkeypatch.setattr(snapshot, "host", lambda: LINUX)
    monkeypatch.setattr(snapshot.subprocess, "run", _fake_run(_systemctl))
    before = snapshot.capture("services")
    global UNIT_FILES
    saved = UNIT_FILES
    UNIT_FILES = UNIT_FILES.replace("foo.service          disabled", "foo.service          enabled ")
    try:
        after = snapshot.capture("services")
    finally:
        UNIT_FILES = saved
    d = snapshot.diff({"services": before}, {"services": after})
    assert list(d["services"]["changed"]) == ["foo.service"]


def test_linux_systemctl_missing_is_not_captured(monkeypatch):
    monkeypatch.setattr(snapshot, "host", lambda: LINUX)
    monkeypatch.setattr(snapshot.subprocess, "run", _fake_run(lambda cmd: FileNotFoundError("systemctl")))
    assert "could not start" in snapshot.capture("services")[snapshot.NOT_CAPTURED]
