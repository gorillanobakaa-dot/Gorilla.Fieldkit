"""R15: the covering tests above the laptop's share are split across free devices, never dropped."""
import json

import pytest

from fieldkit import testsplit as T

FILES = [f"tests/test_f{i:02d}.py" for i in range(70)]


def _row(name, free=2, cpu=5, ok=True, me=False, can=True):
    return {"name": name, "me": me, "ok": ok, "os": "linux", "cpu_pct": cpu, "gate": {"free": free},
            "can": {"pytest": can, "git": can}}


def test_a_70_file_change_runs_every_file_somewhere():
    local, shares = T.deal(FILES, 15, [("vaio", 2), ("mac", 1)])
    got = set(local) | {f for fs in shares.values() for f in fs}
    assert got == set(FILES) and len(local) == 15
    assert sum(len(v) for v in shares.values()) == 55           # nothing dropped, nothing twice
    assert len(local) + sum(len(v) for v in shares.values()) == 70


def test_no_free_device_keeps_everything_here_never_drops():
    local, shares = T.deal(FILES, 15, [])
    assert sorted(local) == sorted(FILES) and shares == {}


def test_the_split_follows_the_weights():
    w = {f: (50 if f.endswith("00.py") else 1) for f in FILES}
    local, shares = T.deal(FILES, 3, [("a", 1), ("b", 1)], lambda f: w[f])
    assert "tests/test_f00.py" in local                           # the heaviest stays within the local share
    a, b = (sum(w[f] for f in shares[d]) for d in ("a", "b"))
    assert abs(a - b) <= 1


def test_only_idle_capable_other_devices_are_chosen():
    rows = [_row("me", me=True), _row("busy", free=0), _row("hot", cpu=90), _row("down", ok=False),
            _row("nopy", can=False), _row("vaio", free=3)]
    assert T.free_devices(rows, "windows") == [("vaio", 3)]


def _sender(log, refuse=()):
    def send(kind, device, tree, project, argv=None, wall=0, **k):
        if device in refuse:
            return None, "refused", None
        log.append((device, argv[2:]))
        return f"job-{device}", "sent", {"digest": "d-" + device}
    return send


def test_start_sends_the_overflow_and_records_the_pending_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_TESTCOST_DIR", str(tmp_path))
    monkeypatch.setattr("fieldkit.orchestrate_load.export_tree", lambda repo: (tmp_path / "t" / "x", "k"))
    (tmp_path / "t" / "x").mkdir(parents=True)
    log = []
    r = T.start(str(tmp_path), FILES, "key1", 15, rows=[_row("me", me=True), _row("vaio")],
                send=_sender(log), say=lambda s: None, me="windows")
    assert len(r["local"]) == 15 and sorted(log[0][1]) == sorted(set(FILES) - set(r["local"]))
    pend = json.loads(T._pending_file("key1").read_text())
    assert pend["sent"]["vaio"]["job"] == "job-vaio"


def test_a_device_that_refuses_gives_its_files_back_to_this_computer(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_TESTCOST_DIR", str(tmp_path))
    monkeypatch.setattr("fieldkit.orchestrate_load.export_tree", lambda repo: (tmp_path / "t" / "x", "k"))
    (tmp_path / "t" / "x").mkdir(parents=True)
    r = T.start(str(tmp_path), FILES, "key2", 15, rows=[_row("me", me=True), _row("vaio")],
                send=_sender([], refuse={"vaio"}), say=lambda s: None, me="windows")
    assert sorted(r["local"]) == sorted(FILES) and r["sent"] == {}


def test_at_or_under_the_cap_nothing_is_sent():
    log = []
    r = T.start(".", FILES[:15], "k", 15, rows=[_row("vaio")], send=_sender(log), say=lambda s: None)
    assert r["local"] == FILES[:15] and log == []


def _pending(tmp_path, monkeypatch, key="k3"):
    monkeypatch.setenv("FIELDKIT_TESTCOST_DIR", str(tmp_path))
    T._pending_file(key).write_text(json.dumps({"sent": {"vaio": {"job": "j", "digest": "d",
                                                                   "files": ["tests/test_a.py"]}}}))
    return key


def test_collect_passes_only_on_a_signed_pass(tmp_path, monkeypatch):
    k = _pending(tmp_path, monkeypatch)
    rc, again, bad = T.collect(k, wait_fn=lambda j, w: True, result_fn=lambda *a: {"status": "pass"},
                               say=lambda s: None)
    assert (rc, again, bad) == (0, [], [])
    assert not T._pending_file(k).exists()


def test_a_share_that_failed_on_another_device_runs_here_and_never_refuses_alone(tmp_path, monkeypatch):
    k = _pending(tmp_path, monkeypatch)
    said = []
    rc, again, bad = T.collect(k, wait_fn=lambda j, w: True,
                               result_fn=lambda *a: {"status": "fail", "evidence": "1 failed"}, say=said.append)
    assert rc == T.UNVOUCHED and again == ["tests/test_a.py"] and bad == []
    assert "vaio" in said[0] and "run here" in said[0]


@pytest.mark.parametrize("wait,result", [(False, None), (True, None)])
def test_an_unsigned_or_missing_result_is_not_a_pass_the_files_run_here(tmp_path, monkeypatch, wait, result):
    k = _pending(tmp_path, monkeypatch)
    rc, again, bad = T.collect(k, wait_fn=lambda j, w: wait, result_fn=lambda *a: result, say=lambda s: None)
    assert rc == T.UNVOUCHED and again == ["tests/test_a.py"] and bad == []
