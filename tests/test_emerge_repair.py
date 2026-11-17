"""EMERGENCY REPAIR (owner 2026-11-17: "small fix = one step, never full emerge"): fieldkit emerge repair puts a small
frontend fix straight into the installed omni archive, checks it, puts the archive back when the check fails, and
refuses anything that needs a build."""
import subprocess
import zipfile
from pathlib import Path

import pytest

from fieldkit.emerge import repair as rp

GOOD = {"items": [{"rule": "RT-CONTENT", "item": "about:newtab@1x", "verdict": "PASS", "evidence": "logo"}]}
BAD = {"items": [{"rule": "RT-CONTENT", "item": "about:newtab@1x", "verdict": "FAIL", "evidence": "no about-logo"}]}


class FakeRecipe:
    name = "firefox-test"

    def __init__(self, tmp, repair=True):
        self.vars = {"version": "160.0"}
        self.spec = {"repair": {
            "source": str(tmp / "src"), "patches": str(tmp / "patches"), "series": "21.PORT.FIXES",
            "program": "firefox.exe", "packages": ["browser/omni.ja", "omni.ja"],
            "extensions": [".mjs", ".css"], "max_files": 2, "check": {"pages": ["about:newtab"], "dprs": [1]}}
            } if repair else {}

    def _resolve(self, v):
        return v


def _world(tmp):
    src = tmp / "src"
    (src / "browser").mkdir(parents=True)
    f = src / "browser" / "Logo.mjs"
    f.write_text("export const logo = 1;\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
                   check=True)
    inst = tmp / "install"
    (inst / "browser").mkdir(parents=True)
    with zipfile.ZipFile(inst / "browser" / "omni.ja", "w") as z:
        z.writestr(zipfile.ZipInfo("modules/Logo.mjs"), b"export const logo = 1;\n")
        z.writestr("chrome/other.css", b"a{}", compress_type=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(inst / "omni.ja", "w") as z:
        z.writestr("modules/Same.css", b"x")
        z.writestr("res/Same.css", b"y")
    f.write_text("export const logo = 2;\n", encoding="utf-8", newline="\n")
    return src, inst


def _run(tmp, result, **kw):
    src, inst = _world(tmp)
    res = rp.repair(FakeRecipe(tmp), ["browser/Logo.mjs"], install_dir=inst, name="logo fix", why="logo back",
                    say=lambda m: None, check=lambda d, p: result, procs=lambda d: [], stamp="T", **kw)
    with zipfile.ZipFile(inst / "browser" / "omni.ja") as z:
        return res, z.read("modules/Logo.mjs"), z.getinfo("chrome/other.css").compress_type


def test_a_passing_fix_lands_in_the_archive_and_becomes_the_next_patch(tmp_path):
    res, body, ct = _run(tmp_path, GOOD)
    assert res["ok"] and body == b"export const logo = 2;\n"
    assert ct == zipfile.ZIP_DEFLATED                                   # other entries keep their compression
    patch = Path(res["patch"])
    assert patch.name == "001-logo-fix.patch" and patch.parent.name == "21.PORT.FIXES.160"
    assert "# Gorilla 160 port fix" in patch.read_text(encoding="utf-8")
    assert (tmp_path / "install" / "browser" / "omni.ja.before-repair-T").is_file()   # backup kept, never deleted


def test_a_failing_check_puts_the_archive_back_and_writes_no_patch(tmp_path):
    res, body, _ = _run(tmp_path, BAD)
    assert not res["ok"] and res["patch"] is None
    assert body == b"export const logo = 1;\n"
    assert not (tmp_path / "patches").exists()


def test_a_page_the_check_never_measured_is_a_failure(tmp_path):
    res, body, _ = _run(tmp_path, {"items": []})
    assert not res["ok"] and "about:newtab: not measured" in res["problems"]


def test_a_running_product_is_stopped_and_reopened(tmp_path):
    src, inst = _world(tmp_path)
    stopped, opened = [], []
    rp.repair(FakeRecipe(tmp_path), ["browser/Logo.mjs"], install_dir=inst, say=lambda m: None,
              check=lambda d, p: GOOD, procs=lambda d: [41], stop=stopped.append, reopen=opened.append, stamp="T")
    assert stopped == [41] and opened == [inst / "firefox.exe"]


def test_what_needs_a_build_is_refused(tmp_path):
    src, inst = _world(tmp_path)
    sp = rp.spec_of(FakeRecipe(tmp_path))
    (src / "a.cpp").write_text("int x;", encoding="utf-8")
    with pytest.raises(rp.Refused, match="not a frontend file"):
        rp.plan(sp, ["a.cpp"], inst)
    (src / "browser" / "Logo.mjs").write_text("#ifdef XP_WIN\nx\n#endif\n", encoding="utf-8")
    with pytest.raises(rp.Refused, match="preprocesses"):
        rp.plan(sp, ["browser/Logo.mjs"], inst)
    with pytest.raises(rp.Refused, match="not a small fix"):
        rp.plan(sp, ["a", "b", "c"], inst)
    with pytest.raises(rp.Refused, match="declares no repair"):
        rp.spec_of(FakeRecipe(tmp_path, repair=False))


def test_an_ambiguous_name_needs_a_map(tmp_path):
    src, inst = _world(tmp_path)
    sp = rp.spec_of(FakeRecipe(tmp_path))
    (src / "Same.css").write_text("z{}", encoding="utf-8")
    with pytest.raises(rp.Refused, match="--map"):
        rp.plan(sp, ["Same.css"], inst)
    assert rp.plan(sp, ["Same.css"], inst, maps={"Same.css": "omni.ja:res/Same.css"})[0][1:] == ("omni.ja",
                                                                                               "res/Same.css")
