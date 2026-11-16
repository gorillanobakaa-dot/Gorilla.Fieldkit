"""P29 (owner order 2026-11-16): `migrate preflight` reads a patched Firefox tree, the patch policy and the recipe
before a compile, and names what Firefox 160 found only after hours of building. Each check is proven both ways on a
small git tree: FAIL on the defect, PASS once it is fixed.

Mutations: fieldkit mutate --plan tests/migrate_preflight_mutations.yaml -- python -m pytest -q -x -p no:cacheprovider tests/test_migrate_preflight.py
"""
import json
import subprocess
from types import SimpleNamespace

import pytest

from fieldkit.migrate import preflight


def _git(d, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(d), *a], check=True,
                   capture_output=True)


def _write(root, files):
    for rel, text in files.items():
        p = root / rel
        if text is None:
            if p.exists():
                p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


def tree(tmp_path, base, patched):
    """A repo whose root commit is `base` and HEAD is `base` + `patched` (a None value deletes the file)."""
    repo = tmp_path / "src"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write(repo, base)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pristine")
    _write(repo, patched)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "patched")
    return repo


GENAI = "browser/components/genai/GenAI.sys.mjs"
TABS = "browser/components/preferences/tabs.mjs"
GETTER = ('const lazy = XPCOMUtils.declareLazy({\n  GenAI: "moz-src:///' + GENAI + '",\n});\n')


def _static(repo):
    return preflight.static(repo, "HEAD")


# --- LAZY ------------------------------------------------------------------------------------------------------------

def test_a_used_lazy_getter_whose_module_the_patch_set_removed_fails(tmp_path):
    repo = tree(tmp_path, {GENAI: "export const GenAI = {};\n", TABS: GETTER + "lazy.GenAI.go();\n"},
                {GENAI: None, TABS: GETTER + "lazy.GenAI.go();\n// gorilla\n"})
    s = _static(repo)
    assert len(s["LAZY"]) == 1 and "lazy.GenAI is used" in s["LAZY"][0] and TABS in s["LAZY"][0]


def test_the_fix_that_drops_the_use_passes(tmp_path):
    # ce237aa97e removed the highlightToSearch settings: the getter stays declared, nobody reads it
    repo = tree(tmp_path, {GENAI: "export const GenAI = {};\n", TABS: GETTER + "lazy.GenAI.go();\n"},
                {GENAI: None, TABS: GETTER + "// lazy.GenAI.go(); dropped with the AI settings\n"})
    assert _static(repo)["LAZY"] == []


def test_a_module_left_in_the_tree_but_excised_from_the_build_still_fails(tmp_path):
    base = {GENAI: "export const GenAI = {};\n", TABS: GETTER + "lazy.GenAI.go();\n",
            "browser/components/moz.build": 'DIRS += [\n    "genai",\n    "preferences",\n]\n'}
    repo = tree(tmp_path, base, {TABS: GETTER + "lazy.GenAI.go();\n// gorilla\n",
                                 "browser/components/moz.build":
                                 'DIRS += [\n    # GORILLA excised "genai",\n    "preferences",\n]\n'})
    s = _static(repo)
    assert len(s["LAZY"]) == 1 and GENAI in s["LAZY"][0]


def test_a_module_already_missing_upstream_is_not_the_patch_sets_fault(tmp_path):
    repo = tree(tmp_path, {TABS: GETTER + "lazy.GenAI.go();\n"}, {TABS: GETTER + "lazy.GenAI.go();\n// x\n"})
    assert _static(repo)["LAZY"] == []


# --- SETTING ---------------------------------------------------------------------------------------------------------

CONF = "browser/components/preferences/config/ai.mjs"
MAIN = "browser/components/preferences/main.js"


def test_a_setting_id_used_but_no_longer_registered_fails_and_an_option_value_does_not(tmp_path):
    used = 'export const items = [{ id: "linkPreviewEnabled", l10nId: "x" }, { id: "fast", value: 1 }];\n'
    repo = tree(tmp_path, {CONF: used, MAIN: 'Preferences.addSetting({ id: "linkPreviewEnabled" });\n'},
                {MAIN: '// Preferences.addSetting({ id: "linkPreviewEnabled" }); GORILLA removed\n'})
    s = _static(repo)
    assert s["SETTING"] == [f'{preflight.PREFS}: Setting "linkPreviewEnabled" is used but no addSetting registers it']


