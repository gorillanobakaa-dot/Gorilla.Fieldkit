"""Live run 16 (2026-10-01 23:50): toolkit/components/ml/moz.build. The owner removes the `if ... android:` block
holding backends/llama; Firefox 157 added `"ipc",` inside that block. The merge dropped `"ipc",` as an enclosed line
and left `]` dangling. Rules: an enclosed upstream line goes with the block only when it belongs to it (shares a
token) or is trivial; a hand port may keep a line the hunk removes when the submit note says `keeps: <line>`; a
copy of the owner's old file is harmless when upstream only changed lines the fork removes.
"""
from fieldkit.buildh import firefox, verify

BEFORE = """DIRS += [
    "tests",
]

if CONFIG["MOZ_WIDGET_TOOLKIT"] != "android":
    DIRS += [
        "backends/llama",
        "ipc",
    ]

XPCSHELL_TESTS_MANIFESTS += ["tests/xpcshell/xpcshell.toml"]
""".splitlines()

H1 = {"lines": [
    '     "tests",', " ]", " ",
    '-if CONFIG["MOZ_WIDGET_TOOLKIT"] != "android":',
    '-    DIRS += ["backends/llama"]',
    "+# GORILLA excised: backends/llama (llama.cpp C++ backend).",
    " ", ' XPCSHELL_TESTS_MANIFESTS += ["tests/xpcshell/xpcshell.toml"]']}

GOOD = BEFORE[:4] + ["# GORILLA excised: backends/llama (llama.cpp C++ backend).",
                     'if CONFIG["MOZ_WIDGET_TOOLKIT"] != "android":', "    DIRS += [", '        "ipc",', "    ]"] + BEFORE[9:]


def test_an_upstream_line_inside_the_removed_block_is_not_collateral_free():
    bad = BEFORE[:4] + ["# GORILLA excised: backends/llama (llama.cpp C++ backend).", "    ]"] + BEFORE[9:]
    why = firefox.collateral(BEFORE, bad, H1)
    assert any('"ipc",' in w for w in why)                 # dropping upstream's new entry is damage, not the port


def test_a_declared_keep_passes_the_hand_check_and_is_remembered():
    assert firefox.hand_keeps('keeps: if CONFIG["MOZ_WIDGET_TOOLKIT"] != "android":; keeps: DIRS += [') == \
        ['if CONFIG["MOZ_WIDGET_TOOLKIT"] != "android":', "DIRS += ["]
    assert any("should be gone" in w for w in firefox.hand_port_check(BEFORE, GOOD, H1, BEFORE))
    keeps = firefox.hand_keeps('keeps: if CONFIG["MOZ_WIDGET_TOOLKIT"] != "android":')
    assert firefox.hand_port_check(BEFORE, GOOD, H1, BEFORE, keeps=keeps) == []
    assert firefox.still_holds({"done_by": "hand", "hand_note": 'keeps: if CONFIG["MOZ_WIDGET_TOOLKIT"] != "android":'},
                               "toolkit/components/ml/moz.build", H1, BEFORE, GOOD) == []


def test_a_copy_of_the_old_file_is_harmless_when_upstream_only_touched_removed_lines(tmp_path, monkeypatch):
    old = b"a\nGeneratedFile(\n    x.json,\n)\n"
    new = b"a\nGeneratedFile(\n    y.json,\n)\n"
    monkeypatch.setattr(verify, "hunks_in_scope", lambda hr: [("g", "p", "f/moz.build", 1, {"lines": ["-GeneratedFile(", "-    y.json,", "-)"]})])
    assert verify._upstream_changes_inside_removed_blocks(new, old, "f/moz.build", "hr")
    # the hunk removes another block: upstream's new line is not inside what the fork removes
    monkeypatch.setattr(verify, "hunks_in_scope", lambda hr: [("g", "p", "f/moz.build", 1, {"lines": ["-Other(", "-    z.json,", "-)"]})])
    assert not verify._upstream_changes_inside_removed_blocks(new, old, "f/moz.build", "hr")


