"""Process-tree attribution survives Windows process-id reuse."""
from fieldkit.leakgate import sensors


def test_an_older_process_whose_parent_id_was_reused_is_not_a_child():
    rows = [{"ProcessId": 100, "ParentProcessId": 1, "Name": "firefox.exe", "Created": 2000},
            {"ProcessId": 101, "ParentProcessId": 100, "Name": "firefox.exe", "Created": 2100},     # a real child
            {"ProcessId": 300, "ParentProcessId": 100, "Name": "msedge.exe", "Created": 1500}]      # older: id reuse
    names = sorted(r["Name"] for r in sensors.tree(rows, 100))
    assert names == ["firefox.exe", "firefox.exe"]


def test_without_creation_times_every_descendant_still_counts():
    rows = [{"ProcessId": 100, "ParentProcessId": 1, "Name": "a"}, {"ProcessId": 300, "ParentProcessId": 100, "Name": "b"}]
    assert len(sensors.tree(rows, 100)) == 2                  # fail closed: no time, no exclusion


def test_the_sampler_follows_a_browser_that_is_not_in_the_launchers_tree(monkeypatch):
    """Build 27 gate (2026-10-05): from an elevated shell Firefox's launcher starts the browser de-elevated through
    the shell. The real browser's parent is not the process the gate started, the sampler saw none of it, WM_CLOSE
    never reached its window, and the shutdown check reported it 'still running' in all six graceful runs."""
    from fieldkit.leakgate import sensors
    rows = [{"ProcessId": 900, "ParentProcessId": 4, "ExecutablePath": r"C:\x\build-direct\firefox.exe", "Created": 2},
            {"ProcessId": 901, "ParentProcessId": 900, "ExecutablePath": r"C:\x\build-direct\firefox.exe", "Created": 3},
            {"ProcessId": 950, "ParentProcessId": 4, "ExecutablePath": r"C:\Program Files\other.exe", "Created": 1}]
    monkeypatch.setattr(sensors, "snapshot_processes", lambda: rows)
    monkeypatch.setattr(sensors, "snapshot_sockets", lambda pids: [])
    s = sensors.Sampler(777, interval=0.01, build_dir=r"C:\x\build-direct")
    s.start()
    import time
    time.sleep(0.1)
    s.stop_flag.set()
    s.join(timeout=5)
    assert set(s.procs) == {900, 901}
    assert sensors.build_pids(r"C:\x\build-direct") == [900, 901]


def test_a_packet_is_another_programs_only_when_that_program_held_the_port_at_that_moment():
    """2026-10-06 (owner): WhatsApp wakes on every message and the Claude app keeps talking; a packet capture names
    no process, and the browser's old ports get reused. The owner of a port is taken from the socket table sampled
    around the packet's own time. No sample near it: unknown, and the packet stays the browser's (fail closed)."""
    from fieldkit.leakgate import sensors
    o = sensors.PortOwners(slack=3.0)
    o.add(100.0, [("udp", "Bound", "0.0.0.0", "50000", "", "", "11")], build_pids={11})      # browser holds 50000
    o.add(110.0, [("udp", "Bound", "0.0.0.0", "50000", "", "", "77")], build_pids={11})      # then WhatsApp does
    assert o.owner("udp", 50000, 101.0) == ("build", 11)
    assert o.owner("udp", 50000, 111.0) == ("other", 77)
    assert o.owner("udp", 50000, 105.5) == (None, None)            # between samples: unknown -> the browser's
    assert o.owner("tcp", 50000, 111.0) == (None, None)            # another protocol is another port


def test_a_foreign_packet_is_named_under_other_programs_and_fails_nothing():
    from fieldkit.leakgate import gate
    ev = [{"scenario": "drm-request", "mode": "direct", "sensor": "pktmon", "kind": "foreign-packet",
           "value": "57.144.63.32", "port": 443, "detail": "tcp from local port 50001, held then by pid 77 (WhatsApp.Root.exe)",
           "rep": 0}]
    fail, lists = gate.judge(ev, {"entries": []}, {"drm-request": []}, [], [], 3, True, False)
    assert not [x for x in fail["NETWORK_POLICY"] if "57.144.63.32" in x]
    assert any("WhatsApp.Root.exe" in x for x in lists["OTHER_PROGRAMS"])


