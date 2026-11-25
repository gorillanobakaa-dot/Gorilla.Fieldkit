"""Offline, zero-model Wi-Fi and internet diagnostic engine for Windows, Linux, and macOS.

Reads local radio state, fresh air/BSSID scan, saved profile metadata (never passwords),
OS Wi-Fi association/authentication logs, DHCP events, NCSI connectivity probes, and routing
without needing internet access or touching active backup connections.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
import re

from . import radios as R
from . import triage as T


# ---------------------------------------------------------------------------
# Cross-platform OS connection event readers (100% offline, telemetry-filtered)
# ---------------------------------------------------------------------------

PS_WIN_EVENTS = r"""
param([double]$Hours = 12)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$since = (Get-Date).AddHours(-$Hours)
$out = @()

foreach ($e in Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-WLAN-AutoConfig/Operational'; StartTime=$since} -MaxEvents 160) {
  if ($e.Id -notin 8000,8001,8002,8003,8011,11000,11001,11002,11004,11005,11006,11010,12011,12012,12013,12014) { continue }
  $msg = ($e.Message -replace '\r?\n', ' | ')
  $ssid = ''
  if ($e.Message -match '(?m)^\s*(?:Network )?SSID:\s*(.+?)\s*$') { $ssid = $Matches[1] }
  elseif ($e.Message -match '(?m)^\s*Profile Name:\s*(.+?)\s*$') { $ssid = $Matches[1] }
  $reason = ''
  if ($e.Message -match '(?m)^\s*(?:Failure )?Reason:\s*(.+?)\s*$') { $reason = $Matches[1] }
  $enc = ''
  if ($e.Message -match '(?m)^\s*Encryption:\s*(.+?)\s*$') { $enc = $Matches[1] }
  $out += [pscustomobject][ordered]@{
    time=$e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); log='WLAN'; id=$e.Id; level=[string]$e.LevelDisplayName;
    ssid=$ssid; reason=$reason; encryption=$enc; msg=$msg.Substring(0, [Math]::Min(220, $msg.Length))
  }
}

foreach ($e in Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Dhcp-Client/Admin'; StartTime=$since} -MaxEvents 50) {
  $msg = ($e.Message -replace '\r?\n', ' ')
  $ssid = ''
  if ($msg -match 'Identifier\(SSID\)\s+(.+?)\(Hexadecimal') { $ssid = $Matches[1].Trim() }
  $out += [pscustomobject][ordered]@{
    time=$e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); log='DHCP'; id=$e.Id; level=[string]$e.LevelDisplayName;
    ssid=$ssid; reason=''; encryption=''; msg=$msg.Substring(0, [Math]::Min(200, $msg.Length))
  }
}

foreach ($e in Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-NCSI/Operational'; StartTime=$since} -MaxEvents 50) {
  if ($e.Id -ne 4042) { continue }
  $msg = ($e.Message -replace '\r?\n', ' ')
  $out += [pscustomobject][ordered]@{
    time=$e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); log='NCSI'; id=$e.Id; level=[string]$e.LevelDisplayName;
    ssid=''; reason=''; encryption=''; msg=$msg.Substring(0, [Math]::Min(200, $msg.Length))
  }
}

foreach ($e in Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$since} -MaxEvents 120) {
  if ($e.ProviderName -notmatch 'Netwtw|rtwlan|WLAN-AutoConfig|e1d|e2f') { continue }
  if ($e.Id -eq 7021) { continue }
  $msg = ($e.Message -replace '\r?\n', ' ')
  $out += [pscustomobject][ordered]@{
    time=$e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); log=[string]$e.ProviderName; id=$e.Id; level=[string]$e.LevelDisplayName;
    ssid=''; reason=''; encryption=''; msg=$msg.Substring(0, [Math]::Min(200, $msg.Length))
  }
}

