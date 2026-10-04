"""The claims audit: every public claim needs evidence that ran and passed; every enabled patch must be in the tree
or explained by the maintainer. Small fake patch sets, trees, installs and claims for every verdict."""
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

from fieldkit.buildh import claims as C

A_PRISTINE = """// a.js: the submit path
function one() {
  const telemetryEndpoint = "https://example.invalid/submit";
  return sendEverythingToTheServerNow(telemetryEndpoint);
}
function two() {
  return computeTheDefaultValueForTwo();
}
"""
A_PATCH = """# Gorilla: the submit function never sends telemetry anywhere.
--- a/a.js
+++ b/a.js
@@ -2,4 +2,4 @@
 function one() {
   const telemetryEndpoint = "https://example.invalid/submit";
-  return sendEverythingToTheServerNow(telemetryEndpoint);
+  return null; // GORILLA: telemetry never leaves the browser
 }
@@ -6,3 +6,3 @@
 function two() {
-  return computeTheDefaultValueForTwo();
+  return computeTheGorillaValueForTwoInstead();
 }
"""
B_PRISTINE = "function bee() {\n  return startTheRemoteAutomationServer();\n}\n"
B_PATCH = """--- a/b.js
+++ b/b.js
@@ -1,3 +1,3 @@
 function bee() {
-  return startTheRemoteAutomationServer();
+  return refuseTheRemoteAutomationServerForever();
 }
"""
C_PRISTINE = "function sea() {\n  return alreadyDoneUpstreamByMozillaItself();\n}\n"
C_PATCH = """--- a/c.js
+++ b/c.js
@@ -1,3 +1,3 @@
 function sea() {
-  return theOldWayThatUpstreamRemovedLongAgo();
+  return alreadyDoneUpstreamByMozillaItself();
 }
"""
GONE_PATCH = """--- a/gone.js
+++ b/gone.js
@@ -1,3 +1,3 @@
 function gone() {
-  return somethingThatNoLongerExistsAnywhere();
+  return somethingTheForkWantedHereInstead();
 }
"""
E_PRISTINE = "// e.js settings\nconst untouchedLeadingSetting = 0;\n"
E1_PATCH = """--- a/e.js
+++ b/e.js
@@ -1,2 +1,3 @@
 // e.js settings
 const untouchedLeadingSetting = 0;
+const firstVersionOfTheSetting = 1;
"""
E2_PATCH = """--- a/e.js
+++ b/e.js
@@ -1,3 +1,3 @@
 // e.js settings
 const untouchedLeadingSetting = 0;
-const firstVersionOfTheSetting = 1;
+const secondVersionOfTheSetting = 2;
"""
PREFS = 'pref("gorilla.test.switch", false, locked);\n'
README = """# Test browser

No telemetry is ever sent by this browser.

The sky is blue and nothing else matters here at all.

Built with `--disable-updater` so it never updates itself.

The `01.A` group removes the submit call.

The `gorilla.test.switch` pref is false and locked.
"""


def _git(w, *a):
    subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)


