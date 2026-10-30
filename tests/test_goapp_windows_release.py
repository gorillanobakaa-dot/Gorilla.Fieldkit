"""Every release file from a Windows machine (fieldkit/build/goapp.py, v0.1.152 of Gorilla OpenCode, 2026-10-25).

What is held, each by a test that fails when the rule breaks (fieldkit mutate, see PLAN.md):
- without dpkg-deb/rpmbuild the packages come from the project's nfpm script, given the version, the Linux binary,
  a folder and --compare <the last release's .deb>; a script that makes no .deb/.rpm fails by name;
- the tarball is made without running the binary: one member, 0755, the reproduced binary's bytes, the stamp;
  a binary the repro stage did not reproduce is refused;
- the Mac build uses a cached go1.24.x when the system go is another series, and refuses when none is cached;
- the published zip has the release page's name, and SHA256SUMS lists exactly the published files;
- RELEASE-FILES-<v>.md is the measured table, and a table that disagrees fails the checksums check.
"""
import gzip
import json
import sys
import tarfile
from pathlib import Path

import pytest

from fieldkit.build import goapp, relfiles
from fieldkit.core.pipeline import Context, Pipeline

PIPE = Path(goapp.__file__).parent / "pipelines" / "go-app.yaml"
STAMP = b"\x7fELF stand-in v1.2.3 go1.27.0"

DEB = r'''import io, lzma, tarfile
def deb(payload, dict_size):
    """A real .deb: ar with debian-binary and data.tar.xz (one LZMA2 block, the given dictionary)."""
    def member(name, data):
        head = f"{name:<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
        return head + data + (b"\n" if len(data) % 2 else b"")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        ti = tarfile.TarInfo("usr/bin/hello")
        ti.size = len(payload)
        t.addfile(ti, io.BytesIO(payload))
    xz = lzma.compress(buf.getvalue(), format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64,
                       filters=[{"id": lzma.FILTER_LZMA2, "preset": 6, "dict_size": dict_size}])
    return b"!<arch>\n" + member("debian-binary", b"2.0\n") + member("data.tar.xz", xz)
'''
GOOD_DICT = 16 << 10        # a 10 KiB tar: the smallest power of two that holds it
NFPM = DEB + r'''import sys, pathlib
v, binary, out = sys.argv[1:4]
rest = sys.argv[4:]
out = pathlib.Path(out)
data = pathlib.Path(binary).read_bytes()
assert ("v" + v).encode() in data, "no stamp"
d = pathlib.Path(__file__).with_name("dict.txt")
(out / f"hello_{v}_amd64.deb").write_bytes(deb(data, int(d.read_text()) if d.is_file() else 16 << 10))
(out / f"hello-{v}-1.x86_64.rpm").write_bytes(b"rpm" + data)
(out.parent / "nfpm-args.json").write_text(__import__("json").dumps(rest))
print(f"deb: hello_{v}_amd64.deb  3 files, modes as planned")
print(f"rpm: hello-{v}-1.x86_64.rpm  (9 bytes)")
'''


def _built(tmp_path, **vars_):
    src = tmp_path / "hello"
    src.mkdir()
    (src / "go.mod").write_text("module example.com/hello\n\ngo 1.21\n", encoding="utf-8")
    v = {"src": str(src), "name": "hello", "version": "v1.2.3", "version_var": "main.Version",
         "targets": "linux/amd64,windows/amd64"}
    v.update(vars_)
    p = Pipeline.load(PIPE, state_dir=tmp_path / "state", overrides=v, strict=False)
    ctx = Context(p)
    out = goapp._out(ctx, src)
    out.mkdir(parents=True)
    linux, win = out / "hello-v1.2.3-linux-amd64", out / "hello.exe"
    linux.write_bytes(STAMP)
    win.write_bytes(b"MZ v1.2.3")
    built = [{"target": "linux/amd64", "file": str(linux), "sha256": goapp._sha(linux), "bytes": linux.stat().st_size},
             {"target": "windows/amd64", "file": str(win), "sha256": goapp._sha(win), "bytes": win.stat().st_size}]
    goapp._save(ctx, src, "build", {"ok": True, "version": "v1.2.3", "built": built})
    goapp._save(ctx, src, "repro", {"ok": True, "results": [
        {"target": b["target"], "sha256": b["sha256"], "second_sha256": b["sha256"], "identical": True} for b in built]})
    return src, ctx, out


