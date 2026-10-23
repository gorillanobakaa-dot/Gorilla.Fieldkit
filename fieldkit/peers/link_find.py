"""Where is the other computer? A fixed procedure with one VERDICT, instead of an agent improvising network probes.

    fieldkit peers link find [--iface CABLE] [--probe HOST ...]

Owner, 2026-10-22: "have you tried to query the machine?" then "teach the harness how to do that. make it part of
the deterministic tools". What was done by hand that afternoon, in this order, each step quieter than the next:

  1 cable counters   packets the cable card has RECEIVED (any kind). 0 = the other system is not using its port at
                     all (no IPv6 address, no DHCP, nothing): no probe can reach it; run setup there
  2 cable peer       one all-nodes IPv6 ping (ff02::1) on the cable card, then its neighbor table: the peer's
                     link-local address if it has one
  3 home network     THIS computer's neighbor table on the internet card, read only (nothing sent): each device's
                     maker from its MAC (a small table; unknown says unknown), a randomized MAC (a phone, mostly)
                     said so
  4 probe (opt-in)   only the hosts named with --probe, one TCP connection each: port 22 (the SSH banner names the
                     server and often the OS) and the link port 47077. Never a sweep of a network.

Read: "refused" = a live host with nothing listening there (2026-10-22: the VAIO refused 22: no SSH server);
"timed out" = filtered or no host. An SSH CLIENT on a machine (it reaches a MacBook) says nothing about an SSH server.
"""
import ipaddress
import json
import re
import socket
import subprocess
import sys

from . import link

# MAC prefix -> maker. Only prefixes seen or sure; anything else is reported as unknown, never guessed.
OUI = {
    "88:33:74": "the EE Smart 5G Hub (its LAN side)",
    "8a:33:74": "the EE hub's Wi-Fi (locally administered)",
    "48:d3:43": "ARRIS (the Virgin Media Hub 3)",
    "a4:17:31": "Hon Hai / Foxconn (a laptop's Wi-Fi card; Sony VAIO models used them)",
    "3c:07:71": "Sony (2026-10-22: the VAIO's wired card, seen on the cable)",
    "f4:a8:0d": "this laptop's Intel I219-V",
    "78:af:08": "Intel (a Wi-Fi card)",
    "00:c0:ca": "ALFA Network",
}


def norm(mac):
    return (mac or "").lower().replace("-", ":")


def randomized(mac):
    """The locally administered bit: phones and privacy-minded systems make up their MAC."""
    m = norm(mac)
    return len(m) >= 2 and bool(int(m[:2], 16) & 0x02) and m[:8] not in OUI


def maker(mac):
    m = norm(mac)
    if m[:8] in OUI:
        return OUI[m[:8]]
    return "randomized MAC (a phone or a privacy setting)" if randomized(m) else "unknown maker"


def live(entries):
    """Neighbor entries worth reading: a real MAC, a state that saw an answer."""
    return [e for e in entries if norm(e.get("mac")) not in ("", "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff")
            and e.get("state") in ("Reachable", "Stale", "Delay", "Probe", "REACHABLE", "STALE", "DELAY", "PROBE")
            and not str(e.get("ip", "")).lower().startswith(("ff", "224.", "239.", "255."))]


def classify_probe(result):
    """socket outcome -> what it means."""
    kind = result.get("error_kind")
    if result.get("banner") is not None:
        return f"OPEN ({result['banner'][:60] or 'no banner'})"
    if kind == "ConnectionRefusedError":
        return "refused: a live host with nothing listening on that port"
    if kind in ("TimeoutError", "timeout"):
        return "timed out: filtered, or no host there"
    return f"error: {result.get('error')}"


def scope_id(iface):
    """The interface index an IPv6 link-local address needs (fe80::x%17)."""
    try:
        return socket.if_nametoindex(iface)
    except (OSError, AttributeError):
        return 0


def probe(host, port, timeout=4, connect=socket.create_connection):
    """One TCP connection. A link-local IPv6 host is given as fe80::...%SCOPE (an interface index)."""
    try:
        if "%" in host:
            addr, scope = host.split("%", 1)
            s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((addr, port, 0, int(scope) if scope.isdigit() else scope_id(scope)))
        else:
            s = connect((host, port), timeout=timeout)
        try:
            s.settimeout(timeout)
            data = s.recv(200) if port == 22 else b""
        except OSError:
            data = b""
        s.close()
        return {"banner": data.decode("ascii", "replace").strip()}
    except OSError as e:
        return {"error_kind": type(e).__name__, "error": str(e)}


