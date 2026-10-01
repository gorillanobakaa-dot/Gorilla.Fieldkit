"""Live run 16 (2026-10-01): upstream RENAMED identifiers inside the lines a hunk removes, and every judge said "gone".

tabbrowser.js moved into Tabbrowser.sys.mjs in Firefox 157 and, on the way, `AIWindow.x(window)` became
`lazy.AIWindow.x(this.documentGlobal)`, `this._allowTransparentBrowser` became `lazy.allowTransparentBrowser`, and one
`if (A || B) {` was re-wrapped over three lines. Judged by exact text, the removed lines were absent: h3 was recorded
DONE by tier 0 ("already in place") and h4 OBSOLETE, with the code still there. The text below is the real 157 text.

Rules, all deterministic:
  * a removed line present under new names is PRESENT (tier 0, obsolete, the verifier, the final re-check);
  * a pure-removal hunk whose lines all map uniquely, in order, to their renamed forms is removed by the harness,
    with the hunk's trivial edge lines (`);`, `}`, blanks) so nothing dangles;
  * a hunk that ADDS code over renamed lines is deferred to a person with the renames listed: an added line would
    carry the old names, and no tool or model may rewrite it.
"""
from fieldkit.buildh import firefox, verify

R1 = """      browser
    );
    browser.fixupAndLoadURIString =
      URILoadingWrapper.fixupAndLoadURIString.bind(URILoadingWrapper, browser);

    if (lazy.AIWindow.isAIWindowActive(this.documentGlobal)) {
      let uriToLoad = this.documentGlobal.gBrowserInit.uriToLoadPromise;
      let firstURI = Array.isArray(uriToLoad) ? uriToLoad[0] : uriToLoad;

      if (!lazy.allowTransparentBrowser) {
        // firstURI may be a Promise (uriToLoadPromise still resolving while
        // SessionStore restores) or empty; only build a URI from a real
        // string, otherwise default to transparent like the no-URI case.
        browser.toggleAttribute(
          "transparent",
          !firstURI ||
            typeof firstURI != "string" ||
            lazy.AIWindow.isAIWindowContentPage(Services.io.newURI(firstURI))
        );
      }
    }

    let uniqueId = this.#generateUniquePanelID();
    let panel = this.getPanel(browser);
    panel.id = uniqueId;
    this.tabpanels.appendChild(panel);
""".splitlines()

H3 = {"lines": [
    "           browser",
    "         );",
    " ",
    "-      if (AIWindow.isAIWindowActive(window)) {",
    "-        let uriToLoad = gBrowserInit.uriToLoadPromise;",
    "-        let firstURI = Array.isArray(uriToLoad) ? uriToLoad[0] : uriToLoad;",
    "-",
    "-        if (!this._allowTransparentBrowser) {",
    "-          // firstURI may be a Promise (uriToLoadPromise still resolving while",
    "-          // SessionStore restores) or empty; only build a URI from a real",
    "-          // string, otherwise default to transparent like the no-URI case.",
    "-          browser.toggleAttribute(",
    '-            "transparent",',
    "-            !firstURI ||",
    '-              typeof firstURI != "string" ||',
    "-              AIWindow.isAIWindowContentPage(Services.io.newURI(firstURI))",
    "-          );",
    "-        }",
    "-      }",
    "-",
    "       let uniqueId = this._generateUniquePanelID();",
    "       let panel = this.getPanel(browser);",
    "       panel.id = uniqueId;"]}

R2 = """      // XXX: The `name` property is special in HTML and XUL. Should
      // we use a different attribute name for this?
      b.setAttribute("name", name);
    }

    if (
      lazy.AIWindow.isAIWindowActive(this.documentGlobal) ||
      lazy.allowTransparentBrowser
    ) {
      b.setAttribute("transparent", "true");
    }

    Services.obs.notifyObservers(
""".splitlines()

H4 = {"lines": [
    '         b.setAttribute("name", name);',
    "       }",
    " ",
    "-      if (AIWindow.isAIWindowActive(window) || this._allowTransparentBrowser) {",
    "+      if (this._allowTransparentBrowser) {",
    '         b.setAttribute("transparent", "true");',
    "       }",
    " "]}