def test_a_declared_removed_line_is_not_reported_as_renamed_onto_an_earlier_gorilla_line():
    """Build 27 (2026-10-04): nsHttpChannel.cpp. The hunk removes `s == ...::Private;`; a few lines above stands
    Gorilla's own earlier `nsILoadInfo::IPAddressSpace target = literal.GetIpAddressSpace();` (not in pristine), and
    the rename check called it the removed line under new names. The note's `keeps:` loses its `;` in hand_keeps."""
    hunk = {"lines": [
        "         if (NS_SUCCEEDED(trigAddr.InitFromString(trigHost))) {",
        "-          auto s = trigAddr.GetIpAddressSpace();",
        "-          fromLanPage = s == nsILoadInfo::IPAddressSpace::Local ||",
        "-                        s == nsILoadInfo::IPAddressSpace::Private;",
        "+          fromLanPage = trigAddr.GetIpAddressSpace() == target;",
        "         }"]}
    pristine = ["nsresult nsHttpChannel::Connect() {", "  return NS_OK;", "}"]
    after = ["nsresult nsHttpChannel::Connect() {",
             "      nsILoadInfo::IPAddressSpace target = literal.GetIpAddressSpace();",
             "         if (NS_SUCCEEDED(trigAddr.InitFromString(trigHost))) {",
             "          fromLanPage = trigAddr.GetIpAddressSpace() == target;",
             "         }", "  return NS_OK;", "}"]
    keeps = firefox.hand_keeps("why\nkeeps: s == nsILoadInfo::IPAddressSpace::Private;")
    assert not [w for w in firefox.hand_port_check(pristine, after, hunk, pristine, keeps) if "new names" in w]



def test_a_declared_removal_is_the_twin_of_a_declared_keep():
    """StaticPrefList.yaml h1 of the 158 port (2026-10-14): the hunk removes `value: @IS_NIGHTLY_BUILD@`, upstream had
    made it `value: true`; removing THAT line shares no distinctive token with the hunk and read as damage."""
    assert firefox.hand_removes("upstream changed it; removes: value: true") == ["value: true"]
    hunk = {"lines": ["#ifdef MOZ_ENABLE_SKIA_PDF", "- name: accessibility.tagged_pdf_output.enabled", "   type: bool",
                      "-  value: @IS_NIGHTLY_BUILD@", "+  value: false", "   mirror: always", "#endif"]}
    before = ["#ifdef MOZ_ENABLE_SKIA_PDF", "- name: accessibility.tagged_pdf_output.enabled", "  type: bool",
              "  value: true", "  mirror: always", "#endif", ""]
    after = ["#ifdef MOZ_ENABLE_SKIA_PDF", "- name: accessibility.tagged_pdf_output.enabled", "  type: bool",
             "  value: false", "  mirror: always", "#endif", ""]
    assert any("you removed" in w for w in firefox.hand_port_check(before, after, hunk, before))
    assert firefox.hand_port_check(before, after, hunk, before, removes=firefox.hand_removes("removes: value: true")) == []


def test_spans_end_on_the_same_line_when_upstream_inserted_a_block():
    """158 browser-shared.css h4 (2026-10-14): upstream put a mini-window block between the hunk's lines and its
    trailing context, and that block holds a second `--toolbox-background-image: none;`. A correct literal port
    was refused because only the after-side span reached that copy."""
    import json
    from pathlib import Path
    from fieldkit.buildh import firefox as f
    d = json.loads((Path(__file__).parent / "data" / "span-mini-window-158.json").read_text())
    hunk, before = d["hunk"], d["before"]
    body = hunk["lines"][:-2]                                   # the trailing context moved in 158
    old = "\n".join(l[1:] for l in body if l[:1] in " -")
    new = "\n".join(l[1:] for l in body if l[:1] in " +")
    after = "\n".join(before).replace(old, new).splitlines()
    assert after != before
    assert f.hunk_problems(before, after, hunk) == []
    i = after.index("  --toolbox-background-image: var(--lwt-additional-images);")
    left = after[:i] + ["  --toolbox-background-image: none;"] + after[i:]
    assert f.hunk_problems(before, left, hunk) == ["line should be gone: --toolbox-background-image: none;"]


