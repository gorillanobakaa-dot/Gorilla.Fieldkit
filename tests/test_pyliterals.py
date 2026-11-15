"""pyliterals: stray backslashes are found; --apply only ever changes a literal when its value stays the same
(or it is a drive path that did not parse); a fix that would not parse writes nothing."""
import json

from fieldkit.build import pyliterals

BS = "\\"


def test_invalid_escape_fixed_value_preserving(tmp_path):
    f = tmp_path / "a.py"
    f.write_text('import re\nRX = re.compile("' + BS + 'd+' + BS + 's")\n', encoding="utf-8")
    r = pyliterals.check_file(f)
    assert [x["kind"] for x in r["findings"]] == ["invalid-escape"] and r["findings"][0]["fixable"]
    r = pyliterals.check_file(f, apply=True)
    assert r["applied"] == 1 and r["error"] is None
    assert 'r"' + BS + 'd+' + BS + 's"' in f.read_text(encoding="utf-8")


def test_mixed_valid_and_invalid_escape_is_not_changed(tmp_path):
    # "\n" is a real newline: making the literal raw would change its value, so it is reported, not fixed.
    src = 'X = "a' + BS + 'n' + BS + 'd"\n'
    f = tmp_path / "b.py"
    f.write_text(src, encoding="utf-8")
    r = pyliterals.check_file(f, apply=True)
    assert r["findings"] and not r["findings"][0]["fixable"] and r["applied"] == 0
    assert f.read_text(encoding="utf-8") == src


def test_valid_escapes_and_raw_strings_untouched(tmp_path):
    f = tmp_path / "c.py"
    f.write_text('A = "a' + BS + 'tb' + BS + 'n"\nB = r"' + BS + 'd"\nC = "' + BS + 'b"\n', encoding="utf-8")
    assert pyliterals.check_file(f)["findings"] == []


def test_unicode_escape_drive_path_fixed(tmp_path):
    f = tmp_path / "d.py"
    f.write_text('P = "C:' + BS + 'Users' + BS + 'x' + BS + 'file.txt"\n', encoding="utf-8")
    r = pyliterals.check_file(f, apply=True)
    assert r["findings"][0]["kind"] == "unicode-escape" and r["applied"] == 1
    assert f.read_text(encoding="utf-8").startswith('P = r"C:')


def test_unicode_escape_not_a_path_reported_only(tmp_path):
    src = 'P = "see ' + BS + 'Users here"\n'
    f = tmp_path / "e.py"
    f.write_text(src, encoding="utf-8")
    r = pyliterals.check_file(f, apply=True)
    assert r["findings"][0]["kind"] == "unicode-escape" and not r["findings"][0]["fixable"]
    assert f.read_text(encoding="utf-8") == src


def test_crlf_kept(tmp_path):
    f = tmp_path / "f.py"
    f.write_bytes(('X = "' + BS + 'd"\r\nY = 1\r\n').encode())
    pyliterals.check_file(f, apply=True)
    assert f.read_bytes() == ('X = r"' + BS + 'd"\r\nY = 1\r\n').encode()


def test_cli_exit_codes(tmp_path, capsys):
    f = tmp_path / "g.py"
    f.write_text('X = "' + BS + 'd"\n', encoding="utf-8")
    assert pyliterals.main([str(f)]) == 3
    assert pyliterals.main([str(f), "--apply"]) == 0
    assert pyliterals.main([str(f), "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("["):])[0]["findings"] == []
    assert pyliterals.main([str(tmp_path / "missing.py")]) == 1


def test_self_clean():
    assert pyliterals.check_file(pyliterals.__file__)["findings"] == []
