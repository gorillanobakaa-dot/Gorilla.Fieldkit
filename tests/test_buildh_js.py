"""JS hunks from live run 16 (2026-10-01): duplicated blocks, a private-field rename, a line inside a removed
block, an anchor landing inside a long removed block, and a true refactor that stays with a person."""
import pytest

from fieldkit.buildh import firefox

# navigator-toolbox.js: the same `case` block exists in TWO handlers (onClick / onKeyPress); h3 is the first
TOOLBOX = """        case "translations-button":
          FullPageTranslationsPanel.open(event);
          break;

        case "split-view-button":
          if (isLeftClick) {
            gBrowser.openSplitViewMenu(element);
          }
          break;

        case "smartwindow-ask-button":
          if (isLeftClick) {
            AIWindowUI.toggleSidebar(window);
          }
          break;

        default:
          throw new Error(`Missing case for #${element.id}`);
      }
    }
    navigatorToolbox.addEventListener("click", onClick);
    function onKeyPress(event) {
      switch (element.id) {
        case "split-view-button":
          if (isLikeLeftClick) {
            gBrowser.openSplitViewMenu(element);
          }
          break;

        case "smartwindow-ask-button":
          if (isLikeLeftClick) {
            AIWindowUI.toggleSidebar(window);
          }
          break;

        default:
          throw new Error(`Missing case for #${element.id}`);
      }
    }
""".splitlines()

H3 = {"header": "@@ -319,18 +312,6 @@ document.addEventListener(", "lines": [
    "           }", "           break;", " ",
    '-        case "smartwindow-ask-button":', "-          if (isLeftClick) {", "-            AIWindowUI.toggleSidebar(window);",
    "-          }", "-          break;", "-",
    '-        case "smartwindow-group-tabs-button":', "-          if (isLeftClick) {", "-            AIWindowUI.toggleGroupTabsPanel(window);",
    "-          }", "-          break;", "-",
    "         default:", "           throw new Error(`Missing case for #${element.id}`);", "       }"]}
H5 = {"header": "@@ -465,18 +444,6 @@ document.addEventListener(", "lines": [l.replace("isLeftClick", "isLikeLeftClick") for l in H3["lines"]]}


def test_a_block_that_exists_twice_is_removed_at_the_copy_nearest_the_hunk():
    body = list(TOOLBOX)
    new = firefox.auto_merge(body, H3)
    assert new.count('        case "smartwindow-ask-button":') == 1          # the first copy went
    assert new.index('        case "smartwindow-ask-button":') > new.index("    function onKeyPress(event) {")
    assert "            gBrowser.openSplitViewMenu(element);" in new        # neighbours untouched
    new2 = firefox.auto_merge(new, H5)
    assert new2.count('        case "smartwindow-ask-button":') == 0
    assert firefox.hunk_problems(body, new, H3) == [] and firefox.collateral(body, new, H3) == []


# SessionStore h2: `this._windows` became the private `this.#windows`
SS2 = {"header": "@@ -2114,10 +2114,6 @@ var SessionStoreInternal = {", "lines": [
    "       this._windows[aWindow.__SSi].isTaskbarTab = true;", "     }", " ",
    "-    if (lazy.AIWindow.isAIWindowActiveAndEnabled(aWindow)) {", "-      this._windows[aWindow.__SSi].isAIWindow = true;", "-    }", "-",
    "     let tabbrowser = aWindow.gBrowser;", " ", "     // add tab change listeners to all already existing tabs"]}
SS2_157 = """      this.#windows[aWindow.__SSi].isTaskbarTab = true;
    }

    if (lazy.AIWindow.isAIWindowActiveAndEnabled(aWindow)) {
      this.#windows[aWindow.__SSi].isAIWindow = true;
    }

    let tabbrowser = aWindow.gBrowser;

    // add tab change listeners to all already existing tabs
    for (let i = 0; i < tabbrowser.tabs.length; i++) {
""".splitlines()


def test_a_private_field_rename_does_not_hide_the_same_line():
    assert firefox._key("      this.#windows[aWindow.__SSi].isAIWindow = true;") == firefox._key("      this._windows[aWindow.__SSi].isAIWindow = true;")
    new = firefox.auto_merge(list(SS2_157), SS2)
    assert "    if (lazy.AIWindow.isAIWindowActiveAndEnabled(aWindow)) {" not in new
    assert "      this.#windows[aWindow.__SSi].isTaskbarTab = true;" in new
    assert firefox.collateral(SS2_157, new, SS2) == []


