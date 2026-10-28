"""Keep-alive of the cable listener (2026-10-26): the units, the status read from fake systemctl/schtasks output,
the watchdog restarting through the manager, and the mandate prerequisites that install them."""
import types

import yaml

from fieldkit.core import settings
from fieldkit.mandate import machine
from fieldkit.peers import link_keepalive as K
from fieldkit.peers import link_watchdog as W


def test_the_units_restart_forever_and_start_at_boot(tmp_path):
    u = K.units(root="/opt/Fieldkit", py="/usr/bin/python3")
    for name, verb in ((K.SERVICE, "peers link serve"), (K.RESCUE, "peers link rescue serve")):
        t = u[name]
        assert "Restart=always" in t and "StartLimitIntervalSec=0" in t and "WantedBy=default.target" in t
        assert f"ExecStart=/usr/bin/python3 -P -m fieldkit {verb}\n" in t
        assert "Environment=PYTHONPATH=/opt/Fieldkit" in t
    for p in K.write_units(tmp_path):
        assert b"\r\n" not in open(p, "rb").read()


def test_the_watchdog_unit_never_kills_the_listener_it_started():
    assert "KillMode=process" in W.UNIT


def _run(answers):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        key = " ".join(argv)
        rc, out = next(((rc, out) for k, (rc, out) in answers.items() if k in key), (1, ""))
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="")
    return run, calls


def test_linux_status_reads_units_and_linger():
    run, _ = _run({"is-enabled": (0, "enabled"), "is-active": (0, "active"), "Linger": (0, "Linger=no")})
    rows = K.linux_status(run, user="u")
    assert [r["ok"] for r in rows] == [True, True, True, False]
    assert "units stop at logout" in rows[-1]["detail"]
    assert K.summary(rows) == {"ok": False, "missing": ["linger"]}


def test_windows_status_reads_the_task():
    run, _ = _run({"/Query": (0, "TaskName: \\fieldkit-link\nStatus: Running\n")})
    assert K.windows_status(run) == [{"item": "task fieldkit-link", "ok": True,
                                      "detail": "installed, status Running"}]
    run, _ = _run({})
    assert K.windows_status(run)[0]["ok"] is False


def test_restart_goes_through_the_manager_when_it_is_installed():
    run, calls = _run({"is-enabled": (1, "disabled")})
    assert K.restart_via_manager("linux", run) == (False, 0, [])
    run, calls = _run({"is-enabled": (0, "enabled"), "restart": (0, "")})
    handled, code, _ = K.restart_via_manager("linux", run)
    assert handled and code == 0 and calls[-1] == ["systemctl", "--user", "restart", K.SERVICE]
    run, calls = _run({"/Query": (0, ""), "/End": (0, ""), "/Run": (0, "")})
    assert K.restart_via_manager("win32", run)[1] == 0
    assert [c[1] for c in calls] == ["/Query", "/End", "/Run"]


def test_install_on_linux_writes_units_enables_them_and_linger(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    run, calls = _run({"daemon-reload": (0, ""), "enable": (0, ""), "enable-linger": (0, "")})
    code, lines = K.install("linux", run, home=tmp_path)
    assert code == 0, lines
    assert (tmp_path / ".config/systemd/user" / K.SERVICE).is_file()
    assert ["systemctl", "--user", "enable", "--now", K.SERVICE, K.RESCUE] in calls
    assert any(c[:2] == ["loginctl", "enable-linger"] for c in calls)
    assert K.install("win32", run)[0] == 3


def test_rescue_firewall_verify_is_parsed_and_unknown_is_never_present():
    run, _ = _run({"verify": (0, "LINK-RESCUE-FIREWALL: OK verify: firewall ufw, rule ... present = no")})
    assert K.rescue_firewall_present("linux", run, ["10.77.0.1", "10.77.0.2", "47078"]) is False
    run, _ = _run({"verify": (4, "UNKNOWN: ufw ...")})
    assert K.rescue_firewall_present("linux", run, ["10.77.0.1", "10.77.0.2", "47078"]) is None
    assert K.rescue_firewall_present("win32") is True


def test_the_link_prerequisites_are_pinned_and_checked():
    m = yaml.safe_load((settings.ROOT / "fieldkit/mandate/owner-wide.prereqs.yaml").read_text(encoding="utf-8"))
    link_rows = [e for e in machine.entries(m) if e["id"].startswith("link-")]
    assert {e["id"] for e in link_rows} == {"link-keepalive-windows", "link-keepalive-linux", "link-rescue-firewall"}
    for e in link_rows:
        assert machine.sha_ok(e)[0], e["id"]
        assert e["verify"] in machine.VERIFY and e.get("cost")


def test_linux_admin_prerequisites_run_in_one_pkexec_runner(monkeypatch, tmp_path):
    e = {"id": "link-rescue-firewall", "os": "linux", "admin": True, "script": "x.sh", "sha256": "ab" * 32,
         "apply": ["apply", "10.77.0.1"], "verify": "fw"}
    t = machine.runner_text_sh([e], root=tmp_path)
    assert "sha256sum" in t and "FAILED sha256 changed, not run" in t and "'apply' '10.77.0.1'" in t
    monkeypatch.setattr(machine, "sha_ok", lambda e, root=None: (True, "ok"))
    monkeypatch.setattr(machine, "_attempted_recently", lambda now=None: False)
    monkeypatch.setattr(machine, "_mark_attempt", lambda now=None: None)
    monkeypatch.setattr(machine, "here", lambda e, platform=None: True)
    state = {"present": False}
    got = []

    def elev(todo, run=None, say=None):
        got.append([x["id"] for x in todo])
        state["present"] = True
        return True, "finished"
    code, lines = machine.apply({"machine_prerequisites": [e]}, True, verify={"fw": lambda: state["present"]},
                                elevate_fn=elev, nt=False)
    assert code == 0 and got == [["link-rescue-firewall"]] and "  pkexec run: finished" in lines


def test_doctor_names_a_listener_python_without_pytest(tmp_path):
    # 2026-10-26: units written with the system python3 (no pytest): every test job collected 0 tests
    assert K.listener_python(home=tmp_path) is None                               # no unit installed: no line
    d = K.unit_dir(tmp_path)
    d.mkdir(parents=True)
    (d / K.SERVICE).write_text("[Service]\nExecStart=/usr/bin/python3 -P -m fieldkit peers link serve\n")
    run, calls = _run({"/usr/bin/python3 -c import pytest": (1, "ModuleNotFoundError: No module named 'pytest'")})
    ok, line = K.listener_python(home=tmp_path, run=run)
    assert not ok and line.startswith("PROBLEM /usr/bin/python3") and calls[0][0] == "/usr/bin/python3"
    run, _ = _run({"import pytest": (0, "")})
    assert K.listener_python(home=tmp_path, run=run)[0] is True
