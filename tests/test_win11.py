"""fieldkit win11: the framework driven safely - module data, the elevated launcher, fit and the OEM check."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.win11 import cli, fit, oem, run

REAL = Path(os.environ.get("FIELDKIT_WIN11", "")) / "modules"


def fixture_repo(tmp_path, profiles=None):
    """A framework folder with every module's trio as empty scripts, and a profiles.json."""
    root = tmp_path / "w11"
    for m in run.modules().values():
        d = root / "modules" / m["folder"]
        d.mkdir(parents=True)
        for f in (m["check"][0], m["apply"], m["undo"][0]):
            (d / f).write_text("[CmdletBinding(SupportsShouldProcess)]\nparam()\n", encoding="utf-8")
    prof = profiles or {"profiles": {
        "light": {"services": [{"service": "WwanSvc", "reason": "No SIM in use on this machine."},
                               {"service": "fhsvc", "reason": "File History - not in use on this machine."}]},
        "moderate": {"services": [{"service": "TermService", "reason": "RDP is DISABLED on this machine"}]},
        "super": {"services": [{"service": "LxssManager", "reason": "WSL manager. Not installed here."},
                               {"service": "WpcMonSvc", "reason": "Parental Controls. Not in use on this machine."}]}}}
    (root / "modules" / "06-services" / "profiles.json").write_text(json.dumps(prof), encoding="utf-8")
    (root / "modules" / "06-services" / "Disable-LenovoServices.ps1").write_text(
        "$oem = @(\n [pscustomobject]@{ Service = 'ImControllerService'; Description = 'x' }\n"
        " [pscustomobject]@{ Service = 'LenovoVantageService'; Description = 'y' }\n)\n", encoding="utf-8")
    return root


def all_true(**over):
    return dict({k: (lambda: True) for k in fit.PROBES},
                **{k: (v if callable(v) else (lambda v=v: v)) for k, v in over.items()})


# -- the launcher ----------------------------------------------------------------------------------------------
def test_preview_adds_whatif_and_never_elevates(tmp_path):
    root = fixture_repo(tmp_path)
    m = run.modules()["services"]
    argv, elevate = run.command(m, "preview", {"profile": "light"}, root)
    assert argv[-3:] == ["-Profile", "light", "-WhatIf"] and elevate is False     # MODULE-STANDARD R6.16
    argv, elevate = run.command(m, "apply", {"profile": "light"}, root)
    assert "-WhatIf" not in argv and elevate is True
    argv, elevate = run.command(run.modules()["onedrive"], "undo", {}, root)
    assert argv[-2:] == ["-Part", "Machine"] and elevate is True
    assert run.command(run.modules()["visual-effects"], "apply", {}, root)[1] is False


def test_parameters_are_checked_before_anything_runs():
    m = run.modules()["services"]
    with pytest.raises(ValueError, match="required"):
        run.ps_args(m, {})
    with pytest.raises(ValueError, match="one of light, moderate, super"):
        run.ps_args(m, {"profile": "everything"})
    assert run.ps_args(run.modules()["copilot"], {"remove_app": True}) == ["-RemoveApp"]
    assert run.ps_args(run.modules()["copilot"], {"remove_app": False}) == []


class Fake:
    def __init__(self, code, out="", writes=None):
        self.code, self.out, self.writes, self.calls = code, out, writes, []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if self.writes is not None:                       # the elevated child writes its output to the file
            import re
            target = re.search(r"\*> '+(.+?)'+;", argv[-1]).group(1)
            Path(target).write_text(self.writes, encoding="utf-8")
        return subprocess.CompletedProcess(argv, self.code, self.out, "")


def test_an_elevated_run_is_waited_for_and_its_output_read(tmp_path):
    root = fixture_repo(tmp_path)
    fake = Fake(0, writes="backup written and verified\n12 services changed")
    code, text = run.run("services", "apply", {"profile": "light"}, root, runner=fake, admin=False)
    assert code == 0 and "12 services changed" in text
    cmd = " ".join(fake.calls[0])
    assert "-Verb RunAs -Wait -PassThru" in cmd and "exit $p.ExitCode" in cmd
    code, _ = run.run("services", "apply", {"profile": "light"}, root, runner=Fake(0), admin=True)
    assert code == 0                                               # already elevated: run directly


@pytest.mark.parametrize("script_exit,ours", [(0, 0), (3, 3), (4, 3), (6, 3), (1, 1), (5, 1)])
def test_the_frameworks_exit_contract(tmp_path, script_exit, ours):
    root = fixture_repo(tmp_path)
    code, text = run.run("visual-effects", "apply", {}, root, runner=Fake(script_exit), admin=True)
    assert code == ours
    if ours == 3:
        assert "Nothing was changed" in text