def _nfpm_vars(src, ctx, prev):
    (src / "scripts").mkdir(exist_ok=True)
    (src / "scripts" / "nfpm.py").write_text(NFPM, encoding="utf-8")
    ctx.vars.update({"packages": "scripts/build-deb.sh,scripts/build-arch.sh,scripts/build-tarball.sh,scripts/build-rpm.sh",
                     "packager": "nfpm", "nfpm_script": "scripts/nfpm.py", "previous_deb": str(prev),
                     "tarball": "hello-linux-x86_64.tar.gz", "tarball_member": "hello"})


def test_packager_auto_is_nfpm_on_windows_or_without_dpkg_deb(monkeypatch):
    monkeypatch.setattr(goapp.os, "name", "nt")
    assert goapp.packager({}) == "nfpm"
    monkeypatch.setattr(goapp.os, "name", "posix")
    monkeypatch.setattr(goapp.shutil, "which", lambda x: None)
    assert goapp.packager({"packager": "auto"}) == "nfpm"
    monkeypatch.setattr(goapp.shutil, "which", lambda x: "/usr/bin/" + x)
    assert goapp.packager({}) == "scripts" and goapp.packager({"packager": "nfpm"}) == "nfpm"


def test_nfpm_packages_and_the_tarball_from_the_reproduced_binary(tmp_path):
    src, ctx, out = _built(tmp_path)
    prev = tmp_path / "hello_1.2.2_amd64.deb"
    prev.write_bytes(b"!<arch>\nold")
    _nfpm_vars(src, ctx, prev)
    r = goapp.stage_packages(ctx)
    assert r["ok"], r
    names = sorted(Path(f["file"]).name for f in r["files"])
    assert names == ["hello-1.2.3-1.x86_64.rpm", "hello-linux-x86_64.tar.gz", "hello_1.2.3_amd64.deb"]
    args = json.loads((goapp._reports(ctx, src) / "nfpm-args.json").read_text())
    assert args == ["--compare", str(prev)]                       # compared with the last release's .deb
    assert "scripts/build-arch.sh" in r["detail"] and "not made here" in r["detail"]
    # the tarball: one member, 0755, the binary's bytes, without running it
    with tarfile.open(out / "hello-linux-x86_64.tar.gz", "r:gz") as t:
        (m,) = t.getmembers()
        assert m.name == "hello" and m.mode == 0o755 and m.uid == 0 and t.extractfile(m).read() == STAMP
    assert (out / "hello-linux-x86_64.tar.gz").read_bytes()[:2] == b"\x1f\x8b"
    first = goapp._sha(out / "hello-linux-x86_64.tar.gz")
    assert goapp.stage_packages(ctx)["ok"] and goapp._sha(out / "hello-linux-x86_64.tar.gz") == first   # same bytes
    assert goapp.verify_packages(ctx)["ok"]
    goapp.stage_checksums(ctx)
    sums = relfiles.parse_sums(out / "SHA256SUMS-v1.2.3.txt")
    assert set(sums) == set(names) | {"hello-v1.2.3-linux-amd64", "hello.exe"}
    assert goapp.verify_checksums(ctx)["ok"]


