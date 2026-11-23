"""Cable first, ssh only as the fallback (owner 2026-11-23). Runs on both OSes."""
from fieldkit.autopilot import toolrouter
from fieldkit.capture import audit


def _call(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def test_warns_on_hand_run_ssh_to_cable_peer_when_link_up():
    w = toolrouter.cable_ssh_warning(_call("ssh -o BatchMode=yes me@10.77.0.2 uname"), link_up=lambda: True)
    assert "cable" in w and "WARNING" in w


def test_no_warning_when_link_down_or_other_host_or_not_ssh():
    assert toolrouter.cable_ssh_warning(_call("ssh me@10.77.0.2 uname"), link_up=lambda: False) == ""
    assert toolrouter.cable_ssh_warning(_call("ssh me@example.org uname"), link_up=lambda: True) == ""
    assert toolrouter.cable_ssh_warning(_call("echo 10.77.0.2"), link_up=lambda: True) == ""


def test_warning_fails_open():
    def boom():
        raise OSError("x")
    assert toolrouter.cable_ssh_warning(_call("ssh 10.77.0.2 id"), link_up=boom) == ""


def _patch(monkeypatch, probe, ssh):
    from fieldkit.peers import link
    monkeypatch.setattr(link, "household", lambda: {})
    monkeypatch.setattr(link, "device_named", lambda h, n: {"name": n})
    monkeypatch.setattr(link, "cable_probe", probe)
    monkeypatch.setattr(audit, "_ssh_audit", ssh)


def test_audit_auto_skips_ssh_when_the_cable_answered(monkeypatch):
    good = {"schema": 1, "host": "vaio", "roots": []}

    def no_ssh(*a):
        raise AssertionError("ssh tried")
    _patch(monkeypatch, lambda n, p: {"ok": True, "data": good}, no_ssh)
    r = audit.remote("vaio", "auto")
    assert list(r["routes"]) == ["cable"] and r["hosts"][0]["host"] == "vaio"


def test_audit_auto_falls_back_to_ssh_when_the_cable_fails(monkeypatch):
    good = {"schema": 1, "host": "vaio", "roots": []}
    _patch(monkeypatch, lambda n, p: {"ok": False, "error": "down"}, lambda *a: good)
    r = audit.remote("vaio", "auto")
    assert list(r["routes"]) == ["cable", "ssh"] and r["routes"]["ssh"]["status"] == "audited"