def test_socket_records_process_name_path_and_parent_chain_and_judges_only_browser_tree(monkeypatch):
    """2026-10-17 (owner): the socket sensor recorded only a pid (9168, gone). Record the process name, path and
    parent chain for every socket finding, and judge only the process tree of the browser under test."""
    from fieldkit.leakgate import gate, sensors
    rows = [
        {"ProcessId": 100, "ParentProcessId": 4, "Name": "explorer.exe", "ExecutablePath": r"C:\Windows\explorer.exe", "Created": 10},
        {"ProcessId": 200, "ParentProcessId": 100, "Name": "firefox.exe", "ExecutablePath": r"C:\work\build-direct\firefox.exe", "Created": 20},
        {"ProcessId": 201, "ParentProcessId": 200, "Name": "firefox.exe", "ExecutablePath": r"C:\work\build-direct\firefox.exe", "Created": 25},
        {"ProcessId": 9168, "ParentProcessId": 100, "Name": "Codex.exe", "ExecutablePath": r"C:\Program Files\Codex\Codex.exe", "Created": 30},
    ]
    by_pid = {r["ProcessId"]: r for r in rows}
    chain = sensors.parent_chain(by_pid, 201)
    assert [c["pid"] for c in chain] == [201, 200, 100]
    assert [c["name"] for c in chain] == ["firefox.exe", "firefox.exe", "explorer.exe"]
    assert r"C:\work\build-direct\firefox.exe" in sensors.format_chain(chain)

    # An orphan whose ParentProcessId matches a root_pid that is not in the snapshot is NOT part of tree()
    assert sensors.tree(rows, 9999) == []

    # browser_tree keeps only the browser under test's process tree, excluding non-build processes even if spawned
    bt = sensors.browser_tree(rows, 200, r"C:\work\build-direct")
    assert {r["ProcessId"] for r in bt} == {200, 201}

    # Sampler records sock_chains with process name, path, and parent chain for every sampled socket
    monkeypatch.setattr(sensors, "snapshot_processes", lambda: rows)
    monkeypatch.setattr(sensors, "snapshot_all_sockets", lambda: [
        ("tcp", "Established", "::", "50100", "2606:4700::6811:cf05", "443", "201"),
        ("tcp", "Established", "::", "50200", "2606:4700::6811:cf05", "443", "9168"),
    ])
    s = sensors.Sampler(200, interval=0.01, build_dir=r"C:\work\build-direct")
    s.start()
    import time
    time.sleep(0.05)
    s.stop_flag.set()
    s.join(timeout=5)
    assert len(s.socks) == 1
    sock = next(iter(s.socks))
    assert [c["pid"] for c in s.sock_chains[sock]] == [201, 200, 100]

    # gate.judge excuses a socket outside the browser under test's process tree (listing it under OTHER_PROGRAMS)
    # and convicts a socket inside the browser's process tree with its full parent chain
    foreign_sock = {
        "scenario": "canary", "mode": "direct", "sensor": "sockets", "kind": "dest-ip",
        "value": "2606:4700::6811:cf05", "port": 443, "pid": 9168, "process": "Codex.exe",
        "exe": r"C:\Program Files\Codex\Codex.exe", "in_build": False,
        "parent_chain": sensors.parent_chain(by_pid, 9168),
        "detail": "tcp Established " + sensors.format_chain(sensors.parent_chain(by_pid, 9168)), "rep": 0,
    }
    browser_sock = {
        "scenario": "canary", "mode": "direct", "sensor": "sockets", "kind": "dest-ip",
        "value": "2606:4700::6811:cf05", "port": 443, "pid": 201, "process": "firefox.exe",
        "exe": r"C:\work\build-direct\firefox.exe", "in_build": True,
        "parent_chain": chain,
        "detail": "tcp Established " + sensors.format_chain(chain), "rep": 0,
    }
    fail, lists = gate.judge([foreign_sock], {"entries": []}, {"canary": []}, [r"C:\work\build-direct"], [], 3, True, False)
    assert not [x for x in fail["NETWORK_POLICY"] if "2606:4700" in x]
    assert any("Codex.exe" in x and "explorer.exe" in x for x in lists["OTHER_PROGRAMS"])

    fail2, lists2 = gate.judge([browser_sock], {"entries": []}, {"canary": []}, [r"C:\work\build-direct"], [], 3, True, False)
    assert any("2606:4700::6811:cf05:443" in x and "firefox.exe" in x and "pid 200" in x for x in fail2["NETWORK_POLICY"])


