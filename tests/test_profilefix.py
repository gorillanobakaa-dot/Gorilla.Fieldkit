"""profilefix (kits/profilefix/profilefix.py): the path rewrite rules, and fix/undo on a real registry key.

The live tests write only under HKCU\\Software\\FieldkitProfilefixTest, with made-up profile names, and remove it.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "kits" / "profilefix" / "profilefix.py"
sys.path.insert(0, str(TOOL.parent))
import profilefix as pf  # noqa: E402

TEST_KEY = r"HKCU\Software\FieldkitProfilefixTest"
OLD, NEW = "pfOldTest", "pfNewTest"


def run(*args):
    r = subprocess.run([sys.executable, "-I", str(TOOL), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return r.returncode, r.stdout + r.stderr


@pytest.mark.parametrize("text,want", [
    (r"C:\Users\olduser\AppData\Local", r"C:\Users\newuser\AppData\Local"),  # privacy-scan: allow
    (r"C:\Users\olduser", r"C:\Users\newuser"),  # privacy-scan: allow
    ("file:///C:/Users/olduser/.gemini/x", "file:///C:/Users/newuser/.gemini/x"),  # privacy-scan: allow
    ("file:///c%3A/Users/olduser/Documents", "file:///c%3A/Users/newuser/Documents"),
    (r"\Device\HarddiskVolume3\Users\olduser\a.exe", r"\Device\HarddiskVolume3\Users\newuser\a.exe"),
    (r"file:///C:\[2b2b6684]\Users\olduser\.cache\\", r"file:///C:\[2b2b6684]\Users\newuser\.cache\\"),
    (r'"C:\users\OLDUSER\py.exe" "%L"', r'"C:\users\newuser\py.exe" "%L"'),
    (r"C:\Users\olduserX\a", r"C:\Users\olduserX\a"),            # another name that starts the same: untouched  # privacy-scan: allow
    ("olduser.someone@example.com", "olduser.someone@example.com"),  # not a path: untouched
    (r"C:\Data\olduser\a", r"C:\Data\olduser\a"),                # not under the profiles folder: untouched
    (r"D:\OldUsers\olduser\a", r"D:\OldUsers\olduser\a"),        # a folder that only ends in "Users": untouched
])
def test_rewrite(text, want):
    assert pf.rewrite(text, "Users", "olduser", "newuser") == want


def test_rewrite_data_types():
    assert pf.rewrite_data("REG_MULTI_SZ", [r"C:\Users\a\x", "y"], "Users", "a", "b") == (True, [r"C:\Users\b\x", "y"])  # privacy-scan: allow
    assert pf.rewrite_data("REG_BINARY", b"C:\\Users\\a", "Users", "a", "b") == (False, b"C:\\Users\\a")  # privacy-scan: allow
    assert pf.rewrite_data("REG_SZ", "nothing", "Users", "a", "b") == (False, "nothing")


def test_names_and_choice():
    assert pf.profile_names_in(r"x C:\Users\Jo Smith\Desktop;C:\Users\b", "Users") == ["Jo Smith", "b"]  # privacy-scan: allow
    c = pf.stale_candidates({"old": 9, "newuser": 50, "Public": 3, "%USERNAME%": 2, "ghost": 9}, ["newuser"], "newuser")
    assert c == [("ghost", 9), ("old", 9)]
    assert pf.choose_old(c, ["old", "newuser"], None)[0] == "old"
    assert pf.choose_old(c, ["newuser"], None)[0] is None          # ambiguous: refuses to guess
    assert pf.choose_old(c, ["old", "ghost"], None)[0] is None      # two stale names in the cache: refuses too
    assert pf.choose_old(c, [], "ghost")[0] == "ghost"
    assert pf.choose_old([], ["newuser"], None)[0] is None
    assert pf.choose_old([("old", 3)], ["newuser"], None)[0] == "old"   # one left after the cache was fixed


def test_windows_paths_on_any_system():
    assert pf.users_segment(r"C:\Users") == "Users"
    assert pf.users_segment(r"D:\Profiles") == "Profiles"


def test_uninstall_parsing():
    assert pf.uninstall_target(r'"C:\a b\Uninstall X.exe" /currentuser /S') == r"C:\a b\Uninstall X.exe"
    assert pf.uninstall_target(r"C:\x\unins000.exe /SILENT") == r"C:\x\unins000.exe"
    assert pf.name_variants("Antigravity 2.19.1", r"C:\P\antigravity") == ["antigravity"]
    assert pf.name_variants("Foo Bar", r"C:\P\FooBar") == ["foo bar", "foobar"]


win = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")


@pytest.fixture
def test_key():
    import winreg
    base = r"Software\FieldkitProfilefixTest"
    subprocess.run(["reg", "delete", TEST_KEY, "/f"], capture_output=True)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
        winreg.SetValueEx(k, "Path", 0, winreg.REG_SZ, rf"C:\Users\{OLD}\AppData\Local")  # privacy-scan: allow
        winreg.SetValueEx(k, "Expand", 0, winreg.REG_EXPAND_SZ, rf"%SystemDrive%\Users\{OLD}\x;C:\Users\{OLD}")  # privacy-scan: allow
        winreg.SetValueEx(k, "Multi", 0, winreg.REG_MULTI_SZ, [rf"C:\Users\{OLD}\a", "keep"])  # privacy-scan: allow
        winreg.SetValueEx(k, rf"C:\Users\{OLD}\app.exe", 0, winreg.REG_BINARY, b"\x01\x02")  # privacy-scan: allow
        winreg.SetValueEx(k, "Other", 0, winreg.REG_SZ, r"C:\Users\someoneelse\x")  # privacy-scan: allow
        winreg.SetValueEx(k, "Num", 0, winreg.REG_DWORD, 7)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + rf"\Tiles\W~C:\Users\{OLD}\child") as k:  # privacy-scan: allow
        winreg.SetValueEx(k, "v", 0, winreg.REG_SZ, "x")
    yield base
    subprocess.run(["reg", "delete", TEST_KEY, "/f"], capture_output=True)


def values(base):
    import winreg
    out = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
        i = 0
        while True:
            try:
                n, d, _ = winreg.EnumValue(k, i)
            except OSError:
                return out
            out[n] = d
            i += 1


def key_exists(sub):
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub).Close()
        return True
    except OSError:
        return False


@win
def test_scan_dry_run_changes_nothing(test_key):
    before = values(test_key)
    code, out = run("scan", "--old", OLD, "--new", NEW, "--key", TEST_KEY, "--verbose")
    assert code == 3, out                                    # stale entries found
    assert "5 change(s)" in out and "delete-key 1" in out, out
    code, out = run("fix", "--old", OLD, "--new", NEW, "--key", TEST_KEY)
    assert code == 0 and "dry run" in out, out
    assert values(test_key) == before


@win
def test_fix_then_undo(test_key, tmp_path):
    before = values(test_key)
    code, out = run("fix", "--old", OLD, "--new", NEW, "--key", TEST_KEY, "--apply", "--backup-dir", tmp_path)
    assert code == 0, out
    v = values(test_key)
    assert v["Path"] == rf"C:\Users\{NEW}\AppData\Local"  # privacy-scan: allow
    assert v["Expand"] == rf"%SystemDrive%\Users\{NEW}\x;C:\Users\{NEW}"  # privacy-scan: allow
    assert v["Multi"] == [rf"C:\Users\{NEW}\a", "keep"]  # privacy-scan: allow
    assert v[rf"C:\Users\{NEW}\app.exe"] == b"\x01\x02" and rf"C:\Users\{OLD}\app.exe" not in v  # privacy-scan: allow
    assert v["Other"] == r"C:\Users\someoneelse\x" and v["Num"] == 7  # privacy-scan: allow
    assert not key_exists(test_key + rf"\Tiles\W~C:\Users\{OLD}")  # privacy-scan: allow
    code, out = run("scan", "--old", OLD, "--new", NEW, "--key", TEST_KEY)
    assert code == 0, out                                    # nothing left
    manifest = next(tmp_path.rglob("manifest.json"))
    assert len(json.loads(manifest.read_text(encoding="utf-8"))["done"]) == 5
    code, out = run("undo", manifest)
    assert code == 0, out
    assert values(test_key) == before
    assert key_exists(test_key + rf"\Tiles\W~C:\Users\{OLD}\child")  # privacy-scan: allow


@win
def test_refuses_same_name_and_purge_unknown(test_key):
    code, out = run("fix", "--old", OLD, "--new", OLD, "--key", TEST_KEY)
    assert code == 2 and "same" in out
    code, out = run("purge-app", "^no such program fieldkit test$")
    assert code == 1 and "no installed program" in out


@win
def test_doctor_runs():
    code, out = run("doctor")
    assert code in (0, 3) and "profiles folder" in out, out
