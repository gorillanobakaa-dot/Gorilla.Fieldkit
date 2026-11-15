import os
import sys
import time

import pytest

from fieldkit.core import asuser

posix = pytest.mark.skipif(sys.platform == "win32", reason="sudo is POSIX")


H = "/" + "h" + "ome"
HOME_A, HOME_B = H + "/alice", H + "/bob"
U = "C:" + "\\" + "Users" + "\\"


def fake_root(monkeypatch, home=HOME_A):
    monkeypatch.setattr(asuser, "_is_root", lambda: True)
    monkeypatch.setattr(asuser, "_invoker", lambda env: ("alice", 1000, 1000, home) if env.get("SUDO_USER") == "alice" else None)
    monkeypatch.setattr(asuser.atexit, "register", lambda f: None)


@posix
def test_sudo_points_home_at_the_invoker(monkeypatch, tmp_path):
    fake_root(monkeypatch)
    env = {"HOME": "/root", "SUDO_USER": "alice", "XDG_CACHE_HOME": "/root/.cache"}
    assert asuser.adopt(env, root=tmp_path) == HOME_A
    assert env["HOME"] == HOME_A and env["USER"] == "alice" and "XDG_CACHE_HOME" not in env


@posix
def test_no_change_without_sudo_user_or_not_root(monkeypatch, tmp_path):
    fake_root(monkeypatch)
    env = {"HOME": "/root"}
    assert asuser.adopt(env, root=tmp_path) is None and env["HOME"] == "/root"
    monkeypatch.setattr(asuser, "_is_root", lambda: False)
    env = {"HOME": HOME_B, "SUDO_USER": "alice"}
    assert asuser.adopt(env, root=tmp_path) is None and env["HOME"] == HOME_B


@posix
def test_invoker_ignores_root_and_unknown():
    assert asuser._invoker({"SUDO_USER": "root"}) is None
    assert asuser._invoker({"SUDO_USER": "no-such-user-xyz"}) is None
    assert asuser._invoker({}) is None


@posix
def test_restore_chowns_only_recent_root_files(monkeypatch, tmp_path):
    monkeypatch.setattr(asuser, "_is_root", lambda: True)
    old, new = tmp_path / "old", tmp_path / "sub" / "new"
    new.parent.mkdir()
    old.write_text("x")
    time.sleep(0.05)
    since = time.time()
    time.sleep(0.05)
    new.write_text("y")
    calls = []
    monkeypatch.setattr(os, "lchown", lambda p, u, g: calls.append((str(p), u, g)))
    n = asuser.restore_ownership(1000, 1001, since, [tmp_path], owned_by=os.getuid())
    paths = {c[0] for c in calls}
    assert str(new) in paths and str(old) not in paths and all(c[1:] == (1000, 1001) for c in calls)
    assert n == len(calls)


def test_windows_profile_from_checkout_location():
    co = U + "owner" + "\\Documents\\Fieldkit"
    assert asuser.windows_profile(co, U + "Admin") == U + "owner"
    assert asuser.windows_profile(co, "c:/users/OWNER/") is None
    assert asuser.windows_profile(H + "/x/Fieldkit", "/root") is None
