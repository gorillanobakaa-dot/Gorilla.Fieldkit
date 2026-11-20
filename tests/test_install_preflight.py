"""Tests for P47 deterministic read-only installer preflight (fieldkit.desk.install_preflight)."""
from __future__ import annotations

from pathlib import Path

from fieldkit.desk import install_preflight, size_install


def _ok_installer(root: Path, os_name: str, lean: bool = True) -> list[dict]:
    return [
        {"id": "python", "context": "installer", "status": "ok", "detail": "Python 3.12", "next_step": ""},
        {"id": "install-root", "context": "installer", "status": "ok", "detail": str(root), "next_step": ""},
        {"id": "doctor-packages", "context": "installer", "status": "ok", "detail": "all required packages present", "next_step": ""},
    ]


def test_working_terminal_and_service_runs_terminal_before_service(tmp_path: Path):
    calls: list[str] = []

    def term_runner(cmd: str, args: list[str], cwd: Path, os_name: str) -> dict:
        calls.append(f"terminal:{cmd}")
        return {
            "command": cmd,
            "args": args,
            "context": "terminal",
            "cwd": str(cwd),
            "resolved_path": f"/home/user/.local/bin/{cmd}",
            "status": "ok",
            "returncode": 0,
            "output": f"{cmd} 0.4.0",
            "next_step": "",
        }

    def svc_runner(service_id: str, label: str, cmd: str, args: list[str], cwd: Path, os_name: str) -> dict:
        calls.append(f"service:{service_id}:{cmd}")
        return {
            "service": service_id,
            "label": label,
            "command": cmd,
            "args": args,
            "context": f"service:{service_id}",
            "resolved_path": f"/usr/local/bin/{cmd}",
            "status": "ok",
            "returncode": 0,
            "output": f"{cmd} 0.4.0",
            "next_step": "",
        }

    rep = install_preflight.run_preflight(
        install_root=tmp_path,
        os_name="linux",
        installer_runner=_ok_installer,
        terminal_runner=term_runner,
        service_runner=svc_runner,
    )
    assert rep["ok"] is True
    assert rep["status"] == "ok"
    assert rep["action"] == "ready"
    assert rep["should_stop_before_install"] is False
    assert rep["mutations_performed"] == 0
    assert calls == [
        "terminal:gorilla-size",
        "terminal:gorilla-trash",
        "service:cable-listener:gorilla-size",
    ]
    assert rep["order"] == [
        "installer",
        "terminal:gorilla-size",
        "terminal:gorilla-trash",
        "service:cable-listener:gorilla-size",
    ]


def test_working_terminal_with_failed_service_lookup_stops_before_install(tmp_path: Path, monkeypatch):
    def term_runner(cmd: str, args: list[str], cwd: Path, os_name: str) -> dict:
        return {
            "command": cmd,
            "args": args,
            "context": "terminal",
            "cwd": str(cwd),
            "resolved_path": f"/home/user/.local/bin/{cmd}",
            "status": "ok",
            "returncode": 0,
            "output": f"{cmd} 0.4.0",
            "next_step": "",
        }

    def svc_runner(service_id: str, label: str, cmd: str, args: list[str], cwd: Path, os_name: str) -> dict:
        return {
            "service": service_id,
            "label": label,
            "command": cmd,
            "args": args,
            "context": f"service:{service_id}",
            "resolved_path": None,
            "status": "service_discovery_failed",
            "returncode": 127,
            "output": f"{cmd}: not found",
            "next_step": "Expose gorilla-size to fieldkit-link.service without reinstalling.",
        }

    rep = install_preflight.run_preflight(
        install_root=tmp_path,
        os_name="linux",
        installer_runner=_ok_installer,
        terminal_runner=term_runner,
        service_runner=svc_runner,
    )
    assert rep["ok"] is False
    assert rep["status"] == "service_discovery_failed"
    assert rep["failure_kind"] == "service_discovery"
    assert rep["action"] == "stop_before_install"
    assert rep["should_stop_before_install"] is True
    assert rep["mutations_performed"] == 0
    assert "not found" in rep["service_checks"][0]["output"]

    # Verify size_install stops before any file copy or mutation when service discovery fails.
    mutated: list[str] = []
    monkeypatch.setattr(install_preflight, "run_preflight", lambda **kw: rep)
    monkeypatch.setattr(size_install.shutil, "copy2", lambda *a, **kw: mutated.append("copy2"))
    monkeypatch.setattr(size_install, "install_windows", lambda *a, **kw: mutated.append("windows"))

    rc = size_install.main(["--from-dir", str(tmp_path)], tmp_path)
    assert rc == 3
    assert mutated == []


def test_missing_terminal_command_routes_to_install_workflow_and_skips_service(tmp_path: Path):
    svc_calls: list[str] = []

    def term_runner(cmd: str, args: list[str], cwd: Path, os_name: str) -> dict:
        return {
            "command": cmd,
            "args": args,
            "context": "terminal",
            "cwd": str(cwd),
            "resolved_path": None,
            "status": "missing",
            "returncode": 127,
            "output": f"{cmd}: not found",
            "next_step": "Route through the existing install workflow (`fieldkit size integrate --from-dir RELEASE_DIR`).",
        }

    def svc_runner(service_id: str, label: str, cmd: str, args: list[str], cwd: Path, os_name: str) -> dict:
        svc_calls.append(cmd)
        return {}

    rep = install_preflight.run_preflight(
        install_root=tmp_path,
        os_name="linux",
        installer_runner=_ok_installer,
        terminal_runner=term_runner,
        service_runner=svc_runner,
    )
    assert rep["ok"] is False
    assert rep["status"] == "terminal_install_required"
    assert rep["failure_kind"] == "terminal_command_missing"
    assert rep["action"] == "route_to_install_workflow"
    assert rep["should_stop_before_install"] is False
    assert svc_calls == []
    assert rep["service_checks"][0]["status"] == "skipped_terminal_not_ready"


