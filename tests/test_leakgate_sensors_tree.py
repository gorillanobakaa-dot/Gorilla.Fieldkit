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
