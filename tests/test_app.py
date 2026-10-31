"""fieldkit app find / start: faked registries, .desktop folders, mdfind output and process tables for all three systems."""
import plistlib
import subprocess
import sys

from fieldkit.app import find as F
from fieldkit.app import start as S
from fieldkit.peers import link_probe


def env(osname, files=None, procs=(), lines=None, registry=(), **kw):
    """A fake system. files: {path: text|bytes}; folders are derived from the keys."""
    files = files or {}
    lines = lines or {}
    apps = kw.pop("start_apps", [])

    def listdir(d):
        d = d.rstrip("/\\").replace("\\", "/")
        out = set()
        for p in files:
            q = p.replace("\\", "/")
            if q.startswith(d + "/"):
                out.add(q[len(d) + 1:].split("/")[0])
        return sorted(out)

    base = {"osname": osname, "environ": kw.pop("environ", {}), "home": "/h" if osname != "win32" else "C:/h",
            "procs": list(procs), "listdir": listdir, "isfile": lambda p: p in files or p.replace("\\", "/") in files,
            "isexec": lambda p: True, "glob": lambda p: [], "which": lambda s: None, "run": None,
            "lines": lambda argv: lines.get(argv[0], []),
            "read": lambda p: files[p.replace("\\", "/")], "read_bytes": lambda p: files[p.replace("\\", "/")], "registry": lambda: list(registry),
            "lnk_dirs": kw.pop("lnk_dirs", []), "walk_lnk": lambda d: [p for p in files if p.endswith(".lnk")],
            "lnk_target": lambda p: files[p], "start_apps": lambda: apps}
    base.update(kw)
    return base


def test_name_matching_ignores_case_spaces_dashes():
    assert F.norm("Gorilla OpenCode") == F.norm("gorilla-opencode") == F.norm("Gorilla_OpenCode.exe")
    assert F.score("gorilla opencode", "gorilla-opencode") == 2
    assert F.score("fox", "Firefox") == 1 and F.score("ab", "abc") == 0 and F.score("zzz", "Firefox") == 0


def test_windows_registry_start_menu_and_path():
    reg = [("HKLM", "App Paths", [("tpfancontrol.exe", {"": r"C:\Fan\TPFanControl.exe"})]),
           ("HKCU", "Uninstall", [("x", {"DisplayName": "Gorilla OpenCode", "DisplayIcon": r'"C:\Go\go.exe",0',
                                         "InstallLocation": r"C:\Go", "DisplayVersion": "0.1.145"})])]
    e = env("win32", files={r"C:\SM\Gorilla OpenCode.lnk": r"C:\Go\go.exe", r"C:\Tools\notepad.exe": b""},
            registry=reg, lnk_dirs=["C:/SM"], procs=[(7, "TPFanControl.exe", r"C:\Fan\TPFanControl.exe")],
            environ={"PATH": r"C:\Tools", "PATHEXT": ".EXE"}, isfile=lambda p: True)
    fan = F.find("tpfancontrol", env=e)
    assert fan[0]["path"] == r"C:\Fan\TPFanControl.exe" and fan[0]["running"] == [7]
    go = F.find("Gorilla OpenCode", env=e)
    assert {"Uninstall", "Start Menu"} <= {m["how"] for m in go}
    assert [m["version"] for m in go if m["how"] == "Uninstall"] == ["0.1.145"]
    assert next(m for m in go if m["how"] == "Start Menu")["launch"] == {"kind": "lnk", "target": r"C:\SM\Gorilla OpenCode.lnk"}
    n = F.find("notepad", env=e)
    assert n[0]["how"] == "PATH" and n[0]["path"].endswith("notepad.exe")


def test_windows_start_apps_only_when_nothing_else_matches():
    e = env("win32", start_apps=[("Calculator", "Microsoft.Calc_8wekyb!App")])
    m = F.find("calculator", env=e)
    assert m[0]["launch"] == {"kind": "appsfolder", "target": "Microsoft.Calc_8wekyb!App"}