def world(tmp_path, tree_a=None, decisions=None, readme=README):
    """owner repo (policy, public patch set, README, decision register, mozconfig) + a git tree (pristine root commit,
    then the port)."""
    owner = tmp_path / "owner"
    pset = owner / "gorilla-patchset" / "patches"
    for g in ("01.A", "02.B"):
        (pset / g).mkdir(parents=True)
    (owner / "config").mkdir()
    (owner / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "gorilla-patchset/patches", "groups": {
        "01.A": {"status": "enabled", "reason": "test group", "exclude": ["x.patch"], "exclude_reason": "Linux only: no meaning here"},
        "02.B": {"status": "disabled", "reason": "Linux media"}}}), encoding="utf-8")
    (owner / "config" / "mozconfig.win64").write_text("ac_add_options --disable-updater\n", encoding="utf-8")
    for name, text in (("a.js.patch", A_PATCH), ("b.js.patch", B_PATCH), ("c.js.patch", C_PATCH), ("gone.js.patch", GONE_PATCH),
                       ("e1.patch", E1_PATCH), ("e2.patch", E2_PATCH), ("x.patch", B_PATCH)):
        (pset / "01.A" / name).write_text(text, encoding="utf-8")
    (pset / "02.B" / "z.patch").write_text(B_PATCH, encoding="utf-8")
    nf = pset / "01.A" / "NEW_FILES"
    (nf / "brand").mkdir(parents=True)
    (nf / "brand" / "same.txt").write_text("same bytes\n", encoding="utf-8")
    (nf / "brand" / "changed.txt").write_text("public bytes\n", encoding="utf-8")
    (nf / "brand" / "hand.txt").write_text("public bytes\n", encoding="utf-8")
    (nf / "brand" / "missing.txt").write_text("never copied\n", encoding="utf-8")
    (owner / "gorilla-patchset" / "README.md").write_text(readme, encoding="utf-8")
    (owner / "decisions").mkdir()
    (owner / "decisions" / "PRODUCT-DECISIONS.yaml").write_text(yaml.safe_dump({"release": "157.0", "decisions": decisions or [
        {"id": "D-157-00", "title": "never calls home", "decided": "2026-10-02", "by": "maintainer", "provenance": "test",
         "why": "test", "status": "enforced", "verify": [{"mozconfig_has": {"file": "config/mozconfig.win64", "text": "ac_add_options --disable-updater"}}]},
        {"id": "D-157-30", "title": "a known gap", "decided": "2026-10-03", "by": "maintainer", "provenance": "test",
         "why": "test", "status": "trade-off"}]}), encoding="utf-8")
    w = tmp_path / "tree"
    w.mkdir()
    _git(w, "init", "-q")
    _git(w, "config", "user.email", "t@example.com")
    _git(w, "config", "user.name", "t")
    (w / "browser" / "app" / "profile").mkdir(parents=True)
    for rel, text in (("a.js", A_PRISTINE), ("b.js", B_PRISTINE), ("c.js", C_PRISTINE), ("e.js", E_PRISTINE),
                      ("browser/app/profile/firefox.js", "")):
        (w / rel).write_text(text, encoding="utf-8")
    _git(w, "add", "-A")
    _git(w, "commit", "-qm", "pristine")
    (w / "a.js").write_text(tree_a if tree_a is not None else A_PRISTINE.replace(
        "  return sendEverythingToTheServerNow(telemetryEndpoint);", "  return null; // GORILLA: telemetry never leaves the browser"),
        encoding="utf-8")
    (w / "b.js").write_text(B_PRISTINE.replace("startTheRemoteAutomationServer", "refuseTheRemoteAutomationServerForever"), encoding="utf-8")
    (w / "e.js").write_text(E_PRISTINE + "const secondVersionOfTheSetting = 2;\n", encoding="utf-8")
    (w / "browser" / "app" / "profile" / "firefox.js").write_text(PREFS, encoding="utf-8")
    (w / "brand").mkdir()
    (w / "brand" / "same.txt").write_text("same bytes\n", encoding="utf-8")
    (w / "brand" / "changed.txt").write_text("other bytes\n", encoding="utf-8")
    (w / "brand" / "hand.txt").write_text("hand bytes\n", encoding="utf-8")
    _git(w, "add", "-A")
    _git(w, "commit", "-qm", "port")
    return owner, w


def install(tmp_path, build="20261002161913", prefs=PREFS):
    inst = tmp_path / "inst"
    (inst / "browser").mkdir(parents=True)
    (inst / "application.ini").write_text(f"[App]\nVersion=157.0\nBuildID={build}\n", encoding="utf-8")
    with zipfile.ZipFile(inst / "omni.ja", "w") as z:
        z.writestr("greprefs.js", "")
    with zipfile.ZipFile(inst / "browser" / "omni.ja", "w") as z:
        z.writestr("defaults/preferences/firefox.js", prefs)
    return inst