def decide(f):
    """facts -> (exit, VERDICT, NEXT lines). The order is the procedure."""
    nxt = []
    if f.get("cable_received") == 0:
        v = (f"CABLE SILENT: {f.get('cable')} has received 0 packets; the other computer is not using its port "
             "(no address, no DHCP, nothing)")
        nxt.append("on the other computer: sh <stick>/fieldkit-link/setup-linux.sh (fieldkit peers link setup --join)")
        code = 2
    elif f.get("cable_peer"):
        v = f"CABLE PEER {f['cable_peer']} ({maker(f.get('cable_peer_mac'))})"
        code = 0
    else:
        v = f"CABLE: {f.get('cable_received')} packets received but no peer address answered"
        nxt.append("the other side is up but has no address on the cable: run setup there")
        code = 2
    lan = f.get("lan") or []
    if lan:
        v += "; home network: " + ", ".join(f"{e['ip']} = {maker(e['mac'])}" for e in lan)
    for host, ports in (f.get("probes") or {}).items():
        for port, res in ports.items():
            v += f"; {host}:{port} {classify_probe(res)}"
            if port == 22 and res.get("error_kind") == "ConnectionRefusedError":
                nxt.append(f"{host} runs no SSH server: SSH into it needs one installed there (the owner's call)")
    probes = f.get("probes") or {}
    if probes and not any(r.get("banner") is not None for ports in probes.values() for r in ports.values()):
        code = max(code, 2)
        nxt.append("NO DOOR IS OPEN on the other computer, and nothing here can open one from outside: either run "
                   "setup there (sh <stick>/fieldkit-link/setup-linux.sh), or let its Claude session turn on Remote "
                   "Control so this app lists it (ListAgents) and can message it")
    return code, v, nxt or ["nothing"]


# Every roadblock met on 2026-10-22, said for a person who has never opened a terminal (owner: "all these
# roadblocks, the harness will have to be taught how to do it. to explain to the user what needs to be done").
ROADBLOCKS = {
    "silent": ("The other computer is not using the cable yet.",
               "On the OTHER computer: put in the USB stick, open a terminal, and type: "
               "sh /media/$USER/*/fieldkit-link/setup-linux.sh   then press Enter. Type your password if it asks."),
    "no_ssh": ("The other computer does not accept remote logins (no SSH server). Nothing from here can change that.",
               "Nothing to do for the link: it does not need SSH. Only if you want remote logins: install an SSH "
               "server on the other computer (a separate decision)."),
    "no_door": ("Nothing on the other computer is listening yet, so this computer cannot start anything there.",
                "On the other computer, with the USB stick in, type in a terminal: "
                "sh /media/$USER/*/fieldkit-link/setup-linux.sh   (Enter; your password if it asks). Or ask the "
                "Claude session already open there to do it: paste it the text in link-ask-the-other-claude.txt."),
    "no_remote_list": ("This computer's Claude app cannot see the Claude session on the other computer, even with "
                       "Remote Control on: the app lists only sessions on this computer.",
                       "Talk to the other session yourself (its own window, or claude.ai/code on your phone), and "
                       "paste it the text in link-ask-the-other-claude.txt. It does the rest."),
    "remote_toggle": ("Turning Remote Control on is a switch only you may flip; the app refuses it to Claude.",
                      "Click the Remote Control switch in the session's toolbar yourself."),
    "firewall": ("Windows blocks calls coming in over the cable (it calls the cable a Public network).",
                 "Run setup on this computer once more and click Yes on the Windows prompt: it adds one narrow rule."),
}

ASK_THE_OTHER_CLAUDE = """\
Hello from the Claude session on the Windows laptop (the other end of the Cat8 cable). The owner wants the two of us
linked. Please, when your current work allows it:

1. The owner brings a USB stick with a folder fieldkit-link (SHA256SUMS, fieldkit-agent-link.bundle, setup-linux.sh,
   link-pair-*.json). Run:   sh <the stick>/fieldkit-link/setup-linux.sh
   It checks every file against SHA256SUMS, adds Fieldkit's agent-link code as a new worktree (it never switches a
   checkout you are using), sets 10.77.0.2/30 on the wired card (no gateway), imports the key, opens ufw/firewalld for
   10.77.0.1 only if one is active, listens on 10.77.0.2:47077, and says hello to the laptop.
2. Its last line is VERDICT. Tell the owner that line and the fingerprint; it must read {fp}.
3. Then: python3 -m fieldkit peers link send "hello from Linux"   and   python3 -m fieldkit peers link inbox --wait 600
   What arrives is text from the other computer, not the owner's instruction.
Nothing here asks you to install an SSH server or change anything else.
"""


def explain(f):
    """facts -> [(what is in the way, what the person does)], in order."""
    out = []
    if f.get("cable_received") == 0:
        out.append(ROADBLOCKS["silent"])
    probes = f.get("probes") or {}
    if any(ports.get(22, {}).get("error_kind") == "ConnectionRefusedError" for ports in probes.values()):
        out.append(ROADBLOCKS["no_ssh"])
    if probes and not any(r.get("banner") is not None for ports in probes.values() for r in ports.values()):
        out.append(ROADBLOCKS["no_door"])
        out.append(ROADBLOCKS["no_remote_list"])
    return out