def test_linux_desktop_files_flatpak_snap():
    files = {"/usr/share/applications/org.gimp.GIMP.desktop":
             "[Desktop Entry]\nType=Application\nName=GNU Image Manipulation Program\nExec=gimp-2.10 %U\nTryExec=gimp\n"
             "[Desktop Action x]\nName=Other\n",
             "/usr/share/applications/hidden.desktop": "[Desktop Entry]\nName=gimp\nHidden=true\n"}
    e = env("linux", files=files, lines={"flatpak": ["org.kde.krita\tKrita\t5.2"], "snap": ["Name Version", "vlc 3.0 1 x"]},
            environ={"XDG_DATA_DIRS": "/usr/share"})
    g = F.find("gimp", env=e)
    assert len(g) == 1 and g[0]["launch"]["kind"] == "desktop" and g[0]["launch"]["id"] == "org.gimp.GIMP"
    assert F.find("krita", env=e)[0]["launch"] == {"kind": "flatpak", "target": "org.kde.krita"}
    assert F.find("vlc", env=e)[0]["launch"]["kind"] == "snap"


def test_macos_bundles_from_mdfind_and_applications():
    plist = plistlib.dumps({"CFBundleShortVersionString": "1.9", "CFBundleIdentifier": "com.apple.TextEdit"})
    files = {"/System/Applications/TextEdit.app/Contents/Info.plist": plist,
             "/Applications/Firefox.app/Contents/Info.plist": b""}
    e = env("darwin", files=files, lines={"mdfind": ["/System/Applications/TextEdit.app"]})
    t = F.find("textedit", env=e)
    assert t[0]["launch"]["target"].replace("\\", "/") == "/System/Applications/TextEdit.app" and t[0]["version"] == "1.9"
    assert F.find("com.apple.TextEdit", env=e)[0]["bundle_id"] == "com.apple.TextEdit"
    assert F.find("firefox", env=e)[0]["how"] == "Applications folder"


def test_running_process_is_a_source_and_ambiguity_lists():
    e = env("linux", procs=[(5, "foobar", "/opt/x/foobar")], isfile=lambda p: True)
    assert F.find("foobar", env=e)[0]["how"] == "process"
    m, why = F.pick([{"name": "Alpha Edit", "score": 1}, {"name": "Alpha View", "score": 1}])
    assert m is None and "ambiguous" in why
    assert F.pick([{"name": "Alpha", "score": 2}, {"name": "alpha", "score": 2}])[0]["name"] == "Alpha"
    assert F.pick([])[1] == "not found"


def match(kind, target, **kw):
    return {"name": "Thing", "path": target, "launch": {"kind": kind, "target": target, **kw}, "running": [], **kw}


class P:
    pid = 99


def test_start_exe_verified_by_pid_and_never_elevated():
    calls = []
    table = [[(1, "init", "")], [(1, "init", ""), (99, "thing", "/bin/thing")]]
    r = S.start(match("exe", "/bin/thing"), ("--x",), osname="linux", popen=lambda a, **k: calls.append((a, k)) or P(),
                procs=lambda: table.pop(0) if len(table) > 1 else table[0], sleep=lambda s: None,
                is_elevated=lambda o: False)
    assert r["ok"] and r["pid"] == 99 and r["verified"] and calls[0][0] == ["/bin/thing", "--x"]
    assert calls[0][1]["start_new_session"] is True
    called = []
    r = S.start(match("exe", "/bin/thing"), osname="linux", popen=lambda *a, **k: called.append(1),
                is_elevated=lambda o: True)
    assert not r["ok"] and "elevated" in r["why"] and not called


def test_start_reports_a_program_that_exited_and_uac_refusal():
    r = S.start(match("exe", "/bin/thing"), osname="linux", popen=lambda a, **k: P(), procs=lambda: [(1, "init", "")],
                sleep=lambda s: None, is_elevated=lambda o: False)
    assert not r["ok"] and "not running" in r["why"]
    e = OSError()
    e.winerror = 740

    def boom(*a, **k):
        raise e
    r = S.start(match("exe", "C:/a.exe"), osname="win32", popen=boom, procs=lambda: [], sleep=lambda s: None,
                is_elevated=lambda o: False)
    assert not r["ok"] and "administrator" in r["why"]


