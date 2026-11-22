"""Reuse a verified prefix only after matching inputs and proving the previous runner is idle."""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from fieldkit import proc as fkproc
import sys
from importlib import metadata
from pathlib import Path


FILE = "restart.json"
ARTIFACT_PATHS = ("mitm", "necko", "pcap", "strace", "dns")
WINDOWS_NETWORK = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$network = @{
  addresses = @(Get-NetIPAddress | Sort-Object InterfaceIndex,IPAddress | Select-Object InterfaceIndex,IPAddress,PrefixLength)
  routes = @(Get-NetRoute -PolicyStore ActiveStore | Sort-Object InterfaceIndex,DestinationPrefix,NextHop |
    Select-Object InterfaceIndex,DestinationPrefix,NextHop,RouteMetric)
  dns = @(Get-DnsClientServerAddress | Sort-Object InterfaceIndex,AddressFamily |
    Select-Object InterfaceIndex,AddressFamily,ServerAddresses)
  dnsLog = (Get-WinEvent -ListLog 'Microsoft-Windows-DNS-Client/Operational').IsEnabled
  profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore | Sort-Object Name |
    Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction)
  firewall = @(Get-NetFirewallRule -PolicyStore ActiveStore | Sort-Object Name |
    Select-Object Name,Enabled,Profile,Direction,Action)
  firewallRules = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\FirewallRules' |
    Select-Object -ExcludeProperty PSPath,PSParentPath,PSChildName,PSDrive,PSProvider)
  proxy = @(netsh winhttp show proxy)
}
if ($LASTEXITCODE) { exit $LASTEXITCODE }
$network.packets = @(pktmon status)
if ($LASTEXITCODE) { exit $LASTEXITCODE }
$network | ConvertTo-Json -Compress -Depth 8
"""


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def windows_network():
    try:
        raw = fkproc.check_output(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                       WINDOWS_NETWORK], stderr=subprocess.PIPE, timeout=45)
        data = json.loads(raw)
        required = {"addresses", "routes", "dns", "dnsLog", "profiles", "firewall", "firewallRules", "proxy", "packets"}
        if not isinstance(data, dict) or not required <= data.keys() or not data["addresses"] or not data["packets"]:
            return None
        return digest(data)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def windows_tools():
    return {name: shutil.which(name) for name in ("pktmon.exe", "mitmdump.exe", "powershell.exe", "netsh.exe")}


def owner():
    import psutil

    return {"pid": os.getpid(), "created": psutil.Process().create_time()}


def owner_gone(work, recorded):
    """Refuse reuse when the old PID still owns the run or an old worker still refers to its files."""
    import psutil

    try:
        try:
            if psutil.Process(recorded["pid"]).create_time() == recorded["created"]:
                return False
        except psutil.NoSuchProcess:
            pass
        folder = str(work.resolve()).replace("\\", "/").casefold() + "/"
        for proc in psutil.process_iter(["cmdline"], ad_value=None):
            if any(folder in arg.replace("\\", "/").casefold() for arg in proc.info["cmdline"] or ()):
                return False
        return True
    except (psutil.Error, OSError, TypeError, KeyError):
        return False


def context(package_digest, tree, upstream, policy_paths, tools, options, sha, *, network=None):
    from ..core.pipeline import tree_state

    try:
        source = tree_state(tree)
        if source.startswith("no-git ") or not package_digest:
            return None
        if sys.platform.startswith("linux"):
            boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            runner = [boot, os.geteuid()]
            if not re.fullmatch(r"[a-f0-9-]{36}", boot):
                return None
        elif sys.platform == "win32":
            import psutil

            if not network or not all(tools.values()):
                return None
            runner = [psutil.boot_time(), network]
        else:
            return None
        return digest({
            "format": 2, "job": options, "runner": [runner, platform.uname(), sys.version],
            "source": [source, upstream], "package": package_digest,
            "policy": [sha(path) if path and Path(path).is_file() else None for path in policy_paths],
            "fieldkit": [(path.name, sha(path)) for path in sorted(Path(__file__).parent.glob("*.py"))],
            "tools": {name: sha(path) if path else None for name, path in tools.items()},
            "packages": sorted((d.metadata["Name"], d.version) for d in metadata.distributions()
                               if d.metadata["Name"]),
        })
    except (OSError, ValueError, TypeError):
        return None


def files(work, artifact, sha):
    """Hash only regular sensor files inside this run. Missing optional files are recorded too."""
    root = (work / "runs").resolve()
    result = {}
    for key in ARTIFACT_PATHS:
        if not artifact.get(key):
            continue
        path = Path(artifact[key]).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"sensor file outside this run: {key}")
        result[key] = sha(path) if path.is_file() else None
        if key == "necko":
            for log in sorted(path.parent.glob(path.name + ".*")):
                if not log.resolve().is_relative_to(root) or not log.is_file():
                    raise ValueError("rotated DNS evidence is outside this run or unreadable")
                result[f"necko:{log.name}"] = sha(log)
    return result


def prefix(work, expected, context_id, checkpoint_dir, sha, *, recovered_cleanup=None):
    """Return completed scenario records in order or refuse the old run."""
    try:
        marker = json.loads((work / FILE).read_text(encoding="utf-8"))
        if not context_id or marker.get("context") != context_id or (work / "test-results.json").exists():
            return []
        state = marker.get("state")
        if state != "ready" and (state != "running" or not recovered_cleanup
                                 or not recovered_cleanup(work, marker.get("owner"))):
            return []
        records = marker.get("records", {})
        if not isinstance(records, dict) or set(records) != set(expected[:len(records)]):
            return []
        rows = []
        for name in expected[:len(records)]:
            path = work / checkpoint_dir / f"{name}.json"
            row = json.loads(path.read_text(encoding="utf-8"))
            if (row["scenario_run"] != name or digest(row) != records[name]["row"]
                    or files(work, row["artifact"], sha) != records[name]["files"]):
                return []
            if (not isinstance(row["events"], list) or
                    set(row["observations_since_previous_checkpoint"]) != {"page_results", "page_requests", "doh_queries"}
                    or not all(isinstance(items, list) for items in row["observations_since_previous_checkpoint"].values())):
                return []
            rows.append((row, records[name]))
        return rows
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []
