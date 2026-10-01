"""Live run 16 (2026-10-01): three "file no longer exists" owner steps and one hunk Gemma failed three times.

None of the three files was dead: tabbrowser.js moved into Tabbrowser.sys.mjs; the two newtab CSS files are now
GENERATED from Sass at build time. And the hunk (ActorManagerParent h1) removes one whole `MLEngine: { ... },` block
that upstream re-wrapped inside, so no line tier matched and the line-by-line questions left `child: {` behind.
All text below is the real Firefox 157 text and the real Gorilla hunks.
"""
import subprocess
from pathlib import Path

import pytest

from fieldkit.buildh import firefox, relocate

REAL_157 = """  HPKEConfigManager: {
    remoteTypes: ["privilegedabout"],
    parent: {
      esModuleURI: "resource://gre/modules/HPKEConfigManager.sys.mjs",
    },
  },

  // A single process (shared with translations) that manages machine learning engines.
  MLEngine: {
    remoteTypes: ["inference"],
    parent: {
      esModuleURI:
        "moz-src:///toolkit/components/ml/actors/MLEngineParent.sys.mjs",
    },
    child: {
      esModuleURI:
        "moz-src:///toolkit/components/ml/actors/MLEngineChild.sys.mjs",
    },
    enablePreference: "browser.ml.enable",
  },

  ProcessConduits: {
    // "parent" remoteTypes is currently needed to support MV3 background service workers
    // also when extensions.webextensions.remote is set to false.
    remoteTypes: ["parent", "extension"],
    parent: {
      esModuleURI: "resource://gre/modules/ConduitsParent.sys.mjs",
    },
    child: {
      esModuleURI: "resource://gre/modules/ConduitsChild.sys.mjs",
    },
""".splitlines()

H1 = {"lines": [
    "     },",
    "   },",
    " ",
    "-  // A single process (shared with translations) that manages machine learning engines.",
    "-  MLEngine: {",
    '-    remoteTypes: ["inference"],',
    "-    parent: {",
    '-      esModuleURI: "resource://gre/actors/MLEngineParent.sys.mjs",',
    "-    },",
    "-    child: {",
    '-      esModuleURI: "resource://gre/actors/MLEngineChild.sys.mjs",',
    "-    },",
    '-    enablePreference: "browser.ml.enable",',
    "-  },",
    "+  // GORILLA excised: MLEngine actor (ml component removed).",
    " ",
    "   ProcessConduits: {",
    '     // "parent" remoteTypes is currently needed to support MV3 background service workers',
]}


def test_block_removal_takes_the_whole_rewrapped_block_and_passes_both_checks():
    notes = []
    new = firefox.block_removal(REAL_157, H1, notes)
    assert "  MLEngine: {" not in new and not any("MLEngineParent" in l for l in new)
    assert "  // GORILLA excised: MLEngine actor (ml component removed)." in new
    assert new.count("  ProcessConduits: {") == 1 and "  HPKEConfigManager: {" in new
    # the block's generic inner lines (`child: {`, `},`) went with it; ProcessConduits' own `child: {` stayed
    assert sum(1 for l in new if l.strip() == "child: {") == 1
    assert firefox.hunk_problems(REAL_157, new, H1) == []
    assert firefox.collateral(REAL_157, new, H1) == []           # the re-wrapped esModuleURI lines are the block's
    assert "13 lines in the file, 11 in the hunk" in notes[0]


def test_block_removal_refuses_an_ambiguous_opener_or_a_non_block():
    twice = REAL_157 + ["  MLEngine: {", "  },"]
    with pytest.raises(firefox.Ambiguous):
        firefox.block_removal(twice, H1)
    with pytest.raises(firefox.Ambiguous):
        firefox.block_removal(REAL_157, {"lines": ["-    enablePreference: \"browser.ml.enable\",", "+    x: 1,"]})


