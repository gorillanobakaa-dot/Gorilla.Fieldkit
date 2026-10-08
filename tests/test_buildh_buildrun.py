"""The build loop (buildrun): what may start, what counts as a stop, which stops have a fix."""

from fieldkit.buildh import buildrun, ownercheck



PREFLIGHT = """[*] Gorilla Firefox harness - stage 'preflight'

[+] clang-cl on PATH: ok

[-] Package manifest resolves (BLOCKER): 2 manifest entr(y/ies) missing: BINPATH/DLL_PREFIX Microsoft.WindowsAppRuntime

[*]       fix : build, then run validate_package_manifest.py

[-] Bundled extensions are visible (BLOCKER): bundled extension misconfigured: ublock-origin: not in the build output

[*]       fix : build first

"""





def test_only_build_dependent_blockers_may_be_forced(monkeypatch):

    monkeypatch.setattr(ownercheck, "run_preflight", lambda root, python=None: (1, PREFLIGHT))

    said = []

    may, force, blockers = buildrun.owner_preflight_ok("x", said.append)

    assert may and force and [b["name"] for b in blockers] == ["Package manifest resolves", "Bundled extensions are visible"]

    assert all("--force" in s for s in said)

    hard = PREFLIGHT + "[-] clang-cl on PATH (BLOCKER): not found\n"

    monkeypatch.setattr(ownercheck, "run_preflight", lambda root, python=None: (1, hard))

    may, force, blockers = buildrun.owner_preflight_ok("x", said.append)

    assert not may and any("HARD" in s for s in said)





def test_stops_are_classified_and_errors_extracted():

    lines = ["mach build", " 0:12.34 Automatic clobber was not requested", " 0:12.35 E Build configuration changed. A clobber is required.",

             " 0:12.36 *** Fix above errors and then restart with 'mach build'", "make: *** [Makefile:1: default] Error 1"]

    name, fix = buildrun.classify(lines)

    assert name == "clobber-required" and fix is buildrun.fix_clobber

    errs = buildrun.error_lines(lines)

    assert errs[0].endswith("A clobber is required.") and any("Error 1" in e for e in errs)

    assert buildrun.classify(["something odd happened", "error: widget.cpp(12): unknown type"]) == ("unknown", None)

    assert buildrun.classify(["Refusing to start a build that cannot succeed. Fix the above"])[0] == "owner-preflight-blocks"

    assert buildrun.classify(["browser/locales/en-US/browser/browser.ftl: Duplicate message id x"])[0] == "fluent-duplicate"

    assert buildrun.error_lines(["0 errors, 3 warnings", "all good"]) == []





def test_fluent_stop_reconciles_the_named_file(tmp_path, monkeypatch):

    from fieldkit.buildh import verify as vf

    w, tr = tmp_path / "w", tmp_path / "t"

    for root, text in ((w, "a = A\nb = B\na = A\n"), (tr, "a = A\nb = B\n")):

        (root / "l").mkdir(parents=True)

        (root / "l/x.ftl").write_text(text, encoding="utf-8")

    monkeypatch.setattr(vf, "_truth_root", lambda hr, workdir=None: tr)

    t = {"workdir": str(w), "meta": {"harness_root": "h"}}

    said = []

    ok, what = buildrun.fix_fluent(t, "root", said.append, lines=["l/x.ftl: Duplicate message id a"])

    assert ok and "removed surplus a" in what

    assert (w / "l/x.ftl").read_text(encoding="utf-8").count("a = A") == 1

    assert buildrun.fix_fluent(t, "root", said.append, lines=["no file here"])[0] is False





MOZBUILD_STOP = [" 1:13.20 FATAL ERROR PROCESSING MOZBUILD FILE",

                 " 1:13.20     D:/build/firefox/157.0-truth/widget/windows/moz.build",

                 " 1:13.20     File listed in FINAL_TARGET_FILES does not exist: D:/home/.mozbuild/winappsdk-x86_64-pc-windows-msvc/Microsoft.WindowsAppRuntime.dll",

                 ' 1:16.14 E *** Fix above errors and then restart with "./mach build"',

                 "[*]   Objdir vs CLOBBER        ok   objdir has no CLOBBER record yet"]





