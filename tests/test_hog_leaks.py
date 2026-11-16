"""P28 B: the process-leak pass wired into the hog (check, watch, preflight, prompt hook, SessionStart, `hog leaks`)
and the WMI holder created with no console (fieldkit/hog/leaks.py, fieldkit/buildh/window.py)."""
import json

import pytest

import fieldkit.hog as hog
from fieldkit.hog import leaks

NOW = 1_800_000_000.0


def row(pid, ppid, name, cmd="", age=3600, status="running"):
    return {"pid": pid, "ppid": ppid, "name": name, "cmd": cmd, "created": NOW - age, "status": status}


@pytest.fixture(autouse=True)
def _hog_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_HOG_DIR", str(tmp_path / "hog"))
    (tmp_path / "hog").mkdir()


# ---- classification (pure) ---------------------------------------------------------------------------------------
def test_empty_console_host_is_found_only_when_creator_gone_and_old():
    rows = [row(1, 0, "System", age=99999), row(10, 999, "conhost.exe"),          # creator 999 gone, no child: empty
            row(11, 1, "conhost.exe"),                                              # creator alive: not empty
            row(12, 999, "conhost.exe", age=30),                                    # too young
            row(13, 999, "conhost.exe"), row(14, 13, "cmd.exe")]                    # has a child
    k = leaks.classify(rows, NOW)
    assert k["console_hosts"] == {"count": 4, "empty": [10]}


def test_duplicate_listener_keeps_the_one_holding_the_port():
    cmd = "pythonw -m fieldkit peers link serve"
    rows = [row(1, 0, "System", age=99999), row(20, 999, "pythonw.exe", cmd, age=5000),
            row(21, 999, "pythonw.exe", cmd, age=4000), row(22, 999, "pythonw.exe", cmd, age=3000)]
    k = leaks.classify(rows, NOW, listening={21})
    d = k["duplicates"]["link-listener"]
    assert d["keep"] == 21 and sorted(d["spare"]) == [20, 22] and d["count"] == 3
    assert not set(k["orphans"]) & {20, 21, 22}          # a singleton is never counted again as an orphan


def test_launcher_and_its_child_count_as_one():
    cmd = "python -m fieldkit peers link serve"
    rows = [row(1, 0, "System", age=99999), row(30, 1, "python.exe", cmd), row(31, 30, "pythonw.exe", cmd)]
    assert leaks.classify(rows, NOW)["duplicates"] == {}


def test_orphans_over_threshold_warn_and_are_named():
    rows = [row(1, 0, "System", age=99999)] + [row(100 + i, 999, "git.exe") for i in range(21)]
    k = leaks.classify(rows, NOW)
    f = leaks.findings(k, by_pid={r["pid"]: r for r in rows})
    assert [x["check"] for x in f] == ["leak-orphans"] and "git.exe x21" in f[0]["line"]
    assert leaks.findings(leaks.classify(rows[:20], NOW)) == []              # 19 is under the warn


# ---- sweep: stops only the provably spare ------------------------------------------------------------------------
def _snap():
    cmd = "pythonw -m fieldkit peers link serve"
    rows = [row(1, 0, "System", age=99999), row(10, 999, "conhost.exe"),
            row(20, 999, "pythonw.exe", cmd, age=5000), row(21, 999, "pythonw.exe", cmd, age=4000),
            row(40, 999, "git.exe")]
    return rows, {21}


def test_sweep_dry_stops_nothing_and_act_stops_empty_host_and_spare_listener():
    stopped, logged = [], []
    res = leaks.sweep(act=False, now=NOW, snap=_snap, stop=lambda p, c: stopped.append(p) or True)
    assert stopped == [] and res["stopped"] == []
    res = leaks.sweep(act=True, now=NOW, snap=_snap, stop=lambda p, c: stopped.append(p) or True,
                      log=logged.append)
    assert sorted(stopped) == [10, 20] and 40 not in stopped                  # the orphan is named, never stopped
    assert {x["class"] for x in res["stopped"]} == {"console-host", "link-listener"} and len(logged) == 1
    assert "stopped" in leaks.line(res)


