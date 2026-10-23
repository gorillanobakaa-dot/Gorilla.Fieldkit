"""One command per computer for the agent link: the person plugs the cable, carries a USB stick, types the admin
password once and "yes" once; the tool does the rest.

    Windows (or the first computer):  fieldkit peers link setup --first [--usb E:\\]
    Linux (or the second computer):   sh <stick>/fieldkit-link/setup-linux.sh       (runs: setup --join <stick dir>)

Owner, 2026-10-22: "what happens if the other side, the user has no idea how to do that. the fieldkit was supposed
to be able to set up the whole shebang both from the windows side and from the linux side."

Steps, each checked first and skipped when already true (run it again any time; it repairs, never duplicates):
  1 cable port   the wired card with a link and NOT carrying the internet (the default route); none or two: stop
  2 address      10.77.0.1/30 (first) or 10.77.0.2/30 (join); no gateway, no DNS. Windows: one UAC click (elevate
                 kit); Linux: NetworkManager (nmcli, pkexec when refused), else `ip addr add` (until reboot, said so)
  3 key          first: a new key + pairing file; join: the pairing file from the stick (fingerprint printed)
  4 firewall     Windows: an inbound rule for TCP 47077 from the peer on that card only (same UAC click as step 2);
                 Linux: ufw or firewalld when one is active, the same narrow rule; none active: nothing to open
  5 usb kit      first + --usb: the code (git bundle), the pairing file, setup-linux.sh, SHA256SUMS
  6 listen       `peers link serve` in the background, if it is not already listening
  7 check        doctor; join: hello to the first computer, which proves the whole path
Starting sessions on this computer stays off until its owner runs `fieldkit peers link approve` (typed yes).
"""
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import link

FIRST, SECOND = "10.77.0.1", "10.77.0.2"
PREFIX = 30
RULE = "Fieldkit peers link (cable only)"
WINDOWS = os.name == "nt"


def say(msg):
    print(msg, flush=True)


# ---- 1. which card is the cable --------------------------------------------------------------------------------
def pick_cable(cards, internet_iface):
    """cards: [{name, wired, up, addresses}] -> (name, why) or (None, why). The cable card is wired, has a link, and
    is not the card the internet leaves through (2026-10-22: the laptop's Ethernet carried the internet until the
    Wi-Fi took over; the tool must never re-address the internet card)."""
    cands = [c for c in cards if c.get("wired") and c.get("up") and c.get("name") != internet_iface]
    if not cands:
        busy = [c["name"] for c in cards if c.get("wired") and c.get("up")]
        return None, ("no wired card with a link besides the internet one" +
                      (f" ({', '.join(busy)} carries the internet: move the internet to Wi-Fi first, "
                       "`wifiradios ensure`)" if busy else ": is the cable plugged in at both ends?"))
    if len(cands) > 1:
        return None, f"two or more wired cards have a link ({', '.join(c['name'] for c in cands)}): name one with --iface"
    return cands[0]["name"], f"{cands[0]['name']}: wired, link up, not the internet route"


def windows_cards(run=subprocess.run):
    ps = ("Get-NetAdapter -Physical | Where-Object { $_.MediaType -eq '802.3' } | ForEach-Object { "
          "[ordered]@{ name = $_.Name; wired = $true; up = ($_.Status -eq 'Up'); addresses = @(Get-NetIPAddress "
          "-InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { "
          "\"$($_.IPAddress)/$($_.PrefixLength)\" }) } } | ConvertTo-Json -Depth 3")
    r = run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    d = json.loads(r.stdout or "[]")
    cards = d if isinstance(d, list) else [d]
    rt = run(["powershell", "-NoProfile", "-Command",
              "(Find-NetRoute -RemoteIPAddress 1.1.1.1 | Where-Object { $_.NextHop } | Select-Object -First 1).InterfaceAlias"],
             capture_output=True, text=True)
    return cards, (rt.stdout or "").strip()


