"""Migration control, the intent ledger: one intent per hunk cluster, a quoted purpose (never invented), anchors that
survive refactors, a behaviour check chosen by rule, append-only ids; and the per-migration status of every intent."""
import json
import subprocess
import zipfile

import yaml

from fieldkit.buildh import task
from fieldkit.migrate import ledger, status

FIREFOX_JS_PATCH = """--- a/browser/app/profile/firefox.js
+++ b/browser/app/profile/firefox.js
@@ -1,3 +1,5 @@
 // firefox.js
 pref("browser.keep.me", true);
+pref("browser.safebrowsing.malware.enabled", true);
+pref("toolkit.telemetry.server", "", locked);
 // end of the first block
@@ -10,2 +12,6 @@
 // tail
+pref("browser.safebrowsing.malware.enabled", false, locked);
+#ifdef XP_LINUX
+pref("security.sandbox.content.level", 4);
+#endif
"""
FOG_PATCH = """# Gorilla: Glean never starts.
--- a/toolkit/components/glean/xpcom/FOG.cpp
+++ b/toolkit/components/glean/xpcom/FOG.cpp
@@ -146,6 +146,9 @@ FOG::InitializeFOG(const nsACString& aDataPathOverride) {
 NS_IMETHODIMP
 FOG::InitializeFOG(const nsACString& aDataPathOverride) {
+  // GORILLA: skip Glean initialisation entirely, the dispatcher never spawns
+  return NS_OK;  // GORILLA early return before fog_init
+
   fog_init(&dataPath, &aAppIdOverride, aDisableInternalPings);
   const char* url = "https://incoming.telemetry.mozilla.org/submit";
 }
"""
FTL_PATCH = """--- a/browser/locales/en-US/browser/x.ftl
+++ b/browser/locales/en-US/browser/x.ftl
@@ -1,4 +1,4 @@
 # comment
-x-title = Firefox does a thing
+x-title = Gorilla does a thing
 x-other = unchanged line here

@@ -8,4 +8,4 @@
 y-block =
-  .label = Keep this
+    .label = Keep this
 z-end = end
"""
DELETE_PATCH = """# Gorilla: the chatbot module is gone.
diff --git a/browser/components/genai/Chat.sys.mjs b/browser/components/genai/Chat.sys.mjs
deleted file mode 100644
--- a/browser/components/genai/Chat.sys.mjs
+++ /dev/null
@@ -1,3 +0,0 @@
-export function chat() {
-  return fetch("https://chat.example.invalid/");
-}
"""
NEWFILE_PATCH = """# Gorilla: a stub that refuses every call.
--- /dev/null
+++ b/toolkit/components/stub/Stub.sys.mjs
@@ -0,0 +1,3 @@
+export const Stub = {
+  refuseEverythingThatAsksForTheNetwork() { return null; },
+};
"""


def _git(w, *a):
    subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)


