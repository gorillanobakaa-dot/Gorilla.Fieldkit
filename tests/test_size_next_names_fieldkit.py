"""Row 54: the program prints "then: gorilla-size apply PLAN", which is not on PATH in Git Bash or PowerShell;
`fieldkit size` forwards it with every such line naming the command that runs."""
import sys

from fieldkit.desk import size


def test_next_lines_start_with_fieldkit_size():
    t = "Review it, then: gorilla-size apply C:/p/x-recycle.json\n(undo: gorilla-size undo \"J.jsonl\")\n"
    out = size.rename_next(t)
    assert "then: fieldkit size apply C:" in out and "undo: fieldkit size undo" in out
    assert "gorilla-size apply" not in out and "gorilla-size undo" not in out


def test_other_text_is_untouched():
    t = "gorilla-size 0.3.0\nMISSING gorilla-size is not built\n"
    assert size.rename_next(t) == t


def test_forwarded_output_is_renamed_and_exit_code_kept(tmp_path, capsys):
    fake = tmp_path / "fake.py"
    fake.write_text("import sys\nprint('then: gorilla-size apply P')\nprint('err: gorilla-size undo J', file=sys.stderr)\nsys.exit(4)\n")
    bat = tmp_path / "fake.cmd"
    bat.write_text(f'@"{sys.executable}" "{fake}" %*\n')
    rc = size._run_renamed(bat, ["recycle", "X"])
    cap = capsys.readouterr()
    assert rc == 4
    assert cap.out.startswith("then: fieldkit size apply") and "fieldkit size undo" in cap.err
