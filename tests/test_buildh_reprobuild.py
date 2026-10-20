"""build-harness repro: the compiled Firefox tree built a second time elsewhere, the packages compared byte for byte.

The Firefox build itself cannot run here; `execute` stands in for `mach build` / `mach package` and writes the second
build's packages. What is held: no pin, no run (the BuildID would differ every time); a folder repro did not make is
never touched; the second build is a worktree of HEAD with its own objdir, the mozconfig pointing there and the pin
in the environment; identical packages are REPRODUCED and a build folder inside omni.ja is named as the cause.
"""
import io
import json
import subprocess
import zipfile

import pytest

from fieldkit.buildh import compile as cg, reprobuild as rb, task


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    from fieldkit.buildh import timebombs
    monkeypatch.setattr(timebombs, "rows", lambda workdir, at=None, margin=42, run=None: [])
    w = tmp_path / "work"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "x"],
                   check=True)
    obj = tmp_path / "obj"
    (obj / "dist").mkdir(parents=True)
    h = tmp_path / "harness"
    (h / "config").mkdir(parents=True)
    (h / "config" / "mozconfig.win64").write_text(f"ac_add_options --enable-release\nmk_add_options MOZ_OBJDIR={obj.as_posix()}\n")
    (h / "config" / "patch_policy.json").write_text('{"patchset_root": "patchset", "groups": {}}')
    (h / "config" / "versions.lock.json").write_text(json.dumps({"build": {"moz_build_date": "20261011120000"}}))
    monkeypatch.setattr(cg, "mozconfig_path", lambda root=None: h / "config" / "mozconfig.win64")
    monkeypatch.setattr(cg, "owner_rows", lambda t: [])          # the owner's checks have their own tests
    task.start("r1", "demo", w, [], budget_tokens=1000, meta={"upstream": {"version": "157.0"}, "harness_root": str(h)})
    task.approve("r1", "owner")
    task.journal(task.load("r1"), "script-done", step="final-checks")
    assert [r for r in cg.gate("r1") if not r["ok"]] == []
    _packages(obj, w)
    return {"w": w, "obj": obj, "h": h}


def _omni(folder_in_it=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("modules/AppConstants.sys.mjs", (2026, 10, 11, 12, 0, 0)),
                   "export const AppConstants = {};\n" + (f"// built in {folder_in_it}\n" if folder_in_it else ""))
    return buf.getvalue()


def _packages(obj, src, folder_in_it=None):
    dist = obj / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dist / "firefox-157.0.en-US.win64.zip", "w") as z:
        z.writestr(zipfile.ZipInfo("firefox/firefox.exe", (2026, 10, 11, 12, 0, 0)), b"MZ exe")
        z.writestr(zipfile.ZipInfo("firefox/omni.ja", (2026, 10, 11, 12, 0, 0)), _omni(folder_in_it))
    (dist / "firefox-157.0.en-US.win64.installer.exe").write_bytes(b"7z stub" + (b"x" * 64))


def _fake_build(seen, folder_in_it=None):
    def execute(cmd, cwd, env, log):
        seen.append({"cmd": cmd, "cwd": str(cwd), "env": env})
        if cmd[-1].endswith("package"):
            obj2 = rb.second_paths(rb._record("r1"))[1]
            _packages(obj2, cwd, folder_in_it(cwd) if folder_in_it else None)
        return 0, ["ok"]
    return execute


def test_the_plan_says_where_and_reads_only(world):
    p = rb.plan("r1", owner_root=world["h"], free_gb=500)
    assert p["ok"], p["rows"]
    assert p["work2"].endswith("work-repro") and p["obj2"].endswith("obj-repro") and p["pin"] == "20261011120000"
    assert not (world["w"].parent / "work-repro").exists()


def test_no_pin_no_run_and_no_room_no_run(world):
    (world["h"] / "config" / "versions.lock.json").unlink()
    rows = {r["check"]: r for r in rb.plan("r1", owner_root=world["h"], free_gb=500)["rows"]}
    assert not rows["the build date is pinned"]["ok"] and "BuildID" in rows["the build date is pinned"]["evidence"]
    rows = {r["check"]: r for r in rb.plan("r1", owner_root=world["h"], free_gb=3)["rows"]}
    assert not rows["room for a second build"]["ok"]


def test_a_folder_repro_did_not_make_is_never_touched(world):
    other = world["w"].parent / "obj-repro"
    other.mkdir()
    (other / "someone's.txt").write_text("keep me")
    r = rb.run("r1", say=lambda m: None, execute=_fake_build([]), owner_root=world["h"], free_gb=500)
    assert not r["ok"] and (other / "someone's.txt").read_text() == "keep me"
    assert not (world["w"].parent / "work-repro").exists()


def test_the_second_build_is_a_worktree_of_head_with_its_own_objdir_and_the_pin(world):
    seen = []
    r = rb.run("r1", say=lambda m: None, execute=_fake_build(seen), owner_root=world["h"], free_gb=500)
    assert r["ok"], r
    work2 = world["w"].parent / "work-repro"
    assert (work2 / "a.txt").read_text() == "a\n"
    head = lambda d: subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    assert head(work2) == head(world["w"])
    assert [s["cmd"][-1].split()[-1] for s in seen] == ["build", "package"] and all(s["cwd"] == str(work2) for s in seen)
    env = seen[0]["env"]
    assert env["MOZ_BUILD_DATE"] == "20261011120000"
    moz2 = open(env["MOZCONFIG"]).read()
    assert "obj-repro" in moz2 and f"MOZ_OBJDIR={world['obj'].as_posix()}\n" not in moz2 and "--enable-release" in moz2
    assert all(x["identical"] for x in r["results"]) and {x["package"] for x in r["results"]} == {"zip", "installer"}
    journal = (task.STATE / "r1" / "journal.jsonl").read_text()
    assert '"repro-proof"' in journal and '"ok": true' in journal.split('"repro-proof"')[1].split("\n")[0]
    # a second run reuses the folders repro itself made, from scratch: an objdir left from before is not a new build
    stale = world["w"].parent / "obj-repro" / "left-from-before.o"
    stale.write_text("old")
    assert rb.run("r1", say=lambda m: None, execute=_fake_build([]), owner_root=world["h"], free_gb=500)["ok"]
    assert not stale.exists()


def test_a_build_folder_inside_omni_ja_is_named(world):
    seen = []
    # the FIRST build's omni.ja names its source folder; the second, built elsewhere, names its own
    _packages(world["obj"], world["w"], folder_in_it=world["w"])
    r = rb.run("r1", say=lambda m: None, execute=_fake_build(seen, folder_in_it=lambda cwd: cwd), owner_root=world["h"],
               free_gb=500)
    assert not r["ok"]
    z = next(x for x in r["results"] if x["package"] == "zip")
    f = next(f for f in z["findings"] if "AppConstants" in f["where"])
    assert f["where"] == "firefox/omni.ja!modules/AppConstants.sys.mjs"
    assert any("build folder" in c for c in f["causes"])


def test_the_mozconfig_must_name_the_objdir():
    with pytest.raises(ValueError):
        rb.mozconfig_for("ac_add_options --enable-release\n", "C:/obj", "C:/obj-repro")
    assert rb.mozconfig_for("mk_add_options MOZ_OBJDIR=C:\\obj-ff\n", "C:\\obj-ff", "C:\\obj-ff-repro") == \
        "mk_add_options MOZ_OBJDIR=C:\\obj-ff-repro\n"
