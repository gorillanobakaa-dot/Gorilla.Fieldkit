---
name: fieldkit-network
description: "Unified human-first Wi-Fi, routing, router triage, LAN devices, and 100% offline connection diagnostics across Windows, Linux, and macOS. Use for checking Wi-Fi cards/chipsets, scanning channels, diagnosing why a Wi-Fi SSID fails to connect, joining saved networks, steering default routes between Ethernet and Wi-Fi, and managing the offline watchdog."
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, Python 3.11+). Runs on Windows, Linux, and macOS; diagnostics, scans, radio toggles, saved-profile repair, and auto-reconnect work 100% offline with zero AI model."
---

# Unified Network & Wi-Fi Toolkit (`fieldkit network`)

Every Wi-Fi, routing, router, LAN, and offline-watchdog capability lives under one unified cross-platform interface on Windows, Linux, and macOS, with intuitive human aliases and built-in examples.

## Quick human commands & top-level shortcuts

```bash
fieldkit network help
fieldkit wlan help
fieldkit show
fieldkit show radios
fieldkit show routes
fieldkit diagnose
fieldkit diagnose "MyHomeWiFi"
fieldkit diagnose ":(" --fix-auto
fieldkit diag "MyHomeWiFi"
fieldkit connect
fieldkit connect to "MyHomeWiFi"
fieldkit join "MyHomeWiFi"
fieldkit reconnect "MyHomeWiFi"
```

## Registered cards (Windows, Linux, and macOS)

- **Unified Wi-Fi, routing, router, LAN, and offline-watchdog toolkit with human aliases and examples (Windows, Linux, macOS)**: `fieldkit network` (alias `fieldkit wlan`). Run `fieldkit network help` (or `examples`, `cheatsheet`, `guide`) for copyable usage examples and the complete alias map.
- **Show Wi-Fi radios, active network, routes, profiles, LAN devices, router, events, and watchdog status at a glance (Windows, Linux, macOS)**: `fieldkit show` (or `fieldkit network show`, aliases `status`, `info`, `overview`, `check`). Pass `--fast` to skip live outbound TCP/DNS socket probes.
- **Offline Wi-Fi and network diagnostic across radios, air scan, saved profile mode, OS connection logs, DHCP, NCSI, and routing (Windows, Linux, macOS)**: `fieldkit diagnose` (alias `fieldkit diag`, or `fieldkit network diagnose` / `why` / `troubleshoot` / `doctor`). Works 100% offline with zero model: inspects `WLAN-AutoConfig/Operational` (`8001`, `8002`, `8003`, `11002`, `11006`, `11010`), `Dhcp-Client/Admin`, and `NCSI/Operational` (`4003`, `4042`) on Windows, `journalctl` / `nmcli` on Linux, and `wifi.log` / `ipconfig getsummary` on macOS. Pass `--fix-auto` to restore `connectionmode=auto` when an OS demoted a saved profile to manual after a drop.
- **Switch Wi-Fi radio on if needed, connect to a saved network or strongest saved SSID in range, and verify internet and default route (Windows, Linux, macOS)**: `fieldkit connect` (aliases `fieldkit join`, `fieldkit reconnect`, or `fieldkit network connect` / `rejoin` / `ensure`). Accepts natural phrasing (`fieldkit connect to "MyHomeWiFi"`), notifies the offline watchdog of a manual switch (`manual-switch.until` grace window), and never reads or prints Wi-Fi passwords.

## Subcommands & human aliases under `fieldkit network`

- `radios` (`cards`, `adapters`, `card`, `hardware`): real USB/PCIe chipset, bands, spatial streams, driver, and Windows Mobile Hotspot virtual-adapter carrier.
- `on` (`enable`, `up`, `restore`) / `off` (`disable`, `down`): switch one or all Wi-Fi radios on/off across software radio state, OS adapter state, Device Manager, `rfkill`, or `networksetup`.
- `scan` (`list`, `nearby`, `air`): trigger a fresh hardware rescan and list all BSSIDs with frequency-verified band (`2.4 GHz`, `5 GHz`, `6 GHz`), DFS (`ch 52-144`), and UK/EU (`ch 12-13`) flags; pass `--anonymise` before sharing output.
- `profiles` (`saved`, `known`): saved Wi-Fi profiles, auto-connect state, and in-range signal (`--fix-auto` restores automatic reconnection; passwords are never read).
- `channels` (`survey`, `free-channel`, `channel`) & `gui` (`window`): partial-overlap 2.4 GHz and 5 GHz channel interference analysis and interactive window.
- `routes` (`route`, `interfaces`, `ifaces`), `prefer-route` (`use-interface`, `prefer-iface`), `demote` (`deprioritize`), `restore-route` (`auto-metric`, `unprefer`): inspect per-interface bound-socket TCP/DNS reachability and steer route metrics reversibly without unplugging Ethernet.
- `hotspot-prefer` (`prefer-radio`): place Windows Mobile Hotspot on a chosen Wi-Fi adapter (`fieldkit network on` restores all radios).
- `router` (`gateway`, `hub`): identify the default gateway router, read unauthenticated DOCSIS/5G status safely, and check `--risk` login lockout policy before entering credentials.
- `devices` (`lan`, `neighbours`, `neighbors`): sweep and identify devices on your own local `/24` subnet on Windows, Linux, and macOS.
- `events` (`logs`, `history`): recent Wi-Fi, DHCP, NCSI, and link events with telemetry noise filtered out.
- `speed-code` (`rate`, `link-rate`), `watch-signal` (`watch`, `meter`), `calibrate`, `kind` (`line-kind`), `compare` (`speed-compare`), `antenna` (`cellular`), `upgrade` (`can-upgrade`).
- `watchdog` (`guard`, `keep-online`): `status`, `pause MINUTES`, `resume`, `run`, `test`, `install [--apply]`, `uninstall`, and `windows-task [--out DIR]` (offline logon + Windows `WLAN-AutoConfig` Event ID `8002`/`8003` scheduled-task hook).
