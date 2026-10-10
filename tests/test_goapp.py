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
    assert [s["id"] for s in p.stages] == ["toolchain", "modules", "vet", "test", "build", "checksums"]


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

    # a changed binary is caught by its checksum
    linux.write_bytes(b"tampered")
    assert goapp.verify_checksums(_ctx(p))["ok"] is False


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
