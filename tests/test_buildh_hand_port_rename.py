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
SH = {"lines": [" a {", "-  padding: 20px;", "+  margin-inline: 4px;", " }"]}
SAFTER = ["a {", "  margin-inline: 4px;", "}", "", "b {", "  color: red;", "  padding: 20px;", "}"]   # sibling re-added it in b


def test_sibling_hunk_readding_the_removed_line_is_accepted():
    assert firefox.hand_port_check(SB, SAFTER, SH, SB, siblings={"padding: 20px;": 1}, collateral_check=False) == []


def test_a_real_leftover_is_still_refused():
    left = ["a {", "  padding: 20px;", "  margin-inline: 4px;", "}", "", "b {", "  color: red;", "  padding: 20px;", "}"]
    why = firefox.hand_port_check(SB, left, SH, SB, siblings={"padding: 20px;": 1}, collateral_check=False)
    assert why and "should be gone" in " ".join(why)
    assert firefox.hand_port_check(SB, left, SH, SB, collateral_check=False)   # and with no sibling too


SIB = [[("top: 0;", False), ("padding: 20px;", True), ("left: 0;", False)]]      # the sibling hunk adds it in rule c
A_OLD = ["a {", "  display: block;", "  border: none;", "  padding: 20px;", "  font-size: 1em;", "  margin-top: 2px;", "}", ""]
A_NEW = ["a {", "  display: block;", "  border: none;", "  margin-inline: 4px;", "  font-size: 1em;", "  margin-top: 2px;", "}", ""]
C_OLD = ["c {", "  top: 0;", "  left: 0;", "}", ""]
C_NEW = ["c {", "  top: 0;", "  padding: 20px;", "  left: 0;", "}", ""]
D = ["d {", "  color: red;", "  margin-inline: 4px;", "}"]          # an upstream rule that already holds the added line, further down
PB = A_OLD + C_OLD + D
PH = {"lines": [" a {", "   display: block;", "   border: none;", "-  padding: 20px;", "+  margin-inline: 4px;", "   font-size: 1em;",
                "   margin-top: 2px;", " }"]}
PA_SIB = A_NEW + C_NEW + D
PA_LEFT = A_OLD[:4] + ["  margin-inline: 4px;"] + A_OLD[4:] + C_NEW + D     # the old line left behind AND the sibling's copy


def test_line_check_accepts_sibling_readd():
    assert firefox.hunk_problems(PB, PA_SIB, PH)                      # without the sibling hunk it reads as a leftover
    assert firefox.hunk_problems(PB, PA_SIB, PH, siblings=SIB) == []


def test_line_check_still_refuses_a_real_leftover():
    why = firefox.hunk_problems(PB, PA_LEFT, PH, siblings=SIB)
    assert why and "should be gone" in why[0]
