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
