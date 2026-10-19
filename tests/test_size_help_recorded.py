"""`fieldkit size SUB --help` answers on a fresh download, before gorilla-size is built (2026-10-19: every size card
failed the card check on GitHub CI and for a stranger), and the recorded help stays the program's own (drift)."""
import subprocess

import pytest

from fieldkit.desk import size


def test_help_works_without_the_program(monkeypatch, capsys):
    monkeypatch.setattr(size, "locate", lambda: None)
    for sub in size.HELP_SUBS:
        argv = ([sub] if sub else []) + ["--json", "--help"]
        assert size.main(argv) == 0, sub
        out = capsys.readouterr().out
        assert "gorilla-size" in out and "not built here yet" in out
    assert size.main(["find", "x"]) == 2                     # a real command still needs the program


@pytest.mark.skipif(size.locate() is None, reason="gorilla-size is not built here: nothing to compare against")
def test_the_recorded_help_is_the_programs_own():
    exe = size.locate()
    for sub in size.HELP_SUBS:
        live = subprocess.run([str(exe), *([sub] if sub else []), "--help"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout.replace("\r\n", "\n")
        recorded = (size.HELP_DIR / f"{sub or '_main'}.txt").read_text(encoding="utf-8")
        assert recorded == live, f"{sub or 'main'}: run `fieldkit size record-help` after upgrading gorilla-size"