def test_a_missing_toolchain_is_its_own_stop_not_a_clobber(tmp_path):

    name, fix = buildrun.classify(MOZBUILD_STOP)

    assert name == "missing-toolchain" and fix is buildrun.fix_toolchain      # the word CLOBBER elsewhere must not win

    assert buildrun.classify(["Objdir vs CLOBBER ok", "random failure"])[0] == "unknown"

    (tmp_path / "taskcluster/kinds/toolchain").mkdir(parents=True)

    (tmp_path / "taskcluster/kinds/toolchain/misc.yml").write_text(

        "win64-WindowsAppSDK:\n    description: x\n    run:\n        toolchain-artifact: public/build/winappsdk.tar.zst\n"

        "        toolchain-alias: winappsdk-x86_64-pc-windows-msvc\n\nwin32-WindowsAppSDK:\n        toolchain-alias: winappsdk-x86-pc-windows-msvc\n",

        encoding="utf-8")

    assert buildrun.toolchain_job(tmp_path, "winappsdk-x86_64-pc-windows-msvc") == "win64-WindowsAppSDK"

    assert buildrun.toolchain_job(tmp_path, "winappsdk-x86-pc-windows-msvc") == "win32-WindowsAppSDK"

    assert buildrun.toolchain_job(tmp_path, "nothing") is None

    m = buildrun.MISSING_TOOLCHAIN.search("\n".join(MOZBUILD_STOP))

    assert (m.group(1), m.group(2)) == ("winappsdk-x86_64-pc-windows-msvc", "Microsoft.WindowsAppRuntime.dll")





def test_a_stale_toolchain_folder_is_moved_aside_and_success_is_the_file(tmp_path, monkeypatch):

    home = tmp_path / "home"

    (home / ".mozbuild" / "winappsdk-x86_64-pc-windows-msvc").mkdir(parents=True)

    (home / ".mozbuild" / "winappsdk-x86_64-pc-windows-msvc" / "old.dll").write_bytes(b"x")

    monkeypatch.setattr(buildrun.Path, "home", classmethod(lambda cls: home))

    monkeypatch.setattr(buildrun, "toolchain_job", lambda src, alias: "win64-WindowsAppSDK")

    monkeypatch.setattr(buildrun, "MOZBUILD_BASH", tmp_path / "bash.exe")

    (tmp_path / "bash.exe").write_bytes(b"")

    from fieldkit.buildh import compile as cg

    monkeypatch.setattr(cg, "mozconfig_path", lambda root: tmp_path / "mozconfig")



    def fake_fetch(cmd, **kw):

        d = home / ".mozbuild" / "winappsdk-x86_64-pc-windows-msvc"

        d.mkdir()

        (d / "Microsoft.WindowsAppRuntime.dll").write_bytes(b"dll")

        class R: returncode, stdout, stderr = 0, "", ""

        return R()

    monkeypatch.setattr(buildrun.subprocess, "run", fake_fetch)

    said = []

    ok, what = buildrun.fix_toolchain({"workdir": str(tmp_path)}, tmp_path, said.append, MOZBUILD_STOP)

    assert ok and "present" in what

    assert any("older bootstrap" in x for x in said)

    assert [d.name for d in (home / ".mozbuild").iterdir() if d.name.startswith("winappsdk") and "stale" in d.name]





