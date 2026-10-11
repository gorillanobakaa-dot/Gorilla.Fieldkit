"""fieldkit laptop and the thermal profiles: the academic harness's laptop tools, made general (2026-10-11).

Windows is not here, so every Windows call goes through a fake `run` or `ps` that answers the way Windows does
(typeperf's CSV, tasklist's line, the audio state JSON). What is held is the logic: the verdicts, what is changed and
in what order, that an undo exists before a change, that nothing is spoken twice, that the cap always comes back.
"""
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fieldkit.core import settings
from fieldkit.laptop import alerts, cpu, desktop, rules, sound
from fieldkit.thermal import profiles, sensors


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "local_settings", lambda: {})
    return tmp_path


def _r(stdout="", rc=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=rc)


# -- cpu ---------------------------------------------------------------------------------------------------------
TYPEPERF = '''"(PDH-CSV 4.0)","\\\\PC\\Processor Information(_Total)\\% Processor Performance","\\\\PC\\Processor Information(_Total)\\Processor Frequency"
"10/11/2026 09:00:01.000","{p}","2900"
"10/11/2026 09:00:02.000","{p}","2900"
'''


def test_the_speed_is_read_from_typeperf_and_judged_against_the_base():
    mhz, base = cpu.speed(cpu.parse_typeperf(TYPEPERF.format(p="27.6")))
    assert (mhz, base) == (800, 2900)
    assert cpu.verdict(800, 2900) == "throttled" and cpu.verdict(3100, 2900) == "ok"
    assert cpu.verdict(None, None) == "unknown" and cpu.verdict(3000, 2900, tool_running=False) == "no-tool"


def test_guard_starts_the_unlock_tool_restores_the_cap_and_proves_the_speed(tmp_path, monkeypatch):
    tool = tmp_path / "ThrottleStop.exe"
    tool.write_bytes(b"MZ")
    state = {"running": False, "perf": "27.6", "calls": []}

    def run(cmd, **kw):
        state["calls"].append(cmd[0])
        if cmd[0] == "tasklist":
            return _r("ThrottleStop.exe 1234 Console" if state["running"] else "INFO: No tasks")
        if cmd[0] == "powershell" and "Start-Process" in cmd[-1]:
            state["running"] = True
            return _r()
        if cmd[0] == "taskkill":
            state["running"] = False
            return _r()
        if cmd[0] == "typeperf":
            out = TYPEPERF.format(p=state["perf"])
            state["perf"] = "106.9"                          # the second measurement, after the unlock
            return _r(out)
        if cmd[0] == "powershell":                        # event 37
            return _r("3")
        return _r()
    caps = []
    from fieldkit.thermal import governor
    monkeypatch.setattr(governor, "set_cap", caps.append)
    st = cpu.check(fix=True, platform="win32", run=run, sleep=lambda s: None, tool=str(tool))
    assert st["verdict"] == "ok" and st["fixed_from"] == 800 and st["mhz"] == 3100
    assert caps == [100] and "started the unlock tool" in st["actions"]
    assert st["firmware_limits_since_boot"] == 3 and cpu.exit_code(st) == 0
    assert json.loads((settings.ROOT / "state" / "cpu-guard.json").read_text())["verdict"] == "ok"


def test_linux_reads_cpufreq_and_the_throttle_counters_and_changes_nothing(tmp_path, monkeypatch):
    sysfs = tmp_path / "cpu"
    for i in range(2):
        (sysfs / f"cpu{i}" / "cpufreq").mkdir(parents=True)
        (sysfs / f"cpu{i}" / "cpufreq" / "scaling_cur_freq").write_text("800000")
        (sysfs / f"cpu{i}" / "thermal_throttle").mkdir()
        (sysfs / f"cpu{i}" / "thermal_throttle" / "package_throttle_count").write_text("41")
    (sysfs / "cpu0" / "cpufreq" / "base_frequency").write_text("2900000")
    assert cpu.measure_linux(root=str(sysfs), cpuinfo=str(tmp_path / "none")) == (800, 2900, 41)
    monkeypatch.setattr(cpu, "measure_linux", lambda: (800, 2900, 41))
    st = cpu.check(fix=True, platform="linux")
    assert st["verdict"] == "throttled" and "msr-tools" in st["actions"][0] and st["throttle_events"] == 41


def test_the_logon_task_runs_the_guard_elevated_and_only_on_windows():
    s = cpu.task_script("C:\\Python\\python.exe", "C:\\Fieldkit")
    assert "-m fieldkit laptop cpu guard" in s and "-RunLevel Highest" in s and cpu.TASK in s
    assert cpu.install(platform="linux")["status"] == "windows-only"


