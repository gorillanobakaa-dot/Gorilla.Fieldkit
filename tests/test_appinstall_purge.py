from __future__ import annotations

import json
import struct
from pathlib import Path

from fieldkit.method import appinstall, installverify


def _make_fake_antigravity_source(root: Path) -> Path:
    src = root / "Antigravity-x64"
    (src / "bin").mkdir(parents=True)
    (src / "resources" / "app").mkdir(parents=True)

    # Real 64-bit ELF magic + minimal header so main_elf check passes
    elf_magic = b"\x7fELF\x02\x01\x01" + b"\x00" * 57
    main_bin = src / "antigravity"
    main_bin.write_bytes(elf_magic)
    main_bin.chmod(0o755)

    cli_bin = src / "bin" / "antigravity"
    cli_bin.write_bytes(b"#!/bin/sh\necho 'Antigravity CLI 1.107.0'\n")
    cli_bin.chmod(0o755)

    pkg_json = {
        "name": "antigravity",
        "productName": "Antigravity",
        "version": "1.107.0",
        "distroVersion": "44.9.1",
    }
    (src / "resources" / "app" / "package.json").write_text(
        json.dumps(pkg_json), encoding="utf-8"
    )
    return src


def test_generated_icons_are_valid_rgba_png_svg_and_ico():
    for sz in appinstall.HICOLOR_SIZES:
        png = appinstall.make_rgba_png(sz)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        w, h, bit_depth, color_type = struct.unpack(">IIBB", png[16:26])
        assert (w, h) == (sz, sz)
        assert bit_depth == 8
        assert color_type == 6  # RGBA

    svg = appinstall.make_svg("Antigravity")
    assert "<svg" in svg and "</svg>" in svg

    ico = appinstall.make_ico()
    reserved, ico_type, count = struct.unpack("<HHH", ico[:6])
    assert reserved == 0
    assert ico_type == 1
    assert count == len(appinstall.WIN_ICO_SIZES)


def test_linux_install_verify_and_purge_lifecycle(tmp_path: Path):
    src = _make_fake_antigravity_source(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    # Preserve existing ~/.local/bin/agy wrapper
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    agy_wrapper = local_bin / "agy"
    agy_wrapper.write_text("#!/bin/sh\necho agy\n", encoding="utf-8")

    # 1. Dry-run does not write target files
    dry = appinstall.install_linux(
        src,
        name="antigravity",
        replace=False,
        apply=False,
        home=home,
    )
    assert dry["ok"] is True
    assert dry["apply"] is False
    assert not (home / ".local" / "opt" / "antigravity-44.9.1").exists()

    # 2. Apply install
    res = appinstall.install_linux(
        src,
        name="antigravity",
        replace=True,
        apply=True,
        home=home,
    )
    assert res["apply"] is True
    opt_dir = Path(res["opt_dir"])
    assert opt_dir.is_dir()
    assert (opt_dir / "antigravity").is_file()
    assert (local_bin / "antigravity").exists() or (local_bin / "antigravity").is_symlink()
    assert agy_wrapper.exists()  # agy preserved!

    desktop_file = home / ".local" / "share" / "applications" / "antigravity.desktop"
    assert desktop_file.is_file()
    desktop_txt = desktop_file.read_text(encoding="utf-8")
    assert "Icon=antigravity" in desktop_txt
    assert "StartupWMClass=Antigravity" in desktop_txt

    for sz in appinstall.HICOLOR_SIZES:
        icon_p = (
            home
            / ".local"
            / "share"
            / "icons"
            / "hicolor"
            / f"{sz}x{sz}"
            / "apps"
            / "antigravity.png"
        )
        assert icon_p.is_file()
    svg_p = (
        home
        / ".local"
        / "share"
        / "icons"
        / "hicolor"
        / "scalable"
        / "apps"
        / "antigravity.svg"
    )
    assert svg_p.is_file()

    # 3. Verify reference_from_local + verify_linux file/icon/desktop checks
    ref = installverify.reference_from_local(
        "antigravity", opt_dir, home=home, version="44.9.1"
    )
    assert ref["product"] == "antigravity"
    assert ref["binary_sha"]
    assert "256x256" in ref["icons"]
    assert "scalable" in ref["icons"]

    v_res = installverify.verify_linux(ref, home=home)
    by_check = {c["check"]: c for c in v_res["rows"]}
    assert by_check["desktop.entry"]["ok"] is True
    assert by_check["desktop.icon-name"]["ok"] is True
    assert by_check["desktop.terminal-categories"]["ok"] is True
    assert by_check["icon.48x48"]["ok"] is True
    assert by_check["icon.256x256"]["ok"] is True
    assert by_check["icon.scalable"]["ok"] is True

    # 4. Purge on Linux moves install tree, launcher, and icons to quarantine trash with manifest
    prg = appinstall.purge_linux(
        "antigravity",
        apply=True,
        home=home,
    )
    assert prg["ok"] is True
    assert prg["apply"] is True
    assert not opt_dir.exists()
    assert not desktop_file.exists()
    assert not svg_p.exists()
    assert agy_wrapper.exists()  # agy still preserved!

    trash_dir = Path(prg["trash_dir"])
    manifest = json.loads((trash_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["app"] == "antigravity"
    assert len(manifest["done"]) >= 3


def test_windows_install_and_self_test(tmp_path: Path):
    st = installverify.self_test_all()
    assert all(r["ok"] for r in st)

    src = tmp_path / "Antigravity-win"
    src.mkdir()
    exe = src / "Antigravity.exe"
    exe.write_bytes(b"MZ" + b"\x00" * 128)
    (src / "resources" / "app").mkdir(parents=True)
    (src / "resources" / "app" / "package.json").write_text(
        json.dumps({"name": "antigravity", "version": "1.107.0"}), encoding="utf-8"
    )

    install_root = tmp_path / "Programs"
    res = appinstall.install_windows(
        src,
        name="antigravity",
        replace=False,
        apply=True,
        install_root=install_root,
    )
    assert res["ok"] is True
    target_dir = Path(res["target_dir"])
    assert (target_dir / "Antigravity.exe").is_file()
    assert (target_dir / "antigravity.ico").is_file()
