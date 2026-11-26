"""Human-first unified CLI for Wi-Fi, routing, router triage, LAN devices, and offline watchdog.

Every action carries intuitive human aliases and offline examples across Windows, Linux, and macOS.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import re
import sys

from . import diagnose as D
from . import radios as R
from . import triage as T

HELP_EXAMPLES = """\
UNIFIED NETWORK & WI-FI COMMANDS (Windows + Linux + macOS, 100% offline-capable)
================================================================================

1. QUICK HUMAN SHORTCUTS (directly on `fieldkit` or under `fieldkit network` / `fieldkit wlan`)
  fieldkit show                              Overview: Wi-Fi radios, active link, routes, internet
  fieldkit diagnose                          Full offline diagnostic of Wi-Fi, DHCP, NCSI, and routes
  fieldkit diagnose "MyHomeWiFi"             Why a specific Wi-Fi network failed to connect or dropped
  fieldkit diagnose ":(" --fix-auto          Diagnose ":(" and restore automatic reconnection if demoted
  fieldkit connect                           Join the strongest saved Wi-Fi network currently in range
  fieldkit connect to "MyHomeWiFi"           Switch radio on if needed, join "MyHomeWiFi", verify internet

2. INSPECTING WI-FI, AIR, CHANNELS & SAVED PROFILES
  fieldkit network show                      Everything at a glance (aliases: status, info, overview, check)
  fieldkit network radios                    Every Wi-Fi card/dongle: real chip, bands, streams, hotspot (aliases: cards, adapters)
  fieldkit network scan                      Fresh rescan of all nearby Wi-Fi networks & BSSIDs (aliases: list, nearby, air)
  fieldkit network profiles                  Saved Wi-Fi profiles & auto-connect state, never passwords (aliases: saved, known)
  fieldkit network channels                  Which 2.4 GHz channel is clearest and why (aliases: survey, free-channel)
  fieldkit network channels --band 5         Which 5 GHz channel is clearest
  fieldkit network gui                       Open the interactive channel survey window (alias: window)
  fieldkit network watch-signal "MyHomeWiFi" Repeated signal readings to test antenna/spot changes (aliases: watch, meter)
  fieldkit network speed-code 2401           Decode a phone/laptop PHY link rate into Wi-Fi standard & streams (alias: rate)

3. SWITCHING RADIOS, CONNECTING & STEERING ROUTES (ETHERNET vs WI-FI)
  fieldkit network on                        Switch all off/disabled Wi-Fi radios back on (aliases: enable, up, restore)
  fieldkit network on "Wi-Fi 2"              Switch a specific Wi-Fi radio on (Windows name, wlan0, or en0)
  fieldkit network off "Wi-Fi 2"             Switch a specific Wi-Fi radio off (aliases: disable, down)
  fieldkit network connect "MyHomeWiFi"      Deterministic 6-step join + internet & route check (aliases: join, rejoin, ensure)
  fieldkit network routes                    All interfaces, IPs, gateways, metrics, and per-link internet test (aliases: route, ifaces)
  fieldkit network prefer-route "Wi-Fi"      Give an interface a low metric so it wins the default route (alias: use-interface)
  fieldkit network demote "Ethernet"         Keep Ethernet plugged in for LAN, but send internet over Wi-Fi (alias: deprioritize)
  fieldkit network restore-route "Ethernet"  Put an interface back on automatic OS route metric (aliases: auto-metric, unprefer)
  fieldkit network hotspot-prefer "Wi-Fi 2"  Place Windows Mobile Hotspot on a chosen radio (alias: prefer-radio)

4. ROUTER, LAN DEVICES, CELLULAR ANTENNAS & OFFLINE WATCHDOG
  fieldkit network router                    Identify default gateway router & read DOCSIS/5G status safely (aliases: gateway, hub)
  fieldkit network router 192.168.1.1 --risk Check router login lockout risk before entering any password
  fieldkit network devices                   Count and identify devices on your own LAN subnet (aliases: lan, neighbours)
  fieldkit network events --hours 6          Recent Wi-Fi, DHCP, NCSI & link events without telemetry spam (aliases: logs, history)
  fieldkit network kind                      Detect fixed broadband vs shared cellular WAN from latency jitter (alias: line-kind)
  fieldkit network antenna --bands B3+B7+n78 Compute cellular MIMO antenna MHz spec & coax loss budget (alias: cellular)
  fieldkit network watchdog status           Is the offline auto-reconnect & voice-alarm daemon active? (alias: guard)
  fieldkit network watchdog pause 15         Pause offline voice alarms & auto-reconnect for 15 minutes
  fieldkit network watchdog resume           Resume the offline watchdog immediately

ALL ALIASES AT A GLANCE
  network (wlan)
    help          -> examples, cheatsheet, guide
    show          -> status, info, overview, check
    diagnose      -> diag, why, troubleshoot, doctor
    connect       -> join, rejoin, reconnect, ensure   (accepts optional "to": `connect to <SSID>`)
    scan          -> list, nearby, air
    channels      -> survey, free-channel, channel
    gui           -> window
    radios        -> cards, adapters, card, hardware
    on            -> enable, up, restore
    off           -> disable, down
    profiles      -> saved, known
    routes        -> route, interfaces, ifaces
    prefer-route  -> use-interface, prefer-iface
    demote        -> deprioritize
    restore-route -> auto-metric, unprefer
    hotspot-prefer-> prefer-radio
    router        -> gateway, hub
    devices       -> lan, neighbours, neighbors
    events        -> logs, history
    speed-code    -> rate, link-rate
    watch-signal  -> watch, meter
    kind          -> line-kind
    compare       -> speed-compare
    antenna       -> cellular
    upgrade       -> can-upgrade
    watchdog      -> guard, keep-online