HAND_STEP = {"id": "hand-x", "kind": "model", "status": "done", "hand_port": True, "args": {"file": "brand/hand.txt"}}


def patches(res):
    return {p["patch"]: p for p in res["patch_audit"]["patches"]}


def claim(res, start):
    return next(c for c in res["claims"] if c["text"].startswith(start))


@pytest.fixture
def no_privacy_terms(monkeypatch):
    from fieldkit.core import privacy
    monkeypatch.setattr(privacy, "private_terms", lambda local=None: [])


# -- 1. the patch implementation audit --------------------------------------------------------------------------
def test_every_hunk_status_and_patch_verdict(tmp_path):
    owner, w = world(tmp_path)
    res = C.audit(owner, w, steps=[HAND_STEP], write_register=False)
    p = patches(res)
    assert p["01.A/a.js.patch"]["verdict"] == "PARTIAL" and p["01.A/a.js.patch"]["implemented_pct"] == 50.0
    assert [h["status"] for h in p["01.A/a.js.patch"]["hunks"]] == ["APPLIED", "NOT-APPLIED"]
    assert p["01.A/a.js.patch"]["fail"]                                   # partial, no maintainer decision
    assert p["01.A/b.js.patch"]["verdict"] == "IMPLEMENTED" and not p["01.A/b.js.patch"]["fail"]
    assert p["01.A/c.js.patch"]["hunks"][0]["status"] == "ALREADY-UPSTREAM"
    assert p["01.A/gone.js.patch"]["hunks"][0]["status"] == "OBSOLETE" and p["01.A/gone.js.patch"]["fail"]   # unrecorded
    assert p["01.A/e1.patch"]["hunks"][0]["status"] == "SUPERSEDED" and "01.A/e2.patch" in p["01.A/e1.patch"]["hunks"][0]["detail"]
    assert p["01.A/e2.patch"]["verdict"] == "IMPLEMENTED"
    assert p["01.A/x.patch"]["verdict"] == "DROPPED-BY-MAINTAINER" and "Linux only" in p["01.A/x.patch"]["hunks"][0]["detail"]
    assert "02.B/z.patch" not in p and "02.B" in res["patch_audit"]["disabled"]
    nf = {x["file"]: x["status"] for x in res["patch_audit"]["new_files"]}
    assert nf == {"brand/same.txt": "IDENTICAL", "brand/changed.txt": "DIFFERS", "brand/hand.txt": "CHANGED-BY-HAND-STEP",
                  "brand/missing.txt": "MISSING"}


def test_a_recorded_obsolete_step_explains_code_gone_upstream(tmp_path):
    owner, w = world(tmp_path)
    hunk = C.firefox.parse_patch(GONE_PATCH)[0]["hunks"][0]
    step = {"id": "port-01.A-gone.js-gone.js-h1", "kind": "model", "status": "obsolete", "last_why": ["upstream removed it"],
            "args": {"patch": "01.A/gone.js.patch", "file": "gone.js", "hunk": hunk}}
    p = patches(C.audit(owner, w, steps=[step], write_register=False))["01.A/gone.js.patch"]
    assert p["verdict"] == "OBSOLETE-EXPLAINED" and not p["fail"] and "upstream removed it" in p["hunks"][0]["detail"]


def test_a_step_for_a_different_hunk_does_not_speak_for_the_public_one(tmp_path):
    """The task was ported from a snapshot set: a record naming hunk 2 of a.js.patch with OTHER lines is ignored."""
    owner, w = world(tmp_path)
    other = {"header": "@@", "lines": ["-  return somethingElse();", "+  return notTheSameChange();"]}
    step = {"id": "port-01.A-a.js-a.js-h2", "kind": "model", "status": "done", "hand_port": True, "done_by": "hand",
            "args": {"patch": "01.A/a.js.patch", "file": "a.js", "hunk": other}}
    p = patches(C.audit(owner, w, steps=[step], write_register=False))["01.A/a.js.patch"]
    assert p["hunks"][1]["status"] == "NOT-APPLIED"


