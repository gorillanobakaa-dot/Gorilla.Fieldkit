# Version: 1.0.0 · updated 26-10-23-18-38
"""The mandate's guarded store (fieldkit/mandate/guarded.py): trust and stops live where the agent's OS user cannot
write (M1.2, the Windows review of M1.1: a regex on command text cannot stop a write by indirection; the OS can)."""
import subprocess
import sys

import pytest

from fieldkit.mandate import guarded as G

KEY = "ab" * 32
FP = "0123-4567-89ab-cdef"

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="the POSIX store (root, pkexec); Windows has its own tests below")


@POSIX
def test_a_folder_this_user_owns_is_not_guarded(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "root", lambda: tmp_path)
    ok, why = G.guarded()
    assert not ok and "without the owner's password" in why
    (tmp_path / "stopped.txt").write_text("x\n")
    assert G.read("stopped.txt") == "" and G.listing("trusted") == []


@POSIX
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


@POSIX
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


@POSIX
def test_no_password_no_write(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "root", lambda: tmp_path)
    ok, why = G.write(f"trusted/{FP}.pub", KEY, run=lambda argv: subprocess.CompletedProcess(argv, 126))
    assert not ok and "password" in why


# ---- the Windows half (2026-10-23, the owner: "construieste partea windows a folderului protejat") ----------------
# These run on every system: the ACL rules are a pure function, the reader is a seam (Windows, link note #67).
GOOD = {"owner": "BUILTIN\\Administrators",
        "access": ["BUILTIN\\Administrators|FullControl|Allow", "NT AUTHORITY\\SYSTEM|FullControl|Allow",
                   "BUILTIN\\Users|ReadAndExecute, Synchronize|Allow"]}


@pytest.mark.parametrize("acl,me,ok", [
    (GOOD, "LAPTOP\\owner", True),
    (dict(GOOD, owner="LAPTOP\\owner"), "LAPTOP\\owner", False),                       # the user owns it
    (dict(GOOD, access=GOOD["access"] + ["BUILTIN\\Users|Modify, Synchronize|Allow"]), "LAPTOP\\owner", False),
    (dict(GOOD, access=GOOD["access"] + ["Everyone|Write|Allow"]), "LAPTOP\\owner", False),
    (dict(GOOD, access=GOOD["access"] + ["LAPTOP\\owner|FullControl|Allow"]), "LAPTOP\\owner", False),
    (dict(GOOD, access=GOOD["access"] + ["NT AUTHORITY\\Authenticated Users|AppendData|Allow"]), None, False),
    (dict(GOOD, access=GOOD["access"] + ["Everyone|Write|Deny"]), "LAPTOP\\owner", True),   # a Deny never opens
    (dict(GOOD, owner="NT AUTHORITY\\SYSTEM"), "LAPTOP\\owner", True),
])
def test_the_windows_acl_rules(acl, me, ok):
    assert G.acl_verdict(acl, me)[0] is ok


def test_a_windows_store_is_judged_by_its_acl_never_by_write_access(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "root", lambda: tmp_path)
    assert G._guarded_windows(tmp_path, acl_reader=lambda p: GOOD)[0]
    bad = dict(GOOD, access=GOOD["access"] + ["BUILTIN\\Users|FullControl|Allow"])
    ok, why = G._guarded_windows(tmp_path, acl_reader=lambda p: bad)
    assert not ok and "Users may write" in why
    assert not G._guarded_windows(tmp_path, acl_reader=lambda p: None)[0]             # unreadable: fail closed
    assert not G._guarded_windows(tmp_path / "absent", acl_reader=lambda p: GOOD)[0]


def test_the_elevated_script_sets_the_acl_before_writing_and_shows_what_it_pins():
    s = G.ps1_for(r"C:\ProgramData\Fieldkit\mandate-trust", f"trusted/{FP}.pub", KEY, False)
    acl, write = s.index("icacls $root /inheritance:r"), s.index("Set-Content")
    assert acl < write and "*S-1-5-32-544:(OI)(CI)F" in s and "*S-1-5-32-545:(OI)(CI)RX" in s
    assert "trusted\\0123-4567-89ab-cdef.pub" in s and KEY[:16] in s and s.endswith("FIELDKIT-GUARDED-DONE'\r\n")
    assert "Add-Content" in G.ps1_for("C:\\x", "stopped.txt", KEY, True)


def test_the_elevate_kit_is_found_where_it_was_adopted(tmp_path):
    # 2026-10-23, the Windows worker: the kit moved to local/kits/Elevate on 2026-10-19, and the old place alone made
    # `fieldkit mandate trust` answer "the elevate kit is not on this computer"
    adopted, old = tmp_path / "local" / "elevate.py", tmp_path / "old" / "elevate.py"
    assert G._elevate_kit([adopted, old]) == old                     # neither there: the last place is named
    adopted.parent.mkdir()
    adopted.write_text("")
    assert G._elevate_kit([adopted, old]) == adopted
    assert any("local" in str(k) and "kits" in str(k) for k in G._elevate_kits())
