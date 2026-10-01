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
