# Version: 1.0.0 · updated 26-10-16-00-20
"""Browser profile data never reaches a public repository (owner 2026-10-16: "make sure you don't upload my
personal data like my bookmarks and history ... those are mine, private"). Fake data only."""
import subprocess

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
    assert "bookmarks-export" in kinds("export.html", b"<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL>")
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
    assert "/opt/fieldkit/bin/fieldkit privacy push-check . --range" in text and p.stat().st_mode & 0o111
    p.write_text("#!/bin/sh\nexit 0\n")
    try:
        privacy.install_hook(tmp_path, "/opt/fieldkit/bin/fieldkit")
        assert False, "overwrote a foreign hook"
    except ValueError:
        pass
