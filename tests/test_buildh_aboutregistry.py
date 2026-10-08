"""The about: page map (2026-10-08): every page the source can register, with its build condition and flags - the
pages about:about hides included (the owner found about:fingerprintingprotection by hand)."""
from fieldkit.buildh import aboutregistry as ar

TABLE = '''static const RedirEntry kRedirMap[] = {
    {"about", "chrome://global/content/aboutAbout.html", 0},
    {"fingerprintingprotection",
     "chrome://global/content/usercharacteristics/usercharacteristics.html",
     nsIAboutModule::URI_SAFE_FOR_UNTRUSTED_CONTENT |
         nsIAboutModule::HIDE_FROM_ABOUTABOUT | nsIAboutModule::ALLOW_SCRIPT},
#ifndef MOZ_WIDGET_ANDROID
    {"config", "chrome://global/content/aboutconfig/aboutconfig.html", nsIAboutModule::ALLOW_SCRIPT},
#else
    {"config", "chrome://geckoview/content/config.xhtml", nsIAboutModule::HIDE_FROM_ABOUTABOUT},
#endif
};
'''


def test_redirect_table_entries_carry_flags_and_conditions():
    es = {(e["name"], e["line"]): e for e in ar.redirect_entries(TABLE, "x.cpp")}
    fp = es[("fingerprintingprotection", 3)]
    assert "HIDE_FROM_ABOUTABOUT" in fp["flags"] and fp["condition"] is None and fp["line"] == 3
    desktop = es[("config", 8)]
    android = es[("config", 10)]
    assert desktop["condition"] == "!defined(MOZ_WIDGET_ANDROID)" and "HIDE_FROM_ABOUTABOUT" not in desktop["flags"]
    assert android["condition"].startswith("else of") and "HIDE_FROM_ABOUTABOUT" in android["flags"]
    assert ("about", 2) in es                                                    # flags 0 is still an entry


def test_components_conf_lists_and_conditional_appends():
    conf = "about_pages = [\n    'about',\n    # GORILLA: no about:logging\n    'memory',\n]\n" \
           "if defined('NIGHTLY_BUILD'):\n    about_pages.append('inference')\n" \
           "if buildconfig.substs.get('MOZ_NORMANDY'):\n    pages += ['studies']\n"
    es = {e["name"]: e for e in ar.conf_list_entries(conf, "c.conf")}
    assert set(es) == {"about", "memory", "inference", "studies"}
    assert es["inference"]["condition"] == "defined('NIGHTLY_BUILD')" and es["about"]["condition"] is None


def test_contracts_including_the_percent_s_page_loop():
    conf = "pages = [\n    'home',\n    'newtab',\n]\nClasses = [{\n  'contract_ids': ['@mozilla.org/network/protocol/about;1?what=%s' % page\n for page in pages],\n}, {'contract_ids': ['@mozilla.org/network/protocol/about;1?what=blank']}]\n"
    names = sorted(e["name"] for e in ar.contract_entries(conf, "n.conf"))
    assert names == ["blank", "home", "newtab"]


def test_runtime_adds_what_the_build_registers_and_flags_the_unexplained():
    es = [{"name": "about", "sources": []}]
    out = {e["name"]: e for e in ar.with_runtime(es, {"about": "listed", "mystery": "hidden"})}
    assert out["about"]["in_build"] and not out["about"]["hidden_at_runtime"]
    assert out["mystery"]["unexplained"] and out["mystery"]["hidden"]