# -- alerts ------------------------------------------------------------------------------------------------------
def test_the_hook_says_what_the_reply_says_and_never_claims_done_for_it():
    said = []
    assert alerts.hook({"hook_event_name": "Stop", "last_assistant_message": "All checks pass. The task is done."},
                       say=said.append, name="Sam") == "done"
    assert alerts.hook({"hook_event_name": "Stop", "last_assistant_message": "I changed three files."},
                       say=said.append, name="") == "look"
    assert alerts.hook({"hook_event_name": "Notification", "notification_type": "permission_prompt"},
                       say=said.append, name="") == "look"
    assert alerts.hook({"hook_event_name": "Notification", "notification_type": "idle_prompt"}, say=said.append) is None
    assert alerts.hook({"hook_event_name": "Stop", "stop_hook_active": True}, say=said.append) is None
    assert said == ["Sam, the task is done.", "I need you to have a look at this.", "I need you to have a look at this."]


def _transcript(brain, steps):
    p = brain / "conv1" / ".system_generated" / "logs" / "transcript.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")
    return p


def test_the_watcher_speaks_after_a_finished_turn_a_waiting_tool_and_an_error_once(tmp_path):
    brain, now, said = tmp_path / "brain", [1000.0], []
    w = alerts.Watcher(brain, say=said.append, name="", clock=lambda: now[0])
    _transcript(brain, [{"source": "MODEL", "type": "PLANNER_RESPONSE", "content": "Draft written. Have a look at this."}])
    w.tick()
    assert said == []                                     # it waits SETTLE seconds: the agent may go on
    now[0] += alerts.SETTLE + 1
    w.tick()
    assert said == ["I need you to have a look at this."]
    _transcript(brain, [{"source": "MODEL", "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run"}]}])
    w.tick()
    now[0] += alerts.APPROVAL_WAIT + 1
    w.tick()
    assert w.spoken == ["look", "look"]
    _transcript(brain, [{"source": "MODEL", "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run"}]},
                        {"source": "USER_EXPLICIT", "type": "USER_INPUT"}])      # approved: nothing to say
    w.tick()
    now[0] += alerts.APPROVAL_WAIT + 1
    w.tick()
    assert w.spoken == ["look", "look"]
    for _ in range(2):
        _transcript(brain, [{"source": "SYSTEM", "type": "ERROR_MESSAGE", "error": "quota"}])
        w.tick()
    assert w.spoken == ["look", "look", "focus"]         # an error is said once per ERROR_QUIET


def test_claude_code_hooks_are_merged_with_a_backup_and_only_once(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}))
    r = alerts.install_claude(s, apply=False, python="py")
    assert r["adds"] == ["Stop", "Notification"] and not r["written"] and "other" in s.read_text()
    alerts.install_claude(s, apply=True, python="py")
    data = json.loads(s.read_text())
    assert data["model"] == "x" and len(data["hooks"]["Stop"]) == 2 and (tmp_path / "settings.json.bak").is_file()
    assert alerts.install_claude(s, apply=True, python="py")["adds"] == []


# -- sound -------------------------------------------------------------------------------------------------------
STATE = {"manufacturer": "Maker", "model": "Laptop 7", "os": "Windows 11 Pro", "build": 26100,
         "codec": [{"name": "Realtek Audio", "version": "6.0.8934.1", "provider": "Realtek", "date": "2020-01-01",
                    "inf": "oem12.inf", "hwid": "HDAUDIO\\FUNC_01&VEN_10EC&DEV_0256&SUBSYS_102807B0&REV_1000"}],
         "services": {"Audiosrv": "Running", "AudioEndpointBuilder": "Running"},
         "volume": 40, "muted": True, "ducking": 1,
         "sessions": [{"pid": 10, "name": "firefox", "volume": 30, "muted": False}]}


def test_the_diagnosis_names_what_keeps_the_sound_low():
    rows = {w: (s, d) for s, w, d in sound.evaluate(STATE)}
    assert rows["muted"][0] == "FAIL" and rows["master volume"][0] == "CHECK"
    assert "firefox 30%" in rows["app volumes"][1] and rows["lowering sound during calls"][0] == "CHECK"
    ids = sound.parse_hwid(STATE["codec"][0]["hwid"])
    assert ids["search"] == "VEN_10EC&DEV_0256&SUBSYS_102807B0" and ids["subsys_vendor"] == "1028"


def test_fix_shows_a_plan_writes_its_undo_first_and_undo_puts_it_back():
    state, scripts = dict(STATE), []

    def ps(script, timeout=300):
        scripts.append(script)
        if "UserDuckingPreference -Value 3" in script:      # the fix (the class text is in every script)
            state.update(volume=100, muted=False, ducking=3)
            return {"sessions": 1}
        if "SetVolume(0.4)" in script:
            state.update(volume=40, muted=True, ducking=1)
            return {"ok": True}
        return dict(state)
    plan = sound.fix(False, ps=ps, platform="win32")
    assert plan["status"] == "plan" and not any("UserDuckingPreference -Value 3" in s for s in scripts)
    r = sound.fix(True, ps=ps, platform="win32")
    assert r["ok"] and json.loads(Path(r["undo"]).read_text())["volume"] == 40
    u = sound.undo(ps=ps, platform="win32")
    assert u["status"] == "undone" and state["volume"] == 40 and "SetMute($true)" in scripts[-1]
    with pytest.raises(RuntimeError):
        sound.diagnose(ps=ps, platform="linux")


