# Version: 1.0.0 · updated 26-10-23-02-23
"""fk: short plain-English names for long commands, always showing the full command (owner, 2026-10-23)."""
import json
import subprocess
import sys

import pytest

from fieldkit import aliases as A


def test_every_public_short_name_runs_a_real_command():
    """A renamed command must break here, not silently in a person's hands."""
    names, _ = A.load(local="/nonexistent")
    assert names, "no aliases"
    for name, entry in names.items():
        assert entry.get("does"), f"{name}: says nothing about what it does"
        words, missing = A.expand(entry, [], {slot: "x" for slot, _ in A.SLOT.findall(entry["run"])})
        assert not missing, name
        r = subprocess.run([sys.executable, "-m", "fieldkit", *words, "--help"], capture_output=True, text=True,
                           timeout=120, cwd=A.ROOT)
        assert r.returncode == 0, f"{name}: fieldkit {' '.join(words)} --help -> {r.returncode}: {r.stderr[-300:]}"


def test_no_private_value_in_the_public_file():
    text = A.PUBLIC.read_text(encoding="utf-8")
    assert "macair" not in text and "/home/" not in text and "/Users/" not in text


def test_values_fill_slots_in_order_then_defaults_then_the_rest_is_appended():
    e = {"run": "mac screen-text {mac} --tty {tty}"}
    assert A.expand(e, ["air", "/dev/ttys001", "--lines", "5"], {}) == \
        (["mac", "screen-text", "air", "--tty", "/dev/ttys001", "--lines", "5"], [])
    assert A.expand(e, ["/dev/ttys001"], {"mac": "air"})[0][:3] == ["mac", "screen-text", "/dev/ttys001"]   # in order
    assert A.expand({"run": "privacy scan {path=.} --git"}, [], {})[0] == ["privacy", "scan", ".", "--git"]
    assert A.expand({"run": "mac doctor {mac}"}, [], {}) == (["mac", "doctor", "{mac}"], ["mac"])
    assert A.expand({"run": "mac doctor {mac}"}, [], {"mac": "air"})[0] == ["mac", "doctor", "air"]


def test_a_local_name_wins_and_brings_defaults(tmp_path):
    pub, loc = tmp_path / "p.yaml", tmp_path / "l.yaml"
    pub.write_text("aliases:\n  ready: {run: 'doctor --lean', does: 'd'}\n")
    loc.write_text("defaults:\n  mac: air\naliases:\n  ready: {run: 'doctor', does: 'mine'}\n  mine: {run: 'next', does: 'm'}\n")
    names, defaults = A.load(pub, loc)
    assert names["ready"]["does"] == "mine" and "mine" in names and defaults == {"mac": "air"}


def test_fk_prints_the_full_command_before_running_it(monkeypatch, capsys):
    monkeypatch.setattr(A, "load", lambda: ({"ready": {"run": "doctor --lean", "does": "d"}}, {}))
    seen = []
    rc = A.main(["ready"], run=lambda argv: seen.append(argv) or type("R", (), {"returncode": 0})())
    assert rc == 0 and capsys.readouterr().out.startswith("runs: fieldkit doctor --lean")
    assert seen[0][1:] == ["-m", "fieldkit", "doctor", "--lean"]
    assert A.main(["ready", "--show"], run=lambda argv: 1 / 0) == 0          # --show never runs


def test_unknown_names_and_missing_values_are_said_in_words(monkeypatch, capsys):
    monkeypatch.setattr(A, "load", lambda: ({"ready": {"run": "doctor --lean", "does": "d"},
                                              "mac": {"run": "mac doctor {mac}", "does": "m"}}, {}))
    assert A.main(["redy"]) == 2 and "Did you mean: ready" in capsys.readouterr().out
    assert A.main(["mac"]) == 2 and "needs: mac" in capsys.readouterr().out


def test_the_list_and_json_show_every_full_command(monkeypatch, capsys):
    monkeypatch.setattr(A, "load", lambda: ({"ready": {"run": "doctor --lean", "does": "complete?"}}, {}))
    assert A.main([]) == 0 and "fieldkit doctor --lean" in capsys.readouterr().out
    assert A.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ready": {"run": "fieldkit doctor --lean", "does": "complete?"}}
