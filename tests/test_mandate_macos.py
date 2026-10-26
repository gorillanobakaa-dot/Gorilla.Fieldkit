# Version: 1.0.0 · updated 26-10-26-02-00
"""The mandate tools on macOS (owner, 2026-10-26: "Make sure that works on all the operating systems we are working
with. OSX windows Linux etc etc"). CI runs Ubuntu and Windows only, so every macOS fact here is FAKED: the paths, the
owners and modes (stat/access), sys.platform, /usr/bin/git and xcode-select. What a real Mac must still show is in the
handover (state/handover/mandate-macos-2026-10-26/RUN-ON-THE-AIR.md)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fieldkit.mandate import guarded as G
from fieldkit.mandate import mandate as M
from fieldkit.mandate import upkeep as U

from test_mandate_owner_wide import GOOD, clone, git, home, write_mandate  # noqa: F401 - fixtures

MAC = "/Library/Application Support/Fieldkit/mandate-trust"


def posix(p):
    return str(p).replace("\\", "/")


class FakeFS:
    """Owners, modes and this user's write access per POSIX path; every folder up to / must be listed."""
    def __init__(self, entries):
        self.e = {posix(k): v for k, v in entries.items()}

    def stat(self, p):
        k = posix(p) or "/"
        if k not in self.e:
            raise FileNotFoundError(k)
        uid, mode, _w = self.e[k]
        return SimpleNamespace(st_uid=uid, st_mode=mode)

    def access(self, p, _mode):
        return self.e[posix(p) or "/"][2]

    def exists(self, p):
        return posix(p) in self.e


def mac_tree(**over):
    t = {"/": (0, 0o40755, False), "/Library": (0, 0o40755, False),
         "/Library/Application Support": (0, 0o40755, False),
         "/Library/Application Support/Fieldkit": (0, 0o40755, False),
         MAC: (0, 0o40755, False), MAC + "/trusted": (0, 0o40755, False)}
    t.update(over)
    return FakeFS(t)


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr(G, "sys", SimpleNamespace(platform="darwin", executable=sys.executable))


# ---- the command line lets them start on a Mac ------------------------------------------------------------------------
MAC_HOST = {"is_windows": False, "is_linux": False, "system": "Darwin"}


@pytest.mark.parametrize("args", [["mandate", "doctor"], ["mandate", "gate"], ["mandate", "install-hooks"],
                                  ["mandate", "renew", "x.yaml"], ["mandate", "status"], ["ship", "push"],
                                  ["ship", "push", "--tag", "v1"], ["ship", "gh-release", "v1"]])
def test_the_platform_gate_lets_the_mandate_tools_run_on_a_mac(monkeypatch, args):
    from fieldkit.core import host, platform_gate as PG
    monkeypatch.setattr(host, "host", lambda: MAC_HOST)
    assert PG.refusal(args, PG.build_index(PG.public_tools())) is None      # before 2026-10-26: "linux, windows only"


def test_a_card_that_says_darwin_runs_on_a_mac_and_nowhere_else(monkeypatch):
    from fieldkit.core import host
    monkeypatch.setattr(host, "host", lambda: MAC_HOST)
    assert host.platform_ok(["darwin"]) and host.platform_ok(["macos"]) and not host.platform_ok(["linux", "windows"])
    monkeypatch.setattr(host, "host", lambda: {"is_windows": False, "is_linux": True, "system": "Linux"})
    assert not host.platform_ok(["darwin"])


# ---- where the store lives -------------------------------------------------------------------------------------------
def test_the_mac_store_is_in_library_application_support():
    assert posix(G.root("darwin", exists=lambda p: False)) == MAC
    assert posix(G.root("darwin", exists=lambda p: True)) == MAC


def test_a_mac_pinned_before_reads_its_old_etc_store_until_the_new_one_exists():
    only_old = lambda p: posix(p) == G.LINUX_ROOT           # noqa: E731
    assert posix(G.root("darwin", exists=only_old)) == G.LINUX_ROOT