def test_a_tarball_that_is_not_the_binary_is_caught(tmp_path):
    src, ctx, out = _built(tmp_path)
    t = out / "x.tar.gz"
    goapp.make_tarball(out / "hello.exe", t, "hello")
    sha = goapp._sha(out / "hello-v1.2.3-linux-amd64")
    assert "not byte for byte" in goapp.check_tarball(t, "hello", out / "hello.exe", sha, "v1.2.3")
    goapp.make_tarball(out / "hello-v1.2.3-linux-amd64", t, "hello")
    assert goapp.check_tarball(t, "hello", out / "hello-v1.2.3-linux-amd64", sha, "v1.2.3") is None
    assert "stamp" in goapp.check_tarball(t, "hello", out / "hello-v1.2.3-linux-amd64", sha, "v9.9.9")
    assert "expected only" in goapp.check_tarball(t, "other", out / "hello-v1.2.3-linux-amd64", sha, "v1.2.3")


def test_a_tarball_member_with_the_wrong_mode_is_caught(tmp_path):
    import io
    src, ctx, out = _built(tmp_path)
    data = STAMP
    ti = tarfile.TarInfo("hello")
    ti.size, ti.mode = len(data), 0o644
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        t.addfile(ti, io.BytesIO(data))
    p = out / "bad.tar.gz"
    p.write_bytes(gzip.compress(buf.getvalue()))
    assert "0o644" in goapp.check_tarball(p, "hello", out / "x", goapp._sha(out / "hello-v1.2.3-linux-amd64"), "v1.2.3")


def test_a_binary_the_repro_stage_did_not_reproduce_is_not_packaged(tmp_path):
    src, ctx, out = _built(tmp_path)
    prev = tmp_path / "prev.deb"
    prev.write_bytes(b"!<arch>\n")
    _nfpm_vars(src, ctx, prev)
    assert goapp.stage_packages(ctx)["ok"]                          # reproduced: packaged
    rp = goapp._load(ctx, src, "repro")
    rp["results"][0].update(identical=False, second_sha256="0" * 64)   # the second build gave other bytes
    goapp._save(ctx, src, "repro", rp)
    r = goapp.stage_packages(ctx)
    assert not r["ok"] and "is not the binary the repro stage reproduced" in r["detail"]


def test_an_nfpm_script_that_makes_no_package_fails_by_name(tmp_path):
    src, ctx, out = _built(tmp_path)
    _nfpm_vars(src, ctx, tmp_path / "x.deb")
    (tmp_path / "x.deb").write_bytes(b"!<arch>\n")
    (src / "scripts" / "nfpm.py").write_text("print('nothing')\n", encoding="utf-8")
    r = goapp.stage_packages(ctx)
    assert not r["ok"] and "scripts/nfpm.py" in r["detail"] and "one .deb and one .rpm" in r["detail"]


def test_previous_deb_must_exist_or_be_said_none(tmp_path):
    src, ctx, out = _built(tmp_path)
    _nfpm_vars(src, ctx, tmp_path / "missing.deb")
    assert "not a file" in goapp.stage_packages(ctx)["detail"]
    ctx.vars["previous_deb"] = "none"
    r = goapp.stage_packages(ctx)
    assert r["ok"] and "NOT compared" in r["detail"]
    ctx.vars["previous_deb"] = "auto"
    ctx.vars["repo"] = ""
    assert "repo" in goapp.stage_packages(ctx)["detail"]


