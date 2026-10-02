"""The two 2026-10-02 port failures are repaired, not only reported: a half-applied block removal that no longer
parses, and a member read from a module that no longer defines it."""
from fieldkit.buildh import firefox, repair, symbols

# the real shape of DesktopActorRegistry.sys.mjs after the half-applied AITab removal (build 1, 02 Oct)
MANGLED = """export const JSWINDOWACTORS = {
  AboutWelcome: {
    parent: { esModuleURI: "x" },
    matches: ["about:welcome"],
  },

  // GORILLA excised: AIChatContent actor (aiwindow removed).

  // GORILLA excised: AISmartBar actor (aiwindow removed).
  // GORILLA excised: SmartWindowTasks actor (aiwindow removed).

    parent: {
    },
    child: {
        "moz-src:///browser/components/aiwindow/ui/actors/AITabChild.sys.mjs",
      events: {
        "AITab:RequestPage": { wantUntrusted: true },
      },
    },
    matches: ["about:aitab", "about:aitab?*"],
    remoteTypes: ["privilegedabout"],
    enablePreference: "browser.smartwindow.aitab.enabled",
  },

  BackupUI: {
    parent: { esModuleURI: "resource:///actors/BackupUIParent.sys.mjs" },
  },
};
"""


def test_dangling_excision_drops_the_orphan_and_the_file_parses(tmp_path):
    (tmp_path / "browser/components").mkdir(parents=True)
    p = tmp_path / "browser/components/DesktopActorRegistry.sys.mjs"
    p.write_text(MANGLED, encoding="utf-8")
    err = firefox.node_check(p)
    assert err and "line 15" in err
    ok, what = repair.dangling_excision(tmp_path, "browser/components/DesktopActorRegistry.sys.mjs", 15)
    assert ok, what
    text = p.read_text(encoding="utf-8")
    assert firefox.node_check(p) is None
    assert "AITabChild" not in text and "BackupUI: {" in text and "AboutWelcome: {" in text
    assert "13-line orphan" in what


def test_dangling_excision_refuses_without_a_marker(tmp_path):
    p = tmp_path / "x.sys.mjs"
    p.write_text("export const A = {\n  b: {\n    c: 1,\n  },\n  },\n};\n", encoding="utf-8")
    ok, what = repair.dangling_excision(tmp_path, "x.sys.mjs", 5)
    assert not ok and "no GORILLA excised marker" in what


def _urlbar(tmp_path):
    d = tmp_path / "browser/components/urlbar"
    (d / "content").mkdir(parents=True)
    (d / "UrlbarUtils.sys.mjs").write_text("export var UrlbarUtils = {\n  getUrl() {},\n};\n", encoding="utf-8")
    (d / "content/UrlbarShared.mjs").write_text(
        "export const UrlbarShared = Object.freeze({\n  RESULT_SOURCE: Object.freeze({ SEARCH: 1, HISTORY: 2 }),\n  RESULT_TYPE: { SEARCH: 3 },\n});\n", encoding="utf-8")
    (d / "UrlbarProviderSearchSuggestions.sys.mjs").write_text(
        'import { UrlbarUtils } from "moz-src:///browser/components/urlbar/UrlbarUtils.sys.mjs";\n'
        "const lazy = {};\n"
        "ChromeUtils.defineESModuleGetters(lazy, {\n"
        '  UrlbarPrefs: "moz-src:///browser/components/urlbar/UrlbarPrefs.sys.mjs",\n'
        "});\n"
        "export class P {\n"
        "  isActive(q) {\n"
        "    return q.sources.includes(UrlbarUtils.RESULT_SOURCE.SEARCH) && q.r != UrlbarUtils.RESULT_SOURCE.SEARCH;\n"
        "  }\n"
        "  t() { return UrlbarUtils.RESULT_TYPE.SEARCH; }\n"
        "}\n", encoding="utf-8")
    return "browser/components/urlbar/UrlbarProviderSearchSuggestions.sys.mjs"