# ---- wiring ------------------------------------------------------------------------------------------------------
def _res(findings=(), stopped=()):
    return {"k": leaks.classify(_snap()[0], NOW), "findings": list(findings), "stopped": list(stopped)}


def test_hog_leaks_command_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(leaks, "sweep", lambda act=False, cfg=None, **k: _res())
    assert hog.main(["leaks"]) == 0
    assert capsys.readouterr().out.startswith("leaks: OK")
    seen = {}

    def fake(act=False, cfg=None, **k):
        seen["act"] = act
        return _res([{"check": "leak-consoles", "severity": "warn", "line": "41 console hosts", "numbers": {},
                      "action": None}])
    monkeypatch.setattr(leaks, "sweep", fake)
    assert hog.main(["leaks", "--act"]) == 3 and seen["act"] is True
    assert "41 console hosts" in capsys.readouterr().out
    assert hog.main(["leaks", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["findings"][0]["check"] == "leak-consoles"


def test_stale_pass_runs_once_per_window_and_is_quiet_when_watch_fresh():
    cfg = hog.config()
    w = cfg["hook_fresh_min"] * 60
    calls = []

    def run(c):
        calls.append(1)
        return _res(stopped=[{"pid": 10, "class": "console-host", "name": "conhost.exe"}])
    assert hog.stale_leaks_pass(NOW - w, NOW, cfg, run=run) == "" and calls == []          # watch fresh
    line = hog.stale_leaks_pass(NOW - 4 * w, NOW, cfg, run=run)
    assert line.startswith("hog watch stale: leaks:") and calls == [1]
    assert hog.stale_leaks_pass(NOW - 4 * w, NOW + 60, cfg, run=run) == "" and calls == [1]  # throttled
    assert hog.stale_leaks_pass(NOW - 4 * w, NOW + w + 1, cfg, run=run) != "" and calls == [1, 1]


def test_stale_pass_quiet_when_nothing_found():
    cfg = hog.config()
    assert hog.stale_leaks_pass(0, NOW, cfg, run=lambda c: _res()) == ""


def test_preflight_adds_leaks_only_on_a_live_run(monkeypatch):
    called = []
    monkeypatch.setattr(hog, "_leaks_act", lambda cfg=None: called.append(1) or _res())
    facts = {"t": NOW, "ram": {"total": 64 * 2**30, "used": 8 * 2**30}, "commit": {"used": 8 * 2**30, "limit": 64 * 2**30},
             "procs": [], "alive": [], "lms": {}, "ollama": {}, "disks": [], "temp": {"c": None}}
    hog.preflight_check({}, hog.config(), facts=facts, ram_fn=lambda: (8 * 2**30, 64 * 2**30))
    assert called == []                                                    # facts given: not a live run


def test_session_start_appends_the_leak_line(tmp_path, monkeypatch, capsys):
    import io
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")
    monkeypatch.setattr(hog, "hook_line", lambda: "")
    monkeypatch.setattr(hog, "_leaks_act", lambda cfg=None: _res(
        stopped=[{"pid": 10, "class": "console-host", "name": "conhost.exe"}]))
    room.hook(io.StringIO(json.dumps({"hook_event_name": "SessionStart", "session_id": "abc"})))
    out = capsys.readouterr().out
    assert "leaks:" in out


# ---- the stay-awake holder has no console ------------------------------------------------------------------------
def test_awake_holder_is_created_detached_with_no_window():
    from fieldkit.buildh import window
    seen = []

    class R:
        def __init__(self, out):
            self.stdout, self.returncode, self.stderr = out, 0, ""

    def run(argv, **kw):
        seen.append((argv, kw))
        return R("yes" if len(seen) == 1 else "0 4242")
    assert window.hold_awake(123, run=run, platform="win32") == {"pid": 4242}
    assert window.AWAKE_CREATE_FLAGS == 0x08000008
    ps = seen[1][0][-1]
    assert "ProcessStartupInformation=$si" in ps and "CreateFlags=[uint32]134217736" in ps
    assert all(kw.get("creationflags") == 0x08000000 for _a, kw in seen)
