"""The household (stage O1, 2026-10-25): many devices instead of one paired peer, and the boss's fan-out
(fieldkit orchestrate). A boss and two workers are three homes here; the workers are real `serve` processes."""
import json
import os
import subprocess
import sys
import time

import pytest

from fieldkit import orchestrate
from fieldkit.peers import link
from test_peers_link import free_port


def use(monkeypatch, home):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(home))


@pytest.fixture
def house(tmp_path, monkeypatch):
    """boss (this test), w1 on the cable, w2 on the LAN; the household file carried to each worker by hand."""
    homes = {n: tmp_path / n for n in ("boss", "w1", "w2")}
    ports = {n: free_port() for n in homes}
    use(monkeypatch, homes["boss"])
    link.device_add("boss", "127.0.0.1", "boss", "Linux", port=ports["boss"], me=True, via="cable",
                    allow_loopback=True)
    link.device_add("w1", "127.0.0.1", "worker", "Windows", port=ports["w1"], via="cable", allow_loopback=True)
    link.device_add("w2", "127.0.0.1", "worker", "Darwin", brain="http://127.0.0.1:1234/v1", port=ports["w2"],
                    via="lan", allow_loopback=True)
    out, fp = link.household_export(tmp_path / "household")
    for w in ("w1", "w2"):
        use(monkeypatch, homes[w])
        assert link.household_import(out, w) == fp
    use(monkeypatch, homes["boss"])
    return {"homes": homes, "ports": ports, "file": out, "fp": fp}


def serve(home, seconds=20):
    env = dict(os.environ, FIELDKIT_LINK_HOME=str(home))
    p = subprocess.Popen([sys.executable, "-m", "fieldkit", "peers", "link", "serve", "--seconds", str(seconds),
                          "--loopback-test"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        if (home / "link.log").is_file() and "serving on" in (home / "link.log").read_text():
            return p
        time.sleep(0.1)
    p.kill()
    raise AssertionError("serve did not start: " + (p.stdout.read() if p.stdout else ""))


def test_the_boss_sends_one_note_to_two_workers_and_lists_all_three(house, capsys):
    procs = [serve(house["homes"]["w1"]), serve(house["homes"]["w2"])]
    try:
        rows = orchestrate.note("build the kernel tonight", to="workers")
        h, devs = orchestrate.devices(check=True)
        assert orchestrate.main(["devices", "--check"]) == 0
    finally:
        for p in procs:
            p.kill()
            p.wait()
    assert [(r["device"], r["ok"], r["seq"]) for r in rows] == [("w1", True, 1), ("w2", True, 1)]
    for w in ("w1", "w2"):                         # each worker's own counter: #1, no false "never arrived"
        item = json.loads(next((house["homes"][w] / "inbox").glob("*-message.json")).read_text())
        assert "build the kernel tonight" in item["text"] and item["seq"] == 1 and item["flags"] == []
    assert [d["name"] for d in devs] == ["boss", "w1", "w2"] and all(d["answers"] for d in devs if not d["me"])
    out = capsys.readouterr().out
    assert "3 device(s)" in out and "boss: boss" in out and "http://127.0.0.1:1234/v1" in out and "all answer" in out


def test_a_device_that_does_not_answer_is_reported_and_does_not_stop_the_others(house):
    p = serve(house["homes"]["w1"])
    try:
        rows = orchestrate.note("hello", to="all")
    finally:
        p.kill()
        p.wait()
    assert {r["device"]: r["ok"] for r in rows} == {"w1": True, "w2": False}
    assert "did not answer" in rows[1]["error"]
    assert orchestrate.main(["note", "x", "--to", "nobody"]) == 2


def test_jobs_go_on_the_cable_only(house):
    r = link.call({"verb": "job", "id": "j1"}, device="w2")
    assert not r["ok"] and "cable only" in r["error"]
    k = link.key()
    lan = link.handle(link.sign({"verb": "job", "id": "j1"}, k), link.Verifier(k), via="lan")
    assert not lan["ok"] and "cable only" in lan["error"]
    note = link.handle(link.sign({"verb": "hello"}, k), link.Verifier(k), via="lan", agents_fn=lambda: [],
                       toolset_fn=lambda: {})
    assert note["ok"]                                 # a hello or a note is fine over the LAN


def test_one_boss_valid_rows_and_removal_keeps_the_row(house):
    with pytest.raises(ValueError, match="already the boss"):
        link.device_add("w3", "127.0.0.1", "boss", allow_loopback=True)
    for bad in (dict(name="w 3", address="127.0.0.1"), dict(name="w3", address="8.8.8.8"),
                dict(name="w3", address="127.0.0.1", role="chief"),
                dict(name="w3", address="127.0.0.1", brain="ftp://x")):
        with pytest.raises(ValueError):
            link.device_add(allow_loopback=True, **bad)
    link.device_remove("w2")
    h = link.household()
    assert [d["name"] for d in h["devices"]] == ["boss", "w1"] and h["removed"][0]["name"] == "w2"
    with pytest.raises(ValueError):
        link.device_remove("boss")                    # never this device itself


def test_a_tampered_household_file_is_refused(house, monkeypatch, tmp_path):
    d = json.loads(house["file"].read_text())
    d["key"] = "00" * 32
    bad = tmp_path / "bad.key"
    bad.write_text(json.dumps(d))
    use(monkeypatch, tmp_path / "w9")
    with pytest.raises(ValueError, match="fingerprint"):
        link.household_import(bad, "w1")
    with pytest.raises(ValueError, match="not a device"):
        link.household_import(house["file"], "w9")


def test_one_paired_peer_still_works_and_keeps_its_note_numbers(tmp_path, monkeypatch):
    use(monkeypatch, tmp_path / "old")
    (tmp_path / "old").mkdir()
    old = {"me": "10.77.0.2", "peer": "10.77.0.1", "port": 47077, "host": "vaio", "peer_host": "laptop"}
    (tmp_path / "old" / "link.json").write_text(json.dumps(old))
    (tmp_path / "old" / "sent-seq.json").write_text(json.dumps({"last": 41}))
    h = link.household()
    assert h["legacy"] and [d["name"] for d in h["devices"]] == ["vaio", "laptop"]
    assert link.config() == old
    peer = link.device_named(h)
    assert link.last_sent(peer) == 41                 # the laptop has seen #41: the next note is #42, no gap
    link.device_add("air", "192.168.1.51", "worker", "Darwin")          # the household file is written now
    h = link.household()
    assert not h.get("legacy") and [d["name"] for d in h["devices"]] == ["vaio", "laptop", "air"]
    assert link.last_sent(link.device_named(h, "laptop")) == 41 and link.last_sent(link.device_named(h, "air")) == 0
    link._save_sent(link.device_named(h, "air"), 1)
    assert link.last_sent(link.device_named(h, "laptop")) == 41    # air's #1 does not move the laptop's count
    assert link.household_default(h)["name"] == "laptop"   # a relay that names no device still reaches the old peer
    assert (tmp_path / "old" / "link.json").read_text() == json.dumps(old)    # never rewritten


def test_a_worker_with_no_device_named_talks_to_the_boss(house, monkeypatch):
    use(monkeypatch, house["homes"]["w2"])
    h = link.household()
    h["devices"].reverse()                            # the boss last: found by its role, not by its place
    link.save_household(h)
    assert link.household_default(link.household())["name"] == "boss"
