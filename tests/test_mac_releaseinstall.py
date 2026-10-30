# Version: 1.2.0 · updated 26-10-28-09-15
"""fieldkit mac release-install: older copies of the program are MOVED (never deleted) with a manifest, the data and
other programs are left alone, and the download comes from the release page and nowhere else."""
import os
import subprocess
import sys

import pytest

from fieldkit.mac import releaseinstall as R

# The script is bash, run on a Mac: only the tests that RUN it skip on Windows; the ones that read it run everywhere
# (2026-10-28: fieldkit mac runs on Windows too).
needs_bash = pytest.mark.skipif(sys.platform == "win32", reason="runs the bash script (it runs on a Mac)")


def step1(text):
    """The script's definitions plus its quarantine step, nothing downloaded."""
    head = text.split('step "1 ')[0]
    quarantine = text.split('step "3 quarantine')[1].split("\nmkdir -p \"$DL\"")[0]
    return head + 'step "3 quarantine' + quarantine


def fake_bin(tmp_path, sums_text, asset_bytes=b"zip"):
    """curl that writes a given asset and checksum file, so steps 1-2 run off the Mac."""
    b = tmp_path / "bin"
    b.mkdir()
    (b / "sums").write_text(sums_text, newline="")
    (b / "asset").write_bytes(asset_bytes)
    (b / "curl").write_text('#!/bin/sh\nout=$3; case "$out" in SHA256SUMS*) cp "%s/sums" "$out";; *) cp "%s/asset" "$out";; esac\n'
                            % (b, b))
    (b / "curl").chmod(0o755)
    return b


@needs_bash
def test_older_copies_are_moved_with_a_manifest_and_nothing_else_is_touched(tmp_path):
    home = tmp_path
    for d in [".local/bin", "Applications/Gorilla OpenCode.app/Contents", "Desktop", "Downloads/gorilla-0.1.149-dev",
              "Documents/mac gorilla.opencode 0.1.149-dev", "Applications/Gorilla-Test3/Gorilla Unleashed.app",
              ".local/share/gorilla-opencode", "Downloads/Agents.Work.Trash-old/gorilla-opencode-0.1.1-mac"]:
        (home / d).mkdir(parents=True)
    (home / ".local/bin/gorilla-opencode").write_text("old")
    (home / "Desktop/Gorilla OpenCode").symlink_to(home / "Applications/Gorilla OpenCode.app")
    (home / "Downloads/gorilla-opencode-0.1.149-fresh-mac-10.13-x86_64.tar.gz").write_text("tgz")
    (home / ".local/share/gorilla-opencode/gorilla-opencode.db").write_text("conversations")
    (home / "Downloads/gorilla-air-test4.zip").write_text("the browser, not this program")

    text = step1(R.script("opencode", "v0.1.150", "2610231345"))
    r = subprocess.run(["bash", "-c", text], env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

    q = home / "Agents.Work.Trash-linux/opencode-old-versions-2610231345"
    manifest = (q / "MANIFEST.txt").read_text()
    for gone in [".local/bin/gorilla-opencode", "Applications/Gorilla OpenCode.app", "Desktop/Gorilla OpenCode",
                 "Downloads/gorilla-0.1.149-dev", "Documents/mac gorilla.opencode 0.1.149-dev",
                 "Downloads/gorilla-opencode-0.1.149-fresh-mac-10.13-x86_64.tar.gz"]:
        assert not os.path.lexists(home / gone), f"{gone} is still installed"
        assert str(home / gone) in manifest, f"{gone} was moved without a manifest line"
    assert len(list(q.iterdir())) == 7   # six moved items and the manifest: moved, not deleted
    # data, another program, and an existing quarantine folder are left alone
    assert (home / ".local/share/gorilla-opencode/gorilla-opencode.db").read_text() == "conversations"
    assert (home / "Applications/Gorilla-Test3/Gorilla Unleashed.app").is_dir()
    assert (home / "Downloads/gorilla-air-test4.zip").exists()
    assert (home / "Downloads/Agents.Work.Trash-old/gorilla-opencode-0.1.1-mac").is_dir()


@needs_bash
def test_nothing_older_leaves_no_empty_quarantine_folder(tmp_path):
    r = subprocess.run(["bash", "-c", step1(R.script("opencode", "v0.1.150", "x"))],
                       env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True)
    assert r.returncode == 0 and "nothing older was installed" in r.stdout
    assert not (tmp_path / "Agents.Work.Trash-linux/opencode-old-versions-x").exists()


def test_the_download_is_the_release_page_and_is_checked_before_install():
    text = R.script("opencode", "v0.1.150", "x")
    url = "https://github.com/gorillanobakaa-dot/Gorilla.Opencode/releases/download/v0.1.150/"
    assert f'"{url}gorilla-opencode-0.1.150-mac-10.13-x86_64.zip"' in text
    assert f'"{url}SHA256SUMS-v0.1.150.txt"' in text
    assert text.index("shasum -a 256 -c") < text.index("Install Gorilla OpenCode.command\"")
    assert "rm -rf" not in text and "rm -f" not in text   # quarantine, never delete
    assert 'open "$HOME/Desktop/' not in text and 'open "$HOME/Desktop/' in R.script("opencode", "v0.1.150", "x", launch=True)


def test_latest_reads_the_release_page():
    import io
    import json

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = []
    assert R.latest_tag("o/r", opener=lambda u, timeout: seen.append(u) or Resp(json.dumps({"tag_name": "v9"}).encode())) == "v9"
    assert seen == ["https://api.github.com/repos/o/r/releases/latest"]


@pytest.mark.skipif(not __import__("shutil").which("shasum"), reason="needs shasum (perl)")
@needs_bash
def test_a_bad_download_leaves_the_installed_version_where_it_was(tmp_path):
    """2026-10-25: the Air lost v0.1.151 because quarantine ran before the checksum failed."""
    home = tmp_path / "home"
    (home / ".local/bin").mkdir(parents=True)
    (home / "Downloads").mkdir()
    (home / ".local/bin/gorilla-opencode").write_text("old")
    b = fake_bin(tmp_path, "0" * 64 + "  gorilla-opencode-0.1.152-mac-10.13-x86_64.zip\r\n")
    r = subprocess.run(["bash", "-c", R.script("opencode", "v0.1.152", "x")], capture_output=True, text=True,
                       env={**os.environ, "HOME": str(home), "TMPDIR": str(tmp_path), "PATH": f"{b}:{os.environ['PATH']}"})
    assert r.returncode == 1 and "nothing older was moved" in r.stdout, r.stdout + r.stderr
    assert (home / ".local/bin/gorilla-opencode").read_text() == "old"
    assert not (home / "Agents.Work.Trash-linux").exists()


@pytest.mark.skipif(not __import__("shutil").which("shasum"), reason="needs shasum (perl)")
@needs_bash
def test_a_checksum_file_with_windows_line_ends_is_read(tmp_path):
    """v0.1.152's SHA256SUMS was written on Windows (CRLF); macOS 10.13's shasum refused every line."""
    import hashlib
    asset = b"the zip"
    b = fake_bin(tmp_path, hashlib.sha256(asset).hexdigest() + "  gorilla-opencode-0.1.152-mac-10.13-x86_64.zip\r\n",
                 asset)
    text = R.script("opencode", "v0.1.152", "x").split('ditto -x -k')[0] + "echo VERIFIED\n"
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run(["bash", "-c", text], capture_output=True, text=True,
                       env={**os.environ, "HOME": str(home), "TMPDIR": str(tmp_path), "PATH": f"{b}:{os.environ['PATH']}"})
    assert r.returncode == 0 and "VERIFIED" in r.stdout, r.stdout + r.stderr


def test_quarantine_comes_after_the_checksum():
    text = R.script("opencode", "v0.1.152", "x")
    assert text.index("shasum -a 256 -c") < text.index('step "3 quarantine') < text.index("for p in ")
