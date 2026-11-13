"""P27 (2026-11-13): the Linux gate must really see the browser it starts. Relative run folders, the browser smoke, the
packet sensor's per-OS name, and nothing of the owner's machine in a public run log."""
import json
import os
import sys

import pytest

from fieldkit.leakgate import ci, gate, linux, linux_sensors as ls, scenarios as sc


def test_build_rows_match_a_relative_build_dir(tmp_path, monkeypatch):
    """The run's folder was relative (`out/runs/...`) and /proc reports absolute paths: no process matched, the process
    tree and the sockets stayed empty (run 43361542036). A relative build dir must match the same rows."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "b").mkdir()
    rows = [{"ProcessId": 1, "State": "S", "ExecutablePath": str(tmp_path / "b" / "firefox")},
            {"ProcessId": 2, "State": "S", "ExecutablePath": "/usr/bin/other"}]
    assert [r["ProcessId"] for r in ls.build_rows(rows, "b")] == [1]
    assert [r["ProcessId"] for r in ls.build_rows(rows, str(tmp_path / "b"))] == [1]


def test_the_packet_sensor_is_named_per_os(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert gate.packet_sensor_name() == "tcpdump"
    assert gate.shown(["necko-http", "pktmon"]) == ["necko-http", "tcpdump"]
    monkeypatch.setattr(sys, "platform", "win32")
    assert gate.packet_sensor_name() == "pktmon"


def test_a_tcpdump_observation_satisfies_the_packet_class_but_nothing_else_does():
    """The alias stays strict: the class is met only by a real observation, never by an absent sensor."""
    ev = [{"sensor": "tcpdump", "kind": "dest", "value": "x", "scenario": "s", "mode": "direct"}]
    assert "pktmon" in {gate.canon(e["sensor"]) for e in ev}
    assert "pktmon" not in {gate.canon(e["sensor"]) for e in []}


class FakeServer:
    def __init__(self):
        self.results, self.requests, self.bind, self.port = [], [], "10.77.0.1", 1

    def url(self, t):
        return "http://10.77.0.1:1" + t


def _fake_browser(tmp_path, body):
    b = tmp_path / "build"
    b.mkdir()
    f = b / "firefox"
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(0o755)
    return b


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="runs a shell script as the browser")
def test_smoke_fails_with_stderr_and_status_when_the_browser_dies(tmp_path):
    b = _fake_browser(tmp_path, 'echo "XPCOMGlueLoad error for file libxul.so: libfoo.so.1: cannot open shared object file" >&2\nexit 3\n')
    with pytest.raises(RuntimeError) as e:
        linux.smoke(b, FakeServer(), tmp_path / "w", seconds=10, in_ns=False)
    msg = str(e.value)
    assert "exited with status 3" in msg and "libfoo.so.1" in msg and "cannot show the local test page" in msg


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="runs a shell script as the browser")
def test_smoke_passes_when_the_page_reports_and_leaves_no_results_behind(tmp_path):
    srv = FakeServer()
    b = _fake_browser(tmp_path, "sleep 30\n")
    import threading
    threading.Timer(1.0, lambda: srv.results.append({"scenario": "smoke", "ok": True})).start()
    r = linux.smoke(b, srv, tmp_path / "w", seconds=20, in_ns=False)
    assert r["ok"] and srv.results == []
    assert "/smoke" in sc.PAGES


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="runs a shell script as the browser")
def test_smoke_names_a_browser_that_runs_but_never_reports(tmp_path):
    b = _fake_browser(tmp_path, "sleep 30\n")
    with pytest.raises(RuntimeError, match="never reported"):
        linux.smoke(b, FakeServer(), tmp_path / "w", seconds=2, in_ns=False)


def test_scrub_removes_the_owners_addresses_and_names_but_keeps_the_verdict():
    ev = json.dumps({"sensor": "machine", "kind": "machine-ip", "value": "10.77.0.1", "detail": "host gorilla-sve14a3aj 192.168.1.20 ok"})
    out = ci.scrub(ev, {"gorilla-sve14a3aj"})
    assert "10.77.0.1" not in out and "192.168.1.20" not in out and "gorilla-sve14a3aj" not in out
    assert json.loads(out)["kind"] == "machine-ip"
    assert ci.scrub("8.8.8.8 dest", set()) == "8.8.8.8 dest"


def test_collect_scrubs_the_artifact_and_pack_refuses_a_context_with_the_machines_name(tmp_path):
    res = {"FINAL_RESULT": "FAIL", "ARTIFACTS": str(tmp_path / "none")}
    art = ci.collect(tmp_path, res, [{"sensor": "machine", "kind": "machine-ip", "value": "10.77.0.1"}])
    assert "10.77.0.1" not in (art / "events.jsonl").read_text()
    st = tmp_path / "stage"
    (st / "owner").mkdir(parents=True)
    (st / "owner" / "SPEC.md").write_text("written on host-of-the-owner-1")
    assert ci.context_leaks(st, {"host-of-the-owner-1", "10.77.0.1"}) == [("owner/SPEC.md", "host-of-the-owner-1")]
    assert ci.context_leaks(st, {"10.77.0.1"}) == []


def test_workflow_smoke_step_runs_the_real_start_and_prints_stderr():
    import pathlib
    t = (pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "leakgate-linux.yml").read_text()
    assert "-headless -no-remote -profile" in t and "smoke-stderr.txt" in t and "not found" in t