def linux_cards(sysfs="/sys", run=subprocess.run):
    cards = []
    for d in sorted(Path(sysfs, "class", "net").iterdir()) if Path(sysfs, "class", "net").is_dir() else []:
        if d.name == "lo" or (d / "wireless").is_dir() or not (d / "device").exists():
            continue
        carrier = (d / "carrier").read_text().strip() if (d / "carrier").is_file() else "0"
        cards.append({"name": d.name, "wired": True, "up": carrier == "1", "addresses": []})
    out = run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True).stdout
    for c in cards:
        c["addresses"] = re.findall(rf"\b{re.escape(c['name'])}\s+inet\s+(\S+)", out)
    m = re.search(r"dev (\S+)", run(["ip", "route", "get", "1.1.1.1"], capture_output=True, text=True).stdout or "")
    return cards, m.group(1) if m else ""


# ---- 2 + 4. address and firewall ---------------------------------------------------------------------------------
def has_address(card, ip):
    return any(a.split("/")[0] == ip for a in card.get("addresses") or [])


def windows_admin_script(iface, ip, peer, need_ip, need_rule):
    q = "'" + iface.replace("'", "''") + "'"
    L = ["$ErrorActionPreference = 'Stop'"]
    if need_ip:
        L += [f"try {{ Set-NetIPInterface -InterfaceAlias {q} -Dhcp Disabled",
              f"  Get-NetIPAddress -InterfaceAlias {q} -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
              "Where-Object { $_.PrefixOrigin -ne 'WellKnown' } | Remove-NetIPAddress -Confirm:$false",
              f"  New-NetIPAddress -InterfaceAlias {q} -IPAddress {ip} -PrefixLength {PREFIX} | Out-Null",
              f"  Set-DnsClientServerAddress -InterfaceAlias {q} -ResetServerAddresses",
              f"  Write-Host 'LINKSETUP: ADDRESS {ip}' }} catch {{ Write-Host \"LINKSETUP: FAILED address: $_\" }}"]
    if need_rule:
        L += [f"try {{ New-NetFirewallRule -DisplayName '{RULE}' -Direction Inbound -Protocol TCP -LocalPort "
              f"{link.PORT} -LocalAddress {ip} -RemoteAddress {peer} -InterfaceAlias {q} -Action Allow -Profile Any "
              f"| Out-Null; Write-Host 'LINKSETUP: FIREWALL' }} catch {{ Write-Host \"LINKSETUP: FAILED firewall: $_\" }}"]
    return "\n".join(L) + "\n"


def windows_rule_exists(run=subprocess.run):
    r = run(["powershell", "-NoProfile", "-Command",
             f"if (Get-NetFirewallRule -DisplayName '{RULE}' -ErrorAction SilentlyContinue) {{ 'yes' }}"],
            capture_output=True, text=True)
    return "yes" in (r.stdout or "")


def linux_address_plan(iface, ip, nmcli=True):
    if nmcli:
        name = "fieldkit-link"
        return [["nmcli", "connection", "add", "type", "ethernet", "ifname", iface, "con-name", name,
                 "ipv4.method", "manual", "ipv4.addresses", f"{ip}/{PREFIX}", "ipv4.never-default", "yes",
                 "ipv4.ignore-auto-dns", "yes", "ipv6.method", "link-local", "connection.autoconnect", "yes"],
                ["nmcli", "connection", "up", name]], "NetworkManager connection fieldkit-link (kept across reboots)"
    return [["ip", "addr", "add", f"{ip}/{PREFIX}", "dev", iface], ["ip", "link", "set", iface, "up"]], \
        "ip addr add (NOT kept across a reboot: run setup again after one)"


def linux_firewall_plan(active, iface, ip, peer):
    """active: 'ufw' | 'firewalld' | ''. -> commands (empty: nothing filters, nothing to open)."""
    if active == "ufw":
        return [["ufw", "allow", "in", "on", iface, "from", peer, "to", ip, "port", str(link.PORT), "proto", "tcp"]]
    if active == "firewalld":
        rule = (f"rule family=ipv4 source address={peer}/32 destination address={ip}/32 port port={link.PORT} "
                "protocol=tcp accept")
        return [["firewall-cmd", "--permanent", f"--add-rich-rule={rule}"], ["firewall-cmd", "--reload"]]
    return []