def test_start_methods_per_system():
    d = match("desktop", "/a/x.desktop", id="x", exec="x %U")
    assert S.plan(d, [], "linux", which=lambda s: "/u/" + s)[1] == ["gtk-launch", "x"]
    assert S.plan(d, [], "linux", which=lambda s: "/u/gio" if s == "gio" else None)[1] == ["gio", "launch", "/a/x.desktop"]
    d2 = match("desktop", "/a/x.desktop", id="x", exec='"x y" %U --z')
    assert S.plan(d2, ["a"], "linux", which=lambda s: None)[1] == ["x y", "--z", "a"]
    assert S.plan(match("bundle", "/Applications/T.app"), ["f"], "darwin")[1] == ["open", "-a", "/Applications/T.app", "--args", "f"]
    assert S.plan(match("appsfolder", "Pkg!App"), [], "win32")[1] == ["explorer.exe", "shell:AppsFolder\\Pkg!App"]
    assert S.plan(match("lnk", "C:/a.lnk"), [], "win32")[0].startswith("ShellExecute")
    assert S.plan(match("flatpak", "org.a.B"), [], "linux")[1] == ["flatpak", "run", "org.a.B"]
    assert S.plan(match("folder", "C:/dir"), [], "win32")[0] == ""


def test_start_lnk_uses_shellexecute_and_finds_the_new_process():
    opened = []
    table = [[(1, "init", "")], [(1, "init", ""), (50, "Thing.exe", "")]]
    r = S.start(match("lnk", "C:/Thing.lnk"), osname="win32", startfile=opened.append, popen=None,
                procs=lambda: table.pop(0) if len(table) > 1 else table[0], sleep=lambda s: None,
                is_elevated=lambda o: False)
    assert opened == ["C:/Thing.lnk"] and r["ok"] and r["pid"] == 50


def test_start_real_harmless_program(tmp_path):
    script = tmp_path / "sleeper.py"
    script.write_text("import time; time.sleep(30)")
    m = match("exe", sys.executable)
    m["name"] = "python"
    seen = []
    orig = subprocess.Popen

    def popen(argv, **k):
        p = orig([argv[0], str(script)], **k)
        seen.append(p)
        return p
    try:
        r = S.start(m, (), popen=popen, wait=1, is_elevated=lambda o: False)
        assert r["ok"] and r["pid"] == seen[0].pid
    finally:
        for p in seen:
            p.kill()
            p.wait()


def test_link_start_app_allowlist():
    started = []
    row = {"name": "TPFanControl", "path": "C:/f.exe", "score": 2, "launch": {"kind": "exe", "target": "C:/f.exe"},
           "running": []}
    ok = link_probe.start_app("tpfancontrol", allowed=link_probe.DEFAULT_APPS, finder=lambda q: [row],
                              starter=lambda m, a: started.append((m["name"], a)) or {"ok": True, "why": "running"})
    assert ok["ok"] and started == [("TPFanControl", ())]
    for bad in ("notepad", "Fan", "rm -rf", "a;b", None):
        r = link_probe.start_app(bad, allowed=link_probe.DEFAULT_APPS, finder=lambda q: [row],
                                 starter=lambda m, a: started.append("BAD"))
        assert not r["ok"]
    assert started == [("TPFanControl", ())]
    amb = link_probe.start_app("claude", allowed=["claude"],
                               finder=lambda q: [dict(row, name="A claude x", score=1), dict(row, name="B claude y", score=1)],
                               starter=lambda m, a: started.append("BAD"))
    assert not amb["ok"] and "ambiguous" in amb["error"] and "BAD" not in started


def test_start_app_verb_is_cable_only():
    from fieldkit.peers import link
    assert "start-app" in link.JOB_VERBS