def _tree(tmp_path):
    w = tmp_path / "tree"
    (w / "browser/extensions/newtab/content-src/styles/nova").mkdir(parents=True)
    (w / "browser/extensions/newtab/moz.build").write_text(
        'GeneratedFile(\n    "css/activity-stream.css",\n    "css/nova/activity-stream.css",\n'
        '    script="build-newtab-bundles.py",\n    entry_point="generate_css",\n)\n', encoding="utf-8")
    (w / "browser/extensions/newtab/content-src/styles/activity-stream.scss").write_text("hr {\n  color: red;\n}\n",
                                                                                           encoding="utf-8")
    (w / "browser/extensions/newtab/content-src/styles/nova/activity-stream.scss").write_text("a { b: c; }\n",
                                                                                                encoding="utf-8")
    (w / "browser/components/tabbrowser").mkdir(parents=True)
    (w / "browser/components/tabbrowser/Tabbrowser.sys.mjs").write_text("\n".join([
        '  "about:privatebrowsing": "chrome://browser/skin/privatebrowsing/favicon.svg",',
        '  "chrome://browser/content/aiwindow/aiWindow.html":',
        '    "chrome://browser/skin/smart-window-simplified.svg",',
        "};", "    if (lazy.AIWindow.isAIWindowActive(this.documentGlobal)) {",
        "      let firstURI = Array.isArray(uriToLoad) ? uriToLoad[0] : uriToLoad;",
        "        // firstURI may be a Promise (uriToLoadPromise still resolving while",
        "        // SessionStore restores) or empty; only build a URI from a real",
        "        // string, otherwise default to transparent like the no-URI case.",
        "    let uniqueId = this.#generateUniquePanelID();", ""]), encoding="utf-8")
    (w / "browser/components/other.js").write_text("    let uniqueId = this._generateUniquePanelID();\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], check=True)
    return w


CSS_HUNK = {"lines": [" hr {", "   color: red;", " }", "+", "+/* ===== GORILLA-NEWTAB-THEME-BEGIN ===== */",
                      "+:root {", "+  color-scheme: dark;", "+  --newtab-background-color: #000000;", "+}"]}

TAB_HUNKS = [{"lines": [
    '     "about:privatebrowsing":', '       "chrome://browser/skin/privatebrowsing/favicon.svg",',
    '-    "chrome://browser/content/aiwindow/aiWindow.html":', '-      "chrome://browser/skin/smart-window-simplified.svg",',
    "   };"]}, {"lines": [
    "-      if (AIWindow.isAIWindowActive(window)) {",
    "-        let firstURI = Array.isArray(uriToLoad) ? uriToLoad[0] : uriToLoad;",
    "-          // firstURI may be a Promise (uriToLoadPromise still resolving while",
    "-          // SessionStore restores) or empty; only build a URI from a real",
    "-          // string, otherwise default to transparent like the no-URI case.",
    "       let uniqueId = this._generateUniquePanelID();"]}]


def test_generated_css_is_retargeted_to_its_sass_entry_and_appended_once(tmp_path):
    w = _tree(tmp_path)
    src, why = relocate.generated_source(w, "browser/extensions/newtab/css/activity-stream.css")
    assert src == "browser/extensions/newtab/content-src/styles/activity-stream.scss" and "moz.build generates" in why
    assert relocate.generated_source(w, "browser/extensions/newtab/css/nova/activity-stream.css")[0].endswith("nova/activity-stream.scss")
    assert relocate.generated_source(w, "browser/components/tabbrowser/content/tabbrowser.js") == (None, "not a generated file")
    steps, _ = relocate.steps_for_missing(w, "16.D", "16.D/x.patch", "x", "browser/extensions/newtab/css/activity-stream.css",
                                          [CSS_HUNK], set())
    assert [s["id"] for s in steps] == ["append-16.D-x"] and steps[0]["kind"] == "script"
    t = {"workdir": str(w)}
    r = relocate.step_append_source(t, **steps[0]["args"])
    assert r["ok"] and "appended 6 lines" in r["summary"]
    assert relocate.check_append_source(t, **steps[0]["args"])["ok"]
    again = relocate.step_append_source(t, **steps[0]["args"])
    assert again["ok"] and "already" in again["summary"]
    text = (w / src).read_text(encoding="utf-8")
    assert text.count("GORILLA-NEWTAB-THEME-BEGIN") == 1 and text.startswith("hr {")
    # a patch that also REMOVES lines from the compiled file is not an append: the owner decides
    steps, why = relocate.steps_for_missing(w, "16.D", "16.D/x.patch", "x", "browser/extensions/newtab/css/activity-stream.css",
                                            [{"lines": ["-hr {", "+hr2 {"]}], set())
    assert steps == [] and "owner decides" in why


def test_moved_file_is_found_by_its_lines_and_gets_port_steps(tmp_path):
    w = _tree(tmp_path)
    new, why = relocate.moved_file(w, "browser/components/tabbrowser/content/tabbrowser.js", TAB_HUNKS)
    assert new == "browser/components/tabbrowser/Tabbrowser.sys.mjs" and "no other file comes close" in why
    steps, _ = relocate.steps_for_missing(w, "16.D", "16.D/tb.patch", "tb", "browser/components/tabbrowser/content/tabbrowser.js",
                                          TAB_HUNKS, set())
    assert [s["id"] for s in steps] == ["port-16.D-tb-Tabbrowser.sys.mjs-h1", "port-16.D-tb-Tabbrowser.sys.mjs-h2"]
    assert steps[0]["allowed"] == [new] and steps[0]["args"]["file"] == new and "upstream moved" in steps[0]["title"]
    # lines nothing holds: no home, the owner step stands
    assert relocate.moved_file(w, "x/y.js", [{"lines": ["-this line is nowhere in the tree at all();", "-nor is this other one here();"]}])[0] is None


def test_heal_replaces_open_owner_steps_and_journals_nothing_itself(tmp_path):
    w = _tree(tmp_path)
    hr = tmp_path / "harness"
    (hr / "config").mkdir(parents=True)
    pset = hr / "patchset" / "16.D"
    pset.mkdir(parents=True)
    (hr / "config" / "patch_policy.json").write_text('{"patchset_root": "patchset", "groups": {"16.D": {"status": "enabled"}}}',
                                                     encoding="utf-8")
    (pset / "css.patch").write_text("--- a/browser/extensions/newtab/css/activity-stream.css\n+++ b/browser/extensions/newtab/css/activity-stream.css\n"
                                    "@@ -1,3 +1,8 @@\n" + "\n".join(CSS_HUNK["lines"]) + "\n", encoding="utf-8")
    (pset / "dead.patch").write_text("--- a/x/dead.js\n+++ b/x/dead.js\n@@ -1,2 +1,1 @@\n-this line is nowhere in the tree at all();\n"
                                     "-nor is this other one here();\n", encoding="utf-8")
    t = {"workdir": str(w), "steps": [
        {"id": "owner-16.D-css", "kind": "owner", "status": "blocked", "title": "css.patch patches a file that no longer exists in this Firefox"},
        {"id": "owner-16.D-dead", "kind": "owner", "status": "blocked", "title": "dead.patch patches a file that no longer exists in this Firefox"},
        {"id": "export-16.D", "kind": "script", "status": "pending", "title": "export"}]}
    out = relocate.heal(t, hr)
    assert [(o[0], o[2]) for o in out] == [("owner-16.D-css", ["append-16.D-css"])]
    ids = [s["id"] for s in t["steps"]]
    assert ids == ["owner-16.D-css", "append-16.D-css", "owner-16.D-dead", "export-16.D"]
    assert t["steps"][0]["status"] == "obsolete" and "replaced by" in t["steps"][0]["last_why"][0]
    assert t["steps"][1]["status"] == "pending" and t["steps"][2]["status"] == "blocked"   # truly dead: still the owner's