def linux_firewall_active(run=subprocess.run):
    for name, cmd in (("ufw", ["ufw", "status"]), ("firewalld", ["firewall-cmd", "--state"])):
        if shutil.which(cmd[0]):
            r = run(cmd, capture_output=True, text=True)
            if re.search(r"Status: active|^running", (r.stdout or ""), re.M):
                return name
            if "permission" in (r.stderr or "").lower() or r.returncode not in (0, 1, 252):
                r = run(["pkexec", *cmd], capture_output=True, text=True)
                if re.search(r"Status: active|^running", (r.stdout or ""), re.M):
                    return name
    return ""


def run_root(cmds, run=subprocess.run):
    """Each command as this user first; refused -> once more through pkexec (the owner's password prompt)."""
    for c in cmds:
        r = run(c, capture_output=True, text=True)
        if r.returncode != 0 and re.search(r"not permitted|denied|authoriz|privilege|root", (r.stderr or r.stdout or ""), re.I):
            r = run(["pkexec", *c], capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"{' '.join(c[:4])}...: {(r.stderr or r.stdout).strip()[:200]}"
    return True, "ok"


# ---- 3 + 5. key and the USB kit -------------------------------------------------------------------------------
def removable_roots():
    if os.name == "nt":
        import string
        import ctypes
        out = []
        for L in string.ascii_uppercase:
            root = f"{L}:\\"
            try:
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 2:      # DRIVE_REMOVABLE
                    out.append(Path(root))
            except Exception:  # noqa: BLE001
                pass
        return out
    user = os.environ.get("USER", "")
    return [p for base in (f"/media/{user}", f"/run/media/{user}", "/media", "/mnt") if Path(base).is_dir()
            for p in Path(base).iterdir() if p.is_dir()]


def find_pair_file(dirs):
    hits = []
    for d in dirs:
        d = Path(d)
        for p in [*d.glob("link-pair-*.json"), *d.glob("fieldkit-link/link-pair-*.json")]:
            hits.append(p)
    return hits


SETUP_SH = r"""#!/bin/sh
# Fieldkit agent link, the second computer: ONE command. Written by `fieldkit peers link setup --first --usb`.
# It checks every file against SHA256SUMS, puts the code next to an existing Fieldkit (a new worktree, never touching
# a busy checkout) or clones it, then runs `fieldkit peers link setup --join` with this folder.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
sha256sum -c SHA256SUMS || { echo "LINKSETUP: REFUSED - a file on the stick does not match SHA256SUMS"; exit 3; }
FK="${FIELDKIT_HOME:-$HOME/Documents/Scripts/Fieldkit}"
if [ -d "$FK/.git" ]; then
  git -C "$FK" fetch "$HERE/fieldkit-agent-link.bundle" "+feat/agent-link:refs/remotes/usb/agent-link"
  WT="$FK-agent-link"
  if [ -d "$WT" ]; then git -C "$WT" merge --ff-only usb/agent-link; else git -C "$FK" worktree add -b agent-link-usb "$WT" usb/agent-link; fi
else
  git clone -b feat/agent-link "$HERE/fieldkit-agent-link.bundle" "$FK"
  WT="$FK"
fi
cd "$WT"
exec python3 -m fieldkit peers link setup --join "$HERE"
"""


def write_usb_kit(dest, bundle_from, pair_file, run=subprocess.run):
    """-> list of files written into <dest>/fieldkit-link."""
    import hashlib
    d = Path(dest) / "fieldkit-link"
    d.mkdir(parents=True, exist_ok=True)
    b = d / "fieldkit-agent-link.bundle"
    r = run(["git", "-C", str(bundle_from), "bundle", "create", str(b), "feat/agent-link"], capture_output=True, text=True)
    if r.returncode != 0 or not b.is_file():
        raise RuntimeError(f"git bundle failed: {(r.stderr or '').strip()[:200]}")
    shutil.copy2(pair_file, d / Path(pair_file).name)
    (d / "setup-linux.sh").write_bytes(SETUP_SH.encode("utf-8"))           # LF, as sh needs
    sums = []
    for f in sorted(p for p in d.iterdir() if p.is_file() and p.name != "SHA256SUMS"):
        sums.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
    (d / "SHA256SUMS").write_bytes(("\n".join(sums) + "\n").encode("utf-8"))
    return sorted(p.name for p in d.iterdir())


# ---- 6. listen ----------------------------------------------------------------------------------------------------
def listening(ip, port=link.PORT):
    try:
        socket.create_connection((ip, port), timeout=2).close()
        return True
    except OSError:
        return False


def start_serve(popen=subprocess.Popen):
    log = open(link.home() / "serve.log", "a", encoding="utf-8")
    flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)) if os.name == "nt" else 0
    p = popen([sys.executable, "-m", "fieldkit", "peers", "link", "serve"], stdout=log, stderr=subprocess.STDOUT,
              stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=(os.name != "nt"),
              env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2])))
    (link.home() / "serve.pid").write_text(str(p.pid))
    return p.pid


