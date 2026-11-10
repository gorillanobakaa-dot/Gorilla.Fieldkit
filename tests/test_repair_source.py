"""fieldkit repair, source-file tools (I-19, part 1): each tool finds its failure, fixes only what is provably safe,
and a fix that would not parse writes nothing."""
import ast
import json

import pytest

from fieldkit.repair import brace_parity_check as bp
from fieldkit.repair import cp1252_stdout_fix as cp
from fieldkit.repair import keep_both as kb
from fieldkit.repair import missing_import_fix as mi
from fieldkit.repair import plan_row_insert as pr
from fieldkit.repair import pwsh_syntax_check as ps


def _w(tmp_path, name, text):
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


# --- cp1252-stdout-fix ---------------------------------------------------------------------------------------------
def test_cp1252_finds_and_fixes_text_open_only(tmp_path):
    src = ('"""doc"""\nfrom __future__ import annotations\nimport json\n'
           'a = open("x.txt")\nb = open("y.bin", "rb")\nc = open("z", mode="w")\nd = open("e", "r", encoding="utf-8")\n'
           'e = open(f(g(1)), "a")\nP.read_text()\nP.write_text("t")\nprint("hi")\n')
    f = _w(tmp_path, "a.py", src)
    assert cp.main([str(f)]) == 3
    assert f.read_text(encoding="utf-8") == src                       # no --apply, no change
    assert cp.main([str(f), "--apply"]) == 0
    out = f.read_text(encoding="utf-8")
    ast.parse(out)
    assert 'open("x.txt", encoding="utf-8")' in out and 'open("z", mode="w", encoding="utf-8")' in out
    assert 'open("y.bin", "rb")' in out and 'open("e", "r", encoding="utf-8")' in out
    assert 'open(f(g(1)), "a", encoding="utf-8")' in out
    assert 'P.read_text(encoding="utf-8")' in out and 'P.write_text("t", encoding="utf-8")' in out
    lines = out.splitlines()
    assert lines[:2] == ['"""doc"""', "from __future__ import annotations"] and "reconfigure" in lines[2]
    assert cp.main([str(f)]) == 0                                     # a second run finds nothing


def test_cp1252_leaves_star_args_and_syntax_errors(tmp_path):
    f = _w(tmp_path, "b.py", "open(*a)\nopen(**k)\n")
    assert cp.main([str(f)]) == 0
    g = _w(tmp_path, "c.py", "def (:\n")
    assert cp.main([str(g), "--apply"]) == 1 and g.read_text() == "def (:\n"


def test_cp1252_keeps_crlf(tmp_path):
    f = _w(tmp_path, "d.py", 'x = open("a")\r\nprint(x)\r\n')
    assert cp.main([str(f), "--apply"]) == 0
    assert b"\n" not in f.read_bytes().replace(b"\r\n", b"")


# --- missing-import-fix --------------------------------------------------------------------------------------------
def test_missing_import_added_after_docstring_and_future(tmp_path):
    f = _w(tmp_path, "m.py", '"""d"""\nfrom __future__ import annotations\nimport sys\n\nshutil.copy(1, 2)\nre.sub(1, 2, 3)\nsys.exit()\n')
    assert mi.main([str(f)]) == 3
    assert mi.main([str(f), "--apply"]) == 0
    out = f.read_text(encoding="utf-8").splitlines()
    assert out[:2] == ['"""d"""', "from __future__ import annotations"] and out[2:4] == ["import re", "import shutil"]
    assert mi.main([str(f)]) == 0


def test_missing_import_ignores_names_the_file_binds(tmp_path):
    f = _w(tmp_path, "n.py", "def f(time):\n    return time.sleep\n\njson = object()\njson.dumps\nfor re in []:\n    re.x\n")
    assert mi.find_missing(f.read_text()) == []
    assert mi.main([str(f)]) == 0


# --- keep-both -----------------------------------------------------------------------------------------------------
def test_keep_both_keeps_ours_then_theirs_and_line_endings(tmp_path):
    f = _w(tmp_path, "log.md", "a\r\n<<<<<<< HEAD\r\nours\r\n=======\r\ntheirs\r\n>>>>>>> br\r\nz\r\n")
    assert kb.main([str(f)]) == 3 and b"<<<<<<<" in f.read_bytes()
    assert kb.main([str(f), "--apply"]) == 0
    assert f.read_bytes() == b"a\r\nours\r\ntheirs\r\nz\r\n"


def test_keep_both_drops_diff3_base_and_refuses_a_stray_marker(tmp_path):
    f = _w(tmp_path, "x.md", "<<<<<<< a\n1\n||||||| base\n0\n=======\n2\n>>>>>>> b\n")
    assert kb.main([str(f), "--apply"]) == 0 and f.read_text() == "1\n2\n"
    g = _w(tmp_path, "y.md", "<<<<<<< a\n1\n=======\n2\n")           # never closed
    before = g.read_bytes()
    assert kb.main([str(g), "--apply"]) == 1 and g.read_bytes() == before


