# Version: 1.0.0 · updated 26-10-16-00-20
"""Browser profile data never reaches a public repository (owner 2026-10-16: "make sure you don't upload my
personal data like my bookmarks and history ... those are mine, private"). Fake data only."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.core import privacy


def test_profile_files_are_found_by_name_and_by_content():
    kinds = lambda rel, data=b"": {f["kind"] for f in privacy.profile_findings(rel, data)}
    assert "browser-profile-data" in kinds("prof/abc.default/places.sqlite")
    assert "browser-profile-data" in kinds("x/logins.json")
    assert "browser-profile-data" in kinds("x/key4.db")
    assert "browser-profile-data" in kinds("x/sessionstore-backups/recovery.jsonlz4")
    assert "browser-profile-data" in kinds("x/bookmarkbackups/bookmarks-2026-10-16.jsonlz4")
    assert "browser-profile-data" in kinds("Default/Login Data")
    assert "sqlite-database" in kinds("notes.bin", b"SQLite format 3\x00rest")
    assert "browser-profile-data" in kinds("blob", b"mozLz40\x00rest")
    head = b"<!DOCTYPE NETSCAPE-" + b"Bookmark-file-1>"          # split: the scanner reads this file too
    assert "bookmarks-export" in kinds("export.html", head + b"\n<DL>")
    assert "bookmarks-export" not in kinds("notes.py", b"x = 1\n# mentions " + head + b" in passing")
    assert not kinds("browser/base/content/browser-places.js", b"// code that handles places")
    assert not kinds("toolkit/components/places/Database.cpp")


def test_a_patch_that_writes_a_profile_file_is_caught():
    patch = b"diff --git a/profile/places.sqlite b/profile/places.sqlite\nGIT binary patch\n"
    assert any(f["kind"] == "browser-profile-data-in-patch" for f in privacy.profile_findings("p/001-x.patch", patch))


def test_known_public_names_are_not_reported_as_emails():
    t = "about-logo@2x.png default-theme@mozilla.org C:\\Users\\Default User\\Downloads\\readme.txt"
    assert privacy.scan_text(t) == []


def test_push_check_scans_the_pushed_commit_and_refuses_profile_data(tmp_path):
    g = lambda *a: subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                  check=True, capture_output=True, text=True).stdout
    (tmp_path / "README.md").write_text("clean\n")
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "base")
    base = g("rev-parse", "HEAD").strip()
    (tmp_path / "places.sqlite").write_bytes(b"SQLite format 3\x00fake")
    (tmp_path / "code.js").write_text("let a = 1;\n")
    g("add", "-A")
    g("commit", "-qm", "oops")
    r = privacy.push_check(tmp_path, f"{base}..HEAD", terms=[])
    assert r["files"] == 2 and r["profile"] == ["places.sqlite"] and "code.js" not in r["report"]
    (tmp_path / "places.sqlite").unlink()
    g("commit", "-qam", "removed again")
    assert privacy.push_check(tmp_path, "HEAD~1..HEAD", terms=[])["report"] == {}


def test_the_hook_is_installed_in_the_repository_and_never_overwrites_a_foreign_one(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    p = privacy.install_hook(tmp_path, "/opt/fieldkit/bin/fieldkit")
    text = p.read_text()
    assert "/opt/fieldkit/bin/fieldkit privacy push-check . --range" in text
    assert sys.platform == "win32" or p.stat().st_mode & 0o111     # Git for Windows runs hooks without the bit
    p.write_text("#!/bin/sh\nexit 0\n")
    try:
        privacy.install_hook(tmp_path, "/opt/fieldkit/bin/fieldkit")
        assert False, "overwrote a foreign hook"
    except ValueError:
        pass


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh (Git for Windows ships it; every Linux has it)")
def test_the_installed_hook_really_runs_the_check_from_a_windows_path_with_a_space(tmp_path):
    # 2026-10-16: on Windows the hook held C:\Users\...\fieldkit.EXE unquoted; Git's sh read it as C:Users... and
    # refused every push with "command not found". The fake fieldkit records what the hook passed it.
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    fake = tmp_path / "fake fk" / "fieldkit"
    fake.parent.mkdir()
    out = tmp_path / "args.txt"
    fake.write_text(f'#!/bin/sh\necho "$@" > "{out.as_posix()}"\nexit 0\n', newline="\n")
    fake.chmod(0o755)
    hook = privacy.install_hook(repo, str(fake))           # backslashes on Windows, a space everywhere
    a, b = "1" * 40, "2" * 40
    # bytes: Git hands the hook LF lines; text mode on Windows would send CRLF and put a CR into the range
    r = subprocess.run(["sh", hook.as_posix(), "origin", "x"], cwd=repo,
                       input=f"refs/heads/m {b} refs/heads/m {a}\n".encode(), capture_output=True)
    assert r.returncode == 0, (r.stdout + r.stderr).decode(errors="replace")
    assert out.read_text().split() == ["privacy", "push-check", ".", "--range", f"{a}..{b}"]


def test_a_line_added_then_removed_inside_the_push_is_still_found(tmp_path):
    # 2026-10-16: the check read only the last commit, but a push publishes every commit
    g = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    g("init", "-q")
    g("config", "user.name", "t")
    g("config", "user.email", "t@example.com")
    (tmp_path / "a.txt").write_text("clean\n")
    g("add", "-A")
    g("commit", "-qm", "one")
    (tmp_path / "a.txt").write_text("clean\npath C:" + "\\Users\\" + "realperson\\notes\n")
    g("commit", "-qam", "two")
    (tmp_path / "a.txt").write_text("clean\n")
    g("commit", "-qam", "three: removed again")
    r = privacy.push_check(tmp_path, "HEAD~2..HEAD", terms=[])["report"]
    assert any(k.endswith(":a.txt") and any(h["kind"] == "windows-user-path" for h in v) for k, v in r.items()), r


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh (Git for Windows ships it; every Linux has it)")
def test_the_hook_checks_a_new_repository_from_the_empty_tree_first_commit_included(tmp_path):
    # 2026-10-16 (gorilla-size's first push): with no remote yet the hook used root..tip, which leaves the root
    # commit's own files out of the check
    repo = tmp_path / "repo"
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    g("config", "user.name", "t")
    g("config", "user.email", "t@example.com")
    (repo / "first.txt").write_text("one\n")
    g("add", "-A")
    g("commit", "-qm", "root")
    (repo / "second.txt").write_text("two\n")
    g("add", "-A")
    g("commit", "-qm", "two")
    tip = g("rev-parse", "HEAD").stdout.strip()
    empty = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "tree", "--stdin"], input=b"",
                           capture_output=True).stdout.decode().strip()
    fake = tmp_path / "fk" / "fieldkit"
    fake.parent.mkdir()
    out = tmp_path / "args.txt"
    fake.write_text(f'#!/bin/sh\necho "$@" > "{out.as_posix()}"\nexit 0\n', newline="\n")
    fake.chmod(0o755)
    hook = privacy.install_hook(repo, str(fake))
    r = subprocess.run(["sh", hook.as_posix(), "origin", "x"], cwd=repo,
                       input=f"refs/heads/main {tip} refs/heads/main {'0' * 40}\n".encode(), capture_output=True)
    assert r.returncode == 0, (r.stdout + r.stderr).decode(errors="replace")
    assert out.read_text().split()[-1] == f"{empty}..{tip}", out.read_text()


def test_vendored_crate_author_emails_pass_but_nothing_else_does(tmp_path):
    # 2026-10-16 (gorilla-size): crate manifests in vendor/ name their authors; only those emails, only in a folder
    # PROVEN vendored by .cargo-checksum.json, are let through. Secrets and user paths there still fail.
    g = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    g("init", "-q")
    g("config", "user.name", "t")
    g("config", "user.email", "t@example.com")
    (tmp_path / "README.md").write_text("start\n")
    g("add", "-A")
    g("commit", "-qm", "base")
    crate = tmp_path / "vendor" / "itoa"
    crate.mkdir(parents=True)
    (crate / ".cargo-checksum.json").write_text('{"files":{}}')
    (crate / "Cargo.toml").write_text('authors = ["Upstream Author <author@' + 'realdomain.io>"]\n')
    fake = tmp_path / "notvendor" / "x"
    fake.mkdir(parents=True)
    (fake / "Cargo.toml").write_text('authors = ["Someone <someone@' + 'realdomain.io>"]\n')
    leak = tmp_path / "vendor" / "itoa" / "build.rs"
    leak.write_text('// C:' + '\\Users\\' + 'realperson\\secrets\n')
    g("add", "-A")
    g("commit", "-qm", "vendored")
    r = privacy.push_check(tmp_path, "HEAD~1..HEAD", terms=[])["report"]
    assert "vendor/itoa/Cargo.toml" not in r, r
    assert "notvendor/x/Cargo.toml" in r, "an email outside a proven vendored crate still fails"
    assert any(h["kind"] == "windows-user-path" for h in r["vendor/itoa/build.rs"]), "user paths in vendor still fail"
    on_disk = privacy.scan_path(tmp_path, terms=[])
    keys = {Path(k).relative_to(tmp_path).as_posix() for k in on_disk}
    assert "vendor/itoa/Cargo.toml" not in keys and "notvendor/x/Cargo.toml" in keys, keys
