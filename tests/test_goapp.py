"""The Go build pipeline (fieldkit/build/goapp.py, pipelines/go-app.yaml): a tiny Go program built for real when Go
is installed, and the parts that need no Go (test summaries, version comparison, targets) everywhere.

What is held: the toolchain refusal says how to install; a stage is skipped only while the source is unchanged; a
build carries its version stamp and the same commit gives the same bytes; checksums catch a changed file; a failed
test is reported by name, not as a wall of log.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.build import goapp
from fieldkit.core.pipeline import Pipeline
from tests.test_goapp_windows_release import DEB, GOOD_DICT

PIPE = Path(goapp.__file__).parent / "pipelines" / "go-app.yaml"
HAS_GO = goapp.find_go() is not None

MAIN = '''package main

import (
	"fmt"
	"os"
)

var Version = "dev"

func main() {
	if len(os.Args) > 1 && os.Args[1] == "--version" {
		fmt.Println(Version)
		return
	}
	fmt.Println("hello")
}
'''
TEST_OK = '''package main

import "testing"

func TestHello(t *testing.T) {}
'''
TEST_FAIL = '''package main

import "testing"

func TestBroken(t *testing.T) { t.Fatal("the answer is wrong: want 42") }
'''


def _module(tmp_path, go_version="1.21"):
    src = tmp_path / "hello"
    src.mkdir()
    (src / "go.mod").write_text(f"module example.com/hello\n\ngo {go_version}\n", encoding="utf-8")
    (src / "main.go").write_text(MAIN, encoding="utf-8")
    (src / "main_test.go").write_text(TEST_OK, encoding="utf-8")
    return src


def _pipeline(tmp_path, src, **vars_):
    v = {"src": str(src), "name": "hello", "version": "v1.2.3", "version_var": "main.Version",
         "version_flag": "--version", "targets": "linux/amd64,windows/amd64"}
    v.update(vars_)
    return Pipeline.load(PIPE, state_dir=tmp_path / "state", overrides=v, strict=False)


# -- no Go needed ----------------------------------------------------------------------------------------
def test_versions_compare_as_numbers():
    assert goapp._ver("1.24.7") > goapp._ver("1.24.0") > goapp._ver("1.9")
    assert goapp._ver("1.24") == goapp._ver("1.24.0")


def test_test_summary_names_the_failures_and_keeps_their_first_lines():
    out = "\n".join([
        "ok  \texample.com/a\t0.1s",
        "?   \texample.com/b\t[no test files]",
        "--- FAIL: TestBroken (0.00s)",
        "    main_test.go:5: the answer is wrong: want 42",
        "FAIL",
        "FAIL\texample.com/c\t0.2s",
        "ok  \texample.com/d\t(cached)",
    ])
    s = goapp.summarize_tests(out)
    assert s["passed"] == 2 and s["failed"] == ["example.com/c"] and s["no_tests"] == 1
    assert s["failed_tests"] == ["TestBroken"]
    assert s["failures"]["TestBroken"] == ["main_test.go:5: the answer is wrong: want 42"]


def test_targets_must_be_os_slash_arch(tmp_path):
    class Ctx:
        vars = {"targets": "linux/amd64, windows/amd64"}
    assert goapp._targets(Ctx) == [("linux", "amd64"), ("windows", "amd64")]
    Ctx.vars = {"targets": "linux"}
    with pytest.raises(ValueError, match="os/arch"):
        goapp._targets(Ctx)


def test_a_folder_without_go_mod_is_refused(tmp_path):
    class Ctx:
        vars = {"src": str(tmp_path), "name": "x"}
    with pytest.raises(ValueError, match="no go.mod"):
        goapp._src(Ctx)


def test_the_fingerprint_follows_the_source(tmp_path):
    src = _module(tmp_path)
    a = goapp.fingerprint(src)
    assert goapp.fingerprint(src) == a
    (src / "main.go").write_text(MAIN + "\n// changed\n", encoding="utf-8")
    assert goapp.fingerprint(src) != a
    (src / "dist").mkdir()
    b = goapp.fingerprint(src)
    (src / "dist" / "hello.exe").write_bytes(b"built")             # build output does not change the source
    assert goapp.fingerprint(src) == b


def test_the_gorilla_opencode_preset_matches_how_releases_are_built():
    p = Pipeline.load(PIPE.parent / "gorilla-opencode.yaml", state_dir="/tmp/x-unused", strict=False)
    assert p.vars["version_var"] == "github.com/opencode-ai/opencode/internal/version.Version"
    assert p.vars["windows_name"] == "gorilla-opencode.exe" and p.vars["cgo"] == "0"
    assert [s["id"] for s in p.stages] == ["toolchain", "modules", "vet", "test", "build", "repro", "published",
                                            "mac", "packages", "arch", "checksums", "align"]
    # 2026-10-25: byte equality with the published files is opt-in; packages pass xzcheck within 128 MiB
    assert p.vars["compare_with"] == "" and p.vars["git_ref"] == "" and p.vars["xz_cap_mib"] == "128"
    # every release file from one Linux machine (owner, 2026-10-23): the Mac 10.13 binary and the four packages
    assert p.vars["mac_script"] == "scripts/build-darwin-legacy.sh"
    assert p.vars["packages"].split(",") == ["scripts/build-deb.sh", "scripts/build-tarball.sh",
                                             "scripts/build-rpm.sh"]
    assert p.vars["arch_script"] == "scripts/build-arch.sh"      # W6: its own stage, in every release's checksums
    # and from Windows (v0.1.152, 2026-10-25): nfpm, the Mac on go1.24, the page's zip name, the last .deb compared
    assert p.vars["packager"] == "auto" and p.vars["mac_go"] == "1.24" and p.vars["previous_deb"] == "auto"
    assert p.vars["mac_zip"] == "<name>-<version>-mac-10.13-x86_64.zip" and p.vars["repo"]
    assert p.vars["tarball"] == "gorilla-opencode-linux-x86_64.tar.gz" and p.vars["tarball_member"] == "gorilla-opencode"


def test_every_go_signature_compiles_and_names_a_fix():
    import re
    import yaml
    sigs = yaml.safe_load((PIPE.parent.parent / "signatures" / "go.yaml").read_text(encoding="utf-8"))["signatures"]
    for s in sigs:
        re.compile(s["pattern"])
        assert s["cause"] and s["fix"], s["id"]


# -- a real Go build ----------------------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_GO, reason="Go is not installed")
def test_a_real_build_is_stamped_reproducible_and_resumable(tmp_path):
    src = _module(tmp_path)
    p = _pipeline(tmp_path, src)
    r = p.run()
    assert r["ok"], r
    out = src / "dist"
    linux, win = out / "hello-v1.2.3-linux-amd64", out / "hello-v1.2.3-windows-amd64.exe"
    assert linux.is_file() and win.is_file() and (out / "SHA256SUMS-v1.2.3.txt").is_file()
    assert b"v1.2.3" in win.read_bytes()                                  # stamped, even where it cannot run
    if sys.platform.startswith("linux"):
        assert subprocess.run([str(linux), "--version"], capture_output=True, text=True).stdout.strip() == "v1.2.3"
    first = goapp._sha(linux)

    # unchanged source: nothing runs again
    r = p.run()
    assert {s["id"]: s["status"] for s in r["stages"]}["build"] == "up-to-date"

    # the same commit gives the same bytes (-trimpath, cgo off), wherever it is built
    shutil.rmtree(out)
    assert p.run(force=True)["ok"] and goapp._sha(linux) == first

    # the binary proven: built again from a copy elsewhere (own cache, temp, time zone), byte for byte the same
    rp = goapp._load(_ctx(p), src, "repro")
    assert rp["ok"] and all(x["identical"] for x in rp["results"]) and len(rp["results"]) == 2
    assert rp["second_build"]["folder"] != str(src) and "reproduced" in rp["detail"]
    assert goapp.verify_repro(_ctx(p))["ok"]

    # a changed binary is caught by its checksum, and the reproduction no longer vouches for it
    linux.write_bytes(b"tampered")
    assert goapp.verify_checksums(_ctx(p))["ok"] is False
    assert goapp.verify_build(_ctx(p))["ok"] is False


@pytest.mark.skipif(not HAS_GO, reason="Go is not installed")
def test_a_failing_test_stops_the_build_and_is_named(tmp_path):
    src = _module(tmp_path)
    (src / "main_test.go").write_text(TEST_FAIL, encoding="utf-8")
    p = _pipeline(tmp_path, src)
    r = p.run()
    stages = {s["id"]: s for s in r["stages"]}
    assert not r["ok"] and stages["test"]["status"] == "failed" and "build" not in stages
    rec = goapp._load(_ctx(p), src, "test")
    assert rec["failed_tests"] == ["TestBroken"] and "want 42" in rec["failures"]["TestBroken"][0]


@pytest.mark.skipif(not HAS_GO, reason="Go is not installed")
def test_a_go_mod_newer_than_the_toolchain_is_refused_with_the_install_line(tmp_path):
    src = _module(tmp_path, go_version="9.99")
    rec = goapp.stage_toolchain(_ctx(_pipeline(tmp_path, src)))
    assert rec["ok"] is False and "9.99" in rec["detail"] and "go.dev/dl" in rec["detail"] + "winget"


def _ctx(p):
    from fieldkit.core.pipeline import Context
    return Context(p)


def test_repeated_var_options_are_all_kept():
    """`--var a=1 --var b=2` kept only b (nargs="*" without append): a pipeline then ran with src empty."""
    from fieldkit.cli import build_parser
    a = build_parser().parse_args(["pipeline", "plan", "go-app", "--var", "src=/x", "--var", "version=v1"])
    assert a.var == ["src=/x", "version=v1"]
    a = build_parser().parse_args(["next", "go-app", "--var", "src=/x", "version=v1"])
    assert a.var == ["src=/x", "version=v1"]


# -- the Mac binary and the packages (no Go needed: the project's scripts are stand-ins) ---------------------------
def _built(tmp_path, **vars_):
    src = _module(tmp_path)
    p = _pipeline(tmp_path, src, **vars_)
    ctx = _ctx(p)
    out = goapp._out(ctx, src)
    out.mkdir(parents=True, exist_ok=True)
    linux = out / "hello-v1.2.3-linux-amd64"
    linux.write_bytes(b"ELF stand-in")
    goapp._save(ctx, src, "build", {"ok": True, "version": "v1.2.3", "built": [
        {"target": "linux/amd64", "file": str(linux), "sha256": goapp._sha(linux), "bytes": 12}]})
    return src, ctx, out


def _script(src, rel, body):
    f = src / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("#!/bin/sh\nset -e\n" + body, encoding="utf-8")
    return rel


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in scripts are POSIX sh")
def test_mac_stage_runs_the_script_with_the_plain_version_and_wants_a_mach_o(tmp_path):
    src, ctx, out = _built(tmp_path)
    ctx.vars["mac_script"] = _script(src, "scripts/mac.sh",
                                     'printf "\\317\\372\\355\\376$VERSION" > "$1"\n')
    r = goapp.stage_mac(ctx)
    assert r["ok"], r
    f = Path(r["file"])
    assert f.name == "hello-v1.2.3-darwin-amd64-legacy" and f.read_bytes()[4:] == b"1.2.3"   # VERSION without "v"
    assert goapp.verify_mac(ctx)["ok"]
    ctx.vars["mac_script"] = _script(src, "scripts/notmac.sh", 'echo "not a mach-o" > "$1"\n')
    assert not goapp.stage_mac(ctx)["ok"]


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in scripts are POSIX sh")
def test_packages_are_made_from_the_linux_binary_and_counted_in_the_checksums(tmp_path):
    src, ctx, out = _built(tmp_path)
    # a real .deb (data.tar.xz, the right dictionary): the packages stage passes every .deb through xzcheck
    (src / "scripts").mkdir(parents=True, exist_ok=True)
    (src / "scripts" / "mkdeb.py").write_text(
        DEB + f"import sys, pathlib\npathlib.Path(sys.argv[2]).write_bytes(deb(pathlib.Path(sys.argv[1]).read_bytes(), "
              f"{GOOD_DICT}))\n", encoding="utf-8")
    deb = _script(src, "scripts/deb.sh", 'cmp -s hello "$OUTBIN" || true; mkdir -p Compiled.Builds; '
                                         f'"{sys.executable}" scripts/mkdeb.py hello "Compiled.Builds/hello_${{1}}_amd64.deb"\n')
    rpm = _script(src, "scripts/rpm.sh", 'cp "Compiled.Builds/hello_${1}_amd64.deb" '
                                         '"Compiled.Builds/hello-${1}-1.x86_64.rpm"\n')
    tar = _script(src, "scripts/tar.sh", 'cp hello Compiled.Builds/hello-linux-x86_64.tar.gz\n')   # a fixed name
    ctx.vars["packages"] = f"{deb},{rpm},{tar}"
    r = goapp.stage_packages(ctx)
    assert r["ok"], r
    assert (src / "hello").read_bytes() == b"ELF stand-in"                  # the scripts' documented input
    names = sorted(Path(f["file"]).name for f in r["files"])
    assert names == ["hello-1.2.3-1.x86_64.rpm", "hello-linux-x86_64.tar.gz", "hello_1.2.3_amd64.deb"]
    assert goapp.verify_packages(ctx)["ok"]
    goapp.stage_checksums(ctx)
    sums = (out / "SHA256SUMS-v1.2.3.txt").read_text()
    assert all(n in sums for n in names) and "hello-v1.2.3-linux-amd64" in sums
    assert goapp.verify_checksums(ctx)["ok"]


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in scripts are POSIX sh")
def test_a_package_script_that_makes_nothing_fails_by_name(tmp_path):
    src, ctx, out = _built(tmp_path)
    ctx.vars["packages"] = _script(src, "scripts/silent.sh", "true\n")
    r = goapp.stage_packages(ctx)
    assert not r["ok"] and "scripts/silent.sh" in r["detail"] and "made no new file" in r["detail"]


def test_without_mac_script_or_packages_both_stages_skip(tmp_path):
    src, ctx, out = _built(tmp_path)
    ctx.vars["mac_script"] = ""
    ctx.vars["packages"] = ""
    assert goapp.stage_mac(ctx)["skipped"] and goapp.stage_packages(ctx)["skipped"]
    assert goapp.verify_mac(ctx)["ok"] and goapp.verify_packages(ctx)["ok"]


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in scripts are POSIX sh")
def test_mac_stage_also_makes_the_installer_package_and_sums_it(tmp_path):
    src, ctx, out = _built(tmp_path)
    ctx.vars["mac_script"] = _script(src, "scripts/mac.sh", 'printf "\\317\\372\\355\\376" > "$1"\n')
    pkg = src / "scripts" / "pack.py"
    pkg.write_text("import sys, pathlib\nrepo, binary, v, out = sys.argv[1:5]\nd = pathlib.Path(out) / f'app-{v}-mac'\n"
                   "d.mkdir(parents=True)\n(d / f'app-{v}-mac-10.13-x86_64.tar.gz').write_bytes(b'tgz')\n"
                   "(pathlib.Path(out) / f'app-{v}-mac-10.13.zip').write_bytes(b'zip')\n")
    ctx.vars["mac_package"] = "scripts/pack.py"
    r = goapp.stage_mac(ctx)
    assert r["ok"], r
    names = sorted(Path(x["file"]).name for x in r["package"])
    assert names == ["app-1.2.3-mac-10.13-x86_64.tar.gz", "app-1.2.3-mac-10.13.zip"]
    goapp.stage_checksums(ctx)
    sums = (out / "SHA256SUMS-v1.2.3.txt").read_text()
    assert all(n in sums for n in names)


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in scripts are POSIX sh")
def test_mac_package_absent_is_said_not_failed(tmp_path):
    src, ctx, out = _built(tmp_path)
    ctx.vars["mac_script"] = _script(src, "scripts/mac.sh", 'printf "\\317\\372\\355\\376" > "$1"\n')
    ctx.vars["mac_package"] = "scripts/packaging/missing.py"
    r = goapp.stage_mac(ctx)
    assert r["ok"] and "not in this checkout" in r["detail"]


def _align_setup(monkeypatch, tmp_path, peer_status="started", local_rc=0):
    from fieldkit.lifecycle import tasks
    from fieldkit.peers import link
    saved = {}
    monkeypatch.setattr(goapp, "_src", lambda ctx: tmp_path)
    monkeypatch.setattr(goapp, "_load", lambda ctx, src, name: {"ok": True, "version": "v9.9.9", "commit": "c"})
    monkeypatch.setattr(goapp, "_save", lambda ctx, src, name, data: saved.setdefault(name, data))
    monkeypatch.setattr(link, "household", lambda: {"d": 1})
    monkeypatch.setattr(link, "others", lambda h: [{"name": "vaio"}])
    calls = []
    monkeypatch.setattr(tasks, "start_run", lambda argv, on, tree, **kw: calls.append((argv, on)) or
                        {"id": "T-1", "status": peer_status})

    def run(cmd, **kw):
        calls.append(cmd)
        rc = local_rc if "align" in cmd else 0
        return subprocess.CompletedProcess(cmd, rc, "out", "")
    return saved, calls, run


def test_align_runs_parity_align_here_and_on_the_peer_for_the_new_tag(monkeypatch, tmp_path):
    saved, calls, run = _align_setup(monkeypatch, tmp_path)
    r = goapp.stage_align(None, run=run)
    assert r["ok"] and [x["device"] for x in r["rows"]] == ["this computer", "vaio"]
    assert any(c[-4:] == ["align", "--apply", "--version", "v9.9.9"] for c in calls if isinstance(c, list))
    assert (["fieldkit", "parity", "align", "--apply", "--version", "v9.9.9"], "vaio") in calls


def test_align_fails_when_the_peer_job_did_not_start_or_this_computer_failed(monkeypatch, tmp_path):
    _, _, run = _align_setup(monkeypatch, tmp_path, peer_status="not-started")
    assert not goapp.stage_align(None, run=run)["ok"]
    _, _, run = _align_setup(monkeypatch, tmp_path, local_rc=3)
    assert not goapp.stage_align(None, run=run)["ok"]


@pytest.mark.skipif(sys.platform != "linux", reason="the Arch package is made on Linux")
def test_arch_stage_makes_the_package_and_it_is_counted_in_the_checksums(tmp_path):
    import shutil
    if not (shutil.which("zstd") and shutil.which("bsdtar")):
        pytest.skip("needs zstd and bsdtar")
    src, ctx, out = _built(tmp_path)
    ctx.vars["packages"] = ""
    ctx.vars["arch_script"] = _script(src, "scripts/arch.sh", 'mkdir -p Compiled.Builds; '
        'echo pkg | zstd -q -o "Compiled.Builds/hello-${1}-1-x86_64.pkg.tar.zst"\n')
    r = goapp.stage_arch(ctx)
    assert r["ok"] and not r.get("skipped"), r
    assert Path(r["files"][0]["file"]).name == "hello-1.2.3-1-x86_64.pkg.tar.zst"
    assert goapp.verify_arch(ctx)["ok"]
    goapp.stage_packages(ctx)
    goapp.stage_checksums(ctx)
    assert "hello-1.2.3-1-x86_64.pkg.tar.zst" in (out / "SHA256SUMS-v1.2.3.txt").read_text()
    assert goapp.verify_checksums(ctx)["ok"]
    Path(r["files"][0]["file"]).write_bytes(b"changed")                # a changed package is caught
    assert not goapp.verify_arch(ctx)["ok"]


def test_arch_stage_is_not_this_platform_off_linux_and_skipped_without_a_script(tmp_path, monkeypatch):
    src, ctx, out = _built(tmp_path)
    ctx.vars["arch_script"] = "scripts/build-arch.sh"
    monkeypatch.setattr(goapp.sys, "platform", "win32")
    r = goapp.stage_arch(ctx)
    assert r["ok"] and r["status"] == "not-this-platform" and r["files"] == []
    assert goapp.verify_arch(ctx)["ok"]
    ctx.vars["arch_script"] = ""
    assert goapp.stage_arch(ctx)["skipped"]


def test_src_without_embeds_does_not_need_the_git_ignored_helpers(tmp_path, monkeypatch):
    # the Arch stage compiles nothing: a clone whose embed_dirs source is empty must not stop it (2026-11-01)
    src, ctx, _ = _built(tmp_path)
    ctx.vars.update({"git_ref": "HEAD", "embed_dirs": "internal/toolbox/bin/windows-amd64"})
    clone = tmp_path / "clone"
    clone.mkdir()
    monkeypatch.setattr(goapp, "checkout", lambda *a, **k: clone)
    with pytest.raises(ValueError, match="holds no files"):
        goapp._src(ctx)
    assert goapp._src(ctx, embeds=False) == clone