# SessionStore h6: upstream rewrote one line INSIDE the else-branch the owner removes
SS6 = {"header": "@@ -7302,23 +7272,8 @@ var SessionStoreInternal = {", "lines": [
    "     });", " ",
    "-    // A window CANNOT be both a Private Window and an AI Window",
    "     if (winState.isPrivate) {", '       features.push("private");',
    "-    } else if (winState.isAIWindow) {", "-      let tab = winState.tabs[winState.selected - 1];", '-      let restoreSessionURL = "";',
    "-      if (tab.entries.length) {", "-        // tab.index is 1-based in the session store format (0/falsy means unset).",
    "-        let activeIndex = (tab.index || tab.entries.length) - 1;", "-        restoreSessionURL = tab.entries[activeIndex].url;", "-      }",
    "-      argString = lazy.AIWindow.handleAIWindowOptions({", "-        openerWindow: null,", "-        args: argString,",
    "-        aiWindow: winState.isAIWindow,", "-        restoreSessionURL,", "-      });",
    "     }", " ", "     if (!argString) {"]}
SS6_157 = """    });

    // A window CANNOT be both a Private Window and an AI Window
    if (winState.isPrivate) {
      features.push("private");
    } else if (winState.isAIWindow) {
      let tab = winState.tabs[winState.selected - 1];
      let restoreSessionURL = "";
      if (tab.entries.length) {
        // tab.index is 1-based in the session store format (0/falsy means unset).
        let activeIndex = this.historyIndex(tab);
        restoreSessionURL = tab.entries[activeIndex].url;
      }
      argString = lazy.AIWindow.handleAIWindowOptions({
        openerWindow: null,
        args: argString,
        aiWindow: winState.isAIWindow,
        restoreSessionURL,
      });
    }

    if (!argString) {
      argString = Cc["@mozilla.org/supports-string;1"].createInstance(
""".splitlines()


def test_a_new_upstream_line_inside_a_removed_block_goes_with_the_block_no_question():
    body = list(SS6_157)
    at = firefox._anchor(body, [l[1:] for l in SS6["lines"] if l[:1] in (" ", "-")])
    auto_rm, uncertain = firefox.identify_questions(body, SS6, at)
    assert uncertain == [] and 11 in auto_rm                              # `let activeIndex = this.historyIndex(tab);`
    new = firefox.auto_merge(body, SS6)
    assert "    } else if (winState.isAIWindow) {" not in new and "        let activeIndex = this.historyIndex(tab);" not in new
    assert new[new.index('      features.push("private");') + 1] == "    }"
    assert firefox.hunk_problems(body, new, SS6) == []


# DesktopActorRegistry h1: a 76-line block; the anchor lands inside it, the hunk begins before the anchor
REG = {"header": "@@ -229,76 +229,11 @@ let JSWINDOWACTORS = {", "lines": [
    '     enablePreference: "browser.aboutwelcome.enabled",', "   },", " ",
    "-  AIChatContent: {", "-    parent: {", "-      esModuleURI:",
    '-        "moz-src:///browser/components/aiwindow/ui/actors/AIChatContentParent.sys.mjs",', "-    },", "-    child: {",
    "-      esModuleURI:", '-        "moz-src:///browser/components/aiwindow/ui/actors/AIChatContentChild.sys.mjs",', "-      events: {",
    '-        "AIChatContent:DispatchFollowUp": { wantUntrusted: true },', '-        "AIChatContent:Ready": { wantUntrusted: true },',
    "-      },", "-    },", "-    allFrames: true,", '-    matches: ["about:aichatcontent"],', '-    remoteTypes: ["privilegedabout"],',
    '-    enablePreference: "browser.smartwindow.enabled",', "-  },", "-",
    "   AboutLogins: {", "     parent: {"]}