def test_a_maintainer_decision_explains_a_partial_patch_only_when_it_exists(tmp_path):
    owner, w = world(tmp_path)
    (owner / "claims").mkdir()
    reg = {"claims": [], "patch_decisions": {"01.A/a.js.patch": {"decision": "D-157-30", "reason": "known"},
                                             "01.A/gone.js.patch": {"decision": "D-157-99", "reason": "invented"}}}
    (owner / "claims" / "CLAIMS.yaml").write_text(yaml.safe_dump(reg), encoding="utf-8")
    p = patches(C.audit(owner, w, write_register=False))
    assert not p["01.A/a.js.patch"]["fail"] and p["01.A/a.js.patch"]["decision"] == "D-157-30"
    assert p["01.A/gone.js.patch"]["fail"]                               # a decision that is not in the register explains nothing


def test_comment_drift_is_applied_code_but_a_code_difference_is_not(tmp_path):
    hunk = {"header": "@@", "lines": [" function one() {", "-  return sendEverythingToTheServerNow(x);",
                                      "+  // GORILLA UNLEASHED - PHYSICAL LOCK with an emoji the tree does not carry",
                                      "+  return refuseToSendAnythingAnywhereEver(x);", " }"]}
    tree = ["function one() {", "  // GORILLA UNLEASHED - PHYSICAL LOCK", "  return refuseToSendAnythingAnywhereEver(x);", "}"]
    v, d = C.score(tree, hunk, "a.js")
    assert v == "APPLIED" and "comment line" in d
    v, _ = C.score(["function one() {", "  return sendEverythingToTheServerNow(x);", "}"], hunk, "a.js")
    assert v == "NOT-APPLIED"


# -- 2. the claims register ---------------------------------------------------------------------------------------
def test_extraction_and_seeding_link_only_what_the_text_names(tmp_path):
    owner, w = world(tmp_path)
    res = C.audit(owner, w, write_register=True)
    reg = yaml.safe_load((owner / "claims" / "CLAIMS.yaml").read_text(encoding="utf-8"))
    by = {c["text"]: c for c in reg["claims"]}
    tel = by["No telemetry is ever sent by this browser."]
    assert tel["source"] == "gorilla-patchset/README.md" and tel["line"] == 3 and tel["seeded"] == "auto"
    assert tel["sha256"] == C.sha(tel["text"])
    assert {next(k for k in e if k != "seeded") + ":" + str(e[next(k for k in e if k != "seeded")]) for e in tel["evidence"]} == \
        {"leakgate_policy:NETWORK_POLICY", "leakgate_policy:TELEMETRY_POLICY", "decision:D-157-00"}
    assert all(e["seeded"] == "auto" for c in reg["claims"] for e in c["evidence"])
    assert by["The sky is blue and nothing else matters here at all."]["evidence"] == []     # nothing named: no link
    head = by["Gorilla: the submit function never sends telemetry anywhere."]
    assert head["source"] == "gorilla-patchset/patches/01.A/a.js.patch" and {"patch": "01.A/a.js.patch", "seeded": "auto"} in head["evidence"]
    flag = by["Built with `--disable-updater` so it never updates itself."]
    assert {"mozconfig_has": {"file": "config/mozconfig.win64", "text": "ac_add_options --disable-updater"}, "seeded": "auto"} in flag["evidence"]
    pref = by["The `gorilla.test.switch` pref is false and locked."]
    assert {"pref": {"name": "gorilla.test.switch", "value": "false", "locked": True}, "seeded": "auto"} in pref["evidence"]
    assert {"patch_group": "01.A", "seeded": "auto"} in by["The `01.A` group removes the submit call."]["evidence"]
    assert res["new_claims"] == len(reg["claims"]) and res["register_written"]
    # patches of a disabled group and excluded patches are no claim sources for this build
    assert not any("02.B" in c["source"] or "x.patch" in c["source"] for c in reg["claims"])


def test_the_register_is_appended_to_never_rewritten(tmp_path):
    owner, w = world(tmp_path)
    C.audit(owner, w, write_register=True)
    p = owner / "claims" / "CLAIMS.yaml"
    reg = yaml.safe_load(p.read_text(encoding="utf-8"))
    sky = next(c for c in reg["claims"] if c["text"].startswith("The sky"))
    sky["evidence"] = [{"mozconfig_has": {"file": "config/mozconfig.win64", "text": "ac_add_options --disable-updater"}}]   # the maintainer's
    sky.pop("seeded")
    p.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")
    (owner / "gorilla-patchset" / "README.md").write_text(README + "\nThe browser never phones home, not even once.\n", encoding="utf-8")
    res = C.audit(owner, w, write_register=True)
    after = yaml.safe_load(p.read_text(encoding="utf-8"))["claims"]
    assert after[:len(reg["claims"])] == reg["claims"]                     # untouched, in order
    new = after[len(reg["claims"]):]
    assert len(new) == 1 and new[0]["text"].startswith("The browser never phones home") and new[0]["seeded"] == "auto"
    assert new[0]["id"] == f"C-{len(reg['claims']) + 1:04d}"
    assert claim(res, "The sky")["verdict"] == "PROVEN"
    assert p.read_bytes().count(b"\r\n") == 0                              # LF endings


# -- 3. verdicts --------------------------------------------------------------------------------------------------
def test_unlinked_claim_fails_as_unproven_and_strict_fails(tmp_path):
    owner, w = world(tmp_path)
    res = C.audit(owner, w, strict=True, write_register=False)
    c = claim(res, "The sky")
    assert c["verdict"] == "UNPROVEN" and "nothing proves" in c["why"]
    assert not res["ok"] and not res["strict_ok"]


def test_a_check_that_cannot_run_never_passes(tmp_path):
    owner, w = world(tmp_path)
    res = C.audit(owner, w, install_dir=None, write_register=False)          # no installed build at all
    pref = claim(res, "The `gorilla.test.switch`")
    assert pref["verdict"] == "UNPROVEN" and pref["results"][0][2] == "cannot run"
    tel = claim(res, "No telemetry")
    assert tel["verdict"] == "UNPROVEN"                                     # the decision passes; the leak gate cannot run
    assert any(r[2] == "ok" for r in tel["results"]) and any(r[2] == "cannot run" for r in tel["results"])


def test_proven_contradicted_and_the_leak_gate_must_be_this_builds_release_run(tmp_path):
    owner, w = world(tmp_path)
    inst = install(tmp_path)
    (owner / "state").mkdir()
    lg = owner / "state" / "leakgate_result.json"
    lg.write_text(json.dumps({"BUILD": "20261002144433", "release_run": True, "policies": {"NETWORK_POLICY": "PASS", "TELEMETRY_POLICY": "PASS"}}), encoding="utf-8")
    res = C.audit(owner, w, install_dir=inst, write_register=False)
    assert claim(res, "The `gorilla.test.switch`")["verdict"] == "PROVEN"
    assert claim(res, "Built with")["verdict"] == "PROVEN"
    assert claim(res, "The `01.A` group")["verdict"] == "CONTRADICTED"          # a.js.patch is PARTIAL
    assert claim(res, "Gorilla: the submit function")["verdict"] == "CONTRADICTED"
    tel = claim(res, "No telemetry")
    assert tel["verdict"] == "UNPROVEN" and "installed build is 20261002161913" in " ".join(r[3] for r in tel["results"])
    lg.write_text(json.dumps({"BUILD": "20261002161913", "release_run": False, "policies": {"NETWORK_POLICY": "PASS", "TELEMETRY_POLICY": "PASS"}}), encoding="utf-8")
    assert claim(C.audit(owner, w, install_dir=inst, write_register=False), "No telemetry")["verdict"] == "UNPROVEN"
    lg.write_text(json.dumps({"BUILD": "20261002161913", "release_run": True, "policies": {"NETWORK_POLICY": "PASS", "TELEMETRY_POLICY": "PASS"}}), encoding="utf-8")
    assert claim(C.audit(owner, w, install_dir=inst, write_register=False), "No telemetry")["verdict"] == "PROVEN"
    lg.write_text(json.dumps({"BUILD": "20261002161913", "release_run": True, "policies": {"NETWORK_POLICY": "FAIL", "TELEMETRY_POLICY": "PASS"}}), encoding="utf-8")
    assert claim(C.audit(owner, w, install_dir=inst, write_register=False), "No telemetry")["verdict"] == "CONTRADICTED"