def owner_repo(tmp_path, extra_patch=None):
    o = tmp_path / "owner"
    pset = o / "gorilla-patchset" / "patches"
    groups = {"05.PREFS": "enabled", "13.TELEMETRY.KILL": "enabled", "08.Look": "enabled", "12.MOZAMBIQUE.DRILL": "enabled",
              "20.SNAP": "enabled", "01.MEDIA": "disabled"}
    for g in groups:
        (pset / g).mkdir(parents=True)
    (o / "config").mkdir()
    (o / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "gorilla-patchset/patches", "groups": {
        g: {"status": s, "reason": f"the {g} group reason"} for g, s in groups.items()}}), encoding="utf-8")
    (pset / "05.PREFS" / "browser_app_profile_firefox.js.patch").write_text(FIREFOX_JS_PATCH, encoding="utf-8")
    (pset / "13.TELEMETRY.KILL" / "toolkit_components_glean_xpcom_FOG.cpp.patch").write_text(FOG_PATCH, encoding="utf-8")
    (pset / "08.Look" / "x.ftl.patch").write_text(FTL_PATCH, encoding="utf-8")
    (pset / "13.TELEMETRY.KILL" / "genai.patch").write_text(DELETE_PATCH, encoding="utf-8")
    (pset / "13.TELEMETRY.KILL" / "stub.patch").write_text(NEWFILE_PATCH, encoding="utf-8")
    (pset / "01.MEDIA" / "media.patch").write_text(FTL_PATCH.replace("x.ftl", "m.ftl"), encoding="utf-8")
    if extra_patch:
        (pset / "08.Look" / "extra.patch").write_text(extra_patch, encoding="utf-8")
    nf = pset / "12.MOZAMBIQUE.DRILL" / "NEW_FILES" / "distribution"
    nf.mkdir(parents=True)
    (nf / "policies.json").write_text('{"policies": {}}\n', encoding="utf-8")
    (pset / "20.SNAP" / "DELETED_FILES.manifest.txt").write_text("browser/components/aiwindow/A.sys.mjs\n", encoding="utf-8")
    (o / "decisions").mkdir()
    (o / "decisions" / "PRODUCT-DECISIONS.yaml").write_text(yaml.safe_dump({"release": "157.0", "decisions": [
        {"id": "D-157-00", "title": "THE RULE: this browser never calls home", "decided": "2026-10-02", "by": "maintainer",
         "provenance": "test", "why": "test", "status": "enforced",
         "verify": [{"pref": {"name": "toolkit.telemetry.server", "value": "", "locked": True}}]}]}), encoding="utf-8")
    (o / "claims").mkdir()
    (o / "claims" / "CLAIMS.yaml").write_text(yaml.safe_dump({"claims": [
        {"id": "C-0001", "text": "GORILLA early return before fog_init", "sha256": "x", "line": 6,
         "source": "gorilla-patchset/patches/13.TELEMETRY.KILL/toolkit_components_glean_xpcom_FOG.cpp.patch"}]}), encoding="utf-8")
    return o


def by_patch(intents, patch):
    return [e for e in intents if e["patch"] == patch]


def test_parse_reads_hunks_by_their_counts_and_knows_deleted_and_new_files():
    files = ledger.parse(DELETE_PATCH + NEWFILE_PATCH)
    assert [(f["file"], f["key"], f["mode"]) for f in files] == [
        ("browser/components/genai/Chat.sys.mjs", "/dev/null", "deleted"),
        ("toolkit/components/stub/Stub.sys.mjs", "toolkit/components/stub/Stub.sys.mjs", "new")]
    assert len(files[0]["hunks"][0]["lines"]) == 3          # the next file's '--- /dev/null' is not swallowed as a removal


def test_generate_counts_the_whole_patch_set_and_every_intent_has_a_stable_id(tmp_path):
    o = owner_repo(tmp_path)
    g = ledger.generate(o)
    assert g["stats"]["patch_files"] == 6 and g["stats"]["new_files"] == 1 and g["stats"]["deleted_files"] == 1
    ids = [e["id"] for e in g["intents"]]
    assert len(ids) == len(set(ids)) and all(i.startswith("I-") for i in ids)
    assert [e["id"] for e in ledger.generate(o)["intents"]] == ids          # stable across runs


def test_pref_checks_take_the_value_that_wins_and_skip_preprocessed_lines(tmp_path):
    g = ledger.generate(owner_repo(tmp_path))
    pref = by_patch(g["intents"], "05.PREFS/browser_app_profile_firefox.js.patch")
    checks = [c["pref"] for e in pref for c in ledger.checks_of(e) if "pref" in c]
    assert {"name": "browser.safebrowsing.malware.enabled", "value": False, "locked": True} in checks
    assert {"name": "browser.safebrowsing.malware.enabled", "value": True, "locked": False} not in checks
    assert {"name": "toolkit.telemetry.server", "value": "", "locked": True} in checks
    assert not any(c["name"] == "security.sandbox.content.level" for c in checks)
    notes = " ".join(n for e in pref for n in e["check_notes"])
    assert "superseded" in notes and "under #if" in notes
    assert all("D-157-00" in e["decisions"] for e in pref if any(c.get("pref", {}).get("name") == "toolkit.telemetry.server"
                                                                 for c in ledger.checks_of(e)))


