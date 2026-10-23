# Version: 1.0.0 · updated 26-10-23-12-12
"""The mandate's guarded store (fieldkit/mandate/guarded.py): trust and stops live where the agent's OS user cannot
write (M1.2, the Windows review of M1.1: a regex on command text cannot stop a write by indirection; the OS can)."""
import subprocess
import sys

import pytest

from fieldkit.mandate import guarded as G

KEY = "ab" * 32
FP = "0123-4567-89ab-cdef"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the Windows half is the Windows side's (stubbed)")


def test_a_folder_this_user_owns_is_not_guarded(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "root", lambda: tmp_path)
    ok, why = G.guarded()
    assert not ok and "without the owner's password" in why
    (tmp_path / "stopped.txt").write_text("x\n")
    assert G.read("stopped.txt") == "" and G.listing("trusted") == []


def test_an_absent_store_is_not_guarded(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "root", lambda: tmp_path / "none")
    assert not G.guarded()[0]


@pytest.mark.parametrize("name,line", [
    ("trusted/../../etc/passwd", KEY),
    (f"trusted/{FP}.pub", KEY + "'; rm -rf / #"),
    (f"trusted/{FP}.pub", "not hex"),
    ("anything.txt", KEY),
    (f"trusted/{FP}.pub", KEY + "\nsecond line"),
])
def test_only_a_trust_store_line_reaches_the_privileged_shell(name, line, tmp_path, monkeypatch):
    monkeypatch.setattr(G, "root", lambda: tmp_path)
    called = []
    with pytest.raises(ValueError):
        G.write(name, line, run=lambda argv: called.append(argv))
    assert called == []


def test_a_write_goes_through_the_owners_password_and_is_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "root", lambda: tmp_path / "store")
    seen = []

    def lift(argv):                      # pkexec, without the password: run what it was given, as this user
        seen.append(argv)
        return subprocess.run(argv[1:])
    ok, why = G.write(f"trusted/{FP}.pub", KEY, run=lift)
    assert seen[0][:3] == (["pkexec", "/bin/sh", "-c"] if sys.platform.startswith("linux") else ["sudo", "/bin/sh", "-c"])
    assert (tmp_path / "store" / "trusted" / f"{FP}.pub").read_text() == KEY + "\n"
    assert not ok and "not guarded" in why            # written by this user, so it is not trusted: the read-back says so
    monkeypatch.setattr(G, "guarded", lambda path=None: (True, "root-owned"))
    G.write("stopped.txt", KEY + " a.yaml 2026-10-23_12:00", append=True, run=lift)
    ok, why = G.write("stopped.txt", "cd" * 32 + " b.yaml", append=True, run=lift)
    assert ok and (tmp_path / "store" / "stopped.txt").read_text().count("\n") == 2


def test_no_password_no_write(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "root", lambda: tmp_path)
    ok, why = G.write(f"trusted/{FP}.pub", KEY, run=lambda argv: subprocess.CompletedProcess(argv, 126))
    assert not ok and "password" in why
