# Version: 1.0.1 · updated 26-10-15-23-26
"""deb-check and the install stage: the 2026-10-15 "Gorilla Unleashed 154" package, rebuilt in miniature."""
import struct
import subprocess
import sys
import zlib

import pytest

from fieldkit.build import deb_check, deb_install

# the fixture builds a real .deb with dpkg-deb, and the check reads it back with dpkg-deb: a Debian-family tool
LINUX_DEB = pytest.mark.skipif(sys.platform == "win32",
                               reason="Linux-only tool: dpkg-deb builds and reads the .deb; Windows has none")


def _png(w, h):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


GOOD = "Gorilla Unleashed 158"
FIELDS = (f"X-GNOME-FullName={GOOD}\nComment={GOOD} web browser\nKeywords=Gorilla;Unleashed;158;Internet;\n")


def _deb(tmp_path, name="Gorilla Unleashed 154", extra="", remoting="gorilla-unleashed", postinst="gtk-update-icon-cache -f\n", bad_icon=None, exec_ok=True,
         logo=None, logo2x=False):
    st = tmp_path / "stage"
    (st / "DEBIAN").mkdir(parents=True)
    (st / "DEBIAN" / "control").write_text("Package: gorilla-unleashed\nVersion: 158.0-1\nArchitecture: amd64\n"
                                           "Maintainer: t <t@t>\nDescription: t\n")
    (st / "DEBIAN" / "postinst").write_text("#!/bin/sh\n" + postinst)
    (st / "DEBIAN" / "postinst").chmod(0o755)
    app = st / "usr" / "lib" / "gorilla-unleashed"
    app.mkdir(parents=True)
    if exec_ok:
        (app / "firefox").write_text("#!/bin/sh\n")
        (app / "gorilla-unleashed").write_text("#!/bin/sh\n")
    (app / "application.ini").write_text(f"[App]\nRemotingName={remoting}\n")
    master = _png(1400, 1400)
    br_logo = app / "browser" / "chrome" / "browser" / "content" / "branding"
    br_logo.mkdir(parents=True)
    (br_logo / "about-logo.png").write_bytes(master if logo is None else logo)
    if logo2x:
        (br_logo / deb_check.TWO_X).write_bytes(_png(1000, 1000))
    apps = st / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "gorilla-unleashed.desktop").write_text(
        f"[Desktop Entry]\nName={name}\n{extra}Exec=env X=1 /usr/lib/gorilla-unleashed/gorilla-unleashed %u\n"
        "Icon=gorilla-unleashed\nStartupWMClass=gorilla-unleashed\n"
        "\n[Desktop Action new-window]\nName=Open a New Window\nExec=/usr/lib/gorilla-unleashed/firefox\n")
    for sz in deb_check.SIZES:
        d = st / "usr" / "share" / "icons" / "hicolor" / f"{sz}x{sz}" / "apps"
        d.mkdir(parents=True)
        w, h = (2598, 2626) if sz == bad_icon else (sz, sz)
        (d / "gorilla-unleashed.png").write_bytes(_png(w, h))
    deb = tmp_path / "gorilla-unleashed_158.0-1_amd64.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "-b", str(st), str(deb)], check=True, capture_output=True)
    br = tmp_path / "branding"
    br.mkdir()
    (br / "configure.sh").write_text('MOZ_APP_DISPLAYNAME="Gorilla Unleashed"\n')
    (br / "content").mkdir()
    (br / "content" / "about-logo.png").write_bytes(master)
    return deb, br


def test_png_size_reads_the_header():
    assert deb_check.png_size(_png(48, 48)) == (48, 48)
    assert deb_check.png_size(b"not a png at all, long enough") is None


@LINUX_DEB
def test_the_158_package_as_first_built_is_refused(tmp_path):
    deb, br = _deb(tmp_path)
    r = deb_check.check(deb, br)
    assert not r["ok"]
    assert any("Name=Gorilla Unleashed 154" in p for p in r["problems"])        # the 154 the template carried
    assert any("update-desktop-database" in p for p in r["problems"])
    assert not any(p.startswith("icons") for p in r["problems"])        # the icons were already right


@LINUX_DEB
def test_a_raw_non_square_master_in_a_size_folder_is_caught(tmp_path):
    deb, br = _deb(tmp_path, name=GOOD, extra=FIELDS, bad_icon=1024,
                   postinst="gtk-update-icon-cache\nupdate-desktop-database\n")
    r = deb_check.check(deb, br)
    assert [p for p in r["problems"]] == [
        "icons: /usr/share/icons/hicolor/1024x1024/apps/gorilla-unleashed.png is 2598x2626 pixels, not 1024x1024"]