def test_hunk_whose_distinctive_lines_are_all_gone_is_obsolete():
    """158 browser-shared.css h10 (2026-10-14): the CFR milestone block left the tree; generic lines like
    `display: flex;` still exist elsewhere and hid that from the all-lines rule."""
    from fieldkit.buildh import firefox as f
    hunk = {"lines": [
        '   &[data-notification-bucket="CFR_MILESTONE_MESSAGE"] {',
        "-    display: flex;",
        "-    background: radial-gradient(circle farthest-side at top right, var(--short-notification-gradient));",
        "+    &:not([hidden]) {",
        "+      display: flex;",
        "+      background: radial-gradient(circle farthest-side at top right, var(--short-notification-gradient));",
        "+    }",
    ]}
    body = ["#sidebar {", "  display: flex;", "  /* stylelint-disable-next-line */", "}"]
    assert f.obsolete_upstream(body, hunk, css=True)
    assert f.obsolete_upstream(body, hunk) is None              # code and prefs: may be a rename, a model chases it
    still = body + ['&[data-notification-bucket="CFR_MILESTONE_MESSAGE"] {', "    background: radial-gradient(circle farthest-side at top right, var(--short-notification-gradient));", "}"]
    assert f.obsolete_upstream(still, hunk, css=True) is None


def test_short_leading_context_belongs_to_the_span():
    """158 CustomizeMode h2 (2026-10-14): the hunk inserts a line after `enter() {`, a line too short to anchor,
    and 158 rewrote the last context line. The span began below the insertion and refused every correct port."""
    from fieldkit.buildh import firefox as f
    hunk = {"lines": ["    * window.", "    */", "   enter() {",
                      "+    console.warn(`CustomizeMode.enter called, customizing=${this.#customizing}`);",
                      "     if (", "       !this.#window.toolbar.visible ||",
                      '       this.#window.document.documentElement.hasAttribute("taskbartab")']}
    before = ["  /**", "   * window.", "   */", "  enter() {", "    if (", "      !this.#window.toolbar.visible ||",
              '      this.#window.document.documentElement.hasAttribute("taskbartab") ||',
              '      this.#window.document.documentElement.hasAttribute("mini-window")', "    ) {", "      return;", "    }"]
    add = "    console.warn(`CustomizeMode.enter called, customizing=${this.#customizing}`);"
    assert f.hunk_problems(before, before[:4] + [add] + before[4:], hunk) == []
    assert f.hunk_problems(before, before, hunk)


def test_a_declared_keep_matches_a_line_that_ends_in_a_semicolon():
    """hand_keeps splits the note on `;`; a kept CSS declaration must still match its own line (2026-10-14)."""
    from fieldkit.buildh import firefox as f
    hunk = {"lines": ["   --button-background-color: transparent;",
                      "-  --button-background-color-muted: light-dark(var(--color-gray-20), var(--color-gray-55));",
                      "   --button-background-color-primary: var(--color-accent-primary);"]}
    body = ["  --button-background-color: transparent;",
            "  --button-background-color-muted: light-dark(var(--color-gray-20), var(--color-gray-55));",
            "  --button-background-color-primary: var(--color-accent-primary);"]
    keeps = f.hand_keeps("adapted: 158 still uses it\nkeeps: --button-background-color-muted: "
                         "light-dark(var(--color-gray-20), var(--color-gray-55));")
    assert not [w for w in f.hand_port_check(body, body, hunk, keeps=keeps) if "should be gone" in w]
    assert [w for w in f.hand_port_check(body, body, hunk) if "should be gone" in w]