def test_moved_member_rewrites_to_the_one_module_that_defines_it(tmp_path):
    rel = _urlbar(tmp_path)
    assert symbols.problems(tmp_path, [rel]) == [f"{rel}: UrlbarUtils.RESULT_SOURCE (UrlbarUtils.sys.mjs)", f"{rel}: UrlbarUtils.RESULT_TYPE (UrlbarUtils.sys.mjs)"]
    ok, what = repair.moved_member(tmp_path, rel, "UrlbarUtils", "RESULT_SOURCE")
    assert ok, what
    text = (tmp_path / rel).read_text(encoding="utf-8")
    assert text.count("lazy.UrlbarShared.RESULT_SOURCE.SEARCH") == 2 and "UrlbarUtils.RESULT_SOURCE" not in text
    assert 'UrlbarShared: "chrome://browser/content/urlbar/UrlbarShared.mjs",' in text
    ok, what = repair.moved_member(tmp_path, rel, "UrlbarUtils", "RESULT_TYPE")
    assert ok and text.count("UrlbarShared:") == 1 and symbols.problems(tmp_path, [rel]) == []
    assert firefox.node_check(tmp_path / rel) is None


def test_moved_member_refuses_when_two_modules_define_it(tmp_path):
    rel = _urlbar(tmp_path)
    (tmp_path / "browser/components/urlbar/Other.sys.mjs").write_text("export const Other = {\n  RESULT_SOURCE: {},\n};\n", encoding="utf-8")
    ok, what = repair.moved_member(tmp_path, rel, "UrlbarUtils", "RESULT_SOURCE")
    assert not ok and "2 module(s)" in what


def test_upstream_rename_map_and_renamed_member(tmp_path):
    old = "a\n    t => t.type == lazy.UrlbarTokenizer.TYPE.RESTRICT_SEARCH\n  if (x === lazy.UrlbarTokenizer.RESTRICT.SEARCH) {\n  unrelated(lazy.A.B, lazy.C.D);\n"
    new = "a\n    t => t.type == lazy.UrlbarShared.TOKEN_TYPE.RESTRICT_SEARCH\n  if (x === lazy.UrlbarShared.RESTRICT_TOKENS.SEARCH) {\n  unrelated(lazy.E.F, lazy.G.H);\n"
    assert repair.upstream_renames(old, new) == {"UrlbarTokenizer.TYPE": "lazy.UrlbarShared.TOKEN_TYPE",
                                                 "UrlbarTokenizer.RESTRICT": "lazy.UrlbarShared.RESTRICT_TOKENS"}
    rel = _urlbar(tmp_path)
    (tmp_path / "browser/components/urlbar/UrlbarTokenizer.sys.mjs").write_text("export var UrlbarTokenizer = {\n  tokenize() {},\n};\n", encoding="utf-8")
    (tmp_path / "browser/components/urlbar/content/UrlbarShared.mjs").write_text(
        "export const UrlbarShared = Object.freeze({\n  TOKEN_TYPE: { RESTRICT_SEARCH: 1 },\n  RESTRICT_TOKENS: { SEARCH: 2 },\n});\n", encoding="utf-8")
    p = tmp_path / rel
    p.write_text(p.read_text(encoding="utf-8").replace("  t() { return UrlbarUtils.RESULT_TYPE.SEARCH; }",
                 "  t() { return lazy.UrlbarTokenizer.TYPE.RESTRICT_SEARCH; }"), encoding="utf-8")
    p.write_text(p.read_text(encoding="utf-8").replace('  UrlbarPrefs: "moz-src:///browser/components/urlbar/UrlbarPrefs.sys.mjs",',
                 '  UrlbarPrefs: "moz-src:///browser/components/urlbar/UrlbarPrefs.sys.mjs",\n  UrlbarTokenizer: "moz-src:///browser/components/urlbar/UrlbarTokenizer.sys.mjs",'), encoding="utf-8")
    upstream_new = new + '  UrlbarShared: "chrome://browser/content/urlbar/UrlbarShared.mjs",\n'
    ok, what = repair.renamed_member(tmp_path, rel, "UrlbarTokenizer", "TYPE", old, upstream_new)
    assert ok, what
    text = p.read_text(encoding="utf-8")
    assert "lazy.UrlbarShared.TOKEN_TYPE.RESTRICT_SEARCH" in text and "lazy.lazy" not in text
    assert 'UrlbarShared: "chrome://browser/content/urlbar/UrlbarShared.mjs",' in text
