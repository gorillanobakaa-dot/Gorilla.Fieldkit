"""A changed module that reads X.MEMBER from an import whose module no longer defines MEMBER is a stop before the
build: the 2026-10-02 urlbar providers read UrlbarUtils.RESULT_SOURCE after 157 moved it to UrlbarShared."""
from fieldkit.buildh import symbols


def _tree(tmp_path, utils_body):
    (tmp_path / "browser/components/urlbar").mkdir(parents=True)
    (tmp_path / "browser/components/urlbar/UrlbarUtils.sys.mjs").write_text(utils_body, encoding="utf-8")
    (tmp_path / "browser/components/urlbar/content").mkdir()
    (tmp_path / "browser/components/urlbar/content/UrlbarShared.mjs").write_text(
        "export const UrlbarShared = {\n  RESULT_SOURCE: Object.freeze({ SEARCH: 1 }),\n  PROVIDER_TYPE: { NETWORK: 2 },\n};\n", encoding="utf-8")
    prov = (
        'import { UrlbarUtils } from "moz-src:///browser/components/urlbar/UrlbarUtils.sys.mjs";\n'
        "const lazy = {};\n"
        "ChromeUtils.defineESModuleGetters(lazy, {\n"
        '  UrlbarShared: "chrome://browser/content/urlbar/UrlbarShared.mjs",\n'
        '  Gone: "moz-src:///does/not/exist.sys.mjs",\n'
        "});\n"
        "// UrlbarUtils.NOT_REAL in a comment is not code\n"
        "export class P {\n"
        "  get type() { return lazy.UrlbarShared.PROVIDER_TYPE.NETWORK; }\n"
        "  isActive(q) { return q.sources.includes(UrlbarUtils.RESULT_SOURCE.SEARCH) && lazy.Gone.WHATEVER; }\n"
        "  other() { return UrlbarUtils.getUrl(); }\n"
        "}\n")
    (tmp_path / "browser/components/urlbar/P.sys.mjs").write_text(prov, encoding="utf-8")
    return "browser/components/urlbar/P.sys.mjs"


def test_member_moved_out_of_the_imported_module_is_reported(tmp_path):
    rel = _tree(tmp_path, "export var UrlbarUtils = {\n  getUrl() {},\n};\n")
    assert symbols.problems(tmp_path, [rel]) == [f"{rel}: UrlbarUtils.RESULT_SOURCE (UrlbarUtils.sys.mjs)"]


def test_member_still_defined_passes_and_unresolvable_imports_are_not_judged(tmp_path):
    rel = _tree(tmp_path, "export var UrlbarUtils = {\n  RESULT_SOURCE: { SEARCH: 1 },\n  getUrl() {},\n};\n")
    assert symbols.problems(tmp_path, [rel]) == []