def test_previous_release_deb_is_downloaded_and_checked(tmp_path):
    """A fake gh: `release list` names v1.2.2 and v1.2.4; the one before v1.2.3 is v1.2.2."""
    import hashlib
    deb = b"!<arch>\nv1.2.2"
    fake = tmp_path / "gh.py"
    fake.write_text(f'''import sys, json, pathlib
a = sys.argv[1:]
if a[:2] == ["release", "list"]:
    print(json.dumps([{{"tagName": "v1.2.4", "isDraft": False}}, {{"tagName": "v1.2.2", "isDraft": False}},
                      {{"tagName": "v1.2.1", "isDraft": False}}, {{"tagName": "v1.2.0", "isDraft": True}}]))
elif a[:2] == ["release", "download"]:
    assert a[2] == "v1.2.2", a
    d = pathlib.Path(a[a.index("-D") + 1])
    (d / "hello_1.2.2_amd64.deb").write_bytes({deb!r})
    (d / "SHA256SUMS-v1.2.2.txt").write_text("{hashlib.sha256(deb).hexdigest()}  hello_1.2.2_amd64.deb\\n")
''', encoding="utf-8")
    gh = tmp_path / ("gh.cmd" if sys.platform == "win32" else "gh")
    gh.write_text(f'@"{sys.executable}" "{fake}" %*\n' if sys.platform == "win32" else
                  f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8")
    gh.chmod(0o755)
    path, tag = goapp.previous_release_deb("o/r", "v1.2.3", tmp_path / "prev", gh=str(gh))
    assert tag == "v1.2.2" and path.read_bytes() == deb
    path.write_bytes(b"tampered")                       # a changed copy is fetched again, then must match
    path2, _ = goapp.previous_release_deb("o/r", "v1.2.3", tmp_path / "prev", gh=str(gh))
    assert path2.read_bytes() == deb
    with pytest.raises(ValueError, match="older than"):
        goapp.previous_release_deb("o/r", "v1.0.0", tmp_path / "prev", gh=str(gh))


def test_mac_go_prefers_the_system_go_of_the_series_then_the_cache(tmp_path, monkeypatch):
    src, ctx, out = _built(tmp_path, mac_go="1.24")
    exe = "go.exe" if sys.platform == "win32" else "go"
    cache = tmp_path / "mod"
    for v in ("1.24.9", "1.24.13", "1.23.4"):
        b = cache / "golang.org" / f"toolchain@v0.0.1-go{v}.{'windows' if sys.platform == 'win32' else 'linux'}-amd64" / "bin"
        b.mkdir(parents=True)
        (b / exe).write_bytes(b"")
    found = goapp.cached_toolchains("1.24", cache)
    assert [v for v, _ in found] == ["1.24.13", "1.24.9"]           # newest first, other series ignored
    versions = {"system": "1.27.0"}
    monkeypatch.setattr(goapp, "_go", lambda c, s: "system")
    monkeypatch.setattr(goapp, "go_version", lambda go: versions.get(go) or Path(go).parent.parent.name.split("-go")[1].rsplit(".", 1)[0])
    monkeypatch.setattr(goapp.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": str(cache)})())
    go, ver = goapp.mac_go(ctx, src)
    assert ver == "1.24.13" and "1.24.13" in go
    versions["system"] = "1.24.2"
    assert goapp.mac_go(ctx, src) == ("system", "1.24.2")           # the system go is of the series: used
    versions["system"] = "1.27.0"
    monkeypatch.setattr(goapp, "cached_toolchains", lambda want, mc=None: [])
    with pytest.raises(ValueError, match="GOTOOLCHAIN=go1.24.0 go version"):
        goapp.mac_go(ctx, src)


def test_the_published_mac_zip_has_the_page_name_and_the_bare_binary_is_not_published(tmp_path, monkeypatch):
    src, ctx, out = _built(tmp_path, mac_zip="<name>-<version>-mac-10.13-x86_64.zip", mac_go="")
    mac = out / "hello-v1.2.3-darwin-amd64-legacy"

    def fake_run(c, cmd, s, name, env=None, timeout=0):
        if name == "mac-build":
            Path(cmd[-1]).write_bytes(b"\xcf\xfa\xed\xfe go1.27.0")
        else:
            d = Path(cmd[-1])
            (d / "x").mkdir(parents=True)
            (d / "x" / "hello-1.2.3-mac-10.13-x86_64.tar.gz").write_bytes(b"tgz")
            (d / "hello-1.2.3-mac-10.13.zip").write_bytes(b"zip")
        return type("R", (), {"ok": True, "stdout": "", "stderr": "", "log": ""})()
    monkeypatch.setattr(goapp, "_run", fake_run)
    (src / "scripts").mkdir()
    (src / "scripts" / "mac.sh").write_text("", encoding="utf-8")
    (src / "scripts" / "pack.py").write_text("", encoding="utf-8")
    ctx.vars.update({"mac_script": "scripts/mac.sh", "mac_package": "scripts/pack.py", "packages": ""})
    r = goapp.stage_mac(ctx)
    assert r["ok"], r
    assert [Path(x["file"]).name for x in r["package"]] == ["hello-1.2.3-mac-10.13-x86_64.zip"]
    goapp.stage_packages(ctx)
    goapp.stage_checksums(ctx)
    sums = relfiles.parse_sums(out / "SHA256SUMS-v1.2.3.txt")
    assert "hello-1.2.3-mac-10.13-x86_64.zip" in sums and mac.name not in sums
    assert goapp.verify_checksums(ctx)["ok"]
    # a SHA256SUMS that lists one file too many (or too few) fails, even with every listed hash right
    f = out / "SHA256SUMS-v1.2.3.txt"
    f.write_text(f.read_text() + f"{goapp._sha(mac)}  {mac.name}\n")
    assert "extra" in goapp.verify_checksums(ctx)["detail"]


def test_the_release_table_is_checked_with_the_checksums(tmp_path):
    src, ctx, out = _built(tmp_path, packages="")
    goapp.stage_packages(ctx)
    goapp.stage_checksums(ctx)
    t = out / "RELEASE-FILES-v1.2.3.md"
    assert "| `hello.exe` | 9 |" in t.read_text() and goapp.verify_checksums(ctx)["ok"]
    t.write_text(t.read_text().replace("| `hello.exe` | 9 |", "| `hello.exe` | 10 |"))
    assert "10 bytes" in goapp.verify_checksums(ctx)["detail"]


# -- xzcheck on every package made, a build from a git checkout, byte equality with the published files (2026-10-25)
def test_every_deb_made_passes_xzcheck_or_the_stage_fails(tmp_path):
    src, ctx, out = _built(tmp_path)
    prev = tmp_path / "prev.deb"
    prev.write_bytes(b"!<arch>\n")
    _nfpm_vars(src, ctx, prev)
    r = goapp.stage_packages(ctx)
    assert r["ok"], r
    assert [x["file"] for x in r["xz"]] == ["hello_1.2.3_amd64.deb"] and r["xz"][0]["ok"]
    assert "xzcheck passed: hello_1.2.3_amd64.deb" in r["detail"] and goapp.verify_packages(ctx)["ok"]
    (src / "scripts" / "dict.txt").write_text(str(8 << 20))         # nfpm's own xz: 8 MiB for a 10 KiB tar
    r = goapp.stage_packages(ctx)
    assert not r["ok"] and r["detail"].startswith("fieldkit xzcheck: hello_1.2.3_amd64.deb:data.tar.xz: dictionary 8")
    assert "bigger than the content needs" in r["detail"]
    (src / "scripts" / "dict.txt").write_text(str(4 << 10))         # too small: compression left on the table
    r = goapp.stage_packages(ctx)
    assert not r["ok"] and "smaller than the content" in r["detail"]


def test_a_package_record_without_a_passing_xzcheck_is_not_up_to_date(tmp_path):
    src, ctx, out = _built(tmp_path)
    prev = tmp_path / "prev.deb"
    prev.write_bytes(b"!<arch>\n")
    _nfpm_vars(src, ctx, prev)
    assert goapp.stage_packages(ctx)["ok"]
    rec = goapp._load(ctx, src, "packages")
    rec.pop("xz")                                                    # a record from before the check
    goapp._save(ctx, src, "packages", rec)
    r = goapp.verify_packages(ctx)
    assert not r["ok"] and "hello_1.2.3_amd64.deb has no passing fieldkit xzcheck" in r["detail"]


def test_the_scripts_packager_checks_its_debs_too(tmp_path):
    src, ctx, out = _built(tmp_path)
    (src / "scripts").mkdir()
    maker = src / "scripts" / "make.py"
    maker.write_text(DEB + "import sys, pathlib\npathlib.Path('Compiled.Builds').mkdir(exist_ok=True)\n"
                     "pathlib.Path('Compiled.Builds/hello_' + sys.argv[1] + '_amd64.deb').write_bytes("
                     "deb(b'ELF', int(sys.argv[2])))\n", encoding="utf-8")
    py = sys.executable.replace("\\", "/")
    for name, size in (("good.sh", GOOD_DICT), ("bad.sh", 8 << 20)):
        (src / "scripts" / name).write_text(f'"{py}" scripts/make.py "$1" {size}\n', encoding="utf-8", newline="\n")
    ctx.vars.update({"packager": "scripts", "packages": "scripts/good.sh"})
    r = goapp.stage_packages(ctx)
    assert r["ok"] and r["xz"][0]["ok"], r
    ctx.vars["packages"] = "scripts/bad.sh"
    r = goapp.stage_packages(ctx)
    assert not r["ok"] and "fieldkit xzcheck" in r["detail"] and "bigger than the content needs" in r["detail"]


def _git(repo, *args):
    import subprocess
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_git_ref_builds_from_a_clean_clone_of_that_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "go.mod").write_text("module example.com/hello\n\ngo 1.21\n", encoding="utf-8")
    (repo / "main.go").write_text("package main\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "main.go").write_text("package main\n// two\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "two")
    (repo / "untracked.txt").write_text("the shared checkout may be dirty", encoding="utf-8")
    p = Pipeline.load(PIPE, state_dir=tmp_path / "state", overrides={"src": str(repo), "git_ref": first[:7]},
                      strict=False)
    ctx = Context(p)
    src = goapp._src(ctx)
    assert src == repo / "dist" / f"git-{first[:12]}" / f"source-{first[:12]}"
    assert _git(src, "rev-parse", "HEAD") == first and "// two" not in (src / "main.go").read_text()
    assert goapp._out(ctx, src) == src.parent and not (src / "untracked.txt").exists()
    assert _git(repo, "worktree", "list").count("\n") == 0            # nothing written into the source repository
    assert goapp._src(ctx) == src                                     # reused, not cloned again
    ctx.vars["out"] = str(tmp_path / "out")
    assert goapp._src(ctx) == tmp_path / "out" / f"source-{first[:12]}"
    (tmp_path / "out" / f"source-{first[:12]}" / "main.go").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="not clean.*vcs.modified"):
        goapp._src(ctx)
    _git(tmp_path / "out" / f"source-{first[:12]}", "checkout", "-q", "--", "main.go")
    _git(tmp_path / "out" / f"source-{first[:12]}", "fetch", "-q", "origin")
    _git(tmp_path / "out" / f"source-{first[:12]}", "checkout", "-q", "--detach", "origin/HEAD")
    with pytest.raises(ValueError, match="is not a checkout of"):     # another commit there: refused, not deleted
        goapp._src(ctx)
    ctx.vars["git_ref"] = "no-such-ref"
    with pytest.raises(ValueError, match="not a commit"):
        goapp._src(ctx)


def test_the_published_stage_proves_byte_equality_or_names_the_difference(tmp_path):
    src, ctx, out = _built(tmp_path)
    assert goapp.stage_published(ctx)["skipped"] and goapp.verify_published(ctx)["ok"]   # opt-in
    pub = tmp_path / "published"
    pub.mkdir()
    for f in ("hello-v1.2.3-linux-amd64", "hello.exe"):
        (pub / f).write_bytes((out / f).read_bytes())
    ctx.vars["compare_with"] = str(pub)
    assert not goapp.verify_published(ctx)["ok"]                      # compare_with set after a skipped run
    r = goapp.stage_published(ctx)
    assert r["ok"] and r["detail"] == "byte for byte the published files: hello-v1.2.3-linux-amd64, hello.exe"
    assert goapp.verify_published(ctx)["ok"]
    (pub / "hello.exe").write_bytes(b"MZ v1.2.4")                     # one byte differs
    r = goapp.stage_published(ctx)
    assert not r["ok"] and "hello.exe" in r["detail"] and "hello-v1.2.3-linux-amd64" not in r["detail"]
    bad = next(x for x in r["results"] if x["file"] == "hello.exe")
    assert not bad["identical"] and bad["findings"] and bad["bytes"] == [9, 9]
    (pub / "hello.exe").unlink()
    r = goapp.stage_published(ctx)
    assert not r["ok"] and "hello.exe: not in" in r["detail"]
    (pub / "hello.exe").write_bytes((out / "hello.exe").read_bytes())
    assert goapp.stage_published(ctx)["ok"]
    rec = goapp._load(ctx, src, "build")
    rec["built"][1]["sha256"] = "0" * 64                              # rebuilt since: compare again
    goapp._save(ctx, src, "build", rec)
    assert "changed since it was compared" in goapp.verify_published(ctx)["detail"]
    ctx.vars["compare_with"] = str(tmp_path / "missing")
    assert "not a folder" in goapp.stage_published(ctx)["detail"]


def test_a_tree_with_crlf_against_its_gitattributes_is_not_built(tmp_path, monkeypatch):
    """v0.1.152: 574 files CRLF in the working tree under eol=lf; git called it clean, the binaries embedded CRLF."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitattributes").write_bytes(b"*.md text eol=lf\n")
    (repo / "go.mod").write_bytes(b"module example.com/hello\n\ngo 1.21\n")
    (repo / "prompt.md").write_bytes(b"line one\nline two\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")
    assert goapp.eol_drift(repo) == [] and goapp.eol_drift(tmp_path / "not-git") == []
    (repo / "prompt.md").write_bytes(b"line one\r\nline two\r\n")
    _git(repo, "add", "-u")                                          # git normalizes: status is clean again
    assert _git(repo, "status", "--porcelain") == ""
    assert goapp.eol_drift(repo) == ["prompt.md"]
    p = Pipeline.load(PIPE, state_dir=tmp_path / "state", overrides={"src": str(repo), "version": "v1.0.0"},
                      strict=False)
    ctx = Context(p)
    monkeypatch.setattr(goapp, "_go", lambda ctx, src: "go-must-not-run")
    r = goapp.stage_build(ctx)
    assert not r["ok"] and r["detail"].startswith("1 file(s) have CRLF") and "prompt.md" in r["detail"]
    assert "git_ref=HEAD" in r["detail"]
    ctx.vars["git_ref"] = "HEAD"                                      # a clean clone has LF: no drift
    assert goapp.eol_drift(goapp._src(ctx)) == []


def test_the_published_stage_counts_line_ends_when_only_the_bytes_differ(tmp_path):
    src, ctx, out = _built(tmp_path)
    pub = tmp_path / "published"
    pub.mkdir()
    (pub / "hello-v1.2.3-linux-amd64").write_bytes((out / "hello-v1.2.3-linux-amd64").read_bytes())
    (pub / "hello.exe").write_bytes(b"MZ\r\nv1.2.3")
    ctx.vars["compare_with"] = str(pub)
    r = goapp.stage_published(ctx)
    bad = next(x for x in r["results"] if x["file"] == "hello.exe")
    assert bad["crlf"] == [0, 1] and "CRLF pairs 0 built vs 1 published" in r["detail"]


def _two_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "go.mod").write_text("module example.com/hello\n\ngo 1.21\n", encoding="utf-8")
    (repo / "main.go").write_text("package main\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "docs")
    return repo, first, _git(repo, "rev-parse", "HEAD")


def test_a_new_git_ref_makes_the_mac_binary_and_the_sums_stale(tmp_path, monkeypatch):
    """2026-10-27: run 2 (git_ref b9ef96e, docs only) kept run 1's Mac binary (vcs.revision 19fcb0b): mixed commits."""
    repo, first, second = _two_commits(tmp_path)
    out = tmp_path / "out"
    p = Pipeline.load(PIPE, state_dir=tmp_path / "state", overrides={"src": str(repo), "git_ref": first,
                                                                     "out": str(out)}, strict=False)
    ctx = Context(p)
    src = goapp._src(ctx)
    mac = out / "hello-darwin"
    mac.write_bytes(b"\xcf\xfa\xed\xfe mac")
    goapp._save(ctx, src, "mac", {"ok": True, "file": str(mac), "sha256": goapp._sha(mac), "detail": "mac",
                                  "commit": goapp.source_commit(src)})
    rev = {"v": first}
    monkeypatch.setattr(goapp, "_go", lambda ctx, src: "go")
    monkeypatch.setattr(goapp, "_buildinfo", lambda go, f: ["path\texample.com/hello", f"build\tvcs.revision={rev['v']}"])
    assert goapp.verify_mac(ctx)["ok"]                                      # the same ref keeps it
    ctx.vars["git_ref"] = second                                            # the same out folder, a new commit
    r = goapp.verify_mac(ctx)
    assert not r["ok"] and f"made from {first[:12]}" in r["detail"] and second[:12] in r["detail"]
    goapp._save(ctx, goapp._src(ctx), "build", {"ok": True, "version": "v1", "built": [], "commit": first})
    r = goapp.verify_checksums(ctx)
    assert not r["ok"] and "mix commits" in r["detail"]
    # the record says the new commit, the binary's build info says the old one: go version -m decides
    goapp._save(ctx, goapp._src(ctx), "mac", {"ok": True, "file": str(mac), "sha256": goapp._sha(mac),
                                              "detail": "mac", "commit": second})
    r = goapp.verify_mac(ctx)
    assert not r["ok"] and f"vcs.revision={first[:12]}" in r["detail"]
    rev["v"] = second
    assert goapp.verify_mac(ctx)["ok"]


def test_a_record_from_before_the_commit_was_recorded_is_stale(tmp_path):
    repo, _, second = _two_commits(tmp_path)
    assert goapp.stale({"ok": True}, repo, "x") .startswith("x was made from an unrecorded commit")
    assert goapp.stale({"commit": second}, repo, "x") is None
    assert goapp.stale({}, tmp_path / "not-git", "x") is None                 # outside git: nothing to compare


def test_a_missing_goapps_entry_names_the_exact_line(monkeypatch):
    local = {"sync": {"repos": [{"path": "D:/work/Gorilla.Opencode"}]}}
    assert goapp.src_line("gorilla-opencode", local) == '{"goapps": {"gorilla-opencode": "D:/work/Gorilla.Opencode"}}'
    assert "FULL/PATH/TO/THE/CLONE" in goapp.src_line("gorilla-opencode", {})
    row = goapp.doctor_row("gorilla-opencode", local)
    assert not row["ok"] and row["install"] == ('add to fieldkit.local.json: '
                                                '{"goapps": {"gorilla-opencode": "D:/work/Gorilla.Opencode"}}')
    assert goapp.doctor_row("gorilla-opencode", {"goapps": {"gorilla-opencode": "D:/x"}})["ok"]
    assert goapp.doctor_row("no-such-pipeline", local) is None
    monkeypatch.setattr(goapp.settings, "local_settings", lambda: local)

    class Ctx:
        vars = {"name": "gorilla-opencode"}
    with pytest.raises(ValueError, match='add to fieldkit.local.json: {"goapps": {"gorilla-opencode": "D:/work/'):
        goapp._src(Ctx)