def test_packet_sensor_starts_through_elevate_py(tmp_path, monkeypatch):
    """2026-10-17 (owner): the packet sensor needs admin; the gate starts it through Documents\\Scripts\\Elevate\\elevate.py."""
    import json
    import threading
    from fieldkit.leakgate import sensors
    monkeypatch.setattr(sensors, "is_admin", lambda: False)
    fake_elevate = tmp_path / "elevate.py"
    fake_elevate.write_text("# stub\n", encoding="utf-8")
    calls = []

    def fake_runner(argv, **kw):
        calls.append(argv)
        return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    ok, helper = sensors.ensure_packet_sensor(tmp_path, say=lambda m: None, elevate_py=fake_elevate, runner=fake_runner)
    try:
        assert ok and helper is not None and helper.active
        assert len(calls) == 2
        assert str(fake_elevate) in calls[0] and "run" in calls[0]
        assert str(fake_elevate) in calls[1] and "wait" in calls[1] and "PKTMON_WORKER: ready" in calls[1]

        # Simulate the elevated worker acknowledging start, stop, and quit commands
        seen_ops = []
        stop_evt = threading.Event()

        def responder():
            cmd_f = helper.dir / "pktmon-cmd.json"
            ack_f = helper.dir / "pktmon-ack.json"
            while not stop_evt.is_set():
                if cmd_f.is_file():
                    try:
                        c = json.loads(cmd_f.read_text(encoding="utf-8"))
                        cmd_f.unlink(missing_ok=True)
                        seen_ops.append(c["op"])
                        ack_f.write_text(json.dumps({"seq": c["seq"], "ok": True}), encoding="utf-8")
                    except Exception:
                        pass
                stop_evt.wait(0.02)

        t = threading.Thread(target=responder, daemon=True)
        t.start()
        sensors.pktmon_start(tmp_path / "a.etl")
        sensors.pktmon_stop(tmp_path / "a.etl", tmp_path / "a.pcapng")
        sensors.close_packet_sensor()
        stop_evt.set()
        t.join(timeout=2)
        assert seen_ops == ["start", "stop", "quit"]
    finally:
        sensors.close_packet_sensor()


def test_previous_cargo_lock_reads_plain_vault_tree(tmp_path):
    """2026-10-17: non-snapshot harnesses fall back to older_pristine (a plain directory with Cargo.lock and no .git)."""
    from fieldkit.leakgate import gate
    vault = tmp_path / "157.0"
    vault.mkdir()
    (vault / "Cargo.lock").write_text('[[package]]\nname = "foo"\nversion = "1.0.0"\n', encoding="utf-8")
    assert 'name = "foo"' in gate.previous_cargo_lock(vault)


def test_dispositions_approve_from_supports_family_globs(tmp_path, monkeypatch):
    """2026-10-17 (owner): family approval via fnmatch globs (e.g. 'crate:*' or '*') so the owner approves in one go."""
    import json
    from fieldkit.buildh import task
    from fieldkit.leakgate import dispositions as dp
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    prop = tmp_path / "proposed-dispositions-158.0.json"
    disp = tmp_path / "dispositions.json"
    prop.write_text(json.dumps({
        "crate:askama_macros": {"disposition": "not-shipped", "evidence": "proc-macro", "proposal": {"needs_fix": False}},
        "crate:special": {"disposition": "OWNER-DECISION", "evidence": "needs owner", "proposal": {"needs_fix": False}},
        "host:bad": {"disposition": "OPEN", "evidence": "live", "proposal": {"needs_fix": True}},
    }), encoding="utf-8")
    out = dp.approve_from(disp, prop, named=("crate:*",), say=lambda m: None)
    assert out["approved"] == ["crate:askama_macros", "crate:special"]
    assert [k for k, _ in out["left"]] == ["host:bad"]



def test_a_helper_the_browser_starts_from_outside_the_build_is_still_watched():
    """2026-10-18 review: an overnight edit dropped such descendants; a helper in TEMP calling home would vanish."""
    from fieldkit.leakgate import sensors
    snap = [{"ProcessId": 100, "ParentProcessId": 1, "Name": "firefox.exe", "ExecutablePath": "C:/b/firefox.exe"},
            {"ProcessId": 101, "ParentProcessId": 100, "Name": "firefox.exe", "ExecutablePath": "C:/b/firefox.exe"},
            {"ProcessId": 102, "ParentProcessId": 101, "Name": "helper.exe", "ExecutablePath": "C:/Temp/helper.exe"},
            {"ProcessId": 200, "ParentProcessId": 1, "Name": "other.exe", "ExecutablePath": "C:/x/other.exe"}]
    pids = {r["ProcessId"] for r in sensors.browser_tree(snap, 100, build_dir="C:/b")}
    assert pids == {100, 101, 102}                     # the helper stays; an unrelated process does not join
