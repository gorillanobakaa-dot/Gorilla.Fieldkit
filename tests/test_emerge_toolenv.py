"""emerge toolenv: a recipe finds cargo and a usable TMPDIR on Linux/macOS by itself (plan W6)."""
import os
import stat
import sys

import pytest
import yaml

from fieldkit.emerge import recipe as R
from fieldkit.emerge import toolenv

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="Linux and macOS only")
SPEC = {"path_if_missing": {"cargo": ["~/.cargo/bin"]}, "tmpdir": {"min_free_gb": 2, "fallback": "~/fb"}}


def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cargo = tmp_path / ".cargo" / "bin" / "cargo"
    cargo.parent.mkdir(parents=True)
    cargo.write_text("#!/bin/sh\necho cargo 1.0\n")
    cargo.chmod(cargo.stat().st_mode | stat.S_IXUSR)
    return cargo.parent


@POSIX
def test_cargo_added_when_off_path(tmp_path, monkeypatch):
    bin_ = fake_home(tmp_path, monkeypatch)
    out = toolenv.resolve({"path_if_missing": SPEC["path_if_missing"]}, environ={"PATH": "/usr/bin:/bin"})
    assert out["PATH"].split(os.pathsep)[0] == str(bin_) and "/usr/bin" in out["PATH"]


@POSIX
def test_nothing_added_when_on_path(tmp_path, monkeypatch):
    bin_ = fake_home(tmp_path, monkeypatch)
    assert toolenv.resolve({"path_if_missing": SPEC["path_if_missing"]}, environ={"PATH": f"{bin_}:/bin"}) == {}


@POSIX
def test_tmpdir_moves_when_tmp_too_small_or_noexec(tmp_path, monkeypatch):
    fake_home(tmp_path, monkeypatch)
    small = toolenv.resolve({"tmpdir": SPEC["tmpdir"]}, environ={}, free=lambda p: 1 << 20)
    noexec = toolenv.resolve({"tmpdir": SPEC["tmpdir"]}, environ={}, free=lambda p: 1 << 40, can_exec=lambda p: False)
    fine = toolenv.resolve({"tmpdir": SPEC["tmpdir"]}, environ={}, free=lambda p: 1 << 40, can_exec=lambda p: True)
    assert small["TMPDIR"] == noexec["TMPDIR"] == str(tmp_path / "fb") and (tmp_path / "fb").is_dir()
    assert fine == {}


def test_windows_unchanged():
    assert toolenv.resolve(SPEC, environ={"PATH": ""}, platform="win32") == {}


@POSIX
def test_recipe_gives_stages_and_probes_the_env(tmp_path, monkeypatch):
    bin_ = fake_home(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    spec = {"name": "t", "toolenv": {"path_if_missing": SPEC["path_if_missing"]},
            "toolchain": [["cargo", "--version"]],
            "stages": [{"id": "b", "run": {"cmd": ["cargo", "build"]}}]}
    r = R.Recipe(spec, state_root=tmp_path / "st")
    assert r.by_id["b"]["run"]["env"]["PATH"].startswith(str(bin_))
    assert r.toolchain() == {"cargo --version": "cargo 1.0"}


def test_shipped_gorilla_size_recipe_says_it():
    spec = yaml.safe_load(open(R.find("gorilla-size"), encoding="utf-8"))
    assert spec["toolenv"]["path_if_missing"]["cargo"] and spec["toolenv"]["tmpdir"]["fallback"]