"""


def _emit(data, as_json: bool, human_fn):
    if as_json:
        print(json.dumps(data, indent=1, default=str, ensure_ascii=False))
    else:
        human_fn(data)


def _clean_ssid_words(words: list[str] | None, filler: tuple[str, ...] = ()) -> str | None:
    if not words:
        return None
    toks = list(words)
    if len(toks) >= 2 and toks[0].lower() in filler:
        toks = toks[1:]
    val = " ".join(toks).strip()
    return val or None


def build_parser(prog: str = "fieldkit network") -> argparse.ArgumentParser:
    epilog = (
        "Run `fieldkit network help` (or `fieldkit network examples`) for a full cheat-sheet\n"
        "of human commands, aliases, and copyable examples across Windows, Linux, and macOS."
    )
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Unified Wi-Fi, routing, router, LAN, and offline-watchdog toolkit (Windows, Linux, macOS).",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--json", action="store_true", help="machine-readable JSON output")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable JSON output")

    sub = ap.add_subparsers(dest="action", metavar="COMMAND")

    # help / examples
    sub.add_parser(
        "help",
        aliases=["examples", "cheatsheet", "guide"],
        parents=[common],
        help="full list of commands, human aliases, and usage examples",
    )

    # show / status
    p_show = sub.add_parser(
        "show",
        aliases=["status", "info", "overview", "check"],
        parents=[common],
        help="show overview or a specific area (all, radios, routes, networks, profiles, devices, router, events, watchdog)",
    )
    p_show.add_argument(
        "area",
        nargs="?",
        default="all",
        choices=["all", "radios", "routes", "networks", "profiles", "devices", "router", "events", "watchdog"],
        help="what to show (default: all)",
    )
    p_show.add_argument("--fast", action="store_true", help="skip live outbound TCP/DNS probes")

    # diagnose
    p_diag = sub.add_parser(
        "diagnose",
        aliases=["diag", "why", "troubleshoot", "doctor"],
        parents=[common],
        help="offline diagnostic of a specific Wi-Fi SSID or the whole network stack (radios, air, profile, logs, routes)",
    )
    p_diag.add_argument("target", nargs="*", help="optional network SSID, e.g. MyHomeWiFi or ':('")
    p_diag.add_argument("--ssid", help="explicit network SSID (exact name, punctuation included)")
    p_diag.add_argument("--hours", type=float, default=12.0, help="hours of OS connection logs to inspect (12)")
    p_diag.add_argument("--fix-auto", action="store_true", help="restore automatic reconnection if OS demoted the profile to manual")

    # connect / join
    p_conn = sub.add_parser(
        "connect",
        aliases=["join", "rejoin", "reconnect", "ensure"],
        parents=[common],
        help="connect to a saved Wi-Fi network (named, or strongest in range) and verify internet + route",
    )
    p_conn.add_argument("target", nargs="*", help="optional '[to] SSID' (omit to join strongest saved network in range)")
    p_conn.add_argument("--ssid", "--profile", dest="ssid", help="exact saved network SSID")
    p_conn.add_argument("--radio", help="Wi-Fi adapter name (e.g. 'Wi-Fi', 'wlan0', 'en0')")
    p_conn.add_argument("--expect-gateway-mac", help="expected router MAC prefix for default-route verification")
    p_conn.add_argument("--no-fix-auto", action="store_true", help="do not restore connectionmode=auto on the profile")

    # scan
    p_scan = sub.add_parser(
        "scan",
        aliases=["list", "nearby", "air"],
        parents=[common],
        help="fresh scan of nearby Wi-Fi networks, bands, channels, and signal strength",
    )
    p_scan.add_argument("--radio", help="scan on a specific Wi-Fi adapter")
    p_scan.add_argument("--band", choices=["2.4", "5", "6"], help="filter to one band")
    p_scan.add_argument("--no-rescan", action="store_true", help="read cached OS scan without triggering a fresh radio sweep")
    p_scan.add_argument("--anonymise", "--anonymize", action="store_true", dest="anonymise", help="redact neighbour SSIDs and BSSIDs")

    # channels
    p_ch = sub.add_parser(
        "channels",
        aliases=["survey", "free-channel", "channel"],
        parents=[common],
        help="which 2.4 GHz or 5 GHz channel is clearest, accounting for partial-overlap interference",
    )
    p_ch.add_argument("--band", default="2.4", choices=["2.4", "5"], help="band to analyse (default: 2.4)")
    p_ch.add_argument("--avoid", default="", help="channels you use elsewhere, e.g. 6")
    p_ch.add_argument("--check", type=int, metavar="CH", help="show what overlaps channel CH")
    p_ch.add_argument("--why", action="store_true", help="print the argued rationale only")
    p_ch.add_argument("--networks", action="store_true", help="table of every network on the band")
    p_ch.add_argument("--profile", help="named tuning profile")
    p_ch.add_argument("--list-profiles", action="store_true", help="list available channel profiles")
    p_ch.add_argument("--mine", action="append", default=None, metavar="SSID", help="your own AP (excluded from self-interference)")
    p_ch.add_argument("--count-mine", action="store_true", help="count your own AP too")
    p_ch.add_argument("--devices", choices=["mixed", "local"], default=None, help="'local' confirms all devices support ch 12-13")
    p_ch.add_argument("--technical", action="store_true", help="full engineering table")
    p_ch.add_argument("--anonymise", "--anonymize", action="store_true", dest="anonymise", help="redact neighbour SSIDs/BSSIDs")
    p_ch.add_argument("--score", type=int, metavar="CH", help="show the score arithmetic for channel CH")
    p_ch.add_argument("--case", type=int, metavar="CH", help="compare your channel CH against the recommendation")

    # gui
    p_gui = sub.add_parser("gui", aliases=["window"], parents=[common], help="open the interactive Wi-Fi channel survey window")
    p_gui.add_argument("--anonymise", "--anonymize", action="store_true", dest="anonymise")

    # radios
    sub.add_parser(
        "radios",
        aliases=["cards", "adapters", "card", "hardware"],
        parents=[common],
        help="every Wi-Fi radio: real maker, chipset, bands, streams, driver, and hotspot carrier",
    )

    # on / enable
    p_on = sub.add_parser(
        "on",
        aliases=["enable", "up", "restore"],
        parents=[common],
        help="switch Wi-Fi radio(s) on (software toggle, network adapter, Device Manager, or rfkill)",
    )
    p_on.add_argument("radio", nargs="?", help="radio name (omit to switch all off radios back on)")

    # off / disable
    p_off = sub.add_parser("off", aliases=["disable", "down"], parents=[common], help="switch a Wi-Fi radio off")
    p_off.add_argument("radio", help="radio name (e.g. 'Wi-Fi', 'wlan0', 'en0')")

    # profiles
    p_prof = sub.add_parser(
        "profiles",
        aliases=["saved", "known"],
        parents=[common],
        help="list saved Wi-Fi profiles, auto-connect status, and current signal (never reads passwords)",
    )
    p_prof.add_argument("--ssid", help="inspect or repair one saved profile")
    p_prof.add_argument("--fix-auto", action="store_true", help="set saved profile(s) back to automatic connection")

    # routes
    p_rt = sub.add_parser(
        "routes",
        aliases=["route", "interfaces", "ifaces"],
        parents=[common],
        help="all network interfaces, IPs, gateways, metrics, and per-interface internet/DNS test",
    )
    p_rt.add_argument("--fast", action="store_true", help="skip outbound TCP/DNS socket probes")

    # prefer-route / demote / restore-route
    p_pref = sub.add_parser(
        "prefer-route",
        aliases=["use-interface", "prefer-iface"],
        parents=[common],
        help="make an interface win the default route by giving it a low route metric",
    )
    p_pref.add_argument("interface", help="interface name or index (e.g. 'Wi-Fi', 'Ethernet', 'wlan0', 'en0')")
    p_pref.add_argument("--metric", type=int, default=20, help="target route metric (default: 20)")
    p_pref.add_argument("--plan", action="store_true", help="show the exact command without applying")
    p_pref.add_argument("--undo", action="store_true", help="restore automatic route metric")

    p_dem = sub.add_parser(
        "demote",
        aliases=["deprioritize"],
        parents=[common],
        help="give an interface a high metric so another interface carries internet while both stay connected",
    )
    p_dem.add_argument("interface", help="interface name or index (e.g. 'Ethernet')")
    p_dem.add_argument("--metric", type=int, default=150, help="target high metric (default: 150)")
    p_dem.add_argument("--plan", action="store_true", help="show the exact command without applying")

    p_unpref = sub.add_parser(
        "restore-route",
        aliases=["auto-metric", "unprefer"],
        parents=[common],
        help="put an interface back on automatic OS route metric",
    )
    p_unpref.add_argument("interface", help="interface name or index")
    p_unpref.add_argument("--plan", action="store_true", help="show the exact command without applying")

    # hotspot-prefer
    p_hs = sub.add_parser(
        "hotspot-prefer",
        aliases=["prefer-radio"],
        parents=[common],
        help="make Windows Mobile Hotspot land on a chosen Wi-Fi radio (undo with `fieldkit network on`)",
    )
    p_hs.add_argument("radio", help="Wi-Fi adapter name, e.g. 'Wi-Fi 2'")

    # router
    p_rtr = sub.add_parser(
        "router",
        aliases=["gateway", "hub"],
        parents=[common],
        help="identify the router/gateway and read unauthenticated WAN/DOCSIS/5G status safely",
    )
    p_rtr.add_argument("ip", nargs="?", help="router IP address (default: winning default gateway)")
    p_rtr.add_argument("--risk", action="store_true", help="inspect login lockout policy & client-side crypto before any login")

    # devices
    p_dev = sub.add_parser(
        "devices",
        aliases=["lan", "neighbours", "neighbors"],
        parents=[common],
        help="count and identify devices on your own local subnet (ARP + optional ping sweep)",
    )
    p_dev.add_argument("--no-sweep", action="store_true", help="read ARP/neighbor table without pinging the subnet")

    # events
    p_ev = sub.add_parser(
        "events",
        aliases=["logs", "history"],
        parents=[common],
        help="recent OS Wi-Fi, DHCP, NCSI, and link events (telemetry noise filtered out)",
    )
    p_ev.add_argument("--hours", type=float, default=6.0, help="how many hours back to read (default: 6)")
    p_ev.add_argument("--ssid", help="filter events to one Wi-Fi SSID")

    # speed-code / rate
    p_rate = sub.add_parser(
        "speed-code",
        aliases=["rate", "link-rate"],
        parents=[common],
        help="decode a reported PHY connection speed (e.g. 866.7 or 2401 Mbps) into Wi-Fi standard, width, and streams",
    )
    p_rate.add_argument("value", nargs="?", type=float, help="connection rate in Mbps, e.g. 2401")
    p_rate.add_argument("--mbps", type=float, help="connection rate in Mbps")

    # watch-signal
    p_ws = sub.add_parser(
        "watch-signal",
        aliases=["watch", "meter"],
        parents=[common],
        help="take repeated signal samples for before/after antenna or placement tests",
    )
    p_ws.add_argument("target", nargs="*", help="network SSID")
    p_ws.add_argument("--ssid", help="exact network SSID")
    p_ws.add_argument("--bssid", help="specific access point MAC")
    p_ws.add_argument("--samples", type=int, default=8, help="number of scans (default: 8)")

    # calibrate
    p_cal = sub.add_parser(
        "calibrate",
        parents=[common],
        help="fit building path-loss exponent from 2+ known METRES:DBM readings",
    )
    p_cal.add_argument("--at", action="append", required=True, metavar="METRES:DBM", help="reading, e.g. --at 2.5:-36 --at 8.0:-54")

    # kind / compare / antenna / upgrade
    p_kind = sub.add_parser(
        "kind",
        aliases=["line-kind"],
        parents=[common],
        help="classify fixed broadband vs cellular/shared WAN from latency jitter",
    )
    p_kind.add_argument("--samples", type=int, default=10, help="ping samples (default: 10)")

    sub.add_parser(
        "compare",
        aliases=["speed-compare"],
        parents=[common],
        help="measure download speed down each active interface without changing routing",
    )

    p_ant = sub.add_parser(
        "antenna",
        aliases=["cellular"],
        parents=[common],
        help="compute 4G/5G cellular MIMO antenna MHz spec and cable loss budget from hub bands",
    )
    p_ant.add_argument("--bands", required=True, help="carrier band string, e.g. B3+B3+B7+n78+n78")
    p_ant.add_argument("--cable-m", type=float, default=5.0, help="planned coax length in metres (default: 5)")
    p_ant.add_argument("--cable", default="RG174 (thin, supplied with most kits)", help="coax cable type")
    p_ant.add_argument("--read-only-status", action="store_true", help="parse hub status text instead of antenna spec")

    sub.add_parser(
        "upgrade",
        aliases=["can-upgrade"],
        parents=[common],
        help="check what can and cannot be determined in software about upgrading the internal Wi-Fi card",
    )

    # watchdog
    p_wd = sub.add_parser(
        "watchdog",
        aliases=["guard", "keep-online"],
        parents=[common],
        help="offline auto-reconnect and voice alarm daemon (status, pause, resume, run, test, install, windows-task)",
    )
    p_wd.add_argument(
        "wd_action",
        nargs="?",
        default="status",
        choices=["status", "run", "pause", "resume", "test", "install", "uninstall", "windows-task"],
        help="watchdog operation (default: status)",
    )
    p_wd.add_argument("minutes", nargs="?", type=float, help="pause: minutes to pause")
    p_wd.add_argument("--apply", action="store_true", help="install: apply autostart unit")
    p_wd.add_argument("--out", help="windows-task: folder to write logon + WLAN event-hook scripts")
    p_wd.add_argument("--alarm-after", type=int, default=90, help="run: offline seconds before voice alarm (90)")
    p_wd.add_argument("--reconnect-after", type=int, default=20, help="run: offline seconds before auto-reconnect (20)")

    return ap


def _run_channels(a) -> int:
    from ..wifi import channelsurvey as cs

    if getattr(a, "anonymise", False):
        os.environ[cs.ANONYMISE_ENV] = "1"
    if getattr(a, "list_profiles", False):
        for n in cs.list_profiles():
            cfg, _ = cs.load_profile(n)
            print(f"  {n:<12} {cfg.get('description', '')}")
        return 0
    if a.action in ("gui", "window"):
        return cs.gui()
    cfg, prob = cs.load_profile(a.profile)
    if a.devices:
        cfg["devices"] = a.devices
    if prob:
        print("REFUSED: " + prob, file=sys.stderr)
        return 2
    nets, problem = cs.scan()
    if problem:
        print("REFUSED: " + problem, file=sys.stderr)
        return 2
    excl = {int(x) for x in re.findall(r"\d+", a.avoid)}
    mine = set(a.mine or ())
    if not mine and not a.count_mine:
        here = cs.current_network()
        if here:
            mine.add(here)
    if a.count_mine:
        mine = set()
    rows, rules = cs.analyse(nets, a.band, avoid=excl, cfg=cfg, mine=mine)
    rec = cs.recommend(rows, a.band, cfg=cfg)
    here_ch = next((n.channel for n in nets if n.ssid in mine), None)
    if a.json:
        print(cs.to_json(nets, rows, rec, rules, a.band, cfg))
    elif a.score is not None:
        print("\n".join(cs.breakdown_lines(cs.score_breakdown(nets, a.score, a.band, cfg, mine))))
    elif a.case is not None:
        print("\n".join(cs.case_for(rows, rec, a.case, rules, cfg)))
    elif a.check is not None:
        print("\n".join(cs.warn_about(rows, a.check)))
    elif a.networks:
        print(cs.network_table(nets, a.band))
    elif a.why:
        print("\n".join(cs.rationale(rows, rec, rules, a.band, cfg)))
    elif a.technical:
        print(cs.text_report(nets, rows, rec, rules, a.band, cfg))
    else:
        print(cs.plain_report(nets, rows, rec, rules, a.band, cfg, here_ch))
    return 0


def _run_show(area: str, fast: bool, as_json: bool) -> int:
    from .. import watchdog

    if area == "radios":
        rs = R.list_radios()
        _, _, code = R.radios_verdict(rs)
        _emit({"radios": [asdict(r) for r in rs]}, as_json, lambda _: print("\n".join(R.format_radios(rs))))
        return code
    if area == "routes":
        ifs = T.gather_interfaces(test_links=not fast)
        code, _, _ = T.routes_verdict(ifs)
        _emit({"interfaces": [asdict(i) for i in ifs]}, as_json, lambda _: print("\n".join(T.format_routes(ifs))))
        return code
    if area == "networks":
        sc = R.scan_networks(rescan_now=True)
        _emit(sc, as_json, lambda d: print("\n".join(_format_scan(d))))
        return 0 if sc.get("ok") else 2
    if area == "profiles":
        return _run_profiles(None, False, as_json)
    if area == "devices":
        from ..wifi import channelsurvey as cs
        devs = cs.local_devices(sweep=not fast)
        _emit(devs, as_json, lambda x: print("\n".join(cs.device_lines(x))))
        return 0
    if area == "router":
        rr = T.probe_router()
        _emit(rr, as_json, lambda d: print("\n".join(_format_router(d))))
        return 0 if rr.get("http") else 2
    if area == "events":
        evs = D.read_events(hours=6.0)
        _emit({"events": evs}, as_json, lambda d: print("\n".join(_format_events(d["events"]))))
        return 0
    if area == "watchdog":
        st = watchdog.status()
        _emit(st, as_json, lambda d: print("\n".join(_format_watchdog_status(d))))
        return 0 if st.get("ok") else 3

    # area == "all": concise human overview
    rs = R.list_radios()
    ifs = T.gather_interfaces(test_links=not fast)
    wd = watchdog.status()
    rt_code, _, _ = T.routes_verdict(ifs)
    _, _, rd_code = R.radios_verdict(rs)
    payload = {
        "radios": [asdict(r) for r in rs],
        "interfaces": [asdict(i) for i in ifs if i.status != "Not Present"],
        "watchdog": wd,
    }

    def human(_):
        print("=== WI-FI RADIOS ===")
        print("\n".join(R.format_radios(rs)))
        print("\n=== ROUTES & INTERNET ===")
        print("\n".join(T.format_routes(ifs)))
        print(
            f"\n=== WATCHDOG ===\n  online: {wd['online']}  network: {wd.get('ssid') or wd.get('remembered_ssid') or 'unknown'}  "
            f"alarms paused: {wd['paused']}"
        )
        print("\nTip: `fieldkit network help` shows all commands, human aliases, and examples.")

    _emit(payload, as_json, human)
    return 0 if (rt_code == 0 or fast) and rd_code == 0 else 3


def _format_scan(sc: dict) -> list[str]:
    lines = [str(sc.get("rescan") or "scan complete")]
    nets = list(sc.get("networks") or [])
    if not nets:
        lines.append("  no Wi-Fi networks seen (check `fieldkit network radios` or `fieldkit network on`)")
        return lines
    seen = set()
    unique = []
    for n in sorted(nets, key=lambda x: -int(x.get("signal") or 0)):
        key = (n.get("ssid"), n.get("bssid"), n.get("channel"))
        if key not in seen:
            seen.add(key)
            unique.append(n)
    lines.append(f"  {'SIGNAL':<7} {'BAND':<9} {'CH':<5} {'STANDARD':<10} {'SECURITY':<16} {'SSID'}")
    lines.append("  " + "-" * 72)
    for n in unique:
        tags = []
        if n.get("dfs"):
            tags.append("DFS")
        if n.get("uk_eu_ch"):
            tags.append("UK/EU")
        tag_s = f" [{','.join(tags)}]" if tags else ""
        lines.append(
            f"  {str(n.get('signal', 0)) + '%':<7} {str(n.get('band') or '?'):<9} {str(n.get('channel') or '?'):<5} "
            f"{str(n.get('radio') or '-'):<10} {str(n.get('auth') or '-')[:15]:<16} {n.get('ssid')}{tag_s}"
        )
    return lines


def _format_router(rr: dict) -> list[str]:
    lines = [
        f"ROUTER {rr.get('ip') or '(none)'}  MAC {rr.get('mac') or '-'}"
        f"  HTTP {rr.get('http') or 'no answer'}"
        + (f"  kind={rr['label']}" if rr.get("label") else f"  vendor={rr['vendor']}" if rr.get("vendor") else "")
    ]
    for f in rr.get("findings") or []:
        lines.append("  - " + f)
    return lines


def _format_events(evs: list[dict]) -> list[str]:
    if not evs:
        return ["no matching connection events in the requested window"]
    lines = []
    for e in evs[:40]:
        tag = f"{e.get('log')}:{e.get('id')}" if e.get("id") else str(e.get("log"))
        s_part = f" [{e['ssid']}]" if e.get("ssid") else ""
        lines.append(f"  {e.get('time')}  {tag:<14}{s_part} {(e.get('reason') or e.get('msg') or '')[:130]}")
    return lines


def _format_watchdog_status(d: dict) -> list[str]:
    out = [
        f"online:          {d['online']}",
        f"active network:  {d.get('ssid') or 'none'}",
        f"remembered SSID: {d.get('remembered_ssid') or 'none'}",
        f"alarms paused:   {d['paused']}",
        f"log:             {d['log']}",
    ]
    if d.get("missing_tools"):
        out.append(f"MISSING TOOLS:   {', '.join(d['missing_tools'])}")
    return out


def _run_profiles(ssid: str | None, fix_auto: bool, as_json: bool) -> int:
    sc = R.scan_networks(rescan_now=False)
    heard: dict[str, int] = {}
    for n in sc.get("networks") or []:
        if n.get("ssid"):
            heard[n["ssid"]] = max(heard.get(n["ssid"], 0), int(n.get("signal") or 0))
    profs = [R.inspect_profile(ssid)] if ssid else R.list_profiles(with_details=True)
    repaired = []
    if fix_auto:
        for p in profs:
            if p.get("saved") and p.get("autoconnect") is False:
                ok, msg = R.set_autoconnect(p["ssid"], auto=True)
                if ok:
                    p["autoconnect"] = True
                    p["connection_mode"] = "Connect automatically (restored)"
                    repaired.append(p["ssid"])
                p["repair_note"] = msg
    for p in profs:
        p["in_range_signal"] = heard.get(p.get("ssid"))

    def human(d):
        rows = d["profiles"]
        if not rows:
            print("no saved Wi-Fi profiles found")
            return
        print(f"SAVED WI-FI PROFILES ({len(rows)}) — passwords are never read or displayed")
        for p in rows:
            sig = f"in range ({p['in_range_signal']}%)" if p.get("in_range_signal") is not None else "not in range"
            mode = "auto" if p.get("autoconnect") else ("MANUAL (!)" if p.get("autoconnect") is False else str(p.get("connection_mode")))
            print(f"  {p['ssid']:<28} mode={mode:<12} {sig}")
        if d["repaired"]:
            print(f"RESTORED AUTO-CONNECT: {', '.join(d['repaired'])}")

    _emit({"profiles": profs, "repaired": repaired}, as_json, human)
    return 0


def main(argv: list[str] | None = None, prog: str = "fieldkit network") -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser(prog=prog)
    if not args:
        return _run_show("all", fast=False, as_json=False)
    if args[0] == "?":
        print(HELP_EXAMPLES, end="")
        return 0
    a = ap.parse_args(args)
    act = a.action
    if act is None:
        return _run_show("all", fast=False, as_json=a.json)

    if act in ("help", "examples", "cheatsheet", "guide"):
        if getattr(a, "json", False):
            print(json.dumps({"help": HELP_EXAMPLES}, indent=1))
        else:
            print(HELP_EXAMPLES, end="")
        return 0

    if act in ("show", "status", "info", "overview", "check"):
        return _run_show(a.area, fast=a.fast, as_json=a.json)

    if act in ("diagnose", "diag", "why", "troubleshoot", "doctor"):
        ssid = a.ssid or _clean_ssid_words(a.target, filler=("network", "wifi", "ssid", "for", "on"))
        rep = D.diagnose(ssid=ssid, hours=a.hours, fix_auto=a.fix_auto)
        _emit(rep, a.json, lambda d: print("\n".join(D.format_diagnosis(d))))
        return rep["exit"]

    if act in ("connect", "join", "rejoin", "reconnect", "ensure"):
        ssid = a.ssid or _clean_ssid_words(a.target, filler=("to", "network", "wifi", "ssid"))
        rep = R.join_saved(
            ssid=ssid,
            radio_name=a.radio,
            fix_auto=not a.no_fix_auto,
            expect_gateway_mac=a.expect_gateway_mac,
        )
        _emit(
            rep,
            a.json,
            lambda d: print(f"VERDICT: {d['verdict']}\nNEXT:    {d['next']}"),
        )
        return rep["exit"]

    if act in ("scan", "list", "nearby", "air"):
        if a.anonymise:
            from ..wifi import channelsurvey as cs
            os.environ[cs.ANONYMISE_ENV] = "1"
        sc = R.scan_networks(radio_name=a.radio, rescan_now=not a.no_rescan)
        if a.band:
            sc["networks"] = [n for n in (sc.get("networks") or []) if str(n.get("band") or "").startswith(a.band)]
        if a.anonymise:
            for idx, n in enumerate(sc.get("networks") or [], 1):
                if n.get("ssid") and n["ssid"] != "<hidden>":
                    n["ssid"] = f"network-{idx}"
                if n.get("bssid") and ":" in n["bssid"]:
                    n["bssid"] = ":".join(n["bssid"].split(":")[:3] + ["xx", "xx", "xx"])
        _emit(sc, a.json, lambda d: print("\n".join(_format_scan(d))))
        return 0 if sc.get("ok") else 2

    if act in ("channels", "survey", "free-channel", "channel", "gui", "window"):
        return _run_channels(a)

    if act in ("radios", "cards", "adapters", "card", "hardware"):
        rs = R.list_radios()
        v, nxt, code = R.radios_verdict(rs)
        _emit(
            {"radios": [asdict(r) for r in rs], "verdict": v, "next": nxt, "exit": code},
            a.json,
            lambda _: print("\n".join(R.format_radios(rs))),
        )
        return code

    if act in ("on", "enable", "up", "restore"):
        rep = R.switch_radio(a.radio, on=True)
        _emit(rep, a.json, lambda d: print("VERDICT: " + d["verdict"]))
        return rep["exit"]

    if act in ("off", "disable", "down"):
        rep = R.switch_radio(a.radio, on=False)
        _emit(rep, a.json, lambda d: print("VERDICT: " + d["verdict"]))
        return rep["exit"]

    if act in ("profiles", "saved", "known"):
        return _run_profiles(a.ssid, a.fix_auto, a.json)

    if act in ("routes", "route", "interfaces", "ifaces"):
        ifs = T.gather_interfaces(test_links=not a.fast)
        code, v, nxt = T.routes_verdict(ifs)
        _emit(
            {"interfaces": [asdict(i) for i in ifs], "verdict": v, "next": nxt, "exit": code},
            a.json,
            lambda _: print("\n".join(T.format_routes(ifs))),
        )
        return code

    if act in ("prefer-route", "use-interface", "prefer-iface"):
        mode = "restore" if a.undo else "prefer"
        rep = T.steer_route(a.interface, mode=mode, metric=a.metric, apply_it=not a.plan)
        _emit(rep, a.json, lambda d: print("VERDICT: " + d["verdict"]))
        return rep["exit"]

    if act in ("demote", "deprioritize"):
        rep = T.steer_route(a.interface, mode="demote", metric=a.metric, apply_it=not a.plan)
        _emit(rep, a.json, lambda d: print("VERDICT: " + d["verdict"]))
        return rep["exit"]

    if act in ("restore-route", "auto-metric", "unprefer"):
        rep = T.steer_route(a.interface, mode="restore", apply_it=not a.plan)
        _emit(rep, a.json, lambda d: print("VERDICT: " + d["verdict"]))
        return rep["exit"]

    if act in ("hotspot-prefer", "prefer-radio"):
        rep = R.prefer_hotspot_radio(a.radio)
        _emit(rep, a.json, lambda d: print("VERDICT: " + d["verdict"]))
        return rep["exit"]

    if act in ("router", "gateway", "hub"):
        if a.risk:
            rk = T.router_login_risk(a.ip)
            _emit(rk, a.json, lambda d: print(f"ROUTER {d['ip']} LOGIN RISK: {d.get('advice')}"))
            return 0 if rk.get("ip") else 2
        rr = T.probe_router(a.ip)
        _emit(rr, a.json, lambda d: print("\n".join(_format_router(d))))
        return 0 if rr.get("http") else 2

    if act in ("devices", "lan", "neighbours", "neighbors"):
        from ..wifi import channelsurvey as cs
        devs = cs.local_devices(sweep=not a.no_sweep)
        _emit(devs, a.json, lambda x: print("\n".join(cs.device_lines(x))))
        return 0

    if act in ("events", "logs", "history"):
        evs = D.read_events(hours=a.hours, ssid=a.ssid)
        _emit({"events": evs}, a.json, lambda d: print("\n".join(_format_events(d["events"]))))
        return 0

    if act in ("speed-code", "rate", "link-rate"):
        from ..wifi import channelsurvey as cs
        mbps = a.mbps if a.mbps is not None else a.value
        if mbps is None:
            print("REFUSED: pass the link rate in Mbps, e.g. `fieldkit network speed-code 2401`", file=sys.stderr)
            return 2
        d = cs.decode_rate(mbps)
        _emit(d, a.json, lambda x: print("\n".join(cs.rate_lines(x))))
        return 2 if "standard" not in d else 0

    if act in ("watch-signal", "watch", "meter"):
        from ..wifi import channelsurvey as cs
        ssid = a.ssid or _clean_ssid_words(a.target, filler=("on", "for", "ssid"))
        if not (ssid or a.bssid):
            print("REFUSED: pass an SSID or --bssid, e.g. `fieldkit network watch-signal MyHomeWiFi`", file=sys.stderr)
            return 2
        d = cs.watch(ssid, a.bssid, samples=a.samples)
        _emit(d, a.json, lambda x: print("\n".join(cs.watch_lines(x))))
        return 2 if "error" in d else 0

    if act == "calibrate":
        from ..wifi import channelsurvey as cs
        pts = []
        for item in a.at or ():
            m = re.match(r"\s*([0-9.]+)\s*:\s*(-?[0-9.]+)\s*$", item)
            if not m:
                print(f"REFUSED: {item!r} is not METRES:DBM, e.g. 2.88:-34", file=sys.stderr)
                return 2
            pts.append((float(m.group(1)), float(m.group(2))))
        d = cs.calibrate(pts)
        _emit(d, a.json, lambda x: print("\n".join(cs.calibration_lines(x))))
        return 2 if "error" in d else 0

    if act in ("kind", "line-kind"):
        from ..net import route as netroute
        d = netroute.kind(samples=a.samples)
        _emit(d, a.json, lambda x: print("\n".join(netroute.lines(x))))
        return 0

    if act in ("compare", "speed-compare"):
        from ..net import route as netroute
        if sys.platform.startswith("linux"):
            d = netroute.compare()
            _emit(d, a.json, lambda x: print("\n".join(netroute.lines(x))))
            return 0
        ifs = T.gather_interfaces(test_links=True)
        _emit({"interfaces": [asdict(i) for i in ifs]}, a.json, lambda _: print("\n".join(T.format_routes(ifs))))
        return 0

    if act in ("antenna", "cellular"):
        from ..net import cellular
        carriers = cellular.parse_bands(a.bands)
        if a.read_only_status:
            d = cellular.read_status(a.bands)
            _emit(d, a.json, lambda x: print("\n".join(cellular.lines(x))))
            return 0
        d = cellular.antenna_spec(carriers, cable_m=a.cable_m, cable=a.cable)
        _emit(d, a.json, lambda x: print("\n".join(cellular.spec_lines(x))))
        return 2 if "error" in d else 0

    if act in ("upgrade", "can-upgrade"):
        from .. import wifi
        _emit(wifi.upgrade(), a.json, lambda x: print(json.dumps(x, indent=1, default=str)))
        return 0

    if act in ("watchdog", "guard", "keep-online"):
        from .. import watchdog
        from ..watchdog import install as wdinstall
        wact = a.wd_action
        if wact == "status":
            st = watchdog.status()
            _emit(st, a.json, lambda d: print("\n".join(_format_watchdog_status(d))))
            return 0 if st["ok"] else 3
        if wact == "pause":
            mins = a.minutes if a.minutes is not None else 15.0
            _emit(watchdog.set_pause(mins), a.json, lambda d: print(f"alarms paused for {d['paused_minutes']} minutes"))
            return 0
        if wact == "resume":
            _emit(watchdog.clear_pause(), a.json, lambda _: print("watchdog resumed (pause cleared)"))
            return 0
        if wact == "test":
            r = watchdog.speak(watchdog.TEST)
            _emit({"result": r}, a.json, lambda d: print(d["result"]))
            return 0 if r == "spoken" else 3
        if wact == "run":
            watchdog.loop(alarm_after=a.alarm_after, reconnect_after=a.reconnect_after)
            return 0
        if wact == "install":
            r = wdinstall.apply() if a.apply else wdinstall.plan()
            _emit(r, a.json, lambda d: print(json.dumps(d, indent=1, default=str)))
            return 0
        if wact == "uninstall":
            _emit(wdinstall.uninstall(), a.json, lambda d: print(json.dumps(d, indent=1, default=str)))
            return 0
        if wact == "windows-task":
            r = wdinstall.windows_event_hook_scripts(a.out)
            _emit(r, a.json, lambda d: print(f"Windows offline logon + WLAN event-hook script:\n  {d.get('script') or d.get('preview')}"))
            return 0

    return 2


def shortcut_parser(kind: str, prog: str) -> argparse.ArgumentParser:
    epilog = "Run `fieldkit network help` for all commands, aliases, and examples."
    if kind == "show":
        ap = argparse.ArgumentParser(
            prog=prog,
            description="Show Wi-Fi radios, active network, routes, and internet at a glance (Windows, Linux, macOS).",
            epilog=epilog,
        )
        ap.add_argument(
            "area",
            nargs="?",
            default="all",
            choices=["all", "radios", "routes", "networks", "profiles", "devices", "router", "events", "watchdog", "help"],
            help="what to show (default: all)",
        )
        ap.add_argument("--fast", action="store_true", help="skip live outbound TCP/DNS socket probes")
        ap.add_argument("--json", action="store_true", help="machine-readable JSON output")
        return ap
    if kind == "diagnose":
        ap = argparse.ArgumentParser(
            prog=prog,
            description="100% offline Wi-Fi & network diagnostic (radios, air, profile, OS connection logs, DHCP, NCSI, routes).",
            epilog=epilog,
        )
        ap.add_argument("target", nargs="*", help="optional network SSID, e.g. MyHomeWiFi or ':('")
        ap.add_argument("--ssid", help="explicit network SSID (exact name, punctuation included)")
        ap.add_argument("--hours", type=float, default=12.0, help="hours of OS connection logs to inspect (12)")
        ap.add_argument("--fix-auto", action="store_true", help="restore automatic reconnection if OS demoted the profile to manual")
        ap.add_argument("--json", action="store_true", help="machine-readable JSON output")
        return ap
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Switch Wi-Fi radio on if needed, join a saved Wi-Fi network (or strongest in range), and verify internet.",
        epilog=epilog,
    )
    ap.add_argument("target", nargs="*", help="optional '[to] SSID' (omit to join strongest saved network in range)")
    ap.add_argument("--ssid", "--profile", dest="ssid", help="exact saved network SSID")
    ap.add_argument("--radio", help="Wi-Fi adapter name (e.g. 'Wi-Fi', 'wlan0', 'en0')")
    ap.add_argument("--expect-gateway-mac", help="expected router MAC prefix for default-route verification")
    ap.add_argument("--no-fix-auto", action="store_true", help="do not restore connectionmode=auto on the profile")
    ap.add_argument("--json", action="store_true", help="machine-readable JSON output")
    return ap


def shortcut_main(kind: str, argv: list[str] | None = None, prog: str = "fieldkit show") -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ap = shortcut_parser(kind, prog=prog)
    a = ap.parse_args(args)
    if kind == "show":
        if a.area == "help":
            print(HELP_EXAMPLES, end="")
            return 0
        return _run_show(a.area, fast=a.fast, as_json=a.json)
    if kind == "diagnose":
        if a.target == ["help"] and not a.ssid:
            print(HELP_EXAMPLES, end="")
            return 0
        ssid = a.ssid or _clean_ssid_words(a.target, filler=("network", "wifi", "ssid", "for", "on"))
        rep = D.diagnose(ssid=ssid, hours=a.hours, fix_auto=a.fix_auto)
        _emit(rep, a.json, lambda d: print("\n".join(D.format_diagnosis(d))))
        return rep["exit"]
    if a.target == ["help"] and not a.ssid:
        print(HELP_EXAMPLES, end="")
        return 0
    ssid = a.ssid or _clean_ssid_words(a.target, filler=("to", "network", "wifi", "ssid"))
    rep = R.join_saved(
        ssid=ssid,
        radio_name=a.radio,
        fix_auto=not a.no_fix_auto,
        expect_gateway_mac=a.expect_gateway_mac,
    )
    _emit(rep, a.json, lambda d: print(f"VERDICT: {d['verdict']}\nNEXT:    {d['next']}"))
    return rep["exit"]