def test_a_wrong_shipped_pref_contradicts(tmp_path):
    owner, w = world(tmp_path)
    inst = install(tmp_path, prefs='pref("gorilla.test.switch", true);\n')
    assert claim(C.audit(owner, w, install_dir=inst, write_register=False), "The `gorilla.test.switch`")["verdict"] == "CONTRADICTED"


def test_proof_rows_count_only_since_the_latest_install_into_this_folder(tmp_path):
    owner, w = world(tmp_path)
    inst = install(tmp_path)
    j = tmp_path / "journal.jsonl"
    ev = [{"t": "1", "event": "post_install", "target": str(inst), "results": [["egress", 0]]},
          {"t": "2", "event": "install", "target": str(inst), "ok": True},
          {"t": "3", "event": "post_install", "target": str(inst), "results": [["adblock", 0], ["leaks", 0], ["leaks", 1]]}]
    j.write_text("\n".join(json.dumps(e) for e in ev) + "\n", encoding="utf-8")
    ctx = {"journal": str(j), "install": str(inst)}
    with pytest.raises(C.Unreadable):
        C.run_check("proof_row", "egress", ctx)                             # ran before the install: not this build's
    assert C.run_check("proof_row", "adblock", ctx)[0] is True
    assert C.run_check("proof_row", "leaks", ctx)[0] is False               # one of its runs failed


def test_stale_when_the_source_changes_or_the_text_is_edited(tmp_path):
    owner, w = world(tmp_path)
    C.audit(owner, w, write_register=True)
    (owner / "gorilla-patchset" / "README.md").write_text(README.replace("The sky is blue", "The sea is green"), encoding="utf-8")
    p = owner / "claims" / "CLAIMS.yaml"
    reg = yaml.safe_load(p.read_text(encoding="utf-8"))
    tel = next(c for c in reg["claims"] if c["text"].startswith("No telemetry"))
    tel["text"] = "No telemetry is ever sent by this browser, ever."         # edited without a new sha
    p.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")
    res = C.audit(owner, w, write_register=False)
    assert claim(res, "The sky")["verdict"] == "STALE" and "no longer says" in claim(res, "The sky")["why"]
    assert claim(res, "No telemetry is ever sent by this browser, ever.")["verdict"] == "STALE"
    assert not res["lenient_ok"]


def test_strict_passes_only_when_everything_is_proven_and_in_place(tmp_path):
    readme = "# Test\n\nBuilt with `--disable-updater` so it never updates itself.\n"
    owner, w = world(tmp_path, readme=readme)
    for name in ("a.js.patch", "gone.js.patch"):
        (owner / "gorilla-patchset" / "patches" / "01.A" / name).unlink()
    for name in ("changed.txt", "missing.txt"):
        (owner / "gorilla-patchset" / "patches" / "01.A" / "NEW_FILES" / "brand" / name).unlink()
    res = C.audit(owner, w, steps=[HAND_STEP], strict=True, write_register=False)
    assert res["totals"]["claims"] == 1 and res["totals"]["PROVEN"] == 1
    assert res["ok"] and res["strict_ok"]


