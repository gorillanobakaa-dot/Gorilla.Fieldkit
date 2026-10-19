"""fieldkit canary: a check that cannot fail is caught (BLIND), so is one that fails everything (FALSE-ALARM); a missing
check is UNAVAILABLE, never OK; two witnesses that disagree are a finding. Stand-in checks are tiny Python programs, so
no real tool's behavior is assumed here; the real registry is run by test_the_shipped_registry_proves_every_check."""
import sys
from pathlib import Path

import pytest
import yaml

from fieldkit import cli
from fieldkit.method import canary

PY = sys.executable
# a stand-in check: exit 3 when any planted file holds the word BAD, else 0
CHECK = ("import pathlib, sys; d = pathlib.Path(sys.argv[1]); "
         "sys.exit(3 if any('BAD' in p.read_text() for p in d.rglob('*') if p.is_file()) else 0)")


def reg(tmp_path, canaries=(), witnesses=()):
    f = tmp_path / "canaries.yaml"
    f.write_text(yaml.safe_dump({"canaries": list(canaries), "witnesses": list(witnesses)}), encoding="utf-8")
    return [str(f)]


def c(id, run, canary_text="BA" + "D\n", control_text="fine\n"):
    parts = lambda t: t if isinstance(t, list) else [t]
    return {"id": id, "why": id, "run": run, "canary": {"files": {"a.txt": parts(canary_text)}},
            "control": {"files": {"a.txt": parts(control_text)}}, "flagged": {"exit": [3]}, "passed": {"exit": [0]}}


def test_verdicts_ok_blind_false_alarm_unavailable(tmp_path):
    good = c("good", ["{python}", "-c", CHECK, "{dir}"])
    blind = c("blind", ["{python}", "-c", "import sys; sys.exit(0)", "{dir}"])
    alarm = c("alarm", ["{python}", "-c", "import sys; sys.exit(3)", "{dir}"])
    gone = c("gone", ["no-such-program-fieldkit-canary", "{dir}"])
    rows = {r["id"]: r["verdict"] for r in (canary.run_one(x) for x in (good, blind, alarm, gone))}
    assert rows == {"good": "OK", "blind": "BLIND", "alarm": "FALSE-ALARM", "gone": "UNAVAILABLE"}


def test_parts_are_joined_so_the_registry_never_holds_the_canary_whole(tmp_path):
    x = c("split", ["{python}", "-c", CHECK, "{dir}"], canary_text=["B", "A", "D"])
    assert canary.run_one(x)["verdict"] == "OK"


def test_a_side_may_carry_its_own_command_line(tmp_path):
    x = {"id": "cmd", "run": ["{python}", "-c", "import sys; sys.exit(9)"],
         "canary": {"run": ["{python}", "-c", "import sys; sys.exit(2)"]},
         "control": {"run": ["{python}", "-c", "import sys; sys.exit(0)"]},
         "flagged": {"exit": [2]}, "passed": {"exit": [0]}}
    assert canary.run_one(x)["verdict"] == "OK"


def test_cli_exit_codes_and_the_gate(tmp_path, capsys):
    r = reg(tmp_path, [c("good", ["{python}", "-c", CHECK, "{dir}"]),
                       c("blind", ["{python}", "-c", "import sys; sys.exit(0)", "{dir}"])])
    assert cli.main(["canary", "run", "good", "--registry", r[0]]) == 0
    assert cli.main(["canary", "run", "--registry", r[0]]) == 3
    assert "blind BLIND" in capsys.readouterr().out
    assert cli.main(["canary", "run", "nope", "--registry", r[0]]) == 2
    ok, rows = canary.gate(["good"], registry=r)
    assert ok and rows[0]["verdict"] == "OK"
    assert canary.gate(registry=r)[0] is False                      # a blind check cannot sign a release


def test_witnesses_agree_or_disagree(tmp_path, capsys):
    clean = {"run": ["{python}", "-c", "import sys; sys.exit(0)"], "flagged": {"exit": [3]}, "passed": {"exit": [0]}}
    dirty = dict(clean, run=["{python}", "-c", "import sys; sys.exit(3)"])
    agree = {"id": "agree", "why": "both clean", "source": clean, "artifact": clean}
    # 2026-10-18 in miniature: the source reading said clean, the shipped program was not
    disagree = {"id": "disagree", "why": "source clean, artifact not", "source": clean, "artifact": dirty}
    missing = {"id": "missing", "why": "no artifact here", "source": clean,
               "artifact": dict(dirty, requires=[str(tmp_path / "no.exe")])}
    rows = {w["id"]: canary.witness_one(w)["verdict"] for w in (agree, disagree, missing)}
    assert rows == {"agree": "AGREE", "disagree": "DISAGREE", "missing": "UNAVAILABLE"}
    r = reg(tmp_path, witnesses=[agree, disagree])
    assert cli.main(["canary", "witness", "agree", "--registry", r[0]]) == 0
    assert cli.main(["canary", "witness", "--registry", r[0]]) == 3


def test_duplicate_ids_are_refused(tmp_path):
    r = reg(tmp_path, [c("x", ["{python}", "-V"]), c("x", ["{python}", "-V"])])
    with pytest.raises(ValueError):
        canary.load(r)


def test_the_shipped_registry_proves_every_check():
    # the real checks on their real canaries: offline, a few seconds; gorilla-size rows need the program built
    # theme canaries need the owner's theme spec (gorilla-patchset); where it is absent (CI) they must say UNAVAILABLE
    from fieldkit.buildh import themecheck
    from fieldkit.desk import size
    reg_ = canary.load([canary.REGISTRY])
    assert len(reg_["canaries"]) >= 16
    rows = [canary.run_one(x) for x in reg_["canaries"]
            if size.locate() or not x["id"].startswith("size-")]
    no_spec = themecheck.find_spec() is None
    bad = [(r["id"], r["verdict"]) for r in rows
           if r["verdict"] != "OK" and not (no_spec and r["id"].startswith("themecheck-") and r["verdict"] == "UNAVAILABLE")]
    assert not bad, bad


def test_a_stale_gorilla_size_is_caught_blind(tmp_path, monkeypatch):
    # 2026-10-19: a worktree still held gorilla-size 0.2, which exits 0 when it finds duplicates; the size-dupes canary
    # called it BLIND. A stand-in program that always exits 0 must be caught the same way.
    stub = tmp_path / "stub.py"
    stub.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    dupes = next(c for c in canary.load([canary.REGISTRY])["canaries"] if c["id"] == "size-dupes")
    dupes = dict(dupes, run=["{python}", str(stub), "{dir}"])
    assert canary.run_one(dupes)["verdict"] == "BLIND"
