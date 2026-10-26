"""Machine prerequisites in the mandate: declared, pinned by sha256, approved once, applied at a session start."""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from fieldkit.core import settings
from fieldkit.mandate import machine
from fieldkit.mandate import mandate as M

PROPOSED_KEYS = {"id", "os", "admin", "what", "cost", "verify"}


def _sha(rel):
    return hashlib.sha256((settings.ROOT / rel).read_bytes()).hexdigest()


def _m(*entries):
    return {"project": "t", "goal": "g", "expires": "2099-01-01", "repos": {}, "never": [],
            "machine_prerequisites": list(entries)}


LP = {"id": "longpaths-enabled", "os": "any", "admin": True, "what": "LongPathsEnabled = 1", "cost": "none",
      "script": "fieldkit/mandate/prereqs/longpaths-enabled.ps1", "apply": ["-Action", "apply"],
      "undo": ["-Action", "undo"], "verify": "lp"}
GIT = {"id": "git-longpaths", "os": "any", "admin": False, "what": "git core.longpaths", "cost": "none",
       "command": ["git", "--version"], "undo_command": ["git", "--version"], "verify": "git"}


def lp():
    return dict(LP, sha256=_sha(LP["script"]))


def test_doors_show_every_prerequisite_with_its_cost_and_pin():
    lines = M.doors(_m(lp(), GIT))
    pre = [x for x in lines if x.startswith("MACHINE PREREQUISITE")]
    assert len(pre) == 2
    assert "ELEVATED (one UAC click the first time)" in pre[0] and "COST: none" in pre[0] and "sha256" in pre[0]
    assert "as you" in pre[1] and "undo: fieldkit mandate prereqs undo git-longpaths" in pre[1]


def test_a_script_must_hash_to_its_pin():
    assert machine.sha_ok(lp())[0]
    bad = dict(lp(), sha256="0" * 64)
    ok, why = machine.sha_ok(bad)
    assert not ok and "refused" in why
    assert not machine.sha_ok(dict(lp(), script="nope.ps1"))[0]


def test_nothing_is_applied_without_a_mandate_in_force():
    code, lines = machine.apply(_m(lp()), False, verify={"lp": lambda: False})
    assert code == 3 and "no mandate in force" in lines[0]


def test_present_means_nothing_runs():
    calls = []
    code, lines = machine.apply(_m(lp(), GIT), True, verify={"lp": lambda: True, "git": lambda: True},
                                run=lambda *a, **k: calls.append(a), elevate_fn=lambda *a, **k: calls.append(a))
    assert code == 0 and calls == [] and "all 2 present" in lines[-1]


def test_user_entries_run_as_you_and_admin_entries_go_to_one_elevated_run(tmp_path, monkeypatch):
    monkeypatch.setattr(machine, "_state", lambda: tmp_path)
    state = {"lp": False, "git": False}
    ran, elevated = [], []

    class R:
        returncode = 0

    def run(cmd, **k):
        ran.append(cmd)
        state["git"] = True
        return R()

    def elev(todo, **k):
        elevated.append([e["id"] for e in todo])
        state["lp"] = True
        return True, "finished"
    monkeypatch.setattr(os, "name", "nt")
    code, lines = machine.apply(_m(lp(), GIT), True, verify={"lp": lambda: state["lp"], "git": lambda: state["git"]},
                                run=run, elevate_fn=elev, now=1000.0)
    assert code == 0 and elevated == [["longpaths-enabled"]] and len(ran) == 1
    # a second start within 12 h does not put the UAC prompt up again
    state["lp"] = False
    code, lines = machine.apply(_m(lp()), True, verify={"lp": lambda: state["lp"]}, elevate_fn=elev, now=2000.0)
    assert code == 3 and elevated == [["longpaths-enabled"]] and "less than 12 h ago" in " ".join(lines)


def test_a_changed_script_is_never_run(tmp_path, monkeypatch):
    monkeypatch.setattr(machine, "_state", lambda: tmp_path)
    elevated = []
    code, lines = machine.apply(_m(dict(lp(), sha256="1" * 64)), True, verify={"lp": lambda: False},
                                elevate_fn=lambda *a, **k: elevated.append(a) or (True, ""))
    assert code == 3 and elevated == [] and "NOT applied" in lines[0]


def test_an_unknown_check_is_never_present():
    rows = machine.check(_m(dict(lp(), verify="nope")), verify={})
    assert rows[0]["present"] is None
    rows = machine.check(_m(lp()), verify={"lp": lambda: 1 / 0})
    assert rows[0]["present"] is None and "check failed" in rows[0]["why"]


def test_the_runner_checks_each_sha_again_inside_the_elevated_process():
    t = machine.runner_text([lp()], root=settings.ROOT)
    assert "Get-FileHash -Algorithm SHA256" in t and lp()["sha256"] in t and "'-Action' 'apply'" in t
    assert t.rstrip().endswith('Write-Host "PREREQS: done ($failed failed)"')


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"), reason="Windows PowerShell parser")
def test_runner_and_prerequisite_scripts_parse(tmp_path):
    files = [settings.ROOT / LP["script"], settings.ROOT / "fieldkit/scratch/owner/scratch-exclusion.ps1"]
    (tmp_path / "runner.ps1").write_text(machine.runner_text([lp()]), encoding="utf-8")
    files.append(tmp_path / "runner.ps1")
    for f in files:
        cmd = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
               f"'{f}', [ref]$null, [ref]$e); $e.Count")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60)
        assert r.stdout.strip() == "0", (f, r.stdout, r.stderr)


def test_the_proposed_mandate_pins_the_scripts_in_this_tree():
    p = settings.ROOT / "fieldkit" / "mandate" / "owner-wide.prereqs.yaml"
    m = yaml.safe_load(p.read_text(encoding="utf-8"))
    es = machine.entries(m)
    assert {e["id"] for e in es} == {"scratch-defender-exclusion", "longpaths-enabled", "git-longpaths"}
    for e in es:
        assert PROPOSED_KEYS <= set(e) and machine.sha_ok(e)[0], e["id"]
        assert e["verify"] in machine.VERIFY
    ex = next(e for e in es if e["id"] == "scratch-defender-exclusion")
    assert ex["admin"] and "-Root" in ex["apply"] and "nothing" in ex["cost"].lower()
    # the same doors as the shipped owner-wide mandate, plus the prerequisites
    base = yaml.safe_load((settings.ROOT / "fieldkit" / "mandate" / "owner-wide.yaml").read_text(encoding="utf-8"))
    assert {k: v for k, v in m.items() if k not in ("machine_prerequisites", "expires")} == \
        {k: v for k, v in base.items() if k != "expires"}


def test_renew_from_a_changed_file_takes_its_text(tmp_path):
    from fieldkit.mandate import upkeep
    cur = tmp_path / "owner-wide.yaml"
    cur.write_text("project: a\nexpires: 2026-11-01\n", encoding="utf-8")
    src = tmp_path / "new.yaml"
    src.write_text("project: a\nexpires: 2026-11-01\nmachine_prerequisites: []\n", encoding="utf-8")
    from datetime import date
    text, new = upkeep.renewed_text(cur, 7, today=date(2026, 10, 26), source=src)
    assert "machine_prerequisites" in text and "expires: 2026-11-02" in text
    text, _ = upkeep.renewed_text(cur, 7, today=date(2026, 10, 26))
    assert "machine_prerequisites" not in text
