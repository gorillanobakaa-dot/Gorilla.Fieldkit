"""fieldkit power audit: the verdict, the one-UAC fix script, the platform guard."""
from fieldkit.power import audit


def P(**k):
    return dict({"status": "ok", "cmdline": 1, "group": True, "readable": True, "events": True}, **k)


def test_pass_when_events_readable_and_cmdline_on():
    assert audit.verdict(P()) == ("PASS", [])


def test_member_but_old_token_says_sign_out():
    assert audit.verdict(P(readable=False, events=False)) == ("SIGN-OUT", [])


def test_nothing_done_needs_all_three_steps():
    v, need = audit.verdict(P(cmdline=0, group=False, readable=False, events=False))
    assert v == "FAIL" and set(need) == {"audit", "cmdline", "group"}


def test_readable_with_no_4688_means_auditing_is_off():
    assert audit.verdict(P(events=False)) == ("FAIL", ["audit"])


def test_cmdline_flag_alone():
    assert audit.verdict(P(cmdline=0)) == ("FAIL", ["cmdline"])


def test_platform_and_error_verdicts():
    assert audit.verdict({"status": "not-this-platform"})[0] == "not-this-platform"
    assert audit.verdict({"status": "error"})[0] == "error"


def test_fix_script_self_elevates_once_and_holds_only_the_missing_steps():
    s = audit.fix_script(["cmdline", "group"])
    assert s.count("-Verb RunAs") == 1 and "ProcessCreationIncludeCmdLine_Enabled" in s and "Event Log Readers" in s
    assert "/success:enable" not in s.split("exit }")[1].split("Process Creation\"")[0]


def test_write_fix_only_when_something_is_missing(tmp_path, monkeypatch):
    import json
    probe = json.dumps({"cmdline": 0, "group": False, "readable": False, "events": False})
    run = lambda *a, **k: type("R", (), {"stdout": probe, "stderr": ""})()
    monkeypatch.setattr(audit.sys, "platform", "win32")
    out = []
    rc = audit.run_cmd(write_fix=True, out=out.append, state_dir=tmp_path, runner=run)
    assert rc == 3 and (tmp_path / "enable-process-audit.ps1").is_file() and any("NEXT" in x for x in out)