# ---- the whole thing -------------------------------------------------------------------------------------------
def setup(first=False, join=None, usb=None, iface=None, run=subprocess.run, popen=subprocess.Popen, cards_fn=None,
          elevate_fn=None, root_fn=None, firewall_fn=None, roots_fn=removable_roots, wait_listen=10):
    """-> (exit, VERDICT, NEXT)."""
    win = WINDOWS
    me, peer = (FIRST, SECOND) if first else (SECOND, FIRST)
    pair_src = None
    if join:
        found = find_pair_file([join] if join != "auto" else roots_fn())
        if not found:
            return 2, f"NO PAIRING FILE in {join if join != 'auto' else 'any USB stick'}", \
                "plug in the stick from the first computer (it holds fieldkit-link/link-pair-*.json)"
        pair_src = found[0]
        d = json.loads(pair_src.read_text(encoding="utf-8"))
        me, peer = d["your_address"], d["my_address"]
    say(f"STEP 0  this computer: {socket.gethostname()} ({'Windows' if win else 'Linux'}), cable address {me}, peer {peer}")
    cards, inet = (cards_fn or (windows_cards if win else linux_cards))()
    name, why = (iface, f"{iface} (named)") if iface else pick_cable(cards, inet)
    if not name:
        return 2, f"NO CABLE PORT: {why}", "plug the cable in at both ends, or pass --iface NAME"
    card = next((c for c in cards if c["name"] == name), {"name": name, "addresses": []})
    say(f"STEP 1  cable port {why}; the internet leaves via {inet or 'nothing'}")
    need_ip = not has_address(card, me)
    if win:
        need_rule = not windows_rule_exists(run)
        if need_ip or need_rule:
            say(f"STEP 2  {'address ' + me if need_ip else ''}{' + ' if need_ip and need_rule else ''}"
                f"{'firewall rule' if need_rule else ''}: one UAC prompt (click Yes)")
            rc, log = (elevate_fn or _elevate)(windows_admin_script(name, me, peer, need_ip, need_rule))
            if "LINKSETUP: FAILED" in log or rc != 0:
                bad = [l for l in log.splitlines() if "FAILED" in l] or [log[-200:]]
                return 3, f"ADMIN STEP FAILED: {bad[0].strip()}", "run setup again; if it repeats, the transcript is in the elevate results folder"
        else:
            say(f"STEP 2  address {me} and the firewall rule are already in place")
    else:
        if need_ip:
            plan, how = linux_address_plan(name, me, nmcli=bool(shutil.which("nmcli")))
            say(f"STEP 2  address {me}/{PREFIX} on {name} via {how} (the password prompt, if one appears, is the system's)")
            ok, why2 = (root_fn or run_root)(plan)
            if not ok:
                return 3, f"ADDRESS NOT SET: {why2}", f"run setup again; or by hand: sudo ip addr add {me}/{PREFIX} dev {name}"
        else:
            say(f"STEP 2  address {me} already on {name}")
        active = (firewall_fn or linux_firewall_active)()
        fw = linux_firewall_plan(active, name, me, peer)
        if fw:
            ok, why2 = (root_fn or run_root)(fw)
            say(f"STEP 4  firewall ({active}): " + ("opened TCP %d from %s on %s" % (link.PORT, peer, name) if ok else why2))
            if not ok:
                return 3, f"FIREWALL NOT OPENED ({active}): {why2}", "run setup again"
        else:
            say("STEP 4  no active firewall (ufw/firewalld): nothing to open")
    cfg, k = link.config(), link.key()
    if join:
        fp = link.pair_import(pair_src)
        say(f"STEP 3  key imported from {pair_src}: fingerprint {fp} (it must match the first computer's screen)")
    elif not (cfg and k and cfg.get("me") == me and cfg.get("peer") == peer):
        pf, fp = link.pair_init(me, peer)
        say(f"STEP 3  new key: fingerprint {fp}")
    else:
        fp = link.fingerprint(k)
        say(f"STEP 3  key already here: fingerprint {fp}")
    if first and usb:
        pf = next(iter(sorted(link.home().glob("link-pair-*.json"))), None)
        files = write_usb_kit(usb, Path(__file__).resolve().parents[2], pf, run=run)
        say(f"STEP 5  USB kit written to {Path(usb) / 'fieldkit-link'}: {', '.join(files)}")
    if listening(me):
        say(f"STEP 6  already listening on {me}:{link.PORT}")
    else:
        pid = start_serve(popen)
        for _ in range(wait_listen * 4):
            if listening(me):
                break
            time.sleep(0.25)
        if not listening(me):
            return 3, f"NOT LISTENING: serve (PID {pid}) did not open {me}:{link.PORT}", f"read {link.home() / 'serve.log'}"
        say(f"STEP 6  listening on {me}:{link.PORT} (PID {pid}, log {link.home() / 'serve.log'})")
    if join:
        r = link.call({"verb": "hello"})
        if not r.get("ok"):
            return 3, f"NO ANSWER FROM {peer}: {r.get('error')}", "is setup --first done and listening on the first computer?"
        say(f"STEP 7  hello from {r['host']}: " + ", ".join(a["agent"] for a in r["agents"] if a.get("installed") or a.get("running")))
        return 0, f"LINKED: {socket.gethostname()} ({me}) <-> {r['host']} ({peer}), fingerprint {fp}", \
            "to let the other computer start a Claude session here: fieldkit peers link approve (the owner types yes)"
    nxt = (f"carry the stick to the other computer and run there: sh <stick>/fieldkit-link/setup-linux.sh"
           if usb else "run again with --usb <stick folder> to write the kit for the other computer")
    return 0, f"READY: {socket.gethostname()} listens on {me} for {peer}, fingerprint {fp}", nxt