def test_a_setting_still_registered_passes(tmp_path):
    used = 'export const items = [{ id: "linkPreviewEnabled", l10nId: "x" }];\n'
    repo = tree(tmp_path, {CONF: used, MAIN: 'Preferences.addSetting({ id: "linkPreviewEnabled" });\n'},
                {MAIN: 'Preferences.addSetting({ id: "linkPreviewEnabled" });\n// gorilla\n'})
    assert _static(repo)["SETTING"] == []


# --- FLUENT ----------------------------------------------------------------------------------------------------------

FTL = "browser/locales/en-US/browser/genai.ftl"
PREF_FTL = "browser/locales/en-US/browser/preferences.ftl"
XHTML = "browser/components/preferences/preferences.xhtml"


def test_an_l10n_id_whose_ftl_entry_the_patch_set_removed_fails(tmp_path):
    repo = tree(tmp_path, {FTL: "highlight-to-search = Search\n", TABS: 'x = { l10nId: "highlight-to-search" };\n'},
                {FTL: "other = x\n", TABS: 'x = { l10nId: "highlight-to-search" };\n// gorilla\n'})
    assert _static(repo)["FLUENT"] == [f'{TABS}: data-l10n-id "highlight-to-search" is defined in no .ftl']


def test_a_use_inside_a_comment_is_not_shipped(tmp_path):
    repo = tree(tmp_path, {FTL: "highlight-to-search = Search\n", TABS: 'x = { l10nId: "highlight-to-search" };\n'},
                {FTL: "other = x\n", TABS: '/* x = { l10nId: "highlight-to-search" }; */\n'})
    assert _static(repo)["FLUENT"] == []


def test_an_id_whose_only_ftl_the_document_no_longer_links_fails(tmp_path):
    link = '<link rel="localization" href="browser/genai.ftl"/>\n'
    keep = '<link rel="localization" href="browser/preferences.ftl"/>\n'
    base = {FTL: "link-preview-settings = Preview\n", PREF_FTL: "title = Settings\n",
            XHTML: keep + link, TABS: 'x = { l10nId: "link-preview-settings" };\n'}
    repo = tree(tmp_path, base, {XHTML: keep + "<!-- " + link.strip() + " GORILLA -->\n",
                                 TABS: 'x = { l10nId: "link-preview-settings" };\n// gorilla\n'})
    s = _static(repo)
    assert len(s["FLUENT"]) == 1 and "lives only in browser/genai.ftl" in s["FLUENT"][0] and XHTML in s["FLUENT"][0]
    # the fix: the id moves to an ftl the document still links
    _write(repo, {PREF_FTL: "title = Settings\nlink-preview-settings = Preview\n"})
    _git(repo, "commit", "-qam", "fix")
    assert _static(repo)["FLUENT"] == []


# --- SUPPORT-LINK ----------------------------------------------------------------------------------------------------

LINK_JS = ("function formatUTMParams(contentAttribute, url) {\n  let parsedUrl = new URL(url);\n  return parsedUrl;\n}\n")
LINK_FIXED = ("function formatUTMParams(contentAttribute, url) {\n  try {\n    let parsedUrl = new URL(url);\n"
              "    return parsedUrl;\n  } catch (e) { return url; }\n}\n")


def test_an_empty_support_base_url_with_an_unguarded_new_url_fails_until_guarded(tmp_path):
    pref = preflight.PREF_FILES[0]
    repo = tree(tmp_path, {preflight.SUPPORT_LINK: LINK_JS, pref: 'pref("app.support.baseURL", "https://x/");\n'},
                {pref: 'pref("app.support.baseURL", "");\n'})
    assert len(_static(repo)["SUPPORT-LINK"]) == 1
    _write(repo, {preflight.SUPPORT_LINK: LINK_FIXED})
    _git(repo, "commit", "-qam", "fix")
    assert _static(repo)["SUPPORT-LINK"] == []


# --- policy ----------------------------------------------------------------------------------------------------------

def _harness(tmp_path, group):
    root = tmp_path / "ff"
    (root / "patches" / "21.PORT").mkdir(parents=True)
    (root / "patches" / "21.PORT" / "0122-widget_windows_moz_build.patch").write_text(
        "diff --git a/widget/windows/moz.build b/widget/windows/moz.build\n--- a/widget/windows/moz.build\n"
        "+++ b/widget/windows/moz.build\n@@ -1 +1 @@\n-a\n+b\n", newline="\n")
    (root / "config").mkdir()
    (root / "config" / "patch_policy.json").write_text(json.dumps(
        {"patchset_root": "patches", "groups": {"21.PORT": dict({"status": "enabled"}, **group)}}))
    return root


def _kinds(root):
    return [(k, p) for k, p, _w, _m in preflight.policy_check(root, ("linux",))]


