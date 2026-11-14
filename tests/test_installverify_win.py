"""fieldkit install verify on Windows for any app (fieldkit/method/installverify_win.py), on a fake tree (any OS)."""
from pathlib import Path

from fieldkit.method import appinstall, installverify, installverify_win as w


def _tree(tmp_path, ico=None, uninstall=None, extra_lnk=None):
    local = tmp_path / "Local"
    roam = tmp_path / "Roaming"
    prof = tmp_path / "Profile"
    d = local / "Programs" / "Antigravity"
    d.mkdir(parents=True)
    exe = d / "Antigravity.exe"
    exe.write_bytes(b"MZ fake exe")
    desk = prof / "Desktop"
    sm = roam / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desk.mkdir(parents=True)
    sm.mkdir(parents=True)
    lnks = {desk / "Antigravity.lnk": {"target": str(exe), "icon": str(exe) + ",0"},
            sm / "Antigravity.lnk": {"target": str(exe), "icon": ",0"}}
    for p, info in (extra_lnk or {}).items():
        lnks[desk / p] = info
    for p in lnks:
        p.write_bytes(b"lnk")
    if ico is not None:
        (d / "antigravity.ico").write_bytes(ico)
    env = {"LOCALAPPDATA": str(local), "APPDATA": str(roam), "USERPROFILE": str(prof),
           "PUBLIC": str(tmp_path / "Public"), "ProgramData": str(tmp_path / "PD"),
           "__read_lnk": lambda p: lnks.get(Path(p)),
           "__uninstall": uninstall if uninstall is not None else
           [{"key": "HKCU\\X\\{guid}", "name": "Antigravity 2.32.1", "version": "2.32.1", "location": "",
             "icon": str(exe) + ",0", "uninstall": '"%s" /currentuser' % (d / "Uninstall Antigravity.exe")}]}
    (d / "Uninstall Antigravity.exe").write_bytes(b"MZ")
    return env, exe


def _failed(rows):
    return [r["check"] for r in rows if not r["ok"]]


def test_a_correct_install_passes(tmp_path):
    env, exe = _tree(tmp_path)
    rows = w.verify("antigravity", w.environment("antigravity", env))
    assert _failed(rows) == []
    assert any(r["check"] == "binary.exe" and str(exe) in r["line"] for r in rows)


def test_missing_exe_fails(tmp_path):
    env, exe = _tree(tmp_path)
    exe.unlink()
    assert "binary.exe" in _failed(w.verify("antigravity", w.environment("antigravity", env)))


def test_stale_uninstall_entry_fails(tmp_path):
    env, _ = _tree(tmp_path, uninstall=[{"key": "HKCU\\X\\old", "name": "Antigravity 1.0", "version": "1.0",
                                         "location": str(tmp_path / "gone"), "icon": str(tmp_path / "gone" / "a.exe"),
                                         "uninstall": ""}])
    assert "registry.uninstall" in _failed(w.verify("antigravity", w.environment("antigravity", env)))


def test_dead_shortcut_fails(tmp_path):
    env, _ = _tree(tmp_path, extra_lnk={"Antigravity (old).lnk": {"target": str(tmp_path / "nope.exe"), "icon": ""}})
    assert "shortcut.stale" in _failed(w.verify("antigravity", w.environment("antigravity", env)))


def test_shortcut_to_another_exe_fails(tmp_path):
    env, exe = _tree(tmp_path)
    other = tmp_path / "other.exe"
    other.write_bytes(b"MZ other")
    real = env["__read_lnk"]
    env["__read_lnk"] = lambda p: ({"target": str(other), "icon": ""} if Path(p).parent.name == "Desktop" else real(p))
    assert "shortcut.desktop" in _failed(w.verify("antigravity", w.environment("antigravity", env)))


def test_ico_must_match_the_real_icon(tmp_path):
    good = appinstall.make_ico(appinstall.WIN_ICO_SIZES, src_png=None)
    env, _ = _tree(tmp_path, ico=good)
    assert _failed(w.verify("antigravity", w.environment("antigravity", env))) == []
    env2, _ = _tree(tmp_path / "b", ico=b"not an ico")
    assert "icon.ico" in _failed(w.verify("antigravity", w.environment("antigravity", env2)))


def test_registry_value_paths_are_parsed():
    assert w._path_in('"C:\\A B\\x.exe" /S') == "C:\\A B\\x.exe"
    assert w._path_in("C:\\A\\x.exe,0") == "C:\\A\\x.exe"
    assert w._path_in("") == ""


def test_windows_needs_no_release_pin():
    ref = installverify.reference("antigravity-not-pinned", "windows")
    assert ref["repo"] == "local-windows"