def test_linux_and_windows_keep_their_places():
    assert posix(G.root("linux")) == G.LINUX_ROOT
    assert G.root("win32").parts[-2:] == ("Fieldkit", "mandate-trust")


# ---- is it guarded ---------------------------------------------------------------------------------------------------
def test_a_root_owned_mac_store_with_root_owned_parents_is_guarded(darwin, monkeypatch):
    fs = mac_tree()
    monkeypatch.setattr(G, "root", lambda: Path(MAC))
    assert G.guarded(stat=fs.stat, access=fs.access, exists=fs.exists) == (True, "parents guarded")


@pytest.mark.parametrize("path,entry,why", [
    (MAC, (501, 0o40755, True), "can be changed without the owner's password"),          # the owner's own folder
    (MAC, (0, 0o40775, False), "can be changed without the owner's password"),           # group-writable
    ("/Library/Application Support", (0, 0o40775, True), "above the store"),            # admin may write: rename
    ("/Library/Application Support/Fieldkit", (501, 0o40755, True), "above the store"),
])
def test_anything_this_user_could_change_counts_for_nothing(darwin, monkeypatch, path, entry, why):
    fs = mac_tree(**{path: entry})
    monkeypatch.setattr(G, "root", lambda: Path(MAC))
    ok, msg = G.guarded(stat=fs.stat, access=fs.access, exists=fs.exists)
    assert not ok and why in msg, msg


def test_an_absent_mac_store_counts_for_nothing(darwin, monkeypatch):
    fs = FakeFS({"/": (0, 0o40755, False)})
    monkeypatch.setattr(G, "root", lambda: Path(MAC))
    ok, msg = G.guarded(stat=fs.stat, access=fs.access, exists=fs.exists)
    assert not ok and "does not exist" in msg


def test_a_mac_write_asks_sudo_with_the_spaced_path_quoted(darwin, monkeypatch):
    monkeypatch.setattr(G, "root", lambda: Path(MAC))
    seen = []
    G.write("trusted/0123-4567-89ab-cdef.pub", "ab" * 32,
            run=lambda argv: seen.append(argv) or SimpleNamespace(returncode=1))
    assert seen[0][:3] == ["sudo", "/bin/sh", "-c"]
    script = seen[0][3]
    assert "mkdir -p '" in script and "Application Support" in script.split("mkdir -p '", 1)[1].split("'")[0]
    assert "umask 022" in script


# ---- git on a Mac ----------------------------------------------------------------------------------------------------
def test_the_macos_git_stub_is_never_run_as_git():
    ran = []
    ok, why = U.git_here("darwin", which=lambda n: "/usr/bin/git",
                         run=lambda a, **k: ran.append(a) or SimpleNamespace(returncode=2))
    assert not ok and "stub" in why and ran == [["/usr/bin/xcode-select", "-p"]]


def test_macos_git_with_the_developer_tools_is_git():
    assert U.git_here("darwin", which=lambda n: "/usr/bin/git", run=lambda a, **k: SimpleNamespace(returncode=0))[0]
    assert U.git_here("darwin", which=lambda n: "/usr/local/bin/git", run=lambda a, **k: 1 / 0)[0]   # Homebrew's


def test_no_git_at_all_is_said_plainly():
    ok, why = U.git_here("darwin", which=lambda n: None)
    assert not ok and "zip" in why


def test_linux_never_asks_xcode_select():
    assert U.git_here("linux", which=lambda n: "/usr/bin/git", run=lambda a, **k: 1 / 0)[0]


def test_without_git_there_are_no_clones_and_the_router_stays_quiet(monkeypatch, clone):
    from fieldkit.autopilot import toolrouter as tr
    monkeypatch.setattr(U, "git_here", lambda *a, **k: (False, "stub"))
    assert U.find_clones() == {}
    assert tr.route("git push origin feat/x", cwd=str(clone)) is None


def test_the_doctor_names_the_store_and_a_missing_git(home, monkeypatch):
    monkeypatch.setattr(U, "git_here", lambda *a, **k: (False, "/usr/bin/git is Apple's stub (no developer tools)"))
    text = "\n".join(U.doctor_lines(U.doctor_facts(rows=[])))
    assert "protected trust store" in text and "Apple's stub" in text


