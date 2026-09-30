"""Kernel pieces that are pure logic run everywhere; the build itself is Debian-only."""
import datetime

import pytest

from fieldkit.build import kernel

NOW = datetime.datetime(2026, 9, 29, 14, 30)


def test_localversion_fits_and_is_deterministic():
    a = kernel.localversion("7.1.2", ["unleashed", "gorilla", "eapd", "bbr"], now=NOW)
    b = kernel.localversion("7.1.2", ["unleashed", "gorilla", "eapd", "bbr"], now=NOW)
    assert a == b
    assert a["localversion"] == "-unleashed.gorilla-eapd-bbr-26.09.29--14.30"
    assert a["length"] <= 64 and a["uname"].startswith("7.1.2-")


def test_localversion_drops_tags_from_the_end_not_the_prefix():
    tags = ["unleashed", "gorilla"] + [f"feature{i}" for i in range(12)]
    r = kernel.localversion("7.1.2", tags, now=NOW)
    assert r["length"] <= 64 and r["dropped"] and r["dropped"][0] == "feature11"
    assert "-unleashed.gorilla-" in r["localversion"] and r["localversion"].endswith("26.09.29--14.30")


def test_localversion_refuses_impossible():
    with pytest.raises(ValueError):
        kernel.localversion("7.1.2", [], prefix=("x" * 70,), now=NOW)


def test_localversion_slugs_and_removes_duplicate_version():
    r = kernel.localversion("7.1.2", ["Unleashed", "gorilla", "7.1.2", "Big_Feature"], now=NOW)
    assert "7.1.2-" not in r["localversion"] and "big-feature" in r["localversion"]


CONFIG = """#
# Linux kernel configuration
CONFIG_A=y
# CONFIG_B is not set
CONFIG_C="old"
CONFIG_KEEP=m
"""


def test_apply_fragment_is_idempotent(tmp_path):
    cfg = tmp_path / ".config"
    cfg.write_text(CONFIG)
    flags = {"CONFIG_A": "n", "CONFIG_B": "y", "CONFIG_C": '"new"', "CONFIG_NEW": "y"}
    r1 = kernel.apply_fragment(cfg, flags)
    assert sorted(r1["changed"]) == ["CONFIG_A", "CONFIG_B", "CONFIG_C"] and r1["added"] == ["CONFIG_NEW"]
    text = cfg.read_text()
    assert "# CONFIG_A is not set" in text and "CONFIG_B=y" in text and "CONFIG_KEEP=m" in text
    r2 = kernel.apply_fragment(cfg, flags)
    assert r2["changed"] == [] and r2["added"] == [] and cfg.read_text() == text


def test_verify_fragment_catches_dropped_options(tmp_path):
    cfg = tmp_path / ".config"
    cfg.write_text("CONFIG_A=y\n# CONFIG_B is not set\n")
    missing = kernel.verify_fragment(cfg, {"CONFIG_A": "y", "CONFIG_B": "y", "CONFIG_GONE": "n", "CONFIG_X": "m"})
    assert ("CONFIG_B", "y", "n") in missing and ("CONFIG_X", "m", "n") in missing
    assert not any(f == "CONFIG_GONE" for f, _, _ in missing)     # absent == not set == n


def test_fragment_formats(tmp_path):
    y = tmp_path / "f.yaml"
    y.write_text("flags:\n  CONFIG_A: y\n  CONFIG_B: false\n  CONFIG_HZ: 1000\n")
    assert kernel.load_fragment(y) == {"CONFIG_A": "y", "CONFIG_B": "n", "CONFIG_HZ": "1000"}
    c = tmp_path / "f.config"
    c.write_text("CONFIG_A=y\n# CONFIG_B is not set\nrubbish\n")
    assert kernel.load_fragment(c) == {"CONFIG_A": "y", "CONFIG_B": "n"}
    mixed = tmp_path / "m.yaml"                        # real Kconfig symbols can be mixed case
    mixed.write_text("flags:\n  CONFIG_DVB_STV090x: n\n")
    assert kernel.load_fragment(mixed) == {"CONFIG_DVB_STV090x": "n"}
    bad = tmp_path / "bad.yaml"
    bad.write_text("flags:\n  NOT_A_CONFIG: y\n")
    with pytest.raises(ValueError):
        kernel.load_fragment(bad)


def test_fragment_from_injector_never_executes(tmp_path):
    inj = tmp_path / "kernel_config_injector.py"
    inj.write_text("import sys\nsys.exit('EXECUTED')\nMANDATORY_FLAGS = {'CONFIG_A': 'y', 'CONFIG_B': 'n'}\n")
    assert kernel.fragment_from_injector(inj) == {"CONFIG_A": "y", "CONFIG_B": "n"}


def test_sha256_helpers(tmp_path):
    f = tmp_path / "linux-7.1.2.tar.xz"
    f.write_bytes(b"hello")
    h = kernel.sha256_file(f)
    sums = f"-----BEGIN PGP SIGNED MESSAGE-----\n{'0' * 64}  linux-7.1.1.tar.xz\n{h}  linux-7.1.2.tar.xz\n"
    assert kernel.expected_sha256(sums, "linux-7.1.2.tar.xz") == h
    assert kernel.expected_sha256(sums, "linux-9.9.9.tar.xz") is None


def test_tarball_url():
    assert kernel.tarball_url("7.1.2") == "https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.1.2.tar.xz"
    assert kernel.tarball_url("6.19.13").startswith("https://cdn.kernel.org/pub/linux/kernel/v6.x/")


def test_deps_stage_says_why_it_cannot_run_off_debian():
    import shutil
    if shutil.which("dpkg-query"):
        pytest.skip("dpkg-query present: this is a Debian-family machine")
    r = kernel.stage_deps(None)
    assert r["ok"] is False and "Debian" in r["detail"]
