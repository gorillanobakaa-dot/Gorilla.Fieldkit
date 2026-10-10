"""fieldkit icons (IconKit, fieldkit/icons/iconkit.py). From IconKit's own test_iconkit.py (2026-09-28..10-09), plus
the 2026-10-10 rule: it works on any computer (no one person's paths; the other system says not-this-platform).

The crisp check must be able to FAIL: test 3 builds a deliberately soft icon and expects it caught. The Windows tests
only run on Windows and are read-only (they look at the desktop, never change it).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from fieldkit.icons import iconkit as ik

WINDOWS = os.name == "nt"


@pytest.fixture
def profile(tmp_path):
    """A 1024 px picture with sharp detail: a dark 'cap' with white lettering bars and a red dot, on grey stripes."""
    im = Image.new("RGB", (1024, 1024), (120, 120, 120))
    d = ImageDraw.Draw(im)
    for x in range(0, 1024, 16):
        d.line([(x, 0), (x, 1023)], fill=(170, 170, 170), width=4)
    d.ellipse((200, 60, 824, 480), fill=(20, 20, 24))            # cap
    for y in (250, 300):
        d.rectangle((300, y, 724, y + 18), fill=(235, 235, 235))  # lettering
    d.ellipse((480, 120, 544, 184), fill=(200, 20, 30))          # red logo
    d.ellipse((260, 420, 764, 1000), fill=(15, 15, 15))           # face
    im.save(tmp_path / "source.png")
    prof = {"name": "t", "source": "source.png", "crop": [0, 0, 1024, 1024],
            "cap_polygon": [[200, 60], [824, 60], [824, 480], [200, 480]],
            "subject_polygon": [[200, 40], [824, 40], [900, 1024], [120, 1024]],
            "colours": {"green": "#12B35A"}}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(prof), encoding="utf-8")
    return p


def test_1_build_writes_every_size_and_passes_the_gates(profile, tmp_path):
    prof = ik.load_profile(profile)
    master = ik.compose_master(prof, "#12B35A")
    ico = tmp_path / "good.ico"
    ik.write_ico(master, ico, ik.DEFAULT_SIZES + (74,))
    assert [f["size"] for f in ik.read_frames(ico)] == sorted(set(ik.DEFAULT_SIZES + (74,)))
    assert ik.check_ico(ico, master) == []


def test_2_recolour_keeps_logo_and_lettering(profile):
    m = np.asarray(ik.compose_master(ik.load_profile(profile), "#12B35A").convert("RGB")).astype(int)
    assert m[259, 512].min() > 200, m[259, 512]                   # lettering stays near-white
    r, g, b = m[152, 512]
    assert r > 150 and g < 80, (r, g, b)                          # logo stays red
    fr, fg, fb = m[400, 300]
    assert fg > fr and fg > fb, (fr, fg, fb)                      # cap fabric turns green


def test_3_check_catches_a_soft_icon(profile, tmp_path):
    master = ik.compose_master(ik.load_profile(profile), "#12B35A")
    tiny = master.resize((24, 24), Image.LANCZOS)
    soft = tmp_path / "soft.ico"
    frames = [tiny.resize((s, s), Image.BILINEAR) for s in (64, 128, 256)]   # upscaled = soft
    frames[-1].save(soft, format="ICO", sizes=[(64, 64), (128, 128), (256, 256)], append_images=frames[:-1])
    assert any("edge energy" in p for p in ik.check_ico(soft, master))


def test_4_windows_frame_choice_never_upscales():
    frames = [{"size": s, "image": Image.new("RGBA", (s, s))} for s in (48, 96, 256)]
    im, used = ik._frame_windows_would_use(frames, 100)
    assert used == 256 and im.size == (100, 100)
    assert ik._frame_windows_would_use(frames, 96)[1] == 96


def test_5_profile_must_name_source_crop_colours_and_the_picture_must_exist(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"source": "x.png"}), encoding="utf-8")
    with pytest.raises(ValueError, match="crop"):
        ik.load_profile(bad)
    bad.write_text(json.dumps({"source": "x.png", "crop": [0, 0, 1, 1], "colours": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="not found"):
        ik.load_profile(bad)


@pytest.mark.skipif(not WINDOWS, reason="Windows: reads the real desktop")
def test_6_desktop_size_reads_something():
    d = ik.desktop_size()
    assert isinstance(d["folderView"], int) and d["folderView"] > 0


@pytest.mark.skipif(not WINDOWS, reason="Windows: reads the real desktop")
def test_7_shortcuts_lists_the_desktop():
    assert isinstance(ik.list_shortcuts(), list)


@pytest.mark.skipif(not WINDOWS, reason="Windows: reads notepad.exe")
def test_8_frames_reads_an_exe():
    frames = ik.read_frames(Path(os.environ["SystemRoot"]) / "System32" / "notepad.exe")
    assert frames and all(f["image"].size[0] == f["size"] for f in frames)


def test_9_cache_verdict_blames_a_small_icon_file_not_the_cache(tmp_path):
    small = tmp_path / "small.ico"
    Image.new("RGBA", (48, 48), (200, 0, 0, 255)).save(small, sizes=[(16, 16), (48, 48)])
    big = tmp_path / "big.ico"
    Image.new("RGBA", (256, 256), (0, 200, 0, 255)).save(big, sizes=[(48, 48), (256, 256)])
    rows = ik.icon_verdicts([{"path": "a.lnk", "icon": f"{small},0"}, {"path": "b.lnk", "icon": f"{big},0"}], 256)
    assert [r["ok"] for r in rows] == [False, True], rows
    assert "must stretch" in rows[0]["why"]


def test_10_cache_files_finds_only_cache_files(tmp_path, monkeypatch):
    d = tmp_path / "Microsoft" / "Windows" / "Explorer"
    d.mkdir(parents=True)
    for n in ("iconcache_256.db", "iconcache_idx.db", "thumbcache_96.db", "notes.txt"):
        (d / n).write_bytes(b"x")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert [f.name for f in ik.cache_files()] == ["iconcache_256.db", "iconcache_idx.db"]
    assert "thumbcache_96.db" in [f.name for f in ik.cache_files(include_thumbs=True)]


def test_fix_button_runs_the_cache_rebuild_with_a_short_countdown():
    """2026-10-09: one double-click or right-click instead of asking for the cache rebuild every time."""
    text = ik.fix_launcher("C:/py/python.exe", "C:/fk")
    assert '"C:/py/python.exe" -m fieldkit icons cache --apply --countdown 3' in text
    assert 'cd /d "C:/fk"' in text                                              # this machine's Fieldkit folder
    assert "pause" in text.split("if errorlevel 1", 1)[1].split("\r\n", 1)[0]    # a failure stays on screen
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")       # a .cmd file: CRLF only
    assert ik.fix_registry_command("C:\\x\\Fix-blurry-icons.cmd") == \
        '"%SystemRoot%\\System32\\cmd.exe" /c ""C:\\x\\Fix-blurry-icons.cmd""'
    assert ik.FIX_KEY.startswith("HKCU\\")                                       # per user, no administrator
    assert ik.FIX_ICON.endswith("shell32.dll,238")


def test_it_names_no_ones_folders():
    """2026-10-10: the first launcher named one person's Python and script; nothing may name a user's folders."""
    src = Path(ik.__file__).read_text(encoding="utf-8")
    for bad in (r"C:\Users", "C:/Users", "/home/", "scoop", "Documents\\Scripts"):
        assert bad not in src, bad
    text = ik.fix_launcher()
    assert sys.executable in text and str(ik.ROOT) in text                       # found on the machine it runs on
    assert ik.FIX_CMD.is_relative_to(ik.ROOT / "local")                         # private: git-ignored


@pytest.mark.skipif(WINDOWS, reason="the other system")
def test_windows_commands_say_not_this_platform_and_change_nothing(capsys):
    for cmd in (["shortcuts"], ["desktop-size"], ["install-fix-button"], ["tih-check"]):
        assert ik.main(cmd) == 0
        assert "not-this-platform" in capsys.readouterr().out


def test_linux_cache_lists_and_rebuilds_only_what_is_yours(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    theme = home / ".local" / "share" / "icons" / "MyTheme"
    theme.mkdir(parents=True)
    (theme / "index.theme").write_text("[Icon Theme]\nName=MyTheme\nDirectories=\n")
    (theme / "icon-theme.cache").write_bytes(b"old")
    (home / ".cache").mkdir()
    (home / ".cache" / "icon-cache.kcache").write_bytes(b"kde")
    system = tmp_path / "usr" / "share"
    (system / "icons" / "hicolor").mkdir(parents=True)
    (system / "icons" / "hicolor" / "icon-theme.cache").write_bytes(b"sys")
    c = ik.linux_caches(home=home, data_dirs=str(system))
    assert c["gtk"] == [theme] and [f.name for f in c["kde"]] == ["icon-cache.kcache"]
    assert c["system"] == [system / "icons" / "hicolor"]

    monkeypatch.setattr(ik, "linux_caches", lambda: c)
    monkeypatch.setattr(ik.shutil, "which", lambda n: None)                       # no gtk-update-icon-cache here
    a = ik.build_parser().parse_args(["cache", "--apply", "--countdown", "0", "--backup-dir", str(tmp_path / "bk")])
    rc = ik._cache_linux(a)
    out = capsys.readouterr().out
    assert rc == 3 and "libgtk-3-bin" in out                                     # what is missing, and the line
    backup = next((tmp_path / "bk").iterdir())
    assert (backup / "MyTheme" / "icon-theme.cache").read_bytes() == b"old"      # moved, never deleted
    assert (backup / "icon-cache.kcache").is_file()
    assert (system / "icons" / "hicolor" / "icon-theme.cache").is_file()         # the system theme is never touched


def test_the_file_still_runs_on_its_own_and_through_fieldkit():
    me = Path(ik.__file__)
    for cmd in ([sys.executable, str(me), "--help"], [sys.executable, "-m", "fieldkit", "icons", "--help"]):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ik.ROOT))
        assert r.returncode == 0 and "check-ico" in r.stdout, r.stderr


def test_check_ico_exit_code_is_3_on_a_soft_icon(profile, tmp_path):
    master = ik.compose_master(ik.load_profile(profile), "#12B35A")
    master.save(tmp_path / "master.png")
    soft = tmp_path / "soft.ico"
    tiny = master.resize((24, 24), Image.LANCZOS)
    frames = [tiny.resize((s, s), Image.BILINEAR) for s in (64, 256)]
    frames[-1].save(soft, format="ICO", sizes=[(64, 64), (256, 256)], append_images=frames[:-1])
    assert ik.main(["check-ico", str(soft), "--master", str(tmp_path / "master.png")]) == 3
    good = tmp_path / "good.ico"
    ik.write_ico(master, good, (16, 32, 256))
    assert ik.main(["check-ico", str(good), "--master", str(tmp_path / "master.png")]) == 0


def test_example_profile_is_complete():
    p = json.loads((Path(ik.__file__).with_name("example-profile.json")).read_text(encoding="utf-8"))
    assert {"source", "crop", "colours"} <= set(p) and not os.path.isabs(p["source"])