def test_early_return_marker_purpose_anchors_and_claim_links(tmp_path):
    g = ledger.generate(owner_repo(tmp_path))
    fog = by_patch(g["intents"], "13.TELEMETRY.KILL/toolkit_components_glean_xpcom_FOG.cpp.patch")[0]
    assert fog["behaviour_check"] == [{"tree_contains": {"path": "toolkit/components/glean/xpcom/FOG.cpp",
                                                         "text": "return NS_OK;  // GORILLA early return before fog_init"}}]
    assert fog["purpose"]["quote"].startswith("GORILLA: skip Glean initialisation entirely")       # quoted from the hunk
    assert "GORILLA comment" in fog["purpose"]["source"]
    a = fog["anchors"]
    assert a["file"] == "FOG.cpp" and "FOG::InitializeFOG" in a["functions"]
    assert "hosts" not in a                                   # the URL is context, not part of the change
    assert fog["claims"] == ["C-0001"]
    gone = by_patch(g["intents"], "13.TELEMETRY.KILL/genai.patch")[0]
    assert gone["anchors"]["hosts"] == ["chat.example.invalid"] and "chat" in gone["anchors"]["functions"]


def test_files_strings_whitespace_and_scope(tmp_path):
    g = ledger.generate(owner_repo(tmp_path))
    gone = by_patch(g["intents"], "13.TELEMETRY.KILL/genai.patch")[0]
    assert gone["behaviour_check"] == [{"tree_absent": ["browser/components/genai/Chat.sys.mjs"]}]
    stub = by_patch(g["intents"], "13.TELEMETRY.KILL/stub.patch")[0]
    assert stub["behaviour_check"] == [{"tree_present": ["toolkit/components/stub/Stub.sys.mjs"]}]
    nf = [e for e in g["intents"] if e["kind"] == "new-files"][0]
    assert {"installed_present": ["distribution/policies.json"]} in nf["behaviour_check"]       # a layer that ships as a file
    dl = [e for e in g["intents"] if e["kind"] == "deleted-files"][0]
    assert {"proof_row": "excised"} in dl["behaviour_check"]
    ftl = by_patch(g["intents"], "08.Look/x.ftl.patch")
    assert ftl[0]["behaviour_check"] == [{"tree_contains": {"path": "browser/locales/en-US/browser/x.ftl", "text": "x-title = Gorilla does a thing"}}]
    assert "x-title" in ftl[0]["anchors"]["strings"]
    assert ftl[1]["behaviour_check"] == "UNCHECKABLE" and "whitespace" in ftl[1]["uncheckable_why"]
    assert ftl[0]["purpose"] == {"quote": "the 08.Look group reason", "source": "config/patch_policy.json (the group's reason)"}
    media = by_patch(g["intents"], "01.MEDIA/media.patch")
    assert media and all(e["in_scope"] is False and "disabled" in e["scope_note"] for e in media)