REG_157 = """    enablePreference: "browser.aboutwelcome.enabled",
  },

  AIChatContent: {
    parent: {
      esModuleURI:
        "moz-src:///browser/components/aiwindow/ui/actors/AIChatContentParent.sys.mjs",
    },
    child: {
      esModuleURI:
        "moz-src:///browser/components/aiwindow/ui/actors/AIChatContentChild.sys.mjs",
      events: {
        "AIChatContent:DispatchFollowUp": { wantUntrusted: true },
        "AIChatContent:Ready": { wantUntrusted: true },
      },
    },
    allFrames: true,
    matches: ["about:aichatcontent"],
    remoteTypes: ["privilegedabout"],
    enablePreference: "browser.smartwindow.enabled",
  },

  AboutLogins: {
    parent: {
""".splitlines()


def test_a_long_removed_block_is_found_even_when_the_anchor_lands_inside_it():
    body = list(REG_157)
    new = firefox.auto_merge(body, REG)
    assert "  AIChatContent: {" not in new and "  AboutLogins: {" in new
    assert new[:3] == REG_157[:3]
    assert firefox.hunk_problems(body, new, REG) == [] and firefox.collateral(body, new, REG) == []


def test_a_true_refactor_stays_with_a_person_and_names_the_functions():
    hunk = {"header": "@@ -3390,11 +3334,7 @@ export const PanelTestProvider = {", "lines": [
        "     return Promise.resolve(", "       MESSAGES().map(message => ({", "         ...message,",
        "-        targeting:", '-          typeof message.targeting === "string" &&', '-          message.targeting?.includes("isAIWindow")',
        '-            ? `isAIWindow && providerCohorts.panel_local_testing == "SHOW_TEST"`',
        '-            : `providerCohorts.panel_local_testing == "SHOW_TEST"`,',
        '+        targeting: `providerCohorts.panel_local_testing == "SHOW_TEST"`,', "       }))", "     );", "   },"]}
    body = """  tagMessageForTesting(message) {
    message.targeting =
      typeof message.targeting === "string" &&
      message.targeting?.includes("isAIWindow")
        ? `isAIWindow && providerCohorts.panel_local_testing == "SHOW_TEST"`
        : `providerCohorts.panel_local_testing == "SHOW_TEST"`;
    return message;
  },

  getMessages() {
    return Promise.resolve(
      MESSAGES().map(message =>
        PanelTestProvider.tagMessageForTesting({ ...message })
      )
    );
  },
""".splitlines()
    where = firefox.moved_where(body, hunk, "PanelTestProvider.sys.mjs")
    assert "export const PanelTestProvider" in where and "tagMessageForTesting()" in where


def test_a_new_line_inside_a_removed_block_is_not_collateral():
    body = list(SS6_157)
    new = firefox.auto_merge(body, SS6)
    assert firefox.collateral(body, new, SS6) == []
    # but a removed line OUTSIDE any removed block still is
    bad = [l for l in new if l != '      argString = Cc["@mozilla.org/supports-string;1"].createInstance(']
    assert any("not part of the change" in w for w in firefox.collateral(body, bad, SS6))


def test_the_check_sees_inserted_lines_even_when_the_hunks_trailing_context_is_too_short_to_pin():
    """DesktopActorRegistry h1: three '+' comments follow the removed actors; the trailing context is `BackupUI: {`."""
    hunk = {"header": "@@ -229,76 +229,11 @@ let JSWINDOWACTORS = {", "lines": [
        '     enablePreference: "browser.aboutwelcome.enabled",', "   },", " ",
        "-  AIChatContent: {", "-    parent: {", '-      esModuleURI: "moz-src:///browser/components/aiwindow/ui/actors/AIChatContentParent.sys.mjs",',
        "-    },", '-    enablePreference: "browser.smartwindow.enabled",', "-  },",
        "+  // GORILLA excised: AIChatContent actor (aiwindow removed).", " ", "   BackupUI: {", "     parent: {"]}
    before = ['    enablePreference: "browser.aboutwelcome.enabled",', "  },", "", "  AIChatContent: {", "    parent: {",
              '      esModuleURI: "moz-src:///browser/components/aiwindow/ui/actors/AIChatContentParent.sys.mjs",', "    },",
              '    enablePreference: "browser.smartwindow.enabled",', "  },", "", "  BackupUI: {", "    parent: {", "      esModuleURI: 'x',"]
    after = firefox.auto_merge(before, hunk)
    assert "  // GORILLA excised: AIChatContent actor (aiwindow removed)." in after and "  AIChatContent: {" not in after
    assert firefox.hunk_problems(before, after, hunk) == [] and firefox.collateral(before, after, hunk) == []