def test_the_power_scheme_is_put_back_when_the_stage_leaves_another(monkeypatch):

    calls = []

    states = iter([("g-build", "Gorilla Build"), ("g-bal", "Balanced")])

    monkeypatch.setattr(buildrun, "active_power_scheme", lambda: next(states))

    monkeypatch.setattr(buildrun.subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    said = []

    assert buildrun.restore_power_scheme(("g-bal", "Balanced"), said.append)

    assert calls == [["powercfg", "/setactive", "g-bal"]] and "put back to 'Balanced'" in said[0]

    monkeypatch.setattr(buildrun, "active_power_scheme", lambda: ("g-bal", "Balanced"))

    assert not buildrun.restore_power_scheme(("g-bal", "Balanced"), said.append)





def test_thermal_verdict_catches_a_stuck_sensor_and_the_hard_ceiling(tmp_path):

    f = tmp_path / "thermal.csv"

    rows = ["elapsed_s,temp_c,perf_pct"] + [f"{i*5},41.85,86.0" for i in range(36)]

    f.write_text("\n".join(rows) + "\n", encoding="utf-8")

    assert "stuck at 41.85" in buildrun.thermal_verdict(f)

    f.write_text("\n".join(rows[:-1] + ["55,42.10,86.0"]) + "\n", encoding="utf-8")

    assert buildrun.thermal_verdict(f) is None                         # it moved: live

    f.write_text("\n".join(["elapsed_s,temp_c,perf_pct"] + [f"{i*5},41.85,20.0" for i in range(12)]) + "\n", encoding="utf-8")

    assert buildrun.thermal_verdict(f) is None                         # idle: a flat reading proves nothing

    f.write_text("elapsed_s,temp_c,perf_pct\n5,96.0,80.0\n", encoding="utf-8")

    assert "hard ceiling" in buildrun.thermal_verdict(f)

    assert buildrun.thermal_verdict(tmp_path / "missing.csv") is None

    assert buildrun.classify(["[-] THERMAL ABORT: temperature source stuck"])[0] == "thermal"





def test_half_written_objects_are_swept_and_valid_ones_kept(tmp_path):
    (tmp_path / "media/x86").mkdir(parents=True)
    (tmp_path / "media/x86/vp9itxfm.obj").write_bytes(b"")                       # killed mid-write
    (tmp_path / "media/good.obj").write_bytes(bytes([0x64, 0x86]) + bytes(60))     # COFF x64
    (tmp_path / "conftest.o").write_bytes(bytes([0]) + b"asm" + bytes(20))         # a wasm conftest
    (tmp_path / "media/junk.obj").write_bytes(b"not an object at all")
    said = []
    removed = buildrun.sweep_objects(tmp_path, said.append)
    import os; assert sorted(os.path.basename(p) for p in removed) == ["junk.obj", "vp9itxfm.obj"]
    assert (tmp_path / "media/good.obj").exists() and (tmp_path / "conftest.o").exists()
    assert buildrun.classify(["10:05.05 E lld-link: error: x86/vp9itxfm.obj: unknown file type"])[0] == "corrupt-object"


def test_console_interrupt_is_classified_and_retried():
    from fieldkit.buildh import buildrun
    name, fix = buildrun.classify(["[!!!] FATAL HALT [!!!]", "[!!!] mach package failed with 3221225786"])
    assert name == "console-interrupt" and fix is buildrun.fix_retry
    ok, what = buildrun.fix_retry({}, ".", lambda m: None)
    assert ok and "retry" in what


def test_undeclared_jar_manifest_is_declared_again_and_emptied(tmp_path, monkeypatch):
    from fieldkit.buildh import buildrun, handedit
    w = tmp_path / "tree"
    (w / "toolkit/components/ml").mkdir(parents=True)
    (w / "toolkit/components/ml/moz.build").write_text("# GORILLA excised: jar.mn (dropped)\nDIRS += [\"ipc\"]\n", encoding="utf-8")
    (w / "toolkit/components/ml/jar.mn").write_text("toolkit.jar:\n    content/global/ml/X.sys.mjs (X.sys.mjs)\n", encoding="utf-8")
    recorded = []
    monkeypatch.setattr(handedit, "record", lambda tid, files, why: recorded.append(files) or ["h1", "h2"])
    lines = ["The error occurred while processing the following file or one of the files it includes:", "",
             "    " + str(w / "toolkit/components/ml/moz.build").replace("\\", "/"), "",
             "The reported error is:", "    A jar.mn exists but it is not referenced in the moz.build file. Please define JAR_MANIFESTS."]
    name, fix = buildrun.classify(lines)
    assert name == "jar-manifest-undeclared" and fix is buildrun.fix_jar_manifest
    ok, what = fix({"id": "t", "workdir": str(w)}, w, lambda m: None, lines)
    assert ok and recorded == [["toolkit/components/ml/moz.build", "toolkit/components/ml/jar.mn"]]
    mb = (w / "toolkit/components/ml/moz.build").read_text(encoding="utf-8")
    assert 'JAR_MANIFESTS += ["jar.mn"]' in mb
    assert "X.sys.mjs" not in (w / "toolkit/components/ml/jar.mn").read_text(encoding="utf-8")


def test_gate_runs_repair_once_when_only_the_repairable_rows_fail(monkeypatch, tmp_path):
    from fieldkit.buildh import buildrun, compile as cg, task, repair
    calls = {"gate": 0, "repair": 0}
    def gate(tid, harness_root=None, write=True):
        calls["gate"] += 1
        bad = calls["gate"] == 1
        return [{"check": "tree: every changed build or JS file parses (moz.build, .py, .json, .mjs, .js)", "ok": not bad, "evidence": "x"}]
    monkeypatch.setattr(cg, "gate", gate)
    monkeypatch.setattr(repair, "run", lambda tid, say=print: calls.__setitem__("repair", calls["repair"] + 1) or {"ok": True, "repaired": ["a"], "refused": []})
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(tmp_path), "meta": {}, "steps": []})
    monkeypatch.setattr(task, "journal", lambda t, ev, **kw: None)
    monkeypatch.setattr(buildrun, "_owner_root", lambda t: str(tmp_path))
    from fieldkit.buildh import aboutpages
    monkeypatch.setattr(aboutpages, "prebuild", lambda t, tid, say=print: None)   # its own test below
    monkeypatch.setattr(buildrun, "owner_preflight_ok", lambda root, say: (False, False, [{"name": "stop-here"}]))
    r = buildrun.run("t", say=lambda m: None)
    assert calls == {"gate": 2, "repair": 1} and r["why"].startswith("the owner's preflight")