def test_purpose_is_unknown_rather_than_invented(tmp_path):
    o = owner_repo(tmp_path)
    pol = json.loads((o / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    pol["groups"]["08.Look"]["reason"] = ""
    (o / "config" / "patch_policy.json").write_text(json.dumps(pol), encoding="utf-8")
    g = ledger.generate(o)
    assert by_patch(g["intents"], "08.Look/x.ftl.patch")[0]["purpose"] == {"quote": "UNKNOWN", "source": None}
    assert ledger.coverage(g["intents"])["purpose_unknown"] >= 1


def test_merge_is_append_only_and_keeps_the_maintainers_fields(tmp_path):
    extra = """--- a/browser/extra.js\n+++ b/browser/extra.js\n@@ -1,2 +1,2 @@\n-const theOldBehaviourOfTheExtraFile = 1;\n+const theNewGorillaBehaviourOfTheExtraFile = 2;\n"""
    o = owner_repo(tmp_path, extra_patch=extra)
    first = ledger.generate(o)["intents"]
    x = next(e for e in first if e["patch"] == "08.Look/extra.patch")
    x["review"] = "checked by the maintainer"
    x["checks_manual"] = [{"tree_lacks": {"path": "browser/extra.js", "text": "theOldBehaviourOfTheExtraFile"}}]
    (o / "gorilla-patchset" / "patches" / "08.Look" / "extra.patch").unlink()
    merged = ledger.merge(first, ledger.generate(o)["intents"], today="2026-10-03")
    gone = next(e for e in merged if e["id"] == x["id"])
    assert gone["retired"] == "2026-10-03: no longer in the patch set" and gone["review"] == "checked by the maintainer"
    assert ledger.checks_of(gone) == x["checks_manual"]
    assert len(merged) == len(first)
    assert ledger.coverage(merged)["retired"] == 1


def test_save_writes_lf_and_loads_back(tmp_path):
    o = owner_repo(tmp_path)
    g = ledger.generate(o)
    p = ledger.save(o, g["intents"], g["stats"])
    raw = p.read_bytes()
    assert b"\r\n" not in raw and raw.startswith(b"# Gorilla Firefox: the intent ledger")
    assert len(ledger.load(o)["intents"]) == len(g["intents"])


# -- status in one migration ----------------------------------------------------------------------------------------
def tree_and_install(tmp_path, ship_policies=False, shipped_prefs=None):
    w = tmp_path / "tree"
    (w / "browser" / "app" / "profile").mkdir(parents=True)
    (w / "toolkit" / "components" / "glean" / "xpcom").mkdir(parents=True)
    (w / "browser" / "locales" / "en-US" / "browser").mkdir(parents=True)
    (w / "browser" / "components" / "genai").mkdir(parents=True)
    _git(w, "init", "-q")
    _git(w, "config", "user.email", "t@example.com")
    _git(w, "config", "user.name", "t")
    fj = w / "browser" / "app" / "profile" / "firefox.js"
    fj.write_text('// firefox.js\npref("browser.keep.me", true);\n// end of the first block\n' + "\n" * 6 + "// tail\n", encoding="utf-8")
    fog = w / "toolkit" / "components" / "glean" / "xpcom" / "FOG.cpp"
    fog.write_text("x\n" * 145 + 'NS_IMETHODIMP\nFOG::InitializeFOG(const nsACString& aDataPathOverride) {\n'
                   '  fog_init(&dataPath, &aAppIdOverride, aDisableInternalPings);\n'
                   '  const char* url = "https://incoming.telemetry.mozilla.org/submit";\n}\n', encoding="utf-8")
    ftl = w / "browser" / "locales" / "en-US" / "browser" / "x.ftl"
    ftl.write_text("# comment\nx-title = Firefox does a thing\nx-other = unchanged line here\n\n\n\n\ny-block =\n  .label = Keep this\nz-end = end\n",
                   encoding="utf-8")
    (w / "browser" / "components" / "genai" / "Chat.sys.mjs").write_text('export function chat() {\n  return fetch("https://chat.example.invalid/");\n}\n', encoding="utf-8")
    _git(w, "add", "-A")
    _git(w, "commit", "-qm", "pristine")
    fj.write_text(fj.read_text(encoding="utf-8").replace("// end of the first block", 'pref("browser.safebrowsing.malware.enabled", true);\n'
                  'pref("toolkit.telemetry.server", "", locked);\n// end of the first block')
                  + 'pref("browser.safebrowsing.malware.enabled", false, locked);\n#ifdef XP_LINUX\npref("security.sandbox.content.level", 4);\n#endif\n',
                  encoding="utf-8")
    fog.write_text(fog.read_text(encoding="utf-8").replace("  fog_init(", "  // GORILLA: skip Glean initialisation entirely, the dispatcher never spawns\n"
                   "  return NS_OK;  // GORILLA early return before fog_init\n\n  fog_init("), encoding="utf-8")
    (w / "browser" / "components" / "genai" / "Chat.sys.mjs").unlink()
    (w / "toolkit" / "components" / "stub").mkdir(parents=True)
    (w / "toolkit" / "components" / "stub" / "Stub.sys.mjs").write_text(
        "export const Stub = {\n  refuseEverythingThatAsksForTheNetwork() { return null; },\n};\n", encoding="utf-8")
    (w / "distribution").mkdir()
    (w / "distribution" / "policies.json").write_text('{"policies": {}}\n', encoding="utf-8")
    _git(w, "add", "-A")
    _git(w, "commit", "-qm", "port")
    inst = tmp_path / "inst"
    (inst / "browser").mkdir(parents=True)
    (inst / "application.ini").write_text("[App]\nBuildID=20261003000000\n", encoding="utf-8")
    with zipfile.ZipFile(inst / "omni.ja", "w") as z:
        z.writestr("greprefs.js", "")
    with zipfile.ZipFile(inst / "browser" / "omni.ja", "w") as z:
        z.writestr("defaults/preferences/firefox.js", shipped_prefs if shipped_prefs is not None else
                   'pref("browser.safebrowsing.malware.enabled", false, locked);\npref("toolkit.telemetry.server", "", locked);\n')
    if ship_policies:
        (inst / "distribution").mkdir()
        (inst / "distribution" / "policies.json").write_text("{}", encoding="utf-8")
    return w, inst


def _task(w):
    groups = ("05.PREFS", "13.TELEMETRY.KILL", "08.Look", "12.MOZAMBIQUE.DRILL", "20.SNAP")
    return {"id": "t-mig", "workdir": str(w), "steps": [{"id": f"apply-{g}", "kind": "script", "status": "done", "args": {"group": g}}
                                                       for g in groups], "meta": {"upstream": {"version": "157.0"}}}


def test_status_layers_tree_and_build(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    o = owner_repo(tmp_path)
    w, inst = tree_and_install(tmp_path)
    intents = ledger.generate(o)["intents"]
    res = status.compute(_task(w), o, intents, inst)
    rows = res["rows"]
    st = {}
    for e in intents:
        if e["kind"] == "hunks":
            st.setdefault(e["patch"], rows[e["id"]]["status"])        # the first intent of each patch
    pref_rows = [rows[e["id"]] for e in intents if e["patch"].startswith("05.PREFS")]
    assert any(r["status"] == "PROVEN-IN-BUILD" for r in pref_rows)            # shipped value and lock match
    assert st["13.TELEMETRY.KILL/toolkit_components_glean_xpcom_FOG.cpp.patch"] == "VERIFIED-IN-TREE"
    assert st["13.TELEMETRY.KILL/genai.patch"] == "VERIFIED-IN-TREE"
    assert st["01.MEDIA/media.patch"] == "OUT-OF-SCOPE"
    assert st["08.Look/x.ftl.patch"] == "NOT-IN-TREE"                          # the string was never changed
    nf = next(e for e in intents if e["kind"] == "new-files")
    assert rows[nf["id"]]["status"] == "BUILD-CONTRADICTED"                     # in the tree, missing from the install
    assert any("MISSING from the install: distribution/policies.json" in c[3] for c in rows[nf["id"]]["checks"])
    assert res["by_group"]["13.TELEMETRY.KILL"]["in_tree"] == 3
    assert list(res["by_group"])[:4] == ["13.TELEMETRY.KILL", "12.MOZAMBIQUE.DRILL", "05.PREFS", "08.Look"]   # the plan's order


def test_status_build_contradiction_and_the_ledger_stamp(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    o = owner_repo(tmp_path)
    w, inst = tree_and_install(tmp_path, ship_policies=True, shipped_prefs='pref("browser.safebrowsing.malware.enabled", true);\n')
    intents = ledger.generate(o)["intents"]
    res = status.compute(_task(w), o, intents, inst)
    bad = [i for i, r in res["rows"].items() if r["status"] == "BUILD-CONTRADICTED"]
    assert bad and all(any(c[2] == "FAIL" for c in res["rows"][i]["checks"]) for i in bad)
    nf = next(e for e in intents if e["kind"] == "new-files")
    assert res["rows"][nf["id"]]["status"] == "PROVEN-IN-BUILD"
    status.stamp(intents, res["rows"], "157.0")
    assert all(e["status"]["157.0"] == res["rows"][e["id"]]["status"] for e in intents)


def test_a_one_line_file_is_checked_by_a_window_around_the_change():
    old = '{"files":{"a.rs":"' + "0" * 64 + '","b.rs":"' + "1" * 64 + '","c.rs":"' + "2" * 64 + '"}}'
    new = old.replace("1" * 64, "f" * 64)
    h = {"n": 1, "lines": ["-" + old, "+" + new]}
    check, label = ledger._text_check("x/.cargo-checksum.json", h)
    text = check["tree_contains"]["text"]
    assert len(text) <= 120 and text in new and text not in old and "f" * 20 in text
