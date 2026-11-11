"""The hand-port check's "still there under new names" (2026-10-09, D-157-40): removing a whole about: table entry
left other entries' flag lines, each one old; two of them joined read like the removed flag line renamed, and the
gate called three finished removals undone. A run made only of lines that were there before the edit is no rename."""
from fieldkit.buildh import firefox

BEFORE = '''static const RedirEntry kRedirMap[] = {
    {"credits", "chrome://global/content/license.html",
     nsIAboutModule::URI_SAFE_FOR_UNTRUSTED_CONTENT |
         nsIAboutModule::IS_SECURE_CHROME_UI},
    {"fingerprintingprotection",
     "chrome://global/content/usercharacteristics/usercharacteristics.html",
     nsIAboutModule::URI_SAFE_FOR_UNTRUSTED_CONTENT |
         nsIAboutModule::HIDE_FROM_ABOUTABOUT | nsIAboutModule::ALLOW_SCRIPT |
         nsIAboutModule::URI_MUST_LOAD_IN_CHILD |
         nsIAboutModule::URI_CAN_LOAD_IN_PRIVILEGEDABOUT_PROCESS},
    {"httpsonlyerror", "chrome://global/content/httpsonlyerror/errorpage.html",
     nsIAboutModule::URI_SAFE_FOR_UNTRUSTED_CONTENT |
         nsIAboutModule::URI_CAN_LOAD_IN_CHILD | nsIAboutModule::ALLOW_SCRIPT |
         nsIAboutModule::HIDE_FROM_ABOUTABOUT},
    {"license", "chrome://global/content/license.html",
     nsIAboutModule::URI_SAFE_FOR_UNTRUSTED_CONTENT |
         nsIAboutModule::IS_SECURE_CHROME_UI},
};'''.splitlines()

REMOVED = BEFORE[5:11]
NOTE = ["    // GORILLA D-157-40: no about:fingerprintingprotection."]
AFTER = BEFORE[:5] + NOTE + BEFORE[11:]
HUNK = {"lines": [" " + l for l in BEFORE[2:5]] + ["-" + l for l in REMOVED] + ["+" + l for l in NOTE]
        + [" " + l for l in BEFORE[11:14]]}


def test_removing_a_table_entry_is_not_read_as_a_rename_of_old_lines():
    assert firefox.hand_port_holds(AFTER, HUNK, BEFORE) == []


def test_a_real_rename_is_still_caught():
    # the removed flag line survives as a NEW line with the same names re-ordered: that is a rename, still refused
    renamed = AFTER[:5] + ["         nsIAboutModule::ALLOW_SCRIPT | nsIAboutModule::HIDE_FROM_ABOUTABOUT |"] + AFTER[5:]
    why = firefox.hand_port_holds(renamed, HUNK, BEFORE)
    assert why and "under new names" in " ".join(why)


# P27 (2026-11-11): a removed line that a SIBLING hunk of the same patch adds back elsewhere is no leftover
SB = ["a {", "  padding: 20px;", "}", "", "b {", "  color: red;", "}"]
SH = {"lines": [" a {", "-  padding: 20px;", "+  margin: 0;", " }"]}
SAFTER = ["a {", "  margin: 0;", "}", "", "b {", "  color: red;", "  padding: 20px;", "}"]   # sibling re-added it in b


def test_sibling_hunk_readding_the_removed_line_is_accepted():
    assert firefox.hand_port_check(SB, SAFTER, SH, SB, siblings={"padding: 20px;": 1}, collateral_check=False) == []


def test_a_real_leftover_is_still_refused():
    left = ["a {", "  padding: 20px;", "  margin: 0;", "}", "", "b {", "  color: red;", "  padding: 20px;", "}"]
    why = firefox.hand_port_check(SB, left, SH, SB, siblings={"padding: 20px;": 1}, collateral_check=False)
    assert why and "should be gone" in " ".join(why)
    assert firefox.hand_port_check(SB, left, SH, SB, collateral_check=False)   # and with no sibling too
