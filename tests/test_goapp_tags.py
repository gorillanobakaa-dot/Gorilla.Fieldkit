"""Build tags and embedded files in the Go pipeline (fieldkit/build/goapp.py), after Gorilla OpenCode v0.1.155 shipped
without its toolbox (2026-10-27): `go version -m` on the published exe listed no -tags, because the pipeline passed
none, and a git_ref clone does not hold the git-ignored helpers the tag embeds.

What is held: the Gorilla OpenCode preset builds the Windows exe with -tags toolbox and pins the helpers; tags are
per target; every pinned helper must be present with its bytes before anything is built; a git_ref clone gets the
git-ignored folder copied in and stays clean; verify_build reads the tags from the binary itself.
"""
import gzip
import hashlib
import subprocess
from pathlib import Path

import pytest

from fieldkit.build import goapp
from fieldkit.core.pipeline import Context, Pipeline
from tests.test_goapp import HAS_GO, PIPE, _module, _pipeline


def test_the_gorilla_opencode_preset_builds_windows_with_the_toolbox():
    p = Pipeline.load(PIPE.parent / "gorilla-opencode.yaml", state_dir="/tmp/x-unused", strict=False)
    ctx = Context(p)
    assert goapp.target_tags(ctx, "windows", "amd64") == "toolbox"
    assert goapp.target_tags(ctx, "linux", "amd64") == ""
    assert p.vars["embed_dirs"] == "internal/toolbox/bin/windows-amd64"
    assert p.vars["embed_sums"] == "internal/toolbox/SHA256SUMS" and p.vars["embed_root"] == "internal/toolbox/bin"


def test_tags_are_per_target_or_for_all(tmp_path):
    p = _pipeline(tmp_path, _module(tmp_path), tags="windows/amd64=toolbox,x; netgo ; linux/arm64=y")
    ctx = Context(p)
    assert goapp.target_tags(ctx, "windows", "amd64") == "toolbox,x,netgo"
    assert goapp.target_tags(ctx, "linux", "amd64") == "netgo"
    assert goapp.target_tags(ctx, "linux", "arm64") == "netgo,y"


def _pins(src, files):
    """SUMS + bin/windows-amd64/<name>.gz for {name: bytes}; -> the module's ctx vars."""
    lines = []
    for name, data in files.items():
        f = src / "bin" / "windows-amd64" / (name + ".gz")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(gzip.compress(data))
        lines.append(f"{hashlib.sha256(data).hexdigest()}  windows-amd64/{name}")
    (src / "SUMS").write_text("# pins\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return {"tags": "windows/amd64=toolbox", "embed_sums": "SUMS", "embed_root": "bin",
            "embed_dirs": "bin/windows-amd64"}


def test_every_pinned_helper_must_be_there_with_its_bytes_before_a_build(tmp_path):
    src = _module(tmp_path)
    ctx = Context(_pipeline(tmp_path, src, **_pins(src, {"a.exe": b"A" * 100, "b.exe": b"B"})))
    assert goapp.check_embeds(ctx, src, "windows", "amd64") == (2, [])
    assert goapp.check_embeds(ctx, src, "linux", "amd64") == (0, [])          # no tags there: nothing embedded
    (src / "bin" / "windows-amd64" / "b.exe.gz").write_bytes(gzip.compress(b"other"))
    n, problems = goapp.check_embeds(ctx, src, "windows", "amd64")
    assert problems == ["windows-amd64/b.exe is not the pinned bytes"]
    (src / "bin" / "windows-amd64" / "a.exe.gz").unlink()
    assert "windows-amd64/a.exe missing" in goapp.check_embeds(ctx, src, "windows", "amd64")[1]
    # v0.1.155 exactly: tags asked, nothing embedded -> the build stage refuses before compiling
    for f in (src / "bin" / "windows-amd64").iterdir():
        f.unlink()
    r = goapp.stage_build(ctx)
    assert r["ok"] is False and "embedded files wrong" in r["detail"]