def test_a_maintainer_excluded_source_is_listed_not_judged(tmp_path):
    owner, w = world(tmp_path)
    (owner / "claims").mkdir()
    (owner / "claims" / "CLAIMS.yaml").write_text(yaml.safe_dump({"claims": [], "sources_excluded": {
        "gorilla-patchset/README.md": {"reason": "historical log", "by": "maintainer"}}}), encoding="utf-8")
    res = C.audit(owner, w, write_register=False)
    assert not any(c["source"] == "gorilla-patchset/README.md" for c in res["claims"])
    assert "historical log" in C.report(res)
    (owner / "claims" / "CLAIMS.yaml").write_text(yaml.safe_dump({"claims": [], "sources_excluded": {
        "gorilla-patchset/README.md": {"reason": "an agent's idea"}}}), encoding="utf-8")
    res = C.audit(owner, w, write_register=False)
    assert res["problems"] and any(c["source"] == "gorilla-patchset/README.md" for c in res["claims"])


# -- 4. the report ------------------------------------------------------------------------------------------------
def test_report_is_public_safe_relative_and_names_tree_and_build(tmp_path, no_privacy_terms):
    owner, w = world(tmp_path)
    inst = install(tmp_path)
    res = C.audit(owner, w, install_dir=inst, write_register=False)
    p, findings = C.write_report(res, owner / "claims" / "AUDIT-157.md")
    assert not findings and p.is_file()
    text = p.read_text(encoding="utf-8")
    assert res["tree"]["head"] in text and res["tree"]["root"] in text and "20261002161913" in text
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "/") not in text
    assert "`gorilla-patchset/README.md`" in text and "01.A/a.js.patch" in text and "PARTIAL" in text
    assert "## The worst gaps" in text and p.read_bytes().count(b"\r\n") == 0


def test_a_privacy_finding_means_no_report(tmp_path, monkeypatch):
    from fieldkit.core import privacy
    owner, w = world(tmp_path, readme=README + "\nThe browser never phones home to C:\\Users\\alice\\secret at all.\n")  # privacy-scan: allow
    monkeypatch.setattr(privacy, "private_terms", lambda local=None: [])
    res = C.audit(owner, w, write_register=False)
    out = owner / "claims" / "AUDIT-157.md"
    p, findings = C.write_report(res, out)
    assert p is None and findings and not out.exists()


# -- 5. wiring ----------------------------------------------------------------------------------------------------
def test_wiring_cli_post_install_and_proof_row_fail_closed():
    from fieldkit import cli
    from fieldkit.buildh import install as inst
    ap = cli.build_parser() if hasattr(cli, "build_parser") else None
    assert "claims" in inst.PROOF_CHECKS
    row = C.proof_row({"id": "t", "meta": {}, "workdir": "."}, None)
    assert row["check"].split(":")[0] == "claims" and not row["ok"]
    if ap is not None:
        a = ap.parse_args(["build-harness", "claims", "t1", "--strict", "--report", "x.md"])
        assert a.action == "claims" and a.strict and a.report == "x.md"


