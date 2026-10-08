"""probe: a copy of the build answers a question; omni members of the COPY can be replaced to try a JS fix."""
import zipfile

import pytest

from fieldkit.buildh import probe


def test_omni_members_are_replaced_in_the_copy_only(tmp_path):
    app = tmp_path / "app" / "browser"
    app.mkdir(parents=True)
    with zipfile.ZipFile(app / "omni.ja", "w") as z:
        z.writestr("modules/A.sys.mjs", "old")
        z.writestr("modules/B.sys.mjs", "keep")
    fix = tmp_path / "A.sys.mjs"
    fix.write_text("new", encoding="utf-8")
    done = probe.patch_omni(tmp_path / "app", {"browser/omni.ja:modules/A.sys.mjs": fix})
    assert done == ["browser/omni.ja:modules/A.sys.mjs"]
    with zipfile.ZipFile(app / "omni.ja") as z:
        assert z.read("modules/A.sys.mjs") == b"new" and z.read("modules/B.sys.mjs") == b"keep"
    with pytest.raises(KeyError):
        probe.patch_omni(tmp_path / "app", {"browser/omni.ja:modules/Nope.sys.mjs": fix})


def test_ready_made_probes_exist_and_resolve_by_name():
    names = {p.stem for p in probe.PROBES.glob("*.js")}
    assert {"newtab-gates", "remote-settings-dumps", "search-icon"} <= names
    assert probe.resolve_js("newtab-gates").name == "newtab-gates.js"


def test_the_autoconfig_wrapper_reports_and_always_ends():
    cfg = probe.CFG.format(wait=1, wait_ms=1000, body="    say('x');")
    assert "GPROBE " in cfg and "GPROBE-DONE" in cfg and "catch (e)" in cfg


def test_release_check_lists_every_failure_with_its_fix(monkeypatch, tmp_path):
    from fieldkit.buildh import releasecheck as rc, task, buildrun, install
    outs = {"techniques": (0, "OK   T-1"), "decisions": (0, "DECISIONS OK (strict)"), "replay": (3, "REPLAY FAILED: 1"),
            "claims": (0, '{"totals": {"claims": 1, "CONTRADICTED": 0, "STALE": 0, "UNPROVEN": 1, "patches": 1, "patches_failing": 0}}')}
    monkeypatch.setattr(rc, "_run", lambda args, timeout=0: outs[args[0]])
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "meta": {"upstream": {"version": "157.0"}}})
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "leakgate_result.json").write_text('{"release_run": true, "FINAL_RESULT": "PASS", "BUILD": "1"}', encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "application.ini").write_text("BuildID=1\n", encoding="utf-8")
    monkeypatch.setattr(buildrun, "_owner_root", lambda t: str(tmp_path))
    monkeypatch.setattr(install, "find_install", lambda: str(tmp_path / "app"))
    rows = rc.run("t", say=lambda m: None, skip_post_install=True)
    # no release-docs.yaml yet: the release's documents were never held against their sources
    assert [r["check"] for r in rows if not r["ok"]] == ["replay", "release documents"]
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "patch_policy.json").write_text('{"patchset_root": "gorilla-patchset/patches"}', encoding="utf-8")
    rel = tmp_path / "gorilla-patchset" / "release" / "157.0"
    rel.mkdir(parents=True)
    (rel / "PLAN.md").write_text("44 build runs\n", encoding="utf-8")
    (rel / "NOTES.md").write_text("It took 44 build runs.\n", encoding="utf-8")
    (rel / "release-docs.yaml").write_text("sources: [PLAN.md]\nplain: [NOTES.md]\n", encoding="utf-8")
    rows = rc.run("t", say=lambda m: None, skip_post_install=True)
    assert [r["check"] for r in rows if not r["ok"]] == ["replay"]
    (rel / "NOTES.md").write_text("It took 45 build runs.\n", encoding="utf-8")
    bad = [r for r in rc.run("t", say=lambda m: None, skip_post_install=True) if r["check"] == "release documents"][0]
    assert not bad["ok"] and "NOTES.md: 1" in bad["evidence"] and "docs release --manifest" in bad["fix"]


def test_files_of_an_unpacked_build_are_replaced_in_the_copy(tmp_path):
    app = tmp_path / "app"
    (app / "browser" / "chrome").mkdir(parents=True)
    (app / "browser" / "chrome" / "theme.css").write_text("a{}", encoding="utf-8")
    fix = tmp_path / "theme.css"
    fix.write_text("a{color:cyan}", encoding="utf-8")
    assert probe.replace_files(app, {"browser/chrome/theme.css": fix}) == ["browser/chrome/theme.css"]
    assert (app / "browser" / "chrome" / "theme.css").read_text(encoding="utf-8") == "a{color:cyan}"
    with pytest.raises(FileNotFoundError):
        probe.replace_files(app, {"browser/chrome/nope.css": fix})


def test_new_files_are_added_only_into_an_existing_folder(tmp_path):
    app = tmp_path / "app"
    (app / "browser" / "skin").mkdir(parents=True)
    (app / "browser" / "skin" / "old.svg").write_text("<svg/>", encoding="utf-8")
    icon = tmp_path / "new.svg"
    icon.write_text("<svg id='n'/>", encoding="utf-8")
    assert probe.replace_files(app, {}, {"browser/skin/new.svg": icon}) == ["browser/skin/new.svg (new)"]
    assert (app / "browser" / "skin" / "new.svg").read_text(encoding="utf-8") == "<svg id='n'/>"
    with pytest.raises(FileExistsError):          # replacing is file=, not add=
        probe.replace_files(app, {}, {"browser/skin/old.svg": icon})
    with pytest.raises(FileNotFoundError):        # a typo in the folder is refused, not created
        probe.replace_files(app, {}, {"browser/skni/new2.svg": icon})


def test_an_early_return_in_a_probe_still_reports_done():
    # the body is pasted inside try { }; DONE must come from finally, or `return` skips it and the run times out
    cfg = probe.CFG.format(wait=1, wait_ms=1000, body="    return;")
    tail = cfg[cfg.index("    return;"):]
    assert "finally { done(); }" in tail
    assert tail.count("done()") == 1
