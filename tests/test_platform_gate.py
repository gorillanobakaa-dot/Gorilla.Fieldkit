# Version: 1.0.0 · updated 26-10-20-12-45
"""2026-10-20, owner: "the harness queries the os on which operating system is and never tries to run stuff that won't
work on that operating system". That day `fieldkit thermal monitor` started PowerShell on Linux and died. These tests
hold the rule at its three doors (the command line, agent.run, agent.discover) and the thermal monitor's Linux half.
Each test runs on both systems: a card for the OTHER system must be refused wherever CI runs it."""
import json
import sys

import pytest

from fieldkit import agent
from fieldkit.core import platform_gate as gate
from fieldkit.core.host import host, platform_refusal, this_platform
from fieldkit.thermal import governor, profiles, sensors

HERE = "windows" if host()["is_windows"] else "linux"
OTHER = "linux" if host()["is_windows"] else "windows"
PY = sys.executable


def _card(**kw):
    c = {"id": "t", "title": "Write a greeting file", "path": None, "entry": [PY, "-c", "print('hi')"],
         "inputs": [], "effects": [], "safety": "read-only", "modes": {}, "tests": ["x"], "platforms": [],
         "draft": False, "reviewed": True, "portable": True, "probe": "safe", "scope": []}
    c.update(kw)
    return c


# -- the rule -------------------------------------------------------------------------------------------------------
def test_the_refusal_names_the_systems_the_reason_and_what_to_do():
    msg = platform_refusal("fieldkit x", [OTHER], "it drives a service only that system has")
    assert f"runs on {OTHER} only" in msg and "only that system has" in msg
    assert f"this machine is {this_platform()}" in msg and "Nothing was started" in msg and "NEXT:" in msg
    assert platform_refusal("fieldkit x", [HERE]) is None
    assert platform_refusal("fieldkit x", []) is None          # no platforms: anywhere


# -- door 1: the command line ---------------------------------------------------------------------------------------
def _tools():
    return [{"id": "other-tool", "entry": ["python", "-m", "fieldkit", "gadget", "start", "{x}"], "platforms": [OTHER],
             "platforms_why": "the other system's service"},
            {"id": "here-tool", "entry": ["python", "-m", "fieldkit", "gadget", "status"], "platforms": [HERE, OTHER]},
            {"id": "group-a", "entry": ["python", "-m", "fieldkit", "widget", "a"], "platforms": [OTHER]},
            {"id": "group-b", "entry": ["python3", "-m", "fieldkit", "widget", "b", "--json"], "platforms": [OTHER]},
            {"id": "not-fieldkit", "entry": ["python", "tool.py"], "platforms": [OTHER]}]


def test_the_index_holds_fieldkit_commands_and_stops_at_options_and_templates():
    idx = gate.build_index(_tools())
    assert set(idx) == {"gadget start", "gadget status", "widget a", "widget b"}
    assert idx["gadget start"]["platforms"] == [OTHER] and idx["gadget start"]["why"] == "the other system's service"


def test_a_command_for_the_other_system_is_refused_before_it_starts():
    idx = gate.build_index(_tools())
    assert "the other system's service" in gate.refusal(["gadget", "start", "file.txt"], idx)
    assert gate.refusal(["gadget", "status", "--json"], idx) is None
    assert gate.refusal(["unknown", "thing"], idx) is None          # no card: the gate does not invent a rule


def test_help_always_passes():
    idx = gate.build_index(_tools())
    assert gate.refusal(["gadget", "start", "--help"], idx) is None
    assert gate.refusal(["widget", "-h"], idx) is None


def test_a_group_is_refused_only_when_every_card_under_it_is_for_another_system():
    idx = gate.build_index(_tools())
    assert gate.refusal(["widget"], idx) is not None                # every widget card is OTHER
    assert gate.refusal(["gadget"], idx) is None                    # mixed: its own code decides


def test_the_shipped_index_matches_the_public_cards_and_holds_no_private_one():
    """platform_index.json is generated from desk/tools.yaml; a card's platforms cannot change without the gate.
    The private local/tools.yaml never enters it (2026-10-20: the first generation leaked the private ui-check)."""
    shipped = gate.load_index(local=False)
    assert shipped == gate.build_index(gate.public_tools()), "regenerate: python -m fieldkit.core.platform_gate --write"
    public = {t["id"] for t in gate.public_tools()}
    assert {i for row in shipped.values() for i in row["ids"]} <= public