$out | Sort-Object time -Descending | ConvertTo-Json -Depth 3 -Compress
"""


def _windows_events(hours: float = 12.0) -> list[dict]:
    raw = R.run_ps(f"& {{ {PS_WIN_EVENTS} }} -Hours {float(hours)}", timeout=45).strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return [data] if isinstance(data, dict) else list(data)


def _linux_events(hours: float = 12.0) -> list[dict]:
    jc = R.find_tool("journalctl")
    if not jc:
        return []
    mins = max(1, int(hours * 60))
    _, txt, _ = R.run_cmd(
        [jc, "-u", "NetworkManager", "-u", "wpa_supplicant", "--since", f"-{mins}m", "--no-pager", "-n", "120", "-o", "short-iso"],
        timeout=25,
    )
    out = []
    for line in reversed(txt.splitlines()):
        if not line.strip():
            continue
        m = re.match(r"^(\S+)\s+\S+\s+([^\[:]+)(?:\[\d+\])?:\s*(.*)$", line)
        if m:
            t_str, src, body = m.group(1)[:19].replace("T", " "), m.group(2).strip(), m.group(3).strip()
        else:
            t_str, src, body = "", "journal", line.strip()
        ssid_m = re.search(r"connection '([^']+)'|SSID '([^']+)'", body)
        ssid = (ssid_m.group(1) or ssid_m.group(2)) if ssid_m else ""
        reason = body if any(w in body.lower() for w in ("fail", "reason=", "disconnected", "no-secrets", "timeout", "deauth")) else ""
        out.append({"time": t_str, "log": src, "id": 0, "level": "Info", "ssid": ssid, "reason": reason, "encryption": "", "msg": body[:200]})
    return out


def _macos_events(hours: float = 12.0) -> list[dict]:
    out = []
    wifi_log = "/var/log/wifi.log"
    if os.path.isfile(wifi_log):
        try:
            with open(wifi_log, encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-120:]
            for line in reversed(tail):
                s = line.strip()
                if s:
                    out.append({"time": s[:19], "log": "wifi.log", "id": 0, "level": "Info", "ssid": "", "reason": "", "encryption": "", "msg": s[:200]})
        except OSError:
            pass
    return out


def read_events(hours: float = 12.0, ssid: str | None = None, sysname: str | None = None) -> list[dict]:
    s = R.system_name(sysname)
    evs = _windows_events(hours) if s == "windows" else _linux_events(hours) if s == "linux" else _macos_events(hours) if s == "darwin" else []
    if not ssid:
        return evs
    return [
        e
        for e in evs
        if e.get("ssid") == ssid
        or ssid in (e.get("msg") or "")
        or e.get("log") in ("NCSI", "Netwtw14", "Microsoft-Windows-WLAN-AutoConfig")
    ]


# ---------------------------------------------------------------------------
# Deterministic diagnostic analyser (pure reasoning over gathered facts)
# ---------------------------------------------------------------------------

def analyse_ssid_events(ssid: str, events: list[dict], profile: dict, bssids: list[dict], radios: list[R.Radio]) -> dict:
    """Distinguish radio off, out-of-range/DFS, profile manual/missing, WPA3/cipher fail, and connected-without-internet."""
    findings: list[str] = []
    next_steps: list[str] = []

    on_radios = [r for r in radios if r.status in R.ON_STATES]
    if not radios:
        return {
            "verdict": "NO WIFI HARDWARE FOUND on this machine",
            "findings": ["no physical Wi-Fi adapter reported by the OS"],
            "next": ["plug in a USB Wi-Fi adapter or check BIOS / Device Manager"],
            "ok": False,
        }
    if not on_radios:
        return {
            "verdict": f"WIFI RADIO IS OFF ({', '.join(f'{r.name}: {r.status}' for r in radios)})",
            "findings": ["cannot scan or connect while all Wi-Fi radios are switched off"],
            "next": ["fieldkit network on"],
            "ok": False,
        }

    # Air / BSSID facts
    if not bssids:
        findings.append(f"{ssid!r} is NOT currently broadcasting in the air")
    else:
        for b in bssids:
            flags = []
            if b.get("dfs"):
                flags.append("DFS radar channel: AP may go silent for 60s during radar checks")
            if b.get("uk_eu_ch"):
                flags.append("UK/EU channel 12-13: devices with US/world-00 regulatory domain cannot see it")
            if "be" in (b.get("radio") or "").lower():
                flags.append("Wi-Fi 7 (802.11be) AP")
            flag_txt = f" [{'; '.join(flags)}]" if flags else ""
            findings.append(
                f"heard {ssid!r} at {b['signal']}% on {b['band']} ch {b['channel']} ({b.get('radio') or '?'}, {b.get('auth') or '?'}){flag_txt}"
            )

    if any(r.carries_hotspot for r in on_radios) and any(b.get("dfs") or b.get("uk_eu_ch") for b in bssids):
        findings.append(
            "Windows Mobile Hotspot is active on the same Wi-Fi card while the target AP uses a DFS or 12-13 channel; "
            "single-radio channel switching often drops association here"
        )

    # Profile facts
    if not profile.get("saved"):
        findings.append(f"no saved OS profile for {ssid!r}: join once in the system Wi-Fi menu so the OS stores the password")
        next_steps.append(f"join {ssid!r} once in the system Wi-Fi menu, then run: fieldkit connect {json.dumps(ssid)}")
    elif profile.get("autoconnect") is False:
        findings.append(
            f"saved profile for {ssid!r} is set to MANUAL connection ({profile.get('connection_mode')}); "
            "Windows demotes profiles after repeated connection timeouts"
        )
        next_steps.append(f"fieldkit network diagnose {json.dumps(ssid)} --fix-auto")

    # Event log timeline analysis
    ssid_wlan = [e for e in events if e.get("ssid") == ssid and e.get("log") == "WLAN"]
    fails_8002 = [e for e in ssid_wlan if e.get("id") == 8002]
    ok_8001 = [e for e in ssid_wlan if e.get("id") == 8001]
    disc_8003 = [e for e in ssid_wlan if e.get("id") == 8003]
    resets_4003 = [e for e in events if e.get("id") == 4003 and "limited connectivity" in (e.get("msg") or "").lower()]
    dhcp_plumbed = [e for e in events if e.get("log") == "DHCP" and e.get("ssid") == ssid and e.get("id") == 50066]
    gcmp256 = [e for e in ssid_wlan if "GCMP-256" in (e.get("encryption") or "")]

    if gcmp256 and fails_8002 and any("ax" in (r.chip_note or "").lower() for r in on_radios):
        findings.append(
            f"OS attempted WPA3 cipher AES-GCMP-256 ({len(gcmp256)} time(s)) on a Wi-Fi 6 card before falling back to AES-CCMP; "
            "setting the router to WPA2/WPA3 mixed mode avoids Wi-Fi 7 cipher negotiation timeouts"
        )

    if fails_8002:
        reasons = list(dict.fromkeys(e.get("reason") or "unknown" for e in fails_8002[:6]))
        findings.append(
            f"{len(fails_8002)} Wi-Fi connection failure(s) for {ssid!r} (latest {fails_8002[0]['time']}): {'; '.join(reasons)}"
        )

    # Check if L2 Wi-Fi succeeded + DHCP succeeded, followed by limited-connectivity reset (4003)
    connected_no_inet = False
    if ok_8001 and resets_4003:
        last_ok_t = ok_8001[0]["time"]
        near_reset = [r for r in resets_4003 if r["time"] >= last_ok_t]
        if near_reset:
            connected_no_inet = True
            findings.append(
                f"CONNECTED THEN RESET: Wi-Fi + WPA security to {ssid!r} SUCCEEDED at {last_ok_t}"
                + (f" and DHCP assigned an IP ({dhcp_plumbed[0]['time']})" if dhcp_plumbed else "")
                + f", but upstream internet probes did not pass, so Windows AutoConfig triggered adapter recovery (ID 4003 at {near_reset[0]['time']}) and disconnected"
            )

    currently_on = any(r.connected_ssid == ssid for r in on_radios)
    inet_now = R.probe_internet_quick(timeout=2.5) if currently_on else None

    if currently_on and inet_now:
        verdict = f"OK: currently connected to {ssid!r} and internet is working"
        ok = True
    elif currently_on and inet_now is False:
        verdict = f"WIFI OK, NO UPSTREAM INTERNET: connected to {ssid!r}, but the router/hotspot has no internet egress"
        next_steps.insert(0, "fieldkit network router   (inspect the router/hotspot WAN or SIM status)")
        ok = False
    elif connected_no_inet:
        verdict = (
            f"UPSTREAM INTERNET FAULT + CHANNEL CONTENTION on {ssid!r}: "
            "the laptop DID authenticate and get an IP on this Wi-Fi, then Windows reset the adapter because that router had no internet"
        )
        next_steps.insert(0, f"check WAN / mobile data on the {ssid!r} router (and prefer non-DFS 5 GHz ch 36-48 + WPA2/WPA3 mixed mode)")
        ok = False
    elif not bssids:
        verdict = f"NOT IN RANGE / SILENT: {ssid!r} is not broadcasting right now"
        next_steps.insert(0, "check that the router/hotspot is powered on, then run: fieldkit network scan")
        ok = False
    elif fails_8002:
        verdict = f"ASSOCIATION / AUTH FAILURE on {ssid!r}: {fails_8002[0].get('reason') or 'connection timed out'}"
        if any(b.get("dfs") for b in bssids):
            next_steps.append("move the router's 5 GHz band off DFS channels (52-144) onto ch 36, 40, 44, or 48")
        next_steps.append(f"fieldkit connect {json.dumps(ssid)}   (retry deterministic connection)")
        ok = False
    elif disc_8003:
        verdict = f"DISCONNECTED from {ssid!r} (last reason at {disc_8003[0]['time']}: {disc_8003[0].get('reason') or 'disconnected'})"
        next_steps.append(f"fieldkit connect {json.dumps(ssid)}")
        ok = False
    else:
        verdict = f"READY TO CONNECT: {ssid!r} is in range" + (" and saved" if profile.get("saved") else " (not saved yet)")
        if profile.get("saved"):
            next_steps.append(f"fieldkit connect {json.dumps(ssid)}")
        ok = bool(profile.get("saved"))

    return {"verdict": verdict, "findings": findings, "next": next_steps or ["nothing"], "ok": ok}


def diagnose(ssid: str | None = None, hours: float = 12.0, fix_auto: bool = False, sysname: str | None = None) -> dict:
    """Complete offline-capable diagnostic for a given SSID or the whole machine's network stack."""
    s = R.system_name(sysname)
    radios = R.list_radios(s)
    scan_rep = R.scan_networks(rescan_now=True, sysname=s)
    nets = scan_rep.get("networks") or []
    events = read_events(hours=hours, ssid=ssid, sysname=s)
    ifaces = T.gather_interfaces(test_links=True, sysname=s)
    rt_code, rt_verdict, rt_next = T.routes_verdict(ifaces)

    # Pick target SSID: explicit, active, or most recently failed in OS logs
    target = ssid
    if not target:
        active = next((r.connected_ssid for r in radios if r.connected_ssid), "")
        recent_fail = next((e["ssid"] for e in events if e.get("id") == 8002 and e.get("ssid")), "")
        target = recent_fail if (not active or rt_code != 0) and recent_fail else active

    profile = R.inspect_profile(target, sysname=s) if target else {}
    auto_fixed_msg = ""
    if target and fix_auto and profile.get("saved") and profile.get("autoconnect") is False:
        ok_fix, msg = R.set_autoconnect(target, auto=True, sysname=s)
        auto_fixed_msg = msg
        if ok_fix:
            profile["autoconnect"] = True
            profile["connection_mode"] = "Connect automatically (restored)"

    bssids = [n for n in nets if n.get("ssid") == target] if target else []
    ssid_diag = (
        analyse_ssid_events(target, events, profile, bssids, radios)
        if target
        else {
            "verdict": rt_verdict[0] if rt_verdict else "no Wi-Fi network selected",
            "findings": rt_verdict[1:],
            "next": rt_next or ["fieldkit connect   (join strongest saved Wi-Fi)"],
            "ok": rt_code == 0,
        }
    )

    # Check router status on winning gateway if reachable
    win_gw = next((i.gateway for i in ifaces if i.status == "Up" and i.has_default_route and i.gateway), "")
    router_rep = T.probe_router(win_gw, sysname=s) if win_gw else None

    overall_ok = bool(ssid_diag["ok"] and (rt_code == 0 or bool(ssid)))
    return {
        "platform": s,
        "target_ssid": target or None,
        "auto_fixed": auto_fixed_msg or None,
        "radios": [asdict(r) for r in radios],
        "target_bssids": bssids,
        "profile": profile,
        "interfaces": [asdict(i) for i in ifaces if i.status != "Not Present"],
        "route_verdict": rt_verdict,
        "router": router_rep,
        "recent_events": events[:20],
        "findings": ssid_diag["findings"],
        "verdict": ssid_diag["verdict"],
        "next": ssid_diag["next"],
        "ok": overall_ok,
        "exit": 0 if overall_ok else 3,
    }