def test_a_missing_pinned_helper_is_fetched_by_the_module_script_and_checked(tmp_path):
    src = _module(tmp_path)
    pins = _pins(src, {"a.exe": b"A" * 100, "b.exe": b"B"})
    gz = src / "bin" / "windows-amd64" / "b.exe.gz"
    gz.unlink()
    plain = Context(_pipeline(tmp_path, src, **pins))
    probs = goapp.check_embeds(plain, src, "windows", "amd64")[1]
    assert probs == ["windows-amd64/b.exe missing"]
    assert goapp.fetch_missing_embeds(plain, src, "windows", "amd64", probs) == probs     # no embed_fetch: unchanged
    (src / "fetch.sh").write_text('mkdir -p "$1/windows-amd64"; printf B > "$1/windows-amd64/b.exe"\n', newline="\n")
    ctx = Context(_pipeline(tmp_path, src, embed_fetch="fetch.sh", **pins))
    assert goapp.fetch_missing_embeds(ctx, src, "windows", "amd64", probs) == []
    gz.write_bytes(gzip.compress(b"other"))                                  # wrong bytes: never fetched over
    wrong = goapp.check_embeds(ctx, src, "windows", "amd64")[1]
    assert goapp.fetch_missing_embeds(ctx, src, "windows", "amd64", wrong) == wrong


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def test_a_git_ref_clone_gets_the_ignored_helpers_and_stays_clean(tmp_path):
    src = _module(tmp_path)
    (src / ".gitignore").write_text("/bin/*/\n/dist/\n", encoding="utf-8")
    vars_ = _pins(src, {"a.exe": b"A"})
    _git("init", "-q", cwd=src)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=src)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x", cwd=src)
    ctx = Context(_pipeline(tmp_path, src, git_ref="HEAD", **vars_))
    clone = goapp._src(ctx)
    assert clone != src and (clone / "bin" / "windows-amd64" / "a.exe.gz").is_file()
    assert subprocess.run(["git", "status", "--porcelain"], cwd=clone, capture_output=True, text=True).stdout == ""
    assert goapp._src(ctx) == clone                                       # reused: still clean with the copy in it
    # an empty source folder is refused, never a build without the helpers
    for f in (src / "bin" / "windows-amd64").iterdir():
        f.unlink()
    with pytest.raises(ValueError, match="holds no files"):
        goapp._src(ctx)


@pytest.mark.skipif(not HAS_GO, reason="Go is not installed")
def test_the_tag_is_read_back_from_the_binary(tmp_path):
    src = _module(tmp_path)
    (src / "tagged.go").write_text('//go:build toolbox\n\npackage main\n\nvar Toolbox = true\n',
                                   encoding="utf-8")
    p = _pipeline(tmp_path, src, targets="windows/amd64,linux/amd64", tags="windows/amd64=toolbox")
    ctx = Context(p)
    r = goapp.stage_build(ctx)
    assert r["ok"], r
    tags = {b["target"]: b["tags"] for b in r["built"]}
    assert tags == {"windows/amd64": "toolbox", "linux/amd64": ""}
    exe = next(Path(b["file"]) for b in r["built"] if b["target"] == "windows/amd64")
    assert "build\t-tags=toolbox" in subprocess.run([goapp.find_go(), "version", "-m", str(exe)],
                                                    capture_output=True, text=True).stdout
    assert goapp.verify_build(ctx)["ok"], goapp.verify_build(ctx)
    # asked for the tag now, built without it: caught
    ctx2 = Context(_pipeline(tmp_path, src, targets="windows/amd64,linux/amd64", tags="toolbox"))
    v = goapp.verify_build(ctx2)
    assert v["ok"] is False and "linux/amd64 was built with tags ''" in v["detail"]
    # the record says toolbox but the binary does not: caught from `go version -m`, not from the record
    rec = goapp._load(ctx, src, "build")
    plain = subprocess.run([goapp.find_go(), "build", "-trimpath", "-ldflags", rec["ldflags"], "-o", str(exe), "."],
                           cwd=src, env={**__import__("os").environ, "GOOS": "windows", "GOARCH": "amd64",
                                         "CGO_ENABLED": "0"}, capture_output=True)
    assert plain.returncode == 0
    for b in rec["built"]:
        b["sha256"] = goapp._sha(b["file"])
    goapp._save(ctx, src, "build", rec)
    v = goapp.verify_build(ctx)
    assert v["ok"] is False and "records -tags=''" in v["detail"]


def test_seed_folder_fills_pinned_helpers_the_checkout_lacks_and_skips_unpinned(tmp_path):
    src = _module(tmp_path)
    v = _pins(src, {"a.exe": b"A" * 50})
    for f in (src / "bin" / "windows-amd64").iterdir():
        f.unlink()                                          # the checkout holds none, as on a clean machine
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "a.exe").write_bytes(b"A" * 50)
    (seed / "sg.exe").write_bytes(b"not pinned")
    v["embed_seed"] = str(seed)
    seeds = goapp._seed_files(src, v)
    assert list(seeds) == ["a.exe.gz"]
    dest = tmp_path / "clone"
    (dest / "bin").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    (dest / ".gitignore").write_text("bin/windows-amd64/\n")
    goapp.copy_embeds(src, dest, v["embed_dirs"], seeds=seeds)
    assert gzip.decompress((dest / "bin" / "windows-amd64" / "a.exe.gz").read_bytes()) == b"A" * 50
    assert not (dest / "bin" / "windows-amd64" / "sg.exe.gz").exists()
    with pytest.raises(ValueError):                         # no seed, nothing in the checkout: still refused
        goapp.copy_embeds(src, dest, v["embed_dirs"])