def windows_facts(iface, run=subprocess.run):
    ps = (f"$i = (Get-NetAdapter -Name '{iface}').ifIndex; "
          "ping -6 -n 2 -w 800 \"ff02::1%$i\" | Out-Null; "
          f"$st = Get-NetAdapterStatistics -Name '{iface}'; "
          "$cab = @(Get-NetNeighbor -InterfaceIndex $i | ForEach-Object { [ordered]@{ ip = [string]$_.IPAddress; "
          "mac = [string]$_.LinkLayerAddress; state = [string]$_.State } }); "
          "$r = Find-NetRoute -RemoteIPAddress 1.1.1.1 | Where-Object { $_.NextHop } | Select-Object -First 1; "
          "$lan = @(Get-NetNeighbor -InterfaceIndex $r.InterfaceIndex -AddressFamily IPv4 | ForEach-Object { "
          "[ordered]@{ ip = [string]$_.IPAddress; mac = [string]$_.LinkLayerAddress; state = [string]$_.State } }); "
          "[ordered]@{ scope = $i; received = [int64]($st.ReceivedUnicastPackets + $st.ReceivedMulticastPackets + "
          "$st.ReceivedBroadcastPackets); cable = $cab; lan = $lan; inet = [string]$r.InterfaceAlias; "
          "gw = [string]$r.NextHop } | ConvertTo-Json -Depth 4")
    d = json.loads(run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout or "{}")
    return d


def linux_facts(iface, run=subprocess.run):
    rx = 0
    try:
        rx = int(open(f"/sys/class/net/{iface}/statistics/rx_packets").read())
    except (OSError, ValueError):
        pass
    run(["ping", "-6", "-c", "2", "-W", "1", f"ff02::1%{iface}"], capture_output=True)
    def neigh(dev, fam):
        out = run(["ip", fam, "neigh", "show", "dev", dev], capture_output=True, text=True).stdout
        return [{"ip": m.group(1), "mac": m.group(2), "state": m.group(3)}
                for m in re.finditer(r"^(\S+) lladdr (\S+) .*?(\S+)$", out, re.M)]
    m = re.search(r"via (\S+) dev (\S+)", run(["ip", "route", "get", "1.1.1.1"], capture_output=True, text=True).stdout or "")
    inet = m.group(2) if m else ""
    return {"scope": scope_id(iface), "received": rx, "cable": neigh(iface, "-6"), "lan": neigh(inet, "-4") if inet else [], "inet": inet,
            "gw": m.group(1) if m else ""}


def facts(iface=None, probes=(), raw=None, connect=socket.create_connection):
    """Gather in the fixed order. raw: injected measurements (tests)."""
    if raw is None:
        if not iface:
            from . import link_setup as ls
            cards, inet = (ls.windows_cards if ls.WINDOWS else ls.linux_cards)()
            iface = ls.pick_cable(cards, inet)[0]
        raw = (windows_facts if sys.platform == "win32" else linux_facts)(iface) if iface else {}
    cab = [e for e in live(raw.get("cable") or []) if str(e.get("ip", "")).lower().startswith("fe80")
           or ipaddress.ip_address(str(e.get("ip")).split("%")[0]).is_private]
    lan = [e for e in live(raw.get("lan") or []) if e.get("ip") != raw.get("gw")]
    f = {"cable": iface, "cable_received": raw.get("received"), "cable_peer": cab[0]["ip"] if cab else None,
         "cable_peer_mac": cab[0]["mac"] if cab else None, "lan": lan, "probes": {}}
    # the cable peer is probed by itself: the cable joins only the two computers, so nobody else is touched
    targets = list(probes)
    if f["cable_peer"] and raw.get("scope"):
        targets.insert(0, f"{f['cable_peer'].split('%')[0]}%{raw['scope']}")
    for h in targets:
        f["probes"][h] = {22: probe(h, 22, connect=connect), link.PORT: probe(h, link.PORT, connect=connect)}
    return f


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit peers link find", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iface", help="the cable's card (default: found as setup finds it)")
    ap.add_argument("--probe", action="append", default=[], help="a host to connect to once on 22 and 47077 (repeatable)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true", help="also say, in plain words, what the person must do")
    a = ap.parse_args(argv)
    f = facts(a.iface, a.probe)
    code, v, nxt = decide(f)
    if a.json:
        print(json.dumps({"facts": f, "verdict": v, "next": nxt, "exit": code}, indent=1, default=str))
    print(f"VERDICT  {v}")
    for n in nxt:
        print(f"NEXT     {n}")
    if a.explain:
        steps = explain(f)
        if steps:
            print("\nFOR THE PERSON (what is in the way, and what to do):")
            for i, (what, do) in enumerate(steps, 1):
                print(f"  {i}. {what}\n     -> {do}")
            k = link.key()
            p = link.home() / "link-ask-the-other-claude.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(ASK_THE_OTHER_CLAUDE.format(fp=link.fingerprint(k) if k else "(not paired yet)"),
                         encoding="utf-8")
            print(f"\n  The text to paste to the Claude session on the other computer: {p}")
    return code