def test_executable_failing_version_or_help_validation_is_reported(tmp_path: Path):
    def term_runner(cmd: str, args: list[str], cwd: Path, os_name: str) -> dict:
        if cmd == "gorilla-size":
            return {
                "command": cmd,
                "args": args,
                "context": "terminal",
                "cwd": str(cwd),
                "resolved_path": "/home/user/.local/bin/gorilla-size",
                "status": "invalid",
                "returncode": 1,
                "output": "corrupt binary",
                "next_step": "Route through the existing install workflow to replace or repair the executable.",
            }
        return {
            "command": cmd,
            "args": args,
            "context": "terminal",
            "cwd": str(cwd),
            "resolved_path": "/home/user/.local/bin/gorilla-trash",
            "status": "ok",
            "returncode": 0,
            "output": "Usage: gorilla-trash",
            "next_step": "",
        }

    rep = install_preflight.run_preflight(
        install_root=tmp_path,
        os_name="windows",
        installer_runner=_ok_installer,
        terminal_runner=term_runner,
    )
    assert rep["ok"] is False
    assert rep["status"] == "terminal_validation_failed"
    assert rep["failure_kind"] == "terminal_command_invalid"
    assert rep["action"] == "route_to_install_workflow"
    assert rep["should_stop_before_install"] is False


def test_cli_exit_codes_for_allow_missing_terminal(monkeypatch, capsys):
    # Service-only discovery failure always exits 3 even with --allow-missing-terminal.
    monkeypatch.setattr(
        install_preflight,
        "run_preflight",
        lambda **kw: {
            "ok": False,
            "status": "service_discovery_failed",
            "action": "stop_before_install",
            "failure_kind": "service_discovery",
            "should_stop_before_install": True,
            "mutations_performed": 0,
            "os": "linux",
            "probe_cwd": "/tmp",
            "installer_checks": [],
            "terminal_checks": [],
            "service_checks": [],
            "next_step": "Fix service PATH.",
        },
    )
    assert install_preflight.main(["--allow-missing-terminal", "--json"]) == 3
    capsys.readouterr()

    # Missing terminal tool exits 0 with --allow-missing-terminal and 3 without it.
    monkeypatch.setattr(
        install_preflight,
        "run_preflight",
        lambda **kw: {
            "ok": False,
            "status": "terminal_install_required",
            "action": "route_to_install_workflow",
            "failure_kind": "terminal_command_missing",
            "should_stop_before_install": False,
            "mutations_performed": 0,
            "os": "linux",
            "probe_cwd": "/tmp",
            "installer_checks": [],
            "terminal_checks": [],
            "service_checks": [],
            "next_step": "Install via fieldkit size integrate.",
        },
    )
    assert install_preflight.main(["--allow-missing-terminal", "--json"]) == 0
    assert install_preflight.main(["--json"]) == 3
    capsys.readouterr()

    # Invalid terminal executable exits 3 even with --allow-missing-terminal.
    monkeypatch.setattr(
        install_preflight,
        "run_preflight",
        lambda **kw: {
            "ok": False,
            "status": "terminal_validation_failed",
            "action": "route_to_install_workflow",
            "failure_kind": "terminal_command_invalid",
            "should_stop_before_install": False,
            "mutations_performed": 0,
            "os": "windows",
            "probe_cwd": "/tmp",
            "installer_checks": [],
            "terminal_checks": [],
            "service_checks": [],
            "next_step": "Replace broken binary.",
        },
    )
    assert install_preflight.main(["--allow-missing-terminal", "--json"]) == 3


def test_systemd_user_unit_env_is_read_before_system_default(tmp_path: Path, monkeypatch):
    import subprocess

    calls: list[list[str]] = []

    def fake_which(name, path=None):
        if name == "systemctl":
            return "/usr/bin/systemctl"
        return None

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:3] == ["/usr/bin/systemctl", "--user", "show"]:
            return subprocess.CompletedProcess(
                argv, 0, "LoadState=loaded\nActiveState=active\nMainPID=0\nUser=\nEnvironment=PYTHONPATH=/opt/Fieldkit\n", ""
            )
        if argv[:3] == ["/usr/bin/systemctl", "--user", "show-environment"]:
            return subprocess.CompletedProcess(
                argv, 0, "HOME=/opt/demo\nUSER=demo\nPATH=/usr/local/bin:/usr/bin:/bin\n", ""
            )
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(install_preflight.shutil, "which", fake_which)
    monkeypatch.setattr(install_preflight.subprocess, "run", fake_run)
    monkeypatch.setenv("USER", "demo")

    path_val, user_val, source, env_map = install_preflight._service_env_from_systemd("fieldkit-link.service")
    assert path_val == "/usr/local/bin:/usr/bin:/bin"
    assert user_val == "demo"
    assert "systemd --user" in source
    assert env_map is not None and env_map["HOME"] == "/opt/demo"
    assert calls[0][:3] == ["/usr/bin/systemctl", "--user", "show"]
    assert calls[1][:3] == ["/usr/bin/systemctl", "--user", "show-environment"]

