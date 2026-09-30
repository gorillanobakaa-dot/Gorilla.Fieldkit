"""Triage: every shipped signature parses, known failures are named, new ones are not faked."""
import pytest

from fieldkit.build import triage

MACH_CLANG = """ 0:01.20 checking for the target C compiler...
 0:01.21 E Cannot find the target C compiler
 0:01.22 E configure failed
"""
KERNEL_DEPS = """dpkg-buildpackage: info: source package linux-upstream
dpkg-checkbuilddeps: error: Unmet build dependencies: libdw-dev:native
dpkg-buildpackage: warning: build dependencies/conflicts unsatisfied; aborting
make[2]: *** [scripts/Makefile.package:121: bindeb-pkg] Error 3
"""
KERNEL_CERTS = """  CC      certs/system_keyring.o
make[4]: *** No rule to make target 'debian/canonical-certs.pem', needed by 'certs/x509_certificate_list'.  Stop.
make[3]: *** [scripts/Makefile.build:478: certs] Error 2
"""
NOVEL = """compiling foo
src/thing.c:12:3: error: frobnicator 'zork' exploded unexpectedly
make: *** [all] Error 1
"""
SILENT = "configure...\nbuilding...\n"
AGENT_HIDDEN = "Log file could not be created\n"


@pytest.mark.parametrize("name", triage.available_sets())
def test_every_signature_set_loads(name):
    sigs = triage.load_signatures(name)
    assert sigs and len({s["id"] for s in sigs}) == len(sigs)


def test_sets_exist():
    assert {"firefox-windows", "debian-kernel", "debian-packaging"} <= set(triage.available_sets())


def test_known_firefox_failure():
    r = triage.triage_text(MACH_CLANG, "firefox-windows")
    assert r["verdict"] == "known" and r["matches"][0]["id"] == "clang-cl-missing"
    assert r["matches"][0]["check"] == "clang-cl"


def test_known_kernel_failures():
    ids = {m["id"] for m in triage.triage_text(KERNEL_DEPS, "debian-kernel")["matches"]}
    assert "unmet-build-deps" in ids
    ids = {m["id"] for m in triage.triage_text(KERNEL_CERTS, "debian-kernel")["matches"]}
    assert "debian-certs" in ids


def test_auto_searches_all_sets():
    r = triage.triage_text(KERNEL_CERTS, "auto")
    assert any(m["set"] == "debian-kernel" for m in r["matches"])


def test_unknown_failure_is_not_faked():
    r = triage.triage_text(NOVEL, "auto")
    assert r["verdict"] == "unrecognised" and r["matches"] == []
    assert "frobnicator" in r["errors"][0]["line"] and "id: CHANGEME" in r["stub"]


def test_silent_log_gets_a_hint():
    r = triage.triage_text(SILENT, "auto")
    assert r["verdict"] == "no-error-lines" and "suppression" in r["hint"]


def test_whole_log_signature_catches_suppressed_output():
    r = triage.triage_text(AGENT_HIDDEN, "firefox-windows")
    assert any(m["id"] == "agent-env-suppression" for m in r["matches"])


def test_extract_errors_dedupes():
    errs = triage.extract_errors("x error: a\nx error: a\ny error: b\n")
    assert [e["line"] for e in errs] == ["x error: a", "y error: b"]


def test_unknown_set_raises():
    with pytest.raises(FileNotFoundError):
        triage.load_signatures("nope")


def test_successful_build_is_not_reported_as_suspicious():
    log = " 3:51.00 W warning: something minor\n 3:51.53 Your build was successful!\n"
    r = triage.triage_text(log, "firefox-windows")
    assert r["verdict"] == "success" and "hint" not in r


def test_harness_halt_reason_is_extracted_not_the_banner():
    log = ("[-] Installer icon: SFX stub carries the upstream icon\n[-]     fix : Run brand_installer_stub.py\n\n"
           "[!!!] FATAL HALT [!!!]\n[!!!] Refusing to start a build that cannot succeed.\n")
    r = triage.triage_text(log, "firefox-windows")
    lines = [e["line"] for e in r["errors"]]
    assert "FATAL HALT" not in " ".join(lines) and lines[0].startswith("Installer icon")
    assert any(m["id"] == "harness-blocker" for m in r["matches"])