R3 = """        if (
          aRequest instanceof Ci.nsIChannel &&
          !this.#documentGlobal.isBlankPageURL(aRequest.originalURI.spec)
        ) {
          this._browser.originalURI = aRequest.originalURI;
        }

        if (!lazy.allowTransparentBrowser) {
          this._browser.toggleAttribute(
            "transparent",
            lazy.AIWindow.isAIWindowActive(this.#documentGlobal) &&
              lazy.AIWindow.isAIWindowContentPage(aLocation)
          );
        }
      }

      let userContextId = this._browser.getAttribute("usercontextid") || 0;
      if (this._browser.registeredOpenURI) {
        let uri = this._browser.registeredOpenURI;
""".splitlines()

H5 = {"lines": [
    "           ) {",
    "             this._browser.originalURI = aRequest.originalURI;",
    "           }",
    "-",
    "-          if (!gBrowser._allowTransparentBrowser) {",
    "-            this._browser.toggleAttribute(",
    '-              "transparent",',
    "-              AIWindow.isAIWindowActive(window) &&",
    "-                AIWindow.isAIWindowContentPage(aLocation)",
    "-            );",
    "-          }",
    "         }",
    " ",
    '         let userContextId = this._browser.getAttribute("usercontextid") || 0;']}


def test_renamed_lines_are_present_for_every_judge():
    for body, h in ((R1, H3), (R2, H4), (R3, H5)):
        assert not firefox.already_upstream(body, h)
        assert firefox.obsolete_upstream(body, h) is None
        v, d = verify.score_hunk(body, h, "Tabbrowser.sys.mjs")
        assert v == "NOT-APPLIED" and "under new names" in d
    # the re-wrapped three-line form is one renamed line
    assert firefox.renamed_pairs(R2, firefox.hunk_sides(H4)[0]) == [
        ("if (AIWindow.isAIWindowActive(window) || this._allowTransparentBrowser) {",
         "lazy.AIWindow.isAIWindowActive(this.documentGlobal) || lazy.allowTransparentBrowser")]


def test_a_line_with_no_identity_is_never_called_renamed():
    assert firefox.renamed_candidates(["    remoteTypes: [\"parent\", \"extension\"],"], '    remoteTypes: ["inference"],') == []
    assert firefox.renamed_candidates(["  !firstURI ||"], '  typeof firstURI != "string" ||') == []
    # a longer line that merely mentions the identifiers is not the line
    assert firefox.renamed_candidates(["  if (lazy.AIWindow.isAIWindowActive(w) && other.thingHere.isReallyLong() && more.stuffHere) {"],
                                      "  AIWindow.isAIWindowActive(window) &&") == []


def test_pure_removal_over_renamed_lines_is_done_by_the_harness_with_its_edges():
    for body, h, n_file in ((R1, H3, 17), (R3, H5, 8)):
        notes = []
        new, region = firefox.renamed_removal(body, h, notes)
        assert f"({n_file} lines in the file" in notes[0] and "is now" in notes[0]
        assert not any("AIWindow" in l or "allowTransparentBrowser" in l for l in new)
        assert firefox.hunk_problems(body, new, h) == []
        assert firefox.collateral(body, new, h, extra_removals=region) == []
        assert verify.score_hunk(new, h, "x")[0] == "APPLIED" and firefox.already_upstream(new, h)
    new, _ = firefox.renamed_removal(R3, H5)
    assert new[5:8] == ["        }", "      }", ""]                      # `);` and `}` went with the block
    assert new.count("") == R3.count("") - 1


def test_added_code_over_renamed_lines_goes_to_a_person_not_a_model(tmp_path):
    import pytest
    with pytest.raises(firefox.Ambiguous, match="adds code lines"):
        firefox.renamed_removal(R2, H4)
    f = tmp_path / "Tabbrowser.sys.mjs"
    f.write_text("\n".join(R2) + "\n", encoding="utf-8")
    t = {"workdir": str(tmp_path)}
    s = {}
    r = firefox.auto_port(t, s, "p.patch", "Tabbrowser.sys.mjs", H4)
    assert r["ok"] is False and r.get("defer") and not r.get("obsolete")
    assert "renamed identifiers" in r["why"][0] and "by a person, never by a model" in r["why"][0]
    assert f.read_text(encoding="utf-8") == "\n".join(R2) + "\n"
    # and the pure removal lands through auto_port with the region recorded for the collateral check
    f.write_text("\n".join(R3) + "\n", encoding="utf-8")
    s = {}
    r = firefox.auto_port(t, s, "p.patch", "Tabbrowser.sys.mjs", H5)
    assert r["ok"] and any("renamed form" in n for n in r["notes"]) and len(s["renamed_removals"]) == 8