@LINUX_DEB
def test_a_launcher_missing_from_the_package_is_caught(tmp_path):
    deb, br = _deb(tmp_path, name=GOOD, extra=FIELDS, exec_ok=False,
                   postinst="gtk-update-icon-cache\nupdate-desktop-database\n")
    assert any(p.startswith("exec:") for p in deb_check.check(deb, br)["problems"])


@LINUX_DEB
def test_the_fixed_package_passes(tmp_path):
    deb, br = _deb(tmp_path, name=GOOD, extra=FIELDS, postinst="gtk-update-icon-cache\nupdate-desktop-database\n")
    assert deb_check.check(deb, br)["ok"]


@LINUX_DEB
def test_the_name_without_the_version_is_refused(tmp_path):
    # D-158-05: "Gorilla Unleashed followed by the version"; the branding name alone (this check's first rule) is wrong
    deb, br = _deb(tmp_path, name="Gorilla Unleashed", postinst="gtk-update-icon-cache\nupdate-desktop-database\n")
    probs = deb_check.check(deb, br)["problems"]
    assert any("Name=Gorilla Unleashed ," in p and "wants Gorilla Unleashed 158" in p for p in probs)
    assert any("hover tooltip" in p for p in probs) and any("desktop search" in p for p in probs)


def test_without_dpkg_deb_the_check_says_so_and_never_crashes(tmp_path, monkeypatch):
    monkeypatch.setattr(deb_check.shutil, "which", lambda name: None)
    r = deb_check.check(tmp_path / "x.deb", tmp_path)
    assert not r["ok"] and r["problems"] == ["dpkg-deb is not installed: a .deb is checked only on a Debian-family Linux"]
    assert "NOT OK" in deb_check.lines(r)[-1]


def test_expected_name_is_branding_plus_major_version(tmp_path):
    (tmp_path / "configure.sh").write_text('MOZ_APP_DISPLAYNAME="Gorilla Unleashed"\n')
    assert deb_check.expected_name(tmp_path, "158.0-1") == "Gorilla Unleashed 158"
    assert deb_check.expected_name(tmp_path, "") is None


def test_install_never_asks_for_a_password(tmp_path):
    (tmp_path / "gorilla-unleashed_158.0-1_amd64.deb").write_bytes(b"x")
    calls = []

    def run(cmd, timeout=None):
        calls.append(cmd)
        return (1, "a password is required") if cmd[:2] == ["sudo", "-n"] else (0, "")

    class C:
        vars = {"release": str(tmp_path)}
    r = deb_install.stage_install(C, run=run)
    assert not r["ok"] and "a person runs: sudo /usr/bin/apt-get install" in r["detail"]
    assert len(calls) == 1                                               # nothing ran after sudo -n said no


def test_install_reinstalls_a_repackaged_same_version(tmp_path):
    (tmp_path / "gorilla-unleashed_158.0-1_amd64.deb").write_bytes(b"x")
    calls = []

    def run(cmd, timeout=None):
        calls.append(cmd)
        return 0, ""

    class C:
        vars = {"release": str(tmp_path)}
    deb_install.stage_install(C, run=run)
    assert "--reinstall" in calls[-1]


@LINUX_DEB
def test_firefox_default_identity_is_refused(tmp_path):
    deb, br = _deb(tmp_path, name=GOOD, extra=FIELDS, remoting="firefox-default",
                   postinst="gtk-update-icon-cache\nupdate-desktop-database\n")
    probs = deb_check.check(deb, br)["problems"]
    assert probs == ["identity: application.ini RemotingName=firefox-default, the entry's StartupWMClass="
                     "gorilla-unleashed: a plain launch shows under the wrong name"]


@LINUX_DEB
def test_a_logo_rewritten_while_packaging_is_caught(tmp_path):
    """D-157-35, 2026-10-15: build_deb.sh wrote a 500 px logo over the 1400 px master and added an @2x copy."""
    deb, br = _deb(tmp_path, name=GOOD, extra=FIELDS, postinst="gtk-update-icon-cache\nupdate-desktop-database\n",
                   logo=_png(500, 500), logo2x=True)
    probs = [p for p in deb_check.check(deb, br)["problems"] if p.startswith("logo")]
    assert any("500 px" in p and "1400 px" in p for p in probs)
    assert any(deb_check.TWO_X in p for p in probs)
