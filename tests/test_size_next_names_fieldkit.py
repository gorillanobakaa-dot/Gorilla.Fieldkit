"""Row 54: the program prints "then: gorilla-size apply PLAN", which is not on PATH in Git Bash or PowerShell;
`fieldkit size` forwards it with every such line naming the command that runs."""
import os
import sys

from fieldkit.desk import size


def _launcher(tmp_path, name, body):
    """A stand-in for the program that runs `python fake.py ARGS` on Windows and Linux."""
    fake = tmp_path / "fake.py"
    fake.write_text(body)
    if os.name == "nt":
        exe = tmp_path / (name + ".cmd")
        exe.write_text('@"%s" "%s" %%*' % (sys.executable, fake) + chr(10))
    else:
        exe = tmp_path / name
        exe.write_text("#!/bin/sh" + chr(10) + 'exec "%s" "%s" "$@"' % (sys.executable, fake) + chr(10))
        exe.chmod(0o755)
    return exe


def test_next_lines_start_with_fieldkit_size():
    t = "Review it, then: gorilla-size apply C:/p/x-recycle.json\n(undo: gorilla-size undo \"J.jsonl\")\n"
    out = size.rename_next(t)
    assert "then: fieldkit size apply C:" in out and "undo: fieldkit size undo" in out
    assert "gorilla-size apply" not in out and "gorilla-size undo" not in out


def test_other_text_is_untouched():
    t = "gorilla-size 0.3.0\nMISSING gorilla-size is not built\n"
    assert size.rename_next(t) == t


def test_forwarded_output_is_renamed_and_exit_code_kept(tmp_path, capsys):
    bat = _launcher(tmp_path, "gorilla-size", "import sys\nprint('then: gorilla-size apply P')\nprint('err: gorilla-size undo J', file=sys.stderr)\nsys.exit(4)\n")
    rc = size._run_renamed(bat, ["recycle", "X"])
    cap = capsys.readouterr()
    assert rc == 4
    assert cap.out.startswith("then: fieldkit size apply") and "fieldkit size undo" in cap.err


def test_main_routes_recycle_through_the_renamer(tmp_path, monkeypatch, capsys):
    bat = _launcher(tmp_path, "gorilla-size", "print('Review it, then: gorilla-size apply P')\n")
    monkeypatch.setenv("FIELDKIT_GORILLA_SIZE", str(bat))
    assert size.main(["recycle", "X", "--whole-folder"]) == 0
    assert "then: fieldkit size apply P" in capsys.readouterr().out