def test_the_leakgate_baseline_refuses_when_the_strict_claims_audit_fails(tmp_path, monkeypatch):
    import argparse
    from fieldkit.buildh import buildrun, cli as bcli, decisions as dec, install as inst, task
    owner = tmp_path / "owner"
    (owner / "state").mkdir(parents=True)
    (owner / "state" / "leakgate_result.json").write_text(json.dumps({"FINAL_RESULT": "PASS", "release_run": True, "artifacts": "x"}), encoding="utf-8")
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(tmp_path), "meta": {"upstream": {"version": "157.0"}}})
    monkeypatch.setattr(buildrun, "_owner_root", lambda t: str(owner))
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    monkeypatch.setattr(inst, "find_install", lambda: None)
    monkeypatch.setattr(dec, "check", lambda *a, **k: {"ok": True, "sha256": "x", "rows": []})
    seen = {}

    def fake_run(tid, install_dir=None, report_path=None, strict=False, write=True):
        seen.update(strict=strict, write=write)
        return {"ok": False, "task": tid, "tree": {"head": "h"}, "build_id": None, "totals": {}, "problems": [], "strict": True}
    monkeypatch.setattr(C, "run", fake_run)
    monkeypatch.setattr(C, "lines", lambda r: ["CLAIMS NOT OK"])
    from fieldkit.leakgate import gate as lg
    monkeypatch.setattr(lg, "save_baseline", lambda *a, **k: pytest.fail("a baseline was saved with unproven claims"))
    a = argparse.Namespace(action="leakgate-baseline", args=["t1"], task="t1")
    with pytest.raises(task.Refused, match="public claim must be PROVEN"):
        bcli.run(a, lambda obj, human: None)
    assert seen == {"strict": True, "write": False}


def test_the_published_audit_report_is_not_read_back_as_claims(tmp_path):
    pub = tmp_path / "gorilla-patchset"
    pub.mkdir()
    (pub / "README.md").write_text("# Gorilla\n\nThe browser never calls home.\n", encoding="utf-8")
    (pub / "AUDIT-157.md").write_text(C.AUDIT_REPORT_HEAD + " Gorilla Unleashed 157.0\n\nCONTRADICTED C-1: ...\n", encoding="utf-8")
    rels = C.public_files(tmp_path)
    assert "gorilla-patchset/README.md" in rels
    assert "gorilla-patchset/AUDIT-157.md" not in rels


def test_a_patch_explained_by_a_maintainer_decision_proves_its_claims():
    ctx = {"patches": {"07.T/x.patch": {"group": "07.T", "patch": "07.T/x.patch", "verdict": "PARTIAL", "implemented_pct": 92.3,
                                        "explained": True, "decision": "D-157-03"}}, "excluded": set(), "disabled": set()}
    ok, ev = C._patch(ctx, "07.T/x.patch")
    assert ok and "D-157-03" in ev
    assert C._group(ctx, "07.T")[0]
    ctx["patches"]["07.T/x.patch"].update(explained=False, decision=None)
    assert not C._patch(ctx, "07.T/x.patch")[0] and not C._group(ctx, "07.T")[0]


def test_a_deleted_file_is_named_by_its_minus_line():
    from fieldkit.buildh import firefox
    text = ("diff --git a/x/keep.mjs b/x/keep.mjs\n--- a/x/keep.mjs\n+++ b/x/keep.mjs\n@@ -1,2 +1,1 @@\n a\n-b\n"
            "diff --git a/x/gone.mjs b/x/gone.mjs\ndeleted file mode 100644\n--- a/x/gone.mjs\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-c\n")
    f = firefox.parse_patch(text)
    assert [x["file"] for x in f] == ["x/keep.mjs", "x/gone.mjs"] and f[1].get("deleted")
    assert f[0]["hunks"][0]["lines"] == [" a", "-b"]


def test_a_sentence_that_says_a_flag_is_not_used_does_not_link_to_it():
    sc = {"patches": [], "texts": {}, "groups": [], "targets": {}}
    ev = C.seed_evidence({"text": "You might expect a --disable-telemetry flag in mozconfig.", "source": "x.md", "line": 1}, sc, {})
    assert not any("mozconfig_has" in e for e in ev)
    ev = C.seed_evidence({"text": "The build uses --disable-default-browser-agent.", "source": "x.md", "line": 1}, sc, {})
    assert any("mozconfig_has" in e for e in ev)
