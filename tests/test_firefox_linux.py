"""Firefox on Linux: the stages, run against fakes (no mach, no build, nothing on the machine touched)."""
from pathlib import Path
import time
from types import SimpleNamespace

from fieldkit.build import firefox_linux as fl


def ctx(tmp_path, **over):
    src = tmp_path / "work" / "firefox-158.0"
    v = {"harness": str(tmp_path / "harness"), "version": "158.0", "work": str(tmp_path / "work"),
         "src": str(src), "release": str(tmp_path / "work" / "release-158.0")}
    v.update(over)
    return SimpleNamespace(vars=v)


def test_mach_env_drops_the_coding_agent_marker():
    env = fl.mach_env({"CLAUDECODE": "1", "PATH": "/usr/bin"})
    assert "CLAUDECODE" not in env and env["PATH"] == "/usr/bin"


def test_deps_names_each_missing_tool_and_the_disk_and_ram_rows(tmp_path):
    r = fl.stage_deps(ctx(tmp_path), which=lambda t: None if t == "sccache" else "/usr/bin/" + t,
                      free_gb=lambda p: 500.0, ram_gb=lambda: 16.0)
    assert not r["ok"] and r["missing"] == ["sccache"] and "sccache" in r["detail"]
    r = fl.stage_deps(ctx(tmp_path), which=lambda t: "/usr/bin/" + t, free_gb=lambda p: 10.0, ram_gb=lambda: 16.0)
    assert not r["ok"] and "disk 10 GB < 20" in r["detail"]
    r = fl.stage_deps(ctx(tmp_path), which=lambda t: "/usr/bin/" + t, free_gb=lambda p: 500.0, ram_gb=lambda: 16.0)
    assert r["ok"] and r["detail"] == "fit to build"


def test_source_restores_from_the_vault_then_verifies_the_version(tmp_path):
    c = ctx(tmp_path)
    def restore(product, workdir, version=None):
        assert product == "firefox" and version == "158.0"
        p = Path(workdir) / "browser" / "config"; p.mkdir(parents=True)
        (p / "version.txt").write_text("158.0\n", encoding="utf-8")
    r = fl.stage_source(c, restore=restore)
    assert r["ok"] and r["version"] == "158.0"
    (Path(c.vars["src"]) / "browser" / "config" / "version.txt").write_text("157.0\n", encoding="utf-8")
    assert not fl.verify_source(c)["ok"]


def test_mozconfig_is_copied_and_hashed(tmp_path):
    c = ctx(tmp_path)
    Path(c.vars["harness"]).mkdir(); (Path(c.vars["harness"]) / "mozconfig").write_text("ac_add_options --enable-lto\n")
    Path(c.vars["src"]).mkdir(parents=True)
    r = fl.stage_mozconfig(c)
    assert r["ok"] and (Path(c.vars["src"]) / "mozconfig").read_text() == "ac_add_options --enable-lto\n"
    assert (Path(c.vars["src"]) / "mozconfig.sha256").read_text().strip() == r["sha256"]


def test_patches_check_reads_the_counts_and_refuses_on_a_failure(tmp_path):
    c = ctx(tmp_path)
    (Path(c.vars["harness"]) / "patches").mkdir(parents=True)
    (Path(c.vars["harness"]) / "patches" / "apply.sh").write_text("#!/bin/bash\n")
    good = "== Gorilla Unleashed patch set ==\n   patches : 3\n   ok   : 3\n   fail : 0\n"
    bad = "   patches : 3\n   ok   : 2\n   fail : 1\n   FAILED 05.PREFS/x.patch\n"
    assert fl.stage_patches(c, check_only=True, run=lambda cmd, **k: (0, good))["ok"]
    r = fl.stage_patches(c, check_only=True, run=lambda cmd, **k: (1, bad))
    assert not r["ok"] and r["failed"] == 1 and "refusing" in r["detail"]
    r = fl.stage_patches(c, check_only=True, run=lambda cmd, **k: (1, "FATAL: source tree not found: /x"))
    assert not r["ok"] and r["detail"].startswith("apply.sh exited 1: FATAL")
    seen = {}
    fl.stage_patches(c, run=lambda cmd, **k: seen.update(cmd=cmd) or (0, good))
    assert "--check" not in seen["cmd"] and seen["cmd"][-1] == c.vars["src"]


