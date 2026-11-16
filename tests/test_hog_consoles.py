from fieldkit.hog import consoles


def _r(pid, name, ppid, created=0):
    return {"pid": pid, "name": name, "ppid": ppid, "created": created}


def test_only_hosts_with_no_child_and_no_live_creator_are_empty():
    rows = [_r(1, "explorer.exe", 0), _r(2, "conhost.exe", 1), _r(3, "bash.exe", 2),      # 2 has a child: a live shell
            _r(4, "conhost.exe", 999), _r(5, "conhost.exe", 1),                          # 4: creator gone; 5: creator alive
            _r(6, "OpenConsole.exe", 999), _r(7, "OpenConsole.exe", 1), _r(8, "pythonw.exe", 999)]
    assert consoles.empty_hosts(rows, now=10_000) == [4, 6, 7]


def test_a_new_host_is_never_taken():
    assert consoles.empty_hosts([_r(4, "conhost.exe", 999, created=9_900)], now=10_000) == []
