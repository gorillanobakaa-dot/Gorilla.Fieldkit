"""Unified, human-friendly Wi-Fi, routing, router, LAN, and offline-watchdog toolkit.

Works offline with zero model calls across Windows, Linux, and macOS:

    fieldkit network                     overview: radios, active Wi-Fi, routes, internet
    fieldkit network help                every command, its aliases, and copyable examples
    fieldkit network show [AREA]         show all | radios | routes | networks | profiles | devices | router | events | watchdog
    fieldkit network diagnose [SSID]     why Wi-Fi or internet failed (radios, air, profile, OS logs, DHCP, NCSI, routes)
    fieldkit network connect [to] [SSID] join a saved Wi-Fi network (named, or strongest in range) and verify internet
    fieldkit network scan                fresh Wi-Fi scan across bands, channels, and BSSIDs
    fieldkit network channels            which 2.4 / 5 GHz channel is clearest, and why
    fieldkit network radios              every Wi-Fi card/dongle: real maker, chipset, bands, streams, driver, hotspot
    fieldkit network on [RADIO]          switch Wi-Fi radio(s) on (software toggle, adapter, or Device Manager / rfkill)
    fieldkit network off RADIO           switch a Wi-Fi radio off
    fieldkit network profiles            saved Wi-Fi profiles (auto vs manual, in-range signal; never reads passwords)
    fieldkit network routes              interfaces, IPs, gateways, metrics, and per-interface internet/DNS check
    fieldkit network router [IP]         identify and read the router/hub status safely without login lockout
    fieldkit network prefer-route IFACE  make an interface win or yield the default route, reversibly
    fieldkit network devices             devices on your own LAN subnet (ARP + optional ping sweep)
    fieldkit network events              recent Wi-Fi, DHCP, NCSI, and link events without telemetry noise
    fieldkit network watchdog ...        offline auto-reconnect and spoken alarm daemon

Top-level human shortcuts on `fieldkit`:
    fieldkit show [AREA]
    fieldkit diagnose [SSID]
    fieldkit connect [to] [SSID]
"""
from .cli import main

__all__ = ["main"]