def format_diagnosis(d: dict) -> list[str]:
    lines: list[str] = []
    lines.append(f"NETWORK DIAGNOSTIC ({d['platform']})" + (f"  target={d['target_ssid']!r}" if d.get("target_ssid") else ""))
    if d.get("auto_fixed"):
        lines.append(f"  AUTO-REPAIRED PROFILE: {d['auto_fixed']}")
    lines.append("")
    lines.append("1. WIFI RADIOS")
    for r in d.get("radios") or []:
        hs = f" [HOTSPOT: {', '.join(r['virtual_children'])}]" if r.get("carries_hotspot") else ""
        lines.append(f"  {r['name']} [{r['status']}] {r.get('product') or r.get('chipset') or r.get('os_label')}  link={r.get('link') or 'none'}{hs}")
    if not d.get("radios"):
        lines.append("  (none found)")

    if d.get("target_ssid"):
        t = d["target_ssid"]
        lines.append("")
        lines.append(f"2. TARGET NETWORK {t!r} IN AIR & SAVED PROFILE")
        p = d.get("profile") or {}
        if p.get("saved"):
            ciph = f", ciphers={'/'.join(p.get('ciphers') or [])}" if p.get("ciphers") else ""
            lines.append(f"  saved profile: YES, mode={p.get('connection_mode')}{ciph}")
        else:
            lines.append("  saved profile: NO")
        for b in d.get("target_bssids") or []:
            lines.append(
                f"  heard BSSID  : {b.get('bssid') or '-'}  signal {b.get('signal')}%  {b.get('band')} ch {b.get('channel')}  "
                f"{b.get('radio') or ''}  {b.get('auth') or ''}".rstrip()
            )
        if not d.get("target_bssids"):
            lines.append("  heard BSSID  : NONE (not currently seen in scan)")

    lines.append("")
    lines.append("3. ROUTING & CURRENT INTERNET")
    for rv in d.get("route_verdict") or []:
        lines.append("  " + rv)
    if d.get("router") and (d["router"].get("findings") or d["router"].get("label")):
        rr = d["router"]
        lines.append(f"  gateway {rr['ip']}: {rr.get('label') or rr.get('vendor') or 'HTTP ' + str(rr.get('http'))}")
        for rf in (rr.get("findings") or [])[:4]:
            lines.append("    - " + rf)

    evs = d.get("recent_events") or []
    if evs:
        lines.append("")
        lines.append(f"4. RECENT OS CONNECTION EVENTS (latest {min(len(evs), 10)})")
        for e in evs[:10]:
            tag = f"{e.get('log')}:{e.get('id')}" if e.get("id") else str(e.get("log"))
            s_part = f" [{e['ssid']}]" if e.get("ssid") else ""
            detail = e.get("reason") or e.get("msg") or ""
            lines.append(f"  {e.get('time')}  {tag:<12}{s_part} {detail[:120]}")

    lines.append("")
    lines.append("FINDINGS")
    for f in d.get("findings") or []:
        lines.append("  - " + f)
    lines.append("")
    lines.append("VERDICT: " + d["verdict"])
    for n in d.get("next") or []:
        lines.append("NEXT:    " + n)
    return lines
