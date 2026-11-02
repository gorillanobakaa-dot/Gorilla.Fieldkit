"""fieldkit capture install/status: fake settings files, a fake scheduler, a fake peer. Each check can fail (mutations.yaml)."""
import json
import subprocess

import pytest

from fieldkit.capture import install, status
from fieldkit.capture import store as st


class FakeRun:
    """A scheduler: schtasks/crontab/systemctl calls recorded; /Query succeeds for tasks created."""
    def __init__(self, fail=()):
        self.calls, self.tasks, self.fail = [], set(), fail
        self.cron = None

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        ok = subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] in self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        if argv[0] == "schtasks":
            name = argv[argv.index("/TN") + 1] if "/TN" in argv else ""
            if argv[1] == "/Create":
                self.tasks.add(name)
                return ok
            return ok if name in self.tasks else subprocess.CompletedProcess(argv, 1, "", "no")
        if argv[0] == "reg":
            if argv[1] == "add":
                self.runkey = True
                return ok
            return ok if getattr(self, "runkey", False) else subprocess.CompletedProcess(argv, 1, "", "no")
        if argv[0] == "crontab":
            if argv[1] == "-l":
                return subprocess.CompletedProcess(argv, 0 if self.cron else 1, self.cron or "", "")
            self.cron = kw.get("input")
        return ok


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".gemini").mkdir()
    return tmp_path


def own(home_, agent):
    return json.loads(install.settings_path(agent, home_).read_text(encoding="utf-8"))


def test_claude_hooks_added_beside_existing_ones(home):
    p = install.settings_path("claude", home)
    keep = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "speak done"}]}]}, "theme": "dark"}
    p.write_text(json.dumps(keep), encoding="utf-8")
    ok, msg = install.install_json("claude", home, py="py")
    d = own(home, "claude")
    assert ok and d["theme"] == "dark"
    assert d["hooks"]["Stop"][0]["hooks"][0]["command"] == "speak done"          # untouched, still first
    assert "capture hook --agent claude" in d["hooks"]["Stop"][1]["hooks"][0]["command"]
    assert d["hooks"]["PostToolUse"][0]["matcher"].startswith("Bash")
    assert p.with_name("settings.json.fieldkit-bak").is_file()


def test_install_is_idempotent(home):
    install.install_json("claude", home, py="py")
    before = install.settings_path("claude", home).read_text(encoding="utf-8")
    ok, msg = install.install_json("claude", home, py="py")
    assert ok and "already" in msg
    assert install.settings_path("claude", home).read_text(encoding="utf-8") == before


def test_check_reports_missing_then_fine(home):
    assert install.json_missing("claude", home) == ["PostToolUse", "Stop"]
    install.install_json("claude", home)
    assert install.json_missing("claude", home) == []


def test_a_hook_for_another_agent_does_not_count(home):
    install.install_json("gemini", home)
    d = own(home, "gemini")
    assert set(d["hooks"]) == {"AfterTool", "SessionEnd"}
    assert install.json_missing("claude", home) == ["PostToolUse", "Stop"]


def test_invalid_json_is_left_untouched(home):
    p = install.settings_path("claude", home)
    p.write_text("{not json", encoding="utf-8")
    ok, msg = install.install_json("claude", home)
    assert not ok and p.read_text(encoding="utf-8") == "{not json"
    assert install.json_missing("claude", home)


def test_dry_run_writes_nothing(home):
    install.install_json("claude", home, dry=True)
    assert not install.settings_path("claude", home).exists()


def test_opencode_plugin_written_and_recognised(tmp_path):
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    assert not install.plugin_ok(tmp_path)
    install.install_opencode(tmp_path, py="/usr/bin/python3")
    assert install.plugin_ok(tmp_path)
    assert "tool.execute.after" in install.settings_path("opencode", tmp_path).read_text(encoding="utf-8")


