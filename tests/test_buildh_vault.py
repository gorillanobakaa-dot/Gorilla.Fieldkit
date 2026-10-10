"""build-harness: latest stable only, and a vault that stays untouched and restores cleanly."""
import hashlib
import io
import os
import stat
import subprocess
import tarfile

import pytest

from fieldkit.buildh import upstream, vault


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def mozilla(tmp_path):
    """A stand-in for github.com/mozilla-firefox/firefox with an old and a new release tag, and a newer main."""
    r = tmp_path / "upstream"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "browser").mkdir()
    (r / "browser" / "version.txt").write_text("156.0\n")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "156")
    _git(r, "tag", "FIREFOX_156_0_RELEASE")
    (r / "browser" / "version.txt").write_text("157.0\n")
    (r / "dom.cpp").write_text("int x;\n")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "157")
    _git(r, "tag", "FIREFOX_157_0_RELEASE")
    (r / "browser" / "version.txt").write_text("159.0a1\n")               # main moves on: Nightly
    _git(r, "commit", "-q", "-am", "nightly")
    return r


def _info(mozilla):
    return upstream.latest_firefox(versions={"LATEST_FIREFOX_VERSION": "157.0"}, repo=mozilla.as_uri())


def test_stable_versions_only():
    assert upstream.firefox_tag("157.0") == "FIREFOX_157_0_RELEASE"
    assert upstream.firefox_tag("155.0.1") == "FIREFOX_155_0_1_RELEASE"
    for bad in ("159.0a1", "158.0b1", "140.17.0esr", ""):
        with pytest.raises(ValueError, match="not a stable"):
            upstream.firefox_tag(bad)


def test_latest_firefox_is_the_release_tag_not_main(mozilla):
    info = _info(mozilla)
    tag_commit = subprocess.run(["git", "-C", str(mozilla), "rev-parse", "FIREFOX_157_0_RELEASE"],
                                capture_output=True, text=True).stdout.strip()
    main_commit = subprocess.run(["git", "-C", str(mozilla), "rev-parse", "main"], capture_output=True, text=True).stdout.strip()
    assert info["tag"] == "FIREFOX_157_0_RELEASE" and info["commit"] == tag_commit != main_commit


def test_missing_tag_is_refused(mozilla):
    with pytest.raises(ValueError, match="has no tag"):
        upstream.latest_firefox(versions={"LATEST_FIREFOX_VERSION": "158.0"}, repo=mozilla.as_uri())


def test_latest_kernel_is_stable_with_its_tarball():
    rel = {"latest_stable": {"version": "7.2.8"},
           "releases": [{"version": "7.3-rc2", "moniker": "mainline", "source": "x"},
                        {"version": "7.2.8", "moniker": "stable",
                         "source": "https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.8.tar.xz"}]}
    k = upstream.latest_kernel(rel)
    assert k["version"] == "7.2.8" and k["sums"].endswith("/v7.x/sha256sums.asc")


def test_vault_fetch_verify_and_it_is_read_only(mozilla, tmp_path):
    base = tmp_path / "vault"
    out = vault.fetch_firefox(_info(mozilla), base=base)
    d = base / "firefox" / "157.0"
    assert out["status"] == "fetched" and (d / "browser" / "version.txt").read_text() == "157.0\n"
    assert not os.stat(d / "dom.cpp").st_mode & 0o222          # no write bit (os.access says writable to root)
    assert vault.verify("firefox", base=base)["intact"]
    assert vault.fetch_firefox(_info(mozilla), base=base)["status"] == "already in the vault"


def test_vault_damage_is_detected(mozilla, tmp_path):
    base = tmp_path / "vault"
    vault.fetch_firefox(_info(mozilla), base=base)
    f = base / "firefox" / "157.0" / "dom.cpp"
    os.chmod(f, stat.S_IREAD | stat.S_IWRITE)
    f.write_text("int x = 1; // a model was here\n")
    v = vault.verify("firefox", base=base)
    assert not v["intact"] and "dom.cpp" in v["problems"][0]
    with pytest.raises(RuntimeError, match="vault itself is damaged"):
        vault.restore("firefox", tmp_path / "work", base=base)


def test_restore_makes_a_fresh_writable_copy_and_leaves_the_vault_alone(mozilla, tmp_path):
    base = tmp_path / "vault"
    vault.fetch_firefox(_info(mozilla), base=base)
    out = vault.restore("firefox", tmp_path / "work", base=base)
    w = tmp_path / "work"
    assert (w / "dom.cpp").read_text() == "int x;\n" and os.access(w / "dom.cpp", os.W_OK)
    (w / "dom.cpp").write_text("changed\n")
    assert vault.verify("firefox", base=base)["intact"] and out["version"] == "157.0"
    with pytest.raises(FileExistsError):
        vault.restore("firefox", w, base=base)
    with pytest.raises(ValueError, match="never be inside the vault"):
        vault.restore("firefox", base / "firefox" / "scratch", base=base)


def test_vault_and_working_copy_hold_upstreams_bytes_whatever_this_machines_git_says(mozilla, tmp_path, monkeypatch):
    """2026-10-09, GitHub's Windows runners: core.autocrlf=true checked every file out with CRLF, so the vault and
    the working copies no longer matched upstream and the snapshot proof failed. The clones pin it off."""
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    base = tmp_path / "vault"
    vault.fetch_firefox(_info(mozilla), base=base)
    assert (base / "firefox" / "157.0" / "dom.cpp").read_bytes() == b"int x;\n"
    vault.restore("firefox", tmp_path / "work", base=base)
    assert (tmp_path / "work" / "dom.cpp").read_bytes() == b"int x;\n"


def _kernel_server(tmp_path, tamper=False):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as t:
        data = b"VERSION = 7\n"
        ti = tarfile.TarInfo("linux-7.2.8/Makefile")
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    tar = buf.getvalue()
    sums = f"{hashlib.sha256(tar + (b'x' if tamper else b'')).hexdigest()}  linux-7.2.8.tar.xz\n".encode()
    files = {"linux-7.2.8.tar.xz": tar, "sha256sums.asc": sums}

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return lambda url, timeout=None: R(files[url.rsplit("/", 1)[1]])


KINFO = {"product": "kernel", "version": "7.2.8", "tarball": "https://cdn.example/v7.x/linux-7.2.8.tar.xz",
         "sums": "https://cdn.example/v7.x/sha256sums.asc", "source": "https://www.kernel.org"}


def test_kernel_vault_checks_sha256_and_restores(tmp_path):
    base = tmp_path / "vault"
    out = vault.fetch_kernel(KINFO, base=base, opener=_kernel_server(tmp_path))
    assert out["status"] == "fetched" and vault.verify("kernel", base=base)["intact"]
    r = vault.restore("kernel", tmp_path / "work", base=base)
    assert (tmp_path / "work" / "linux-7.2.8" / "Makefile").read_text() == "VERSION = 7\n" and r["sha256"]


def test_kernel_tarball_with_wrong_sha256_never_enters_the_vault(tmp_path):
    base = tmp_path / "vault"
    with pytest.raises(RuntimeError, match="does not match"):
        vault.fetch_kernel(KINFO, base=base, opener=_kernel_server(tmp_path, tamper=True))
    assert vault.listing(base) == []


def test_measuring_never_downloads(mozilla):
    seen = []

    class Head:
        headers = {"Content-Length": "813871236"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        seen.append(req.get_method())
        return Head()
    m = vault.measure_firefox(_info(mozilla), opener=opener)
    assert seen == ["HEAD"] and m["bytes"] == 813871236 and m["archive"].endswith("firefox-157.0.source.tar.xz")