def test_keep_both_refuses_generated_registries(tmp_path):
    f = _w(tmp_path, "tools.yaml", "<<<<<<< a\n1\n=======\n2\n>>>>>>> b\n")
    assert kb.main([str(f), "--apply"]) == 1 and b"<<<<<<<" in f.read_bytes()


# --- brace-parity-check --------------------------------------------------------------------------------------------
def test_brace_parity_balanced_ignores_strings_comments_macros_raw(tmp_path):
    src = ('#define M(x) { \\\n  ( x\n#include <a.h>\nint main() { char c = \'}\'; const char* s = "{(["; // }\n'
           '/* ) */ auto r = R"x(}})x"; int n = 1\'000; return (0); }\n')
    assert bp.check_text(src) == []


def test_brace_parity_reports_each_rule_with_line_numbers():
    f = bp.check_text("int f() {\n  if (x) {\n}\n")
    assert [(x["rule"], x["line"]) for x in f] == [("unclosed", 1)]
    f = bp.check_text("int f() { ( ]\n}")
    assert any(x["rule"] == "mismatched-close" and x["opened"] == "(" for x in f)
    assert bp.check_text("}\n")[0]["rule"] == "unmatched-close"


def test_brace_parity_cli_exit_codes(tmp_path, capsys):
    good = _w(tmp_path, "g.cpp", "int a(){}\n")
    bad = _w(tmp_path, "b.cpp", "int a(){\n")
    assert bp.main([str(good)]) == 0
    assert bp.main([str(bad), "--json"]) == 3
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):])["finding_count"] == 1
    assert bp.main([str(tmp_path / "x.txt")]) == 1


# --- pwsh-syntax-check ---------------------------------------------------------------------------------------------
def test_pwsh_finds_bash_and_unbalanced_but_not_strings():
    clean = 'Write-Host "{0} ( {1}" -f 1,2\n$x = @\'\n{{{\n\'@\n# } comment\nif ($a) { 1 }\n'
    assert ps.check_source(clean) == []
    assert any("heredoc" in i["rule"] for i in ps.check_source("cat <<EOF\nhi\nEOF\n"))
    assert any("export" in i["rule"] for i in ps.check_source("export PATH=/x\n"))
    assert any("unbalanced braces" in i["rule"] for i in ps.check_source("if ($a) {\n 1\n"))


def test_pwsh_cli_lexical(tmp_path):
    good = _w(tmp_path, "g.ps1", "Write-Host 1\n")
    bad = _w(tmp_path, "b.ps1", "export X=1\n")
    assert ps.main([str(good), "--lexical"]) == 0
    assert ps.main([str(bad), "--lexical"]) == 3
    assert ps.main([str(tmp_path / "none.txt")]) == 1


def test_pwsh_real_parser_findings_are_reported(tmp_path):
    f = _w(tmp_path, "p.ps1", "Write-Host 1\n")

    class R:
        stdout = "3|Missing closing '}'\n"
    got = ps.real_parse(f, run=lambda *a, **k: R(), which=lambda n: "pwsh")
    assert got == [{"line": 3, "rule": "parser: Missing closing '}'"}]
    assert ps.real_parse(f, which=lambda n: None) is None


# --- plan-row-insert -----------------------------------------------------------------------------------------------
def test_plan_row_inserted_before_the_one_anchor_line_keeps_crlf(tmp_path):
    f = _w(tmp_path, "PLAN.md", "| a |\r\n## Waiting\r\n| w |\r\n")
    assert pr.main([str(f), "--anchor", "## Waiting", "--row", "| new |"]) == 3
    assert pr.main([str(f), "--anchor", "## Waiting", "--row", "| new |", "--apply"]) == 0
    assert f.read_bytes() == b"| a |\r\n| new |\r\n## Waiting\r\n| w |\r\n"
    assert pr.main([str(f), "--anchor", "## Waiting", "--row", "| new |", "--apply"]) == 0   # idempotent
    assert f.read_bytes().count(b"| new |") == 1


def test_plan_row_refuses_a_mid_line_or_repeated_anchor(tmp_path):
    f = _w(tmp_path, "P.md", "see ## Waiting here\n## Waiting\n## Waiting 2\n")
    before = f.read_bytes()
    assert pr.main([str(f), "--anchor", "## Waiting", "--row", "| zzz |", "--apply"]) == 3          # two lines start with it
    assert pr.main([str(f), "--anchor", "## Nope", "--row", "| zzz |", "--apply"]) == 3
    assert f.read_bytes() == before


def test_all_actions_have_a_module():
    import importlib

    from fieldkit import repair
    for name, mod in repair.ACTIONS.items():
        if name in ("cp1252-stdout-fix", "missing-import-fix", "keep-both", "brace-parity-check", "pwsh-syntax-check",
                    "plan-row-insert"):
            assert callable(importlib.import_module("fieldkit.repair." + mod).main)