def test_a_reason_naming_a_foreign_patch_with_no_declaration_is_a_contradiction(tmp_path):
    root = _harness(tmp_path, {"reason": "carries 0122-widget_windows_moz_build.patch for the linux port"})
    assert _kinds(root) == [("CONTRADICTION", "linux")]


def test_an_undeclared_foreign_patch_is_named(tmp_path):
    assert _kinds(_harness(tmp_path, {})) == [("SELECTS-FOREIGN", "linux")]


def test_a_waiver_with_a_reason_passes_and_one_without_is_stale(tmp_path):
    name = "0122-widget_windows_moz_build.patch"
    root = _harness(tmp_path, {"carry_on": {"linux": {name: "later patches need its context"}}})
    assert _kinds(root) == [("WAIVED", "linux")]
    root2 = _harness(tmp_path / "b", {"carry_on": {"linux": {name: " "}}})
    assert _kinds(root2) == [("STALE-WAIVER", "linux")]
    root3 = _harness(tmp_path / "c", {"exclude_on": {"linux": [name]}, "carry_on": {"linux": {"gone.patch": "x"}}})
    assert _kinds(root3) == [("STALE-WAIVER", "linux")]


# --- verdict ---------------------------------------------------------------------------------------------------------

def test_one_verdict_fail_incomplete_pass(tmp_path, monkeypatch):
    repo = tree(tmp_path, {GENAI: "export const GenAI = {};\n", TABS: GETTER + "lazy.GenAI.go();\n"},
                {GENAI: None, TABS: GETTER + "lazy.GenAI.go();\n// gorilla\n"})
    monkeypatch.setattr(preflight, "stage_key", lambda recipe="x": [])
    monkeypatch.setattr(preflight, "stall_armed", lambda here=None: [])
    monkeypatch.setattr(preflight, "set_proof", lambda *a, **k: ("PASS", ["SET-PROOF ok"]))
    root = _harness(tmp_path, {"exclude_on": {"linux": ["0122-widget_windows_moz_build.patch"]}})
    r = preflight.run(root, src=repo, platforms=("linux",))
    assert r["verdict"] == "FAIL (static)" and not r["ok"] and "do not compile" in r["next"]
    assert "PREFLIGHT VERDICT: FAIL (static)" in preflight.lines(r)
    _write(repo, {TABS: GETTER + "// dropped\n"})
    _git(repo, "commit", "-qam", "fix")
    r = preflight.run(root, src=repo, platforms=("linux",))
    assert r["verdict"] == "PASS" and r["next"].startswith("compile")
    monkeypatch.setattr(preflight, "set_proof", lambda *a, **k: ("NOT RUN", ["no pristine"]))
    r = preflight.run(root, src=repo, platforms=("linux",))
    assert r["verdict"].startswith("INCOMPLETE") and r["ok"]


def test_the_cli_exit_codes_follow_the_verdict(monkeypatch):
    from fieldkit.migrate import cli
    for verdict, ok, code in (("PASS", True, 0), ("FAIL (static)", False, 3), ("INCOMPLETE (x not run)", True, 1)):
        monkeypatch.setattr(preflight, "run", lambda *a, _v=verdict, _o=ok, **k: {"verdict": _v, "ok": _o,
                                                                                  "checks": [], "next": "n"})
        a = SimpleNamespace(args=["preflight", "harness=X", "pristine=P"])
        assert cli._preflight(a, lambda r, f: None) == code


def test_the_stage_key_and_stall_checks_hold_on_this_tree():
    assert preflight.stage_key("firefox-windows") == []
    assert preflight.stall_armed() == []


def test_the_stall_check_fails_without_childrun_or_with_a_bare_patch_child(tmp_path):
    assert "core/childrun.py is missing" in preflight.stall_armed(tmp_path)[0]
    _write(tmp_path, {"core/childrun.py": "def run(): pass\n", "buildh/replay.py": "childrun.run([])\n",
                      "buildh/snapshot.py": "childrun.run([])\n", "buildh/task.py": "subprocess.run([])\n"})
    assert preflight.stall_armed(tmp_path) == ["fieldkit/buildh/task.py runs its child without childrun.run: "
                                               "a stall there is not caught"]


def test_the_firefox_recipe_runs_preflight_before_it_compiles():
    import yaml
    from fieldkit.emerge import lint
    spec = yaml.safe_load((lint.RECIPES / "firefox-windows.yaml").read_text(encoding="utf-8"))
    st = spec["stages"]
    assert "migrate-preflight" in st
    assert "source" in st["migrate-preflight"]["needs"]
    assert "migrate-preflight" in st["bootstrap"]["needs"]
