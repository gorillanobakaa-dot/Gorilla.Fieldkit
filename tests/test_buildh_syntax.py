"""Live run 16 (2026-10-01 23:36): the 157 build stopped at configure on browser/themes/addons/moz.build - a merge had
removed a `GeneratedFile(...)` block but left its opener line, `'(' was never closed`. Three rules: block_removal also
removes call blocks (`Name(` ... `)`); every changed moz.build/.py/.json must parse (final checks and the verifier); a
hand check judges a removed line that other blocks also use by COUNT, before -> after.
"""
from fieldkit.buildh import firefox

MOZ = """JAR_MANIFESTS += ["jar.mn"]

GeneratedFile(
    "privatewindow/manifest.json",
    script="process_tokens.py",
    entry_point="process_tokens",
    inputs=[
        "privatewindow/manifest.in.json",
    ],
)

GeneratedFile(
    "aiwindow-nova/manifest.json",
    script="process_tokens.py",
    entry_point="process_tokens",
    inputs=[
        "aiwindow-nova/manifest.in.json",
        "/browser/themes/shared/tabbrowser/tab.nova.tokens.json",
        "/toolkit/themes/shared/design-system/src/tokens/base/background.tokens.json",
    ],
)
""".splitlines()

H2 = {"lines": [
    "     ],", " )", " ",
    "-GeneratedFile(",
    '-    "aiwindow-nova/manifest.json",',
    '-    script="process_tokens.py",',
    '-    entry_point="process_tokens",',
    "-    inputs=[",
    '-        "aiwindow-nova/manifest.in.json",',
    '-        "/browser/themes/shared/tabbrowser/tab.nova.tokens.json",',
    '-        "/toolkit/themes/shared/design-system/src/tokens/base/background.nova.tokens.json",',
    "-    ],",
    "-)",
    '+# GORILLA excised: "aiwindow-nova/manifest.json" GeneratedFile (aiwindow theme)']}


def test_a_call_block_is_removed_whole_even_when_upstream_changed_a_line_inside():
    # the hunk's opener occurs TWICE in the file (privatewindow keeps its own block): block_removal must find the
    # right one by its inner lines, not the first
    try:
        new = firefox.block_removal(MOZ, H2, [])
        assert '    "aiwindow-nova/manifest.json",' not in new and new.count("GeneratedFile(") == 1
        assert '# GORILLA excised: "aiwindow-nova/manifest.json" GeneratedFile (aiwindow theme)' in new
        import ast
        ast.parse("\n".join(new))
    except firefox.Ambiguous as e:
        assert "opener occurs 2 times" in str(e)          # refusing is acceptable; leaving `GeneratedFile(` is not


def test_changed_build_files_must_parse(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a/moz.build").write_text("GeneratedFile(\n# dangling\n", encoding="utf-8")
    (tmp_path / "a/ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "a/bad.json").write_text("{", encoding="utf-8")
    out = firefox.syntax_problems(tmp_path, ["a/moz.build", "a/ok.py", "a/bad.json", "a/missing.py"])
    assert len(out) == 2 and out[0].startswith("a/moz.build:") and out[1].startswith("a/bad.json:")


def test_hand_check_counts_a_shared_line_before_and_after():
    broken = MOZ[:12] + ['# GORILLA excised: "aiwindow-nova/manifest.json" GeneratedFile (aiwindow theme)']   # opener left
    fixed = MOZ[:11] + ['# GORILLA excised: "aiwindow-nova/manifest.json" GeneratedFile (aiwindow theme)']
    assert firefox.hand_port_check(broken, fixed, H2, MOZ) == []      # shared lines counted against the pristine file
    assert firefox.hand_port_holds(fixed, H2, MOZ) == []
    # a shared opener left dangling is not a text question: the parse check answers it
    assert firefox.hand_port_check(broken, broken, H2, MOZ) == []
    import ast, pytest
    with pytest.raises(SyntaxError):
        ast.parse("\n".join(broken))