def _elevate(script):
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    for kit in (root / "local" / "kits" / "Elevate" / "elevate.py",
                Path.home() / "Documents" / "Scripts" / "Fieldkit" / "local" / "kits" / "Elevate" / "elevate.py"):
        if kit.is_file():
            res = link.home() / "admin"
            res.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            sp, log = res / f"setup-{stamp}.ps1", res / f"setup-{stamp}.log"
            sp.write_text(script, encoding="utf-8-sig")
            subprocess.run([sys.executable, str(kit), "run", str(sp), "--log", str(log), "--close"], capture_output=True)
            w = subprocess.run([sys.executable, str(kit), "wait", str(log), "--marker", "ELEVATE: script finished",
                                "--timeout", "10"], capture_output=True, text=True)
            return w.returncode, log.read_text(encoding="utf-8", errors="replace") if log.is_file() else w.stdout
    return 3, "the elevate kit is not on this computer (local/kits/Elevate)"


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit peers link setup", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--first", action="store_true", help="this computer starts the pair (10.77.0.1)")
    g.add_argument("--join", metavar="DIR", help="the folder holding link-pair-*.json (the stick's fieldkit-link), or auto")
    ap.add_argument("--usb", help="--first: write the kit for the other computer into this folder (the stick)")
    ap.add_argument("--iface", help="the cable's card, when the tool cannot tell")
    a = ap.parse_args(argv)
    code, verdict, nxt = setup(first=a.first, join=a.join, usb=a.usb, iface=a.iface)
    say(f"VERDICT  {verdict}\nNEXT     {nxt}")
    return code