def test_the_command_line_refuses_with_exit_2_and_json(capsys):
    from fieldkit import cli
    idx = gate.load_index(local=False)
    key = next((k for k, v in idx.items() if HERE not in v["platforms"]), None)
    if key is None:
        pytest.skip("no single-system command for the other system in the index")
    assert cli.main(key.split() + ["--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["refused"] == "platform"


# -- doors 2 and 3: a model running or looking for a card -----------------------------------------------------------
def test_agent_run_refuses_a_card_for_the_other_system():
    c = _card(platforms=[OTHER], platforms_why="needs the other system")
    with pytest.raises(agent.Refused, match="needs the other system"):
        agent.run("t", {}, cards=[c])


def test_discover_never_offers_a_tool_for_the_other_system():
    there = _card(id="greet-there", title="Write a greeting file", platforms=[OTHER])
    here = _card(id="greet-here", title="Write a greeting file", platforms=[HERE])
    found = [r["tool"] for r in agent.discover("greeting file", cards=[there, here])]
    assert found == ["greet-here"]


def test_describe_says_where_a_tool_runs():
    d = agent.describe("t", cards=[_card(platforms=[OTHER])])
    assert d["runs_here"] is False and d["this_system"] == this_platform() and "NEXT:" in d["not_here"]


# -- the thermal monitor's Linux half -------------------------------------------------------------------------------
def test_each_system_gets_only_its_own_temperature_sources():
    assert sensors.providers("win32") is sensors.PROVIDERS
    assert [n for n, _ in sensors.providers("linux")] == ["coretemp", "x86_pkg_temp"]
    assert sensors.providers("darwin") == ()


def test_powershell_is_never_started_off_windows(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("Windows runs PowerShell")
    monkeypatch.setattr(sensors.subprocess, "run", lambda *a, **k: pytest.fail("PowerShell started off Windows"))
    assert sensors._ps("Get-Counter x") == ""
    assert sensors.thermalzone() is None and sensors.msacpi() is None


def _hwmon(tmp, chips):
    for i, (name, temps) in enumerate(chips):
        d = tmp / f"hwmon{i}"
        d.mkdir(parents=True)
        (d / "name").write_text(name + "\n")
        for j, (label, milli) in enumerate(temps, 1):
            (d / f"temp{j}_label").write_text(label + "\n")
            (d / f"temp{j}_input").write_text(f"{milli}\n")
    return tmp


def test_coretemp_reads_the_intel_package_and_the_amd_tctl(tmp_path):
    intel = _hwmon(tmp_path / "i", [("acpitz", [("x", 30000)]), ("coretemp", [("Core 0", 50000), ("Package id 0", 61000)])])
    assert sensors.coretemp(intel) == 61.0
    amd = _hwmon(tmp_path / "a", [("k10temp", [("Tctl", 72500)])])
    assert sensors.coretemp(amd) == 72.5
    assert sensors.coretemp(tmp_path / "missing") is None


def test_x86_pkg_temp_is_the_fallback_zone(tmp_path):
    for n, (kind, milli) in enumerate([("acpitz", 40000), ("x86_pkg_temp", 66000)]):
        z = tmp_path / f"thermal_zone{n}"
        z.mkdir()
        (z / "type").write_text(kind + "\n")
        (z / "temp").write_text(f"{milli}\n")
    assert sensors.x86_pkg_temp(tmp_path) == 66.0


def test_the_linux_cap_is_intel_pstate_and_cpufreq_otherwise(tmp_path):
    pstate = tmp_path / "max_perf_pct"
    pstate.write_text("100\n")
    assert governor._linux_set_cap(70, pstate=pstate) and governor._linux_read_cap(pstate=pstate) == 70
    cpu = tmp_path / "cpu"
    for n in range(2):
        d = cpu / f"cpu{n}" / "cpufreq"
        d.mkdir(parents=True)
        (d / "cpuinfo_max_freq").write_text("3000000\n")
        (d / "scaling_max_freq").write_text("3000000\n")
    gone = tmp_path / "no_pstate"
    assert governor._linux_set_cap(50, pstate=gone, cpufreq=cpu)
    assert [(cpu / f"cpu{n}" / "cpufreq" / "scaling_max_freq").read_text() for n in range(2)] == ["1500000"] * 2
    assert governor._linux_read_cap(pstate=gone, cpufreq=cpu) == 50


def test_a_root_only_cap_goes_through_sudo_without_a_password_prompt(tmp_path):
    pstate = tmp_path / "ro" / "max_perf_pct"           # a missing folder: the direct write fails
    seen = []

    class R:
        returncode = 1
    assert governor._write(pstate, 70, run=lambda argv, **k: seen.append(argv) or R()) is False
    assert seen == [["sudo", "-n", "tee", str(pstate)]]


def test_the_monitor_says_it_cannot_cool_and_never_claims_a_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, "_dir", lambda: tmp_path)
    monkeypatch.setattr(governor, "can_cap", lambda: (False, "root-only"))
    monkeypatch.setattr(governor, "set_cap", lambda pct: pytest.fail("the real cap was touched"))
    said = []
    profiles.monitor(sensor=lambda: 99.0, say=said.append, sleep=lambda s: None, ticks=2, report=said.append)
    assert any("CANNOT COOL" in s for s in said) and any("99" in s for s in said)


def test_the_monitor_stops_when_the_build_ends(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, "_dir", lambda: tmp_path)
    caps, beats = [], iter([True, True, False])
    profiles.monitor(sensor=lambda: 40.0, set_cap=caps.append, say=lambda s: None, sleep=lambda s: None,
                     until_pid=12345, alive=lambda pid: next(beats))
    assert caps[-1] == 100                                     # the cap is back at 100% when it stops
    assert json.loads((tmp_path / "monitor.json").read_text())["cpu"] == 40.0


def test_the_index_is_written_with_lf_on_every_system(tmp_path, monkeypatch):
    # 2026-10-23: --write on Windows produced CRLF (text mode); the index must be the same bytes on both systems
    seen = {}
    real = gate.Path.write_text
    def spy(self, data, *a, **kw):
        seen.update(kw)
        return real(self, data, *a, **kw)
    monkeypatch.setattr(gate.Path, "write_text", spy)
    p = gate.write(tmp_path / "idx.json")
    assert seen.get("newline") == "\n" and b"\r" not in p.read_bytes()
