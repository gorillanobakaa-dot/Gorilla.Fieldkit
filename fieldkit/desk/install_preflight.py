"""Installer preflight: deterministic, read-only checks before install or update mutations (P47).

Runs the existing installer prerequisites first, then resolves and validates each expected
terminal command from a fresh user terminal outside the install folder, and only then repeats
lookup and validation in each consuming background service context.

When a terminal command works and a background service cannot resolve or validate it, stops
before any installation or replacement and reports service discovery as the failure.
When a terminal command is genuinely missing or invalid in the user terminal, routes through
the existing install workflow.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from fieldkit import proc as fkproc
import sys
import tempfile

from ..core import doctor


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _current_os() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    return sys.platform


EXPECTED_TERMINAL_COMMANDS = (
    ("gorilla-size", ["--version"], "Gorilla Size"),
    ("gorilla-trash", ["--help"], "Gorilla Trash"),
)

# Background services that consume terminal commands:
# (service_id, label, command_name, validation_args, platforms)
EXPECTED_SERVICE_CONSUMERS = (
    ("cable-listener", "VAIO cable listener (fieldkit-link.service)", "gorilla-size", ["--version"], ("linux",)),
)


def _outside_cwd(install_root: Path | None = None) -> Path:
    root = Path(install_root or _default_root()).resolve()
    candidates = [
        Path.home(),
        Path(tempfile.gettempdir()),
    ]
    for cand in candidates:
        try:
            resolved = cand.resolve()
            if resolved != root and root not in resolved.parents and resolved.is_dir():
                return resolved
        except OSError:
            continue
    return root.parent if root.parent.is_dir() else Path(tempfile.gettempdir())


def _default_installer_runner(install_root: Path, os_name: str, lean: bool = True) -> list[dict]:
    py_ok = sys.version_info >= (3, 11)
    checks = [
        {
            "id": "python",
            "context": "installer",
            "status": "ok" if py_ok else "failed",
            "detail": f"Python {sys.version.split()[0]} ({sys.executable})",
            "next_step": "" if py_ok else "Install Python 3.11 or newer before running the installer.",
        },
        {
            "id": "install-root",
            "context": "installer",
            "status": "ok" if install_root.is_dir() else "failed",
            "detail": str(install_root),
            "next_step": "" if install_root.is_dir() else f"Verify the Fieldkit folder exists at {install_root}.",
        },
    ]
    try:
        doc = doctor.check(lean=lean)
        missing_pkgs = [p["import"] for p in doc.get("packages", []) if not p.get("ok")]
        checks.append({
            "id": "doctor-packages",
            "context": "installer",
            "status": "ok" if not missing_pkgs else "missing",
            "detail": "all required packages present" if not missing_pkgs else f"missing packages: {', '.join(missing_pkgs)}",
            "next_step": "" if not missing_pkgs else (doc.get("install_line") or "Run the installer package step."),
        })
    except Exception as exc:  # noqa: BLE001
        checks.append({
            "id": "doctor-packages",
            "context": "installer",
            "status": "failed",
            "detail": str(exc),
            "next_step": "Run `python -m fieldkit doctor --lean` to inspect package prerequisites.",
        })
    return checks


def _fresh_user_path_windows() -> str:
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return os.environ.get("PATH", "")
    parts: list[str] = []
    for root, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ):
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, "Path")
                if val:
                    parts.append(os.path.expandvars(str(val)))
        except OSError:
            continue
    return ";".join(p for p in parts if p) or os.environ.get("PATH", "")


def _run_terminal_command_windows(cmd: str, args: list[str], cwd: Path) -> dict:
    fresh_path = _fresh_user_path_windows()
    env = dict(os.environ)
    if fresh_path:
        env["PATH"] = fresh_path
    resolved = shutil.which(cmd, path=env.get("PATH"))
    if not resolved:
        return {
            "command": cmd,
            "args": list(args),
            "context": "terminal",
            "cwd": str(cwd),
            "resolved_path": None,
            "status": "missing",
            "returncode": None,
            "output": f"{cmd}: not found",
            "next_step": "Route through the existing install workflow (`fieldkit size integrate --from-dir RELEASE_DIR`).",
        }
    try:
        proc = fkproc.run(
            [resolved, *args],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "command": cmd,
            "args": list(args),
            "context": "terminal",
            "cwd": str(cwd),
            "resolved_path": resolved,
            "status": "invalid",
            "returncode": None,
            "output": str(exc),
            "next_step": "Route through the existing install workflow to replace or repair the executable.",
        }
    out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    ok = proc.returncode == 0 and bool(out)
    return {
        "command": cmd,
        "args": list(args),
        "context": "terminal",
        "cwd": str(cwd),
        "resolved_path": resolved,
        "status": "ok" if ok else "invalid",
        "returncode": proc.returncode,
        "output": out.splitlines()[0][:200] if out else "",
        "next_step": "" if ok else "Route through the existing install workflow to replace or repair the executable.",
    }


def _run_terminal_command_posix(cmd: str, args: list[str], cwd: Path) -> dict:
    bash = shutil.which("bash") or "/bin/bash"
    sh = bash if Path(bash).exists() else (shutil.which("sh") or "/bin/sh")
    shell_flag = "-lc" if Path(sh).name == "bash" else "-c"
    script = 'resolved=$(command -v "$1" 2>/dev/null || true); if [ -z "$resolved" ]; then exit 127; fi; printf "%s\\n" "$resolved"; shift; exec "$resolved" "$@"'
    try:
        proc = fkproc.run(
            [sh, shell_flag, script, "fieldkit-preflight", cmd, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "command": cmd,
            "args": list(args),
            "context": "terminal",
            "cwd": str(cwd),
            "resolved_path": None,
            "status": "invalid",
            "returncode": None,
            "output": str(exc),
            "next_step": "Route through the existing install workflow to replace or repair the executable.",
        }
    if proc.returncode == 127:
        return {
            "command": cmd,
            "args": list(args),
            "context": "terminal",
            "cwd": str(cwd),
            "resolved_path": None,
            "status": "missing",
            "returncode": 127,
            "output": f"{cmd}: not found",
            "next_step": "Route through the existing install workflow (`fieldkit size integrate --from-dir RELEASE_DIR`).",
        }
    lines = (proc.stdout or "").splitlines()
    resolved = lines[0].strip() if lines else None
    rest_stdout = "\n".join(lines[1:]).strip()
    out = (rest_stdout + ("\n" + proc.stderr.strip() if proc.stderr else "")).strip()
    ok = proc.returncode == 0 and bool(resolved) and bool(out)
    return {
        "command": cmd,
        "args": list(args),
        "context": "terminal",
        "cwd": str(cwd),
        "resolved_path": resolved,
        "status": "ok" if ok else "invalid",
        "returncode": proc.returncode,
        "output": out.splitlines()[0][:200] if out else "",
        "next_step": "" if ok else "Route through the existing install workflow to replace or repair the executable.",
    }


def _default_terminal_runner(cmd: str, args: list[str], cwd: Path, os_name: str) -> dict:
    if os_name == "windows":
        return _run_terminal_command_windows(cmd, args, cwd)
    return _run_terminal_command_posix(cmd, args, cwd)


def _parse_proc_environ(pid: int) -> dict[str, str] | None:
    env_file = Path(f"/proc/{pid}/environ")
    if not env_file.is_file():
        return None
    try:
        out: dict[str, str] = {}
        for item in env_file.read_bytes().split(b"\0"):
            if b"=" in item:
                k, v = item.split(b"=", 1)
                out[k.decode("utf-8", errors="replace")] = v.decode("utf-8", errors="replace")
        return out
    except OSError:
        return None


def _parse_unit_file(unit_path: Path) -> tuple[str | None, str | None]:
    user_val = None
    path_val = None
    for raw_line in unit_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("User="):
            user_val = line.split("=", 1)[1].strip()
        elif line.startswith("Environment=") and "PATH=" in line:
            after = line.split("PATH=", 1)[1]
            path_val = after.split('"')[0].strip()
    return path_val, user_val


def _service_env_from_systemd(unit_name: str = "fieldkit-link.service") -> tuple[str | None, str | None, str, dict[str, str] | None]:
    """Read Environment/User from the systemd user unit (primary, per link_keepalive.py) or system unit."""
    default_path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    current_user = os.environ.get("USER") or os.environ.get("LOGNAME") or None
    systemctl = shutil.which("systemctl")

    if systemctl:
        for scope_args, scope_label in ((["--user"], "systemd --user"), ([], "systemd")):
            try:
                proc = fkproc.run(
                    [systemctl, *scope_args, "show", unit_name, "--property=LoadState,ActiveState,MainPID,User,Environment"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                    check=False,
                )
                props: dict[str, str] = {}
                for line in (proc.stdout or "").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip()
                if props.get("LoadState") not in ("loaded", ""):
                    continue
                unit_user = props.get("User") or (current_user if scope_args else None)
                pid = int(props.get("MainPID") or "0")
                if pid > 0:
                    proc_env = _parse_proc_environ(pid)
                    if proc_env and proc_env.get("PATH"):
                        return (
                            proc_env["PATH"],
                            proc_env.get("USER") or unit_user,
                            f"running {scope_label} {unit_name} (PID {pid})",
                            proc_env,
                        )
                env_prop = props.get("Environment") or ""
                for token in env_prop.split():
                    if token.startswith("PATH="):
                        return token[5:].strip('"'), unit_user, f"{scope_label} {unit_name}", None
                if scope_args and props.get("LoadState") == "loaded":
                    mgr = fkproc.run(
                        [systemctl, "--user", "show-environment"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=5,
                        check=False,
                    )
                    mgr_env: dict[str, str] = {}
                    for line in (mgr.stdout or "").splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            mgr_env[k] = v
                    if mgr_env.get("PATH"):
                        return mgr_env["PATH"], unit_user, f"{scope_label} manager environment ({unit_name})", mgr_env
            except (OSError, subprocess.SubprocessError, ValueError):
                continue

    unit_paths = [
        (Path.home() / ".config" / "systemd" / "user" / unit_name, current_user),
        (Path(f"/etc/systemd/system/{unit_name}"), None),
        (Path(f"/lib/systemd/system/{unit_name}"), None),
        (Path(f"/usr/lib/systemd/system/{unit_name}"), None),
    ]
    for unit_path, fallback_user in unit_paths:
        if unit_path.is_file():
            try:
                path_val, user_val = _parse_unit_file(unit_path)
                return path_val or default_path, user_val or fallback_user, str(unit_path), None
            except OSError:
                continue
    return default_path, current_user, f"{unit_name} (systemd default environment)", None


def _default_service_runner(
    service_id: str,
    label: str,
    cmd: str,
    args: list[str],
    cwd: Path,
    os_name: str,
) -> dict:
    service_path, service_user, env_source, captured_env = _service_env_from_systemd("fieldkit-link.service")
    if captured_env:
        env = dict(captured_env)
        env["PATH"] = service_path or env.get("PATH") or "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    else:
        env = {"PATH": service_path or "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        for keep in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "SYSTEMROOT"):
            if keep in os.environ:
                env[keep] = os.environ[keep]
    resolved = shutil.which(cmd, path=env["PATH"])
    if not resolved:
        return {
            "service": service_id,
            "label": label,
            "command": cmd,
            "args": list(args),
            "context": f"service:{service_id}",
            "service_account": service_user or "default",
            "environment_source": env_source,
            "service_path": env["PATH"],
            "resolved_path": None,
            "status": "service_discovery_failed",
            "returncode": 127,
            "output": f"{cmd}: not found",
            "next_step": (
                f"Do not reinstall {cmd}. Expose {cmd} to {label} (for example, add its directory to the "
                f"systemd user service PATH in ~/.config/systemd/user/fieldkit-link.service or symlink into "
                f"/usr/local/bin, then run `systemctl --user daemon-reload && systemctl --user restart fieldkit-link.service`)."
            ),
        }
    try:
        proc = fkproc.run(
            [resolved, *args],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "service": service_id,
            "label": label,
            "command": cmd,
            "args": list(args),
            "context": f"service:{service_id}",
            "service_account": service_user or "default",
            "environment_source": env_source,
            "service_path": env["PATH"],
            "resolved_path": resolved,
            "status": "service_validation_failed",
            "returncode": None,
            "output": str(exc),
            "next_step": f"Verify permissions and runtime environment for {resolved} under {label}.",
        }
    out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    ok = proc.returncode == 0 and bool(out)
    return {
        "service": service_id,
        "label": label,
        "command": cmd,
        "args": list(args),
        "context": f"service:{service_id}",
        "service_account": service_user or "default",
        "environment_source": env_source,
        "service_path": env["PATH"],
        "resolved_path": resolved,
        "status": "ok" if ok else "service_validation_failed",
        "returncode": proc.returncode,
        "output": out.splitlines()[0][:200] if out else "",
        "next_step": "" if ok else f"Verify permissions and runtime environment for {resolved} under {label}.",
    }


def run_preflight(
    *,
    install_root: Path | None = None,
    os_name: str | None = None,
    lean: bool = True,
    installer_runner=None,
    terminal_runner=None,
    service_runner=None,
    commands: tuple[tuple[str, list[str], str], ...] = EXPECTED_TERMINAL_COMMANDS,
    services: tuple[tuple[str, str, str, list[str], tuple[str, ...]], ...] = EXPECTED_SERVICE_CONSUMERS,
) -> dict:
    """Execute read-only installer preflight in strict order:
    1. Existing installer checks
    2. Fresh user terminal resolution and bounded validation outside the install folder
    3. Background service consumer resolution and validation (only after terminal checks complete)
    """
    root = Path(install_root or _default_root()).resolve()
    current_os = (os_name or _current_os()).lower()
    probe_cwd = _outside_cwd(root)
    order_log: list[str] = []

    inst_fn = installer_runner or _default_installer_runner
    term_fn = terminal_runner or _default_terminal_runner
    svc_fn = service_runner or _default_service_runner

    order_log.append("installer")
    installer_checks = inst_fn(root, current_os, lean=lean)

    terminal_checks: list[dict] = []
    terminal_by_cmd: dict[str, dict] = {}
    for cmd_name, cmd_args, _label in commands:
        order_log.append(f"terminal:{cmd_name}")
        res = dict(term_fn(cmd_name, list(cmd_args), probe_cwd, current_os))
        res.setdefault("command", cmd_name)
        res.setdefault("args", list(cmd_args))
        res.setdefault("context", "terminal")
        res.setdefault("cwd", str(probe_cwd))
        terminal_checks.append(res)
        terminal_by_cmd[cmd_name] = res

    service_checks: list[dict] = []
    for service_id, label, cmd_name, cmd_args, platforms in services:
        if current_os not in platforms:
            continue
        term_res = terminal_by_cmd.get(cmd_name)
        if not term_res or term_res.get("status") != "ok":
            order_log.append(f"service:{service_id}:{cmd_name}:skipped")
            service_checks.append({
                "service": service_id,
                "label": label,
                "command": cmd_name,
                "args": list(cmd_args),
                "context": f"service:{service_id}",
                "resolved_path": None,
                "status": "skipped_terminal_not_ready",
                "returncode": None,
                "output": f"skipped because terminal check for {cmd_name} status={term_res.get('status') if term_res else 'not_run'}",
                "next_step": term_res.get("next_step", "") if term_res else "Install and validate the terminal command first.",
            })
            continue
        order_log.append(f"service:{service_id}:{cmd_name}")
        svc_res = dict(svc_fn(service_id, label, cmd_name, list(cmd_args), probe_cwd, current_os))
        svc_res.setdefault("service", service_id)
        svc_res.setdefault("label", label)
        svc_res.setdefault("command", cmd_name)
        svc_res.setdefault("args", list(cmd_args))
        svc_res.setdefault("context", f"service:{service_id}")
        service_checks.append(svc_res)

    installer_failed = any(c.get("status") == "failed" for c in installer_checks)
    terminal_missing = [c for c in terminal_checks if c.get("status") == "missing"]
    terminal_invalid = [c for c in terminal_checks if c.get("status") not in ("ok", "missing")]
    service_failed = [
        c for c in service_checks
        if c.get("status") in ("service_discovery_failed", "service_validation_failed")
    ]

    if installer_failed:
        overall = "installer_check_failed"
        action = "stop_fix_installer_prerequisites"
        should_stop_before_install = True
        failure_kind = "installer_prerequisites"
        next_step = next((c.get("next_step") for c in installer_checks if c.get("status") == "failed" and c.get("next_step")), "Fix installer prerequisites.")
    elif service_failed and not terminal_missing and not terminal_invalid:
        overall = "service_discovery_failed"
        action = "stop_before_install"
        should_stop_before_install = True
        failure_kind = "service_discovery"
        next_step = service_failed[0].get("next_step") or "Fix background service PATH/discovery without reinstalling the terminal binary."
    elif terminal_missing or terminal_invalid:
        overall = "terminal_install_required" if terminal_missing and not terminal_invalid else "terminal_validation_failed"
        action = "route_to_install_workflow"
        should_stop_before_install = False
        failure_kind = "terminal_command_missing" if terminal_missing and not terminal_invalid else "terminal_command_invalid"
        first_bad = (terminal_missing + terminal_invalid)[0]
        next_step = first_bad.get("next_step") or "Route through the existing install workflow."
    elif service_failed:
        overall = "service_discovery_failed"
        action = "stop_before_install"
        should_stop_before_install = True
        failure_kind = "service_discovery"
        next_step = service_failed[0].get("next_step") or "Fix background service discovery."
    else:
        overall = "ok"
        action = "ready"
        should_stop_before_install = False
        failure_kind = None
        next_step = "All terminal and background service checks passed; no binary replacement needed."

    return {
        "ok": overall == "ok",
        "status": overall,
        "action": action,
        "failure_kind": failure_kind,
        "should_stop_before_install": should_stop_before_install,
        "mutations_performed": 0,
        "os": current_os,
        "install_root": str(root),
        "probe_cwd": str(probe_cwd),
        "order": order_log,
        "installer_checks": installer_checks,
        "terminal_checks": terminal_checks,
        "service_checks": service_checks,
        "next_step": next_step,
    }


def format_report(report: dict) -> str:
    lines = [
        f"fieldkit installer preflight ({report.get('os')}) - status: {report.get('status')}",
        f"  probe cwd (outside install folder): {report.get('probe_cwd')}",
    ]
    for item in report.get("installer_checks", []):
        lines.append(f"  [{item.get('status', '').upper()}] {item.get('context')}:{item.get('id')} - {item.get('detail')}")
        if item.get("next_step"):
            lines.append(f"    next: {item['next_step']}")
    for item in report.get("terminal_checks", []):
        cmd_str = " ".join([item.get("command", ""), *(item.get("args") or [])]).strip()
        path_str = item.get("resolved_path") or "not found"
        out_str = f" ({item.get('output')})" if item.get("output") else ""
        lines.append(f"  [{item.get('status', '').upper()}] {item.get('context')} `{cmd_str}` -> {path_str}{out_str}")
        if item.get("next_step"):
            lines.append(f"    next: {item['next_step']}")
    for item in report.get("service_checks", []):
        cmd_str = " ".join([item.get("command", ""), *(item.get("args") or [])]).strip()
        path_str = item.get("resolved_path") or "not found"
        out_str = f" ({item.get('output')})" if item.get("output") else ""
        lines.append(f"  [{item.get('status', '').upper()}] {item.get('context')} ({item.get('label')}) `{cmd_str}` -> {path_str}{out_str}")
        if item.get("next_step"):
            lines.append(f"    next: {item['next_step']}")
    lines.append(f"  next step: {report.get('next_step')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fieldkit install preflight",
        description="Read-only installer preflight for terminal commands and consuming background services.",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable JSON report")
    ap.add_argument("--lean", action="store_true", default=True, help="check lean package requirements (default)")
    ap.add_argument("--full", dest="lean", action="store_false", help="check full package requirements")
    ap.add_argument(
        "--allow-missing-terminal",
        action="store_true",
        help="exit 0 when terminal commands are missing so the caller can proceed with installation, while still exiting 3 on service-only discovery failure",
    )
    ns = ap.parse_args(argv)
    report = run_preflight(lean=ns.lean)
    if ns.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    if report["status"] == "ok":
        return 0
    if ns.allow_missing_terminal and report["status"] == "terminal_install_required":
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