# ---- the lean Mac: no cryptography --------------------------------------------------------------------------------------
def test_without_cryptography_a_mandate_is_not_in_force_and_nothing_crashes(home, monkeypatch, capsys):
    f = write_mandate(home)
    monkeypatch.setitem(sys.modules, "cryptography.exceptions", None)
    ok, why = M.verify(f)
    assert not ok and "cryptography" in why
    assert U.warning() == "" and M.main(["status"]) == 0
    assert "cryptography" in capsys.readouterr().out


# ---- the hook template on a Mac ------------------------------------------------------------------------------------------
def test_ship_push_tells_the_hook_which_python_runs_fieldkit():
    from fieldkit.method import ship
    env = ship.ship_env()
    assert env["FIELDKIT_SHIP_PUSH"] == "1" and env["FIELDKIT_SHIP_PYTHON"] == sys.executable


def test_the_hook_asks_the_gate_of_the_python_ship_push_named(clone, monkeypatch):
    import importlib.util
    monkeypatch.chdir(clone)
    spec = importlib.util.spec_from_file_location("oc_hooks", U.template_dir("Gorilla.Opencode") / "hooks.py")
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    venv_py = clone / "venv-python"
    venv_py.write_text("")
    monkeypatch.setenv("FIELDKIT_SHIP_PYTHON", str(venv_py))
    assert h._fieldkit() == [str(venv_py), "-m", "fieldkit"]
    monkeypatch.setenv("FIELDKIT_SHIP_PYTHON", str(clone / "absent"))          # a stale name: never trusted blindly
    assert h._fieldkit() != [str(clone / "absent"), "-m", "fieldkit"]


def _fake(bindir, name, body):
    f = bindir / name
    f.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
    f.chmod(0o755)


@pytest.mark.skipif(shutil.which("sh") is None, reason="no sh to run the hook with")
@pytest.mark.parametrize("pythons,expect", [
    # macOS 10.13: python3 from python.org, /usr/bin/python is Python 2
    ({"python3": "ok", "python": "py2"}, "RAN python3"),
    # both real (Linux): python3 first
    ({"python3": "ok", "python": "ok"}, "RAN python3"),
    # Windows: python3 is the Store stub, python is real
    ({"python3": "py2", "python": "ok"}, "RAN python"),
    # only a Python 2: refused, never run
    ({"python": "py2"}, "no Python 3.8 or newer"),
])
def test_the_hook_runs_only_a_python_3(clone, tmp_path, pythons, expect):
    hooks = clone / "scripts" / "git-hooks"
    hooks.mkdir(parents=True)
    (hooks / "hooks.py").write_text("# stand-in\n")
    hook = clone / ".git" / "hooks" / "pre-push"
    hook.write_bytes(U._norm((U.template_dir("Gorilla.Opencode") / "pre-push").read_bytes()))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, kind in pythons.items():
        # a Python 2 (or a stub) answers the hook's version question with "no" - and any other one-liner with "yes",
        # so a hook that stops asking the version runs it
        check = ('case "$2" in *"version_info >= (3, 8)"*) exit %d;; *) exit 0;; esac'
                 % (0 if kind == "ok" else 1))
        _fake(bindir, name, 'if [ "$1" = "-c" ]; then %s; fi\necho "RAN %s"\nexit 0\n' % (check, name))
    # PATH: the fakes ONLY, so no real python can answer. git is a stub here that runs the real one by its full path
    # (2026-10-26, #112 on Ubuntu: git and python3 share /usr/bin, so keeping git's folder off PATH lost git too)
    _fake(bindir, "git", 'exec "%s" "$@"\n' % Path(shutil.which("git")).as_posix())
    env = dict(os.environ, PATH=str(bindir))
    r = subprocess.run([shutil.which("sh"), str(hook), "origin", "x"], cwd=str(clone), input="",
                       capture_output=True, text=True, env=env, timeout=60)
    assert expect in r.stdout + r.stderr, r.stdout + r.stderr
