"""P11: the DevTools debugger server door probes (fieldkit/buildh/doors.py) - each can fail."""
import socket
import sys
import textwrap

import pytest

from fieldkit.buildh import doors, install


def test_judge_refused_open_and_not_started():
    assert doors.judge("e1", set(), False, True)[0]
    ok, ev = doors.judge("e1", {6000}, False, True)
    assert not ok and "OPEN" in ev
    assert not doors.judge("e1", set(), True, True)[0]
    assert not doors.judge("e1", set(), False, False)[0]          # a browser that never started proves nothing


def test_rows_one_per_door_and_an_open_door_fails_its_row():
    seen = []

    def run(door, copy):
        seen.append(door)
        return {"ok": door != "e1", "evidence": f"{door}: " + ("OPEN" if door == "e1" else "refused")}
    got = doors.rows("/inst", run=run, copy="/copy", platform="win32")
    assert seen == ["d2", "d3", "e1", "e3", "reg"]
    assert [r["ok"] for r in got] == [True, True, False, True, True]
    assert got[2]["check"].startswith("doors e1:")


def test_registry_row_is_windows_only():
    got = doors.rows("/inst", run=lambda d, c: {"ok": True, "evidence": d}, copy="/c", platform="linux")
    assert [r["check"].split(":")[0] for r in got] == ["doors d2", "doors d3", "doors e1", "doors e3"]


def test_policy_json_locks_every_pref():
    import json
    p = json.loads(doors.policy_json({"devtools.debugger.remote-enabled": True}))
    assert p["policies"]["Preferences"]["devtools.debugger.remote-enabled"] == {"Value": True, "Status": "locked"}


def test_post_install_knows_the_group():
    assert "doors" in install.PROOF_CHECKS


FAKE = textwrap.dedent("""
    import socket, sys, time
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(4)
    time.sleep(30)
""")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fake_browser(monkeypatch, tmp_path, listens):
    """A stand-in 'browser': listens on the DevTools port (an OPEN door) or only sleeps (a refused one)."""
    from fieldkit.buildh import layout
    port = _free_port()
    script = tmp_path / "fake.py"
    script.write_text(FAKE if listens else "import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(doors, "PORT", port)
    monkeypatch.setattr(layout, "exe", lambda d: sys.executable)
    real = layout.start
    monkeypatch.setattr(layout, "start", lambda argv, **kw: real([sys.executable, str(script), str(port)], **kw))
    return port


@pytest.mark.parametrize("door", ["d2", "e1"])
def test_try_door_sees_an_open_listener_and_a_silent_browser(monkeypatch, tmp_path, door):
    _fake_browser(monkeypatch, tmp_path, listens=True)
    (tmp_path / "build").mkdir()
    r = doors.try_door(door, tmp_path / "build", wait=8)
    assert not r["ok"] and "OPEN" in r["evidence"]
    assert not (tmp_path / "build" / "distribution" / "policies.json").exists()      # the copy is left as it was


@pytest.mark.parametrize("door", ["d2", "e1"])
def test_try_door_passes_a_browser_that_listens_nowhere(monkeypatch, tmp_path, door):
    _fake_browser(monkeypatch, tmp_path, listens=False)
    (tmp_path / "build").mkdir()
    r = doors.try_door(door, tmp_path / "build", wait=3)
    assert r["ok"], r
