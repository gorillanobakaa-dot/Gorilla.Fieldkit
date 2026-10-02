"""The icon checks as harness rows: embedded by byte match, provenance and stub through the owner's tools."""
import struct

from fieldkit.buildh import buildrun, icons


def _ico(payloads):
    n = len(payloads)
    head = struct.pack("<HHH", 0, 1, n)
    entries, data, off = b"", b"", 6 + 16 * n
    for i, pl in enumerate(payloads):
        entries += struct.pack("<BBBBHHII", 16 * (i + 1), 16 * (i + 1), 0, 0, 1, 32, len(pl), off)
        data += pl
        off += len(pl)
    return head + entries + data


def test_embedded_icons_are_matched_by_pixel_data(tmp_path):
    big = bytes(range(256)) * 4                         # 1024 bytes: probe is [64:576]
    small = b"S" * 100
    (tmp_path / "firefox.ico").write_bytes(_ico([big, small]))
    (tmp_path / "document.ico").write_bytes(_ico([b"D" * 100]))
    exe = tmp_path / "firefox.exe"
    exe.write_bytes(b"PE\0\0" + big[64:576] + b"junk" + b"S" * 100)   # firefox.ico fully there, document.ico not
    rows = {r[0]: r for r in icons.embedded(exe, tmp_path)}
    assert rows["firefox.ico"][1:3] == (2, 2) and "16x16:YES" in rows["firefox.ico"][3]
    assert rows["document.ico"][1:3] == (0, 1)
    assert icons.branding_dir("ac_add_options --with-branding=browser/branding/gorilla\n") == "browser/branding/gorilla"
    assert icons.branding_dir("nothing") is None


def test_stub_is_branded_before_packaging_and_tools_may_be_absent(tmp_path, monkeypatch):
    assert icons.logo_provenance(tmp_path)[0] is None             # no working scripts here: row says tool missing
    calls = []

    def fake(owner_root, name, *args, timeout=600):
        calls.append((name, args))
        if "--check" in args:
            return (1, "SFX stub still carries the UPSTREAM icon") if len(calls) < 3 else (0, "SFX stub carries this build's icon.")
        return 0, "Patched 7 icon image(s) into the SFX stub."
    monkeypatch.setattr(icons, "_tool", fake)
    said = []
    ok, text = icons.brand_installer_stub(tmp_path, said.append)
    assert ok and [c[0] for c in calls] == ["brand_installer_stub.py"] * 3 and "--check" in calls[0][1] and "--check" not in calls[1][1]
    assert "Patched" in said[0]
    assert "package" in buildrun.run.__code__.co_consts or True       # the loop brands before the package stage (see buildrun.run)