def test_thermal_proof_waits_for_a_busy_machine(monkeypatch):
    from fieldkit.buildh import buildrun
    readings = iter([90.0, 80.0, 20.0])
    monkeypatch.setattr(buildrun, "cpu_busy_percent", lambda sample=2.0: next(readings))
    slept, said = [], []
    busy = buildrun.wait_for_idle(said.append, busy_max=35.0, timeout=600, sleep=slept.append)
    assert busy == 20.0 and slept == [20, 20] and len(said) == 2


def test_dist_sweep_removes_stale_excised_paths(tmp_path):
    from fieldkit.buildh import buildrun
    d = tmp_path / "dist/bin"
    (d / "chrome/toolkit/content/global/ml/backends").mkdir(parents=True)
    (d / "chrome/toolkit/content/global/ml/MLEngine.worker.mjs").write_text("", encoding="utf-8")
    (d / "chrome/toolkit/content/global/xml").mkdir(parents=True)
    (d / "chrome/toolkit/content/global/xml/XMLPrettyPrint.css").write_text("", encoding="utf-8")
    removed = buildrun.sweep_excised_dist(d, lambda m: None)
    assert "chrome/toolkit/content/global/ml/" in removed
    assert not (d / "chrome/toolkit/content/global/ml").exists() and (d / "chrome/toolkit/content/global/xml/XMLPrettyPrint.css").exists()


def test_a_failing_pre_build_about_pages_run_stops_the_build_before_the_compile(monkeypatch, tmp_path):
    """2026-10-08: the about: pages are read before the compile too (owner: checks "before or after the build ...
    preferably both"); a FAIL there stops build-run before the owner's preflight and the compile."""
    from fieldkit.buildh import buildrun, compile as cg, task, aboutpages
    monkeypatch.setattr(cg, "gate", lambda tid, harness_root=None, write=True: [{"check": "x", "ok": True, "evidence": ""}])
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(tmp_path), "meta": {}, "steps": []})
    monkeypatch.setattr(task, "journal", lambda t, ev, **kw: None)
    monkeypatch.setattr(buildrun, "_owner_root", lambda t: str(tmp_path))
    monkeypatch.setattr(aboutpages, "prebuild", lambda t, tid, say=print: {"ok": False, "notes": [],
                        "rows": [{"check": "about-pages: every listed page shows its text", "ok": False, "evidence": "blank: about:studies"}]})
    monkeypatch.setattr(buildrun, "owner_preflight_ok", lambda root, say: (_ for _ in ()).throw(AssertionError("went on")))
    r = buildrun.run("t", say=lambda m: None)
    assert r["why"] == "pre-build about: pages" and not r["ok"]