def test_paths_with_quotes_survive_the_elevation_wrapper():
    w = run.elevated_wrapper(["powershell", "-File", r"C:\it's here\Apply.ps1", "-Profile", "light"], r"C:\t\o.txt")
    # quoted once for the inner command, then again for -ArgumentList
    assert "''C:\\it''''s here\\Apply.ps1''" in w[-1]


# -- fit -------------------------------------------------------------------------------------------------------
def test_fit_reports_entries_whose_fact_does_not_hold_here(tmp_path):
    p = fixture_repo(tmp_path) / "modules" / "06-services" / "profiles.json"
    r = fit.fit(p, "super", probes=all_true(wsl_absent=False, rdp_disabled=None, ask_parental_controls=None))
    assert [x["service"] for x in r["findings"]] == ["LxssManager"]
    assert {x["service"] for x in r["unknown"]} == {"TermService", "WpcMonSvc"}
    assert fit.fit(p, "light", probes=all_true(wsl_absent=False))["findings"] == []       # cumulative, not beyond


def test_a_probe_that_breaks_is_unknown_not_fine(tmp_path):
    p = fixture_repo(tmp_path) / "modules" / "06-services" / "profiles.json"

    def boom():
        raise OSError("no")
    r = fit.fit(p, "light", probes=all_true(wwan_unused=boom))
    assert [x["service"] for x in r["unknown"]] == ["WwanSvc"] and r["findings"] == []


@pytest.mark.skipif(not (REAL / "06-services" / "profiles.json").is_file(), reason="FIELDKIT_WIN11 not set")
def test_every_machine_specific_entry_of_the_real_profiles_is_mapped():
    assert fit.unmapped(REAL / "06-services" / "profiles.json") == []


# -- oem and modules -------------------------------------------------------------------------------------------
def test_oem_reads_the_frameworks_own_list_and_flags_a_profile_that_names_one(tmp_path):
    root = fixture_repo(tmp_path)
    r = oem.check(root / "modules", maker="LENOVO", present=lambda s: s == "ImControllerService")
    assert r["protected"] == ["ImControllerService", "LenovoVantageService"] and r["present"] == ["ImControllerService"]
    assert "finding" not in r
    prof = json.loads((root / "modules" / "06-services" / "profiles.json").read_text())
    prof["profiles"]["light"]["services"].append({"service": "ImControllerService", "reason": "OEM bloat"})
    (root / "modules" / "06-services" / "profiles.json").write_text(json.dumps(prof))
    assert "ImControllerService" in oem.check(root / "modules", maker="LENOVO", present=lambda s: False)["finding"]
    note = oem.check(fixture_repo(tmp_path / "b") / "modules", maker="Dell Inc.", present=lambda s: False)
    assert "no verified list of Dell Inc." in note["note"]


def test_modules_conformance(tmp_path, capsys):
    root = fixture_repo(tmp_path)
    assert cli.main(["--repo", str(root), "modules"]) == 0
    (root / "modules" / "05-xbox-services" / "Restore-XboxServices.ps1").unlink()
    assert cli.main(["--repo", str(root), "modules"]) == 3
    assert "missing: undo" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform == "win32", reason="the refusal is for other systems")
def test_run_refuses_off_windows(capsys):
    assert cli.main(["run", "services", "apply", "--profile", "light"]) == 2


def test_the_cards_follow_the_module_data():
    """Every module has its check card and its change card, whose inputs are the module's parameters, whose
    preview is --preview and whose undo is the module's undo: the cards cannot drift from modules.yaml."""
    import yaml
    from fieldkit.core import settings
    cards = {c["id"]: c for c in yaml.safe_load((settings.ROOT / "fieldkit" / "desk" / "tools.yaml")
                                                .read_text(encoding="utf-8"))["tools"]}
    for m in run.modules().values():
        chk, chg = cards[f"win11-{m['id']}-check"], cards[f"win11-{m['id']}"]
        assert chk["safety"] == "read-only" and chk["entry"][-1] == "check"
        assert chg["entry"][-2:] == [m["id"], "apply"] and chg["modes"]["preview"] == ["+", "--preview"]
        assert chg["modes"]["undo"][-2:] == [m["id"], "undo"]
        assert [i["name"] for i in chg.get("inputs") or []] == [p["name"] for p in m.get("params") or []]
        assert chg["safety"] == ("irreversible" if m.get("undo_partial") else "reversible")
        if m.get("admin"):
            assert "admin" in chg["effects"]                      # so the agent door asks the owner every time