def test_build_and_package_use_the_public_scripts_and_verify_the_artefacts(tmp_path):
    c = ctx(tmp_path)
    h = Path(c.vars["harness"]); (h / "scripts").mkdir(parents=True); (h / "deb_template").mkdir()
    (h / "scripts" / "run_build_and_capture.sh").write_text("#!/bin/bash\n")
    (h / "scripts" / "build_deb.sh").write_text("#!/bin/bash\n")
    calls = []
    def run(cmd, **k):
        calls.append(cmd)
        if cmd[0].endswith("firefox"): return 0, "Mozilla Firefox 158.0\n"
        if cmd[0] == "dpkg-deb": return 0, "158.0-1\n"
        return 0, "ok"
    assert fl.stage_build(c, run=run, env={"CLAUDECODE": "1"})["ok"]
    assert not fl.verify_build(c, run=run)["ok"], "no binary yet"
    fx = fl.dist_bin(c) / "firefox"; fx.parent.mkdir(parents=True); fx.write_text("")
    assert fl.verify_build(c, run=run)["ok"]
    assert fl.stage_package(c, run=run)["ok"]
    assert calls[-1][2] == "" and calls[-1][3] == str(fl.dist_bin(c)) and calls[-1][5].endswith("deb_template")
    assert not fl.verify_package(c, run=run)["ok"], "no deb yet"
    deb = Path(c.vars["release"]) / "gorilla-unleashed_158.0-1_amd64.deb"; deb.write_bytes(b"deb")
    r = fl.verify_package(c, run=run)
    assert r["ok"] and r["version"] == "158.0-1" and (Path(c.vars["release"]) / (deb.name + ".sha256")).is_file()


def test_the_deb_revision_is_the_trees_gorilla_build_number(tmp_path):
    # 2026-10-23: build 2 would have been packaged as 158.0-1 (build_deb.sh's default), build 1's version string
    from types import SimpleNamespace
    src = tmp_path / "src"; f = src / "browser" / "branding" / "gorilla" / "pref" / "firefox-branding.js"
    c = SimpleNamespace(vars={"src": str(src), "version": "158.0"})
    assert fl.deb_version(c) == ""                                    # no branding prefs: build_deb.sh decides, as before
    f.parent.mkdir(parents=True); f.write_text('pref("gorilla.build.number", 2);\n')
    assert fl.build_number(src) == 2 and fl.deb_version(c) == "158.0-2"


def test_one_build_date_for_the_whole_build_and_the_binaries_agree_with_application_ini(tmp_path):
    # 2026-10-23: buildid.h was written twice in one build; libxul said 20261023183213, application.ini 20261023185344
    assert fl.build_env({"CLAUDECODE": "1"}, now=0)["MOZ_BUILD_DATE"] == time.strftime("%Y%m%d%H%M%S", time.localtime(0))
    assert fl.build_env({"MOZ_BUILD_DATE": "20260101000000"})["MOZ_BUILD_DATE"] == "20260101000000"
    c = ctx(tmp_path)
    d = fl.dist_bin(c); d.mkdir(parents=True)
    (d / "firefox").write_bytes(b"\0x20261023183213\0")
    (d / "libxul.so").write_bytes(b"\0" + b"20261023183213" + b"\0")
    (d / "application.ini").write_text("[App]\nBuildID=20261023185344\n")
    run = lambda cmd, **k: (0, "Mozilla Firefox 158.0\n")
    r = fl.verify_build(c, run=run)
    assert not r["ok"] and "20261023185344" in r["detail"] and "firefox (carries 20261023183213)" in r["detail"]
    assert "rebuild" in fl.buildid_split(d)                  # build_mode reads the same: a split build is built again
    (d / "application.ini").write_text("[App]\nBuildID=20261023183213\n")
    assert fl.verify_build(c, run=run)["ok"]
    (d / "libxul.so").write_bytes(b"\0" + b"20261023170000" + b"\0")   # libxul not relinked: its id may be older
    assert fl.verify_build(c, run=run)["ok"]