def test_windows_scheduler_makes_both_tasks(home):
    run = FakeRun()
    ok, _ = install.install_sched("win32", home, py="py", run=run)
    creates = [c for c in run.calls if c[:2] == ["schtasks", "/Create"]]
    assert ok and {c[c.index("/TN") + 1] for c in creates} == set(install.TASKS)
    assert any("MINUTE" in c for c in creates)
    assert any(c[:2] == ["reg", "add"] and install.RUN_NAME in c for c in run.calls)     # the log-on catch-up
    n = len(run.calls)
    assert install.install_sched("win32", home, py="py", run=run)[1].startswith("schedule: already")
    assert not [c for c in run.calls[n:] if c[1] in ("/Create", "add")]


def test_launchd_plist(home):
    ok, _ = install.install_sched("darwin", home, py="py", run=FakeRun())
    t = (home / "Library" / "LaunchAgents" / install.PLIST).read_text(encoding="utf-8")
    assert ok and "capture" in t and "RunAtLoad" in t and "StartInterval" in t


def test_systemd_timer_has_catch_up(home, monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda n: "/bin/systemctl")
    ok, msg = install.install_sched("linux", home, py="py", run=FakeRun())
    d = home / ".config" / "systemd" / "user"
    assert ok and "Persistent=true" in (d / "fieldkit-capture.timer").read_text(encoding="utf-8")
    assert "capture scan" in (d / "fieldkit-capture.service").read_text(encoding="utf-8")


def test_cron_fallback_when_no_user_session(home, monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda n: "/bin/systemctl")
    run = FakeRun(fail=("systemctl",))
    ok, msg = install.install_sched("linux", home, py="py", run=run)
    assert ok and msg == "schedule: cron" and "@reboot" in run.cron
    assert not (home / ".config" / "systemd" / "user" / "fieldkit-capture.timer").exists()


def test_check_whole_and_absent_agents(home):
    run = FakeRun()
    c = install.check(home, "win32", run)
    assert c["claude"] and c["schedule"] and c["opencode"] is None
    install.install(home, "win32", py="py", run=run)
    c = install.check(home, "win32", run)
    assert not any(v for v in c.values())


def test_status_proves_last_hook_event(tmp_path, home):
    s = st.Store(tmp_path / "cap")
    from fieldkit.capture import hook
    hook.handle("claude", json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "x",
                                       "tool_input": {"command": "echo a"}, "tool_response": {"exit_code": 0}}), store=s)
    d = status.collect(s, home, "win32", FakeRun())
    assert d["last_hook_event"] and d["items"] == 1 and not d["ok"]
    assert any(m.startswith("claude") for m in d["missing"])


def test_status_never_hook_event_is_none(tmp_path, home):
    d = status.collect(st.Store(tmp_path / "cap"), home, "win32", FakeRun())
    assert d["last_hook_event"] is None


def test_peer_down_is_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "cap"))
    rc = status.main(["--peer"], call=lambda req, **kw: {"ok": False, "error": "no route"})
    assert rc == 3 and "PEER DOWN" in capsys.readouterr().out


def test_peer_answer_is_printed(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "cap"))
    mine = status.collect(st.Store(tmp_path / "cap"), tmp_path, "linux", FakeRun())
    peer = dict(mine, ok=True, host="vaio", platform="linux")
    seen = {}

    def call(req, **kw):
        seen.update(req)
        return {"ok": True, "data": peer}
    status.main(["--peer"], call=call)
    out = capsys.readouterr().out
    assert seen == {"verb": "probe", "name": "capture"} and "-- peer --" in out and "vaio" in out


def test_probe_is_in_the_fixed_set():
    from fieldkit.peers import link_probe
    assert "capture" in link_probe.PROBES


def test_friction_row_for_missing_hook():
    from fieldkit.lifecycle import friction
    rows = friction.capture_rows({"claude": ["Stop"], "gemini": None, "schedule": []})
    assert len(rows) == 1 and "capture install" in rows[0]["fix"]
    assert friction.capture_rows({"claude": [], "gemini": None, "schedule": []}) == []


def test_a_gemini_command_in_the_claude_file_does_not_count(home):
    p = install.settings_path("claude", home)
    cmd = install.hook_command("gemini", "py")
    p.write_text(json.dumps({"hooks": {ev: [{"hooks": [{"type": "command", "command": cmd}]}] for ev in install.EVENTS["claude"]}}),
                 encoding="utf-8")
    assert install.json_missing("claude", home) == ["PostToolUse", "Stop"]
