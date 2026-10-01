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
    monkeypatch.setattr(verify, "hunks_in_scope", lambda hr: [("g", "p", "f/moz.build", 1, {"lines": ["-GeneratedFile(", "-    x.json,", "-)"]})])
    assert not verify._upstream_changes_inside_removed_blocks(new, old, "f/moz.build", "hr")
