# Version: 1.0.0 · updated 26-10-23-19-30
"""fieldkit heal-round (plan 17.2 item 7): one hypothesis per round, and a round that does not lower the error count is
undone, its changes kept in the trash (the lordcode oscillation: 3 -> 1 -> 5 errors). Real git in a temp folder."""
import subprocess

from fieldkit.method import healloop as H


def _g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=tester", *a], check=True,
                          capture_output=True, text=True).stdout.strip()


def _repo(tmp_path):
    r = tmp_path / "app"
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    (r / "main.c").write_text("int main() { return 0 }\n")
    _g(r, "add", "-A")
    _g(r, "commit", "-qm", "one")
    return r


def _log(tmp_path, name, errors):
    p = tmp_path / name
    p.write_text("".join(f"main.c:{i}: error: something\n" for i in range(errors)) + "make: done\n")
    return p


def test_a_round_needs_one_written_hypothesis_and_only_one_open(tmp_path):
    r = _repo(tmp_path)
    st = tmp_path / "state"
    assert not H.start(r, _log(tmp_path, "a.log", 3), "", state_root=st)["ok"]
    assert H.start(r, _log(tmp_path, "a.log", 3), "the missing semicolon after return 0", state_root=st)["ok"]
    assert "still open" in H.start(r, _log(tmp_path, "a.log", 3), "another theory at once", state_root=st)["error"]
    assert "the missing semicolon" in (st / "heal-loop" / "app" / "HYPOTHESIS.md").read_text()


def test_a_round_that_lowers_the_errors_is_kept(tmp_path):
    r = _repo(tmp_path)
    st, trash = tmp_path / "state", tmp_path / "trash"
    H.start(r, _log(tmp_path, "a.log", 3), "the missing semicolon after return 0", state_root=st)
    (r / "main.c").write_text("int main() { return 0; }\n")
    res = H.end(r, _log(tmp_path, "b.log", 1), state_root=st, trash=trash)
    assert res["outcome"] == "kept" and (r / "main.c").read_text() == "int main() { return 0; }\n"
    assert not trash.exists()


def test_a_round_that_does_not_lower_them_is_undone_and_kept_aside(tmp_path):
    # the oscillation: 1 error, a change, 5 errors: the change is undone, never lost
    r = _repo(tmp_path)
    st, trash = tmp_path / "state", tmp_path / "trash"
    (r / "main.c").write_text("int main() { return 0; }\n")              # uncommitted work from before the round
    H.start(r, _log(tmp_path, "a.log", 1), "include stdio for printf on line 3", state_root=st)
    (r / "main.c").write_text("#include <nothing.h>\nint main() { return 0; }\n")
    (r / "helper.c").write_text("broken\n")                              # a file the round made
    res = H.end(r, _log(tmp_path, "b.log", 5), state_root=st, trash=trash)
    assert res["outcome"] == "undone"
    assert (r / "main.c").read_text() == "int main() { return 0; }\n"     # back to the round's start, not to HEAD
    assert not (r / "helper.c").exists()
    kept = next(trash.iterdir())
    assert "nothing.h" in (kept / "round.patch").read_text() and (kept / "new-files" / "helper.c").read_text() == "broken\n"
    assert "UNDONE" in (st / "heal-loop" / "app" / "HYPOTHESIS.md").read_text()
    assert _g(r, "stash", "list") == ""                                    # the shared stash list is untouched


def test_an_equal_count_is_not_progress(tmp_path):
    r = _repo(tmp_path)
    st = tmp_path / "state"
    H.start(r, _log(tmp_path, "a.log", 2), "rename the variable that shadows main", state_root=st)
    assert H.end(r, _log(tmp_path, "b.log", 2), state_root=st, trash=tmp_path / "t")["outcome"] == "undone"