def test_the_catalog_rows_for_this_windows_are_listed_newest_first():
    page = "".join(
        f'<tr id="{u}_R1"><td></td><td>Realtek - Media - {v}</td><td>Windows 11 Client, version 24H2 and later</td>'
        f'<td>Drivers (Sound)</td><td>1/2/2026</td><td>{v}</td><td>500 MB</td></tr>'
        for u, v in (("11111111-1111-1111-1111-111111111111", "6.0.9600.1"),
                     ("22222222-2222-2222-2222-222222222222", "6.0.9700.1")))
    r = sound.drivers(ps=lambda s, timeout=300: dict(STATE), platform="win32", fetch=lambda url: page)
    assert [c["version"] for c in r["catalog"]] == ["6.0.9700.1", "6.0.9600.1"] and r["newer"]


def test_the_linux_volume_goes_through_wpctl():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return _r("Volume: 0.80")
    r = sound.volume(80, platform="linux", run=run, which=lambda x: x == "wpctl")
    assert r == {"ok": True, "percent": 80, "muted": False} and ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "80%"] in calls


# -- video, rules ------------------------------------------------------------------------------------------------
def test_the_nvidia_panel_is_installed_only_when_asked_and_only_with_an_nvidia_card():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "powershell":
            return _r("Intel(R) UHD Graphics\nNVIDIA Quadro M1200")
        if cmd[:2] == ["winget", "list"]:
            return _r("No installed package found")
        return _r("Successfully installed")
    assert desktop.video(False, run=run, platform="win32")["status"] == "missing"
    assert not any(c[:2] == ["winget", "install"] for c in calls)
    assert desktop.video(True, run=run, platform="win32")["status"] == "installed"
    intel = lambda cmd, **kw: _r("Intel(R) UHD Graphics")
    assert desktop.video(True, run=intel, platform="win32")["status"] == "no-nvidia"


def test_rule_copies_are_checked_and_synced_with_a_backup(tmp_path):
    src = tmp_path / "CLAUDE.md"
    src.write_text("rule 1\nrule 2\n")
    same, other, gone = tmp_path / "a" / "GEMINI.md", tmp_path / "b" / "AGENTS.md", tmp_path / "c" / "GEMINI.md"
    for p, t in ((same, "rule 1\nrule 2\n"), (other, "rule 1\n")):
        p.parent.mkdir()
        p.write_text(t)
    r = rules.check(str(src), [str(same), str(other), str(gone)])
    assert not r["ok"] and [x["state"] for x in r["copies"]] == ["same", "differs", "missing"]
    r = rules.sync(str(src), [str(same), str(other), str(gone)])
    assert r["ok"] and sorted(r["written"]) == sorted([str(other), str(gone)])
    assert (other.parent / "AGENTS.md.bak").read_text() == "rule 1\n"


# -- thermal -----------------------------------------------------------------------------------------------------
def test_hwinfo_values_give_the_cpu_package_preferring_dts():
    v = {"Sensor0": "CPU [#0]: Intel Core", "Label0": "CPU Package", "ValueRaw0": "71",
         "Sensor1": "CPU [#0]: Intel Core: DTS", "Label1": "CPU Package", "ValueRaw1": "73,5",
         "Sensor2": "GPU", "Label2": "GPU Temperature", "ValueRaw2": "0"}
    assert sensors.parse_vsb(v) == 73.5 and sensors.parse_vsb({}) is None


def test_the_profile_cools_quickly_relaxes_slowly_and_always_puts_the_cap_back(tmp_path):
    temps = iter([60, 78, 80, 88, 88, 88, 70, 70, 70, 70, 60, 60, 60, 60, 60, 60])
    now, caps, said = [0.0], [], []

    def clock():
        return now[0]

    def sleep(s):
        now[0] += 30                                       # thirty seconds a tick: DOWN_HOLD passes in two
    profiles.choose("balanced")
    acted = profiles.monitor(sensor=lambda: next(temps), set_cap=caps.append, say=said.append, clock=clock,
                             sleep=sleep, ticks=16)
    kinds = [(e["kind"], e.get("mode")) for e in acted]
    assert kinds[:2] == [("up", 2), ("up", 3)] and ("down", 1) in kinds
    assert caps[0] == 100 and 70 in caps and caps[-1] == 100   # started at 100, cooled to 70, back to 100 at the end
    assert any("Maximum cooling" in s for s in said)


def test_danger_is_spoken_even_on_the_off_profile_and_no_reading_resets_the_cap():
    c = profiles.Controller("off")
    assert [e["kind"] for e in c.update(96, 0)] == ["danger"]
    assert c.update(96, 10) == []                          # not again before REPEAT_DANGER
    temps = iter([None] * 30)
    caps, said, now = [], [], [0.0]
    profiles.monitor(sensor=lambda: next(temps), set_cap=caps.append, say=said.append, clock=lambda: now[0],
                     sleep=lambda s: now.__setitem__(0, now[0] + 10), ticks=10)
    assert said == ["I cannot read the processor temperature. Cooling is back to normal."]
