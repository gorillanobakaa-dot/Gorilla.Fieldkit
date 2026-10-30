#!/usr/bin/env python3
# Version: 1.5.0 · updated 26-10-28-09-30
"""macssh.py - reach an old Mac on the local network over SSH, find it when its address changes, and run things on it.

    fieldkit mac list                                  the Macs this machine knows (local/macs.json, never published)
    fieldkit mac add NAME --user U --key PATH --mdns NAME.local [--hw-addr AA:BB:..] [--note TEXT]
                                                       (no key yet: it prints the ssh-keygen line and the next step)
    fieldkit mac authorize NAME PUBKEY.pub             put another machine's PUBLIC key on the Mac (run where a
                                                       login already works); added once, never twice
    fieldkit mac doctor [NAME]                         key, address, login, macOS version, the ssh alias; exit 1 on a fault
    fieldkit mac find NAME [--save]                    its address now: mDNS name, then hardware address on the network
    fieldkit mac info NAME [--json]                    model, CPU, memory, macOS, disk, uptime, read from the Mac
    fieldkit mac fix-alias NAME [--ip] [--apply]       the "Host NAME" entry in ~/.ssh/config (old one kept in Trash)
    fieldkit mac run NAME -- COMMAND ...               run a command on the Mac; its exit code is this tool's
    fieldkit mac shot NAME [--out FILE]                the Mac's screen now (screencapture), fetched to this machine
    fieldkit mac keep-awake|open-app|screen-text|keys|wait-text|tui-start|watch-dialogs NAME ...   drive its screen like a person (macdrive.py)
    fieldkit mac sensors NAME [--json | --open]        temperatures, fan, battery, memory (sensors.gorilla, SMC, no
                                                       password); --open starts it live in a Terminal on the Mac
    fieldkit mac release-install NAME [--tag vX.Y.Z|latest] [--launch] [--plan]
                                                       install from the GitHub release page as a new user: older
                                                       copies quarantined, download, checksum, installer, version
                                                       check (releaseinstall.py)

Born 2026-10-22. The owner's MacBook Air 4,1 (2011, macOS 10.13.6, no longer supported by Apple) is a test target
of the Gorilla browser, reached from the Linux box over SSH. The way to reach it lived only in handover notes, and one
of them said the "ssh macair" alias was stale because the Mac's name had changed; tested, the alias worked. A home
network's mDNS answers some minutes and not others, and DHCP moves addresses: the tool therefore TESTS the connection
every time and, when the name does not resolve, finds the Mac by its hardware (MAC) address in the neighbour table,
after waking the network with a ping sweep. The owner's aim: old computers their makers no longer support get
support from this harness.

WINDOWS (2026-10-28, owner: the Windows laptop could not reach the Air): the same commands run on Windows with
Windows' own OpenSSH client (ssh.exe and scp.exe in System32/OpenSSH). The .local name goes through Windows' own
resolver (getaddrinfo, which answers mDNS on Windows 10 1803 and later), the neighbour table is `arp -a`, the networks
come from Get-NetIPAddress, the sweep pings with `ping -n 1 -w 1000`, and the key's permissions are read with icacls
(Windows' OpenSSH refuses a key that Everyone, Users or Authenticated Users can read), not from a POSIX mode.
~/.ssh/config is the .ssh folder under USERPROFILE. No sub-command needs Linux any more; a Mac as the CONTROLLING
machine is not tested.

PRIVATE DATA: names, addresses, hardware addresses and key paths live in local/macs.json (git-ignored). This code
holds none of them.
"""
import argparse
import hashlib
import datetime
import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "local" / "macs.json"
SSH_CONFIG = Path.home() / ".ssh" / "config"
TRASH = Path.home() / "Agents.Work.Trash"
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
WINDOWS = sys.platform == "win32"           # tests set it to drive both paths on any machine
OPENSSH_DIR = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH"
# who must NOT be able to read a private key on Windows (ssh.exe: "UNPROTECTED PRIVATE KEY FILE")
OPEN_ACL = ("Everyone", "BUILTIN\\Users", "NT AUTHORITY\\Authenticated Users")


def exe(name):
    """ssh / scp / ssh-keygen: on Windows the OpenSSH client that ships with Windows, by its full path (a Git or
    MSYS ssh earlier on PATH reads a different config and known_hosts); elsewhere the name, from PATH."""
    if WINDOWS:
        path = OPENSSH_DIR / f"{name}.exe"
        if path.exists():
            return str(path)
    return name


def _run(argv, timeout=20, runner=None):
    """(returncode, stdout+stderr). A missing program or a timeout is a failure, never an exception."""
    if runner:
        return runner(argv)
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, f"{argv[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: no answer in {timeout} s"


# ── the list of Macs (private) ──────────────────────────────────────────────

def load(path=CONFIG):
    """The registry. Missing is an explained refusal, never an empty store made up on the spot."""
    if not Path(path).exists():
        raise SystemExit(f"REFUSED: {path} does not exist, so this machine knows no Mac yet. Add one:\n"
                         f"  {self_cmd()} mac add NAME --user USER --key ~/.ssh/KEY --mdns NAME.local")
    return json.loads(Path(path).read_text()).get("macs", {})


def save(macs, path=CONFIG):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"_comment": "Private: never published (local/ is git-ignored). "
                                      "Written by fieldkit mac.", "macs": macs}, indent=1) + "\n")


def get(macs, name):
    if name not in macs:
        raise SystemExit(f"REFUSED: no Mac named {name!r}; known: {', '.join(sorted(macs)) or 'none'}")
    return macs[name]


# ── finding it ──────────────────────────────────────────────────────────────

def resolve_mdns(name, runner=None, resolve=None):
    """IPv4 address of a .local name through Avahi (Linux) or Windows' own resolver (getaddrinfo), or None."""
    if not name:
        return None
    if WINDOWS:
        import socket
        try:
            infos = (resolve or socket.getaddrinfo)(name, None, socket.AF_INET)
        except (OSError, UnicodeError):
            return None
        return infos[0][4][0] if infos else None
    rc, out = _run(["avahi-resolve", "-4", "-n", name], timeout=6, runner=runner)
    m = re.search(r"\s(\d+\.\d+\.\d+\.\d+)\s*$", out) if rc == 0 else None
    return m.group(1) if m else None


def neighbours(runner=None):
    """hardware address -> IPv4 address, from the kernel's neighbour table (ip neigh; on Windows arp -a)."""
    if WINDOWS:
        rc, out = _run(["arp", "-a"], timeout=5, runner=runner)
        table = {}
        for line in out.splitlines() if rc == 0 else []:
            m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})\s", line + " ")
            hw = m.group(2).lower().replace("-", ":") if m else ""
            if hw and set(hw.split(":")) != {"ff"} and not hw.startswith("01:00:5e"):     # broadcast, multicast
                table[hw] = m.group(1)
        return table
    rc, out = _run(["ip", "-4", "neigh", "show"], timeout=5, runner=runner)
    table = {}
    for line in out.splitlines() if rc == 0 else []:
        m = re.match(r"(\d+\.\d+\.\d+\.\d+) .*lladdr (\S+)", line)
        if m and "FAILED" not in line:
            table[m.group(2).lower()] = m.group(1)
    return table


def local_networks(runner=None):
    """IPv4 networks this machine is on (ip -4 addr; on Windows Get-NetIPAddress), largest /24 per interface to keep a
    sweep short. Loopback and link-local (169.254, no DHCP answer) are left out."""
    if WINDOWS:
        rc, out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { 'inet ' + $_.IPAddress + '/' + $_.PrefixLength }"],
                       timeout=15, runner=runner)
    else:
        rc, out = _run(["ip", "-4", "-o", "addr", "show", "scope", "global"], timeout=5, runner=runner)
    nets = []
    for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", out if rc == 0 else ""):
        if m.group(1).startswith(("127.", "169.254.")):
            continue
        net = ipaddress.ip_network(f"{m.group(1)}/{max(int(m.group(2)), 24)}", strict=False)
        if net not in nets:
            nets.append(net)
    return nets


def sweep(nets, runner=None, ping=None):
    """One ping to every address, so silent devices answer ARP and enter the neighbour table."""
    argv = ["ping", "-n", "1", "-w", "1000"] if WINDOWS else ["ping", "-c", "1", "-W", "1"]
    ping = ping or (lambda ip: _run(argv + [str(ip)], timeout=3, runner=runner))
    hosts = [h for n in nets for h in n.hosts()]
    with ThreadPoolExecutor(max_workers=64) as ex:
        list(ex.map(ping, hosts))
    return len(hosts)


def find(mac, runner=None, ping=None):
    """(address, how) for one Mac: its mDNS name, else its hardware address in the neighbour table, else the same
    after a ping sweep of the local networks. (None, why) when none of the three finds it."""
    ip = resolve_mdns(mac.get("mdns"), runner)
    if ip:
        return ip, f"mDNS name {mac['mdns']}"
    hw = (mac.get("hw_addr") or "").lower()
    if not hw:
        return None, f"{mac.get('mdns') or 'no mDNS name'} did not resolve and no hardware address is recorded " \
                     f"(add one with mac add ... --hw-addr)"
    table = neighbours(runner)
    if hw in table:
        return table[hw], f"hardware address {hw} in the neighbour table"
    n = sweep(local_networks(runner), runner, ping)
    table = neighbours(runner)
    if hw in table:
        return table[hw], f"hardware address {hw}, after waking {n} addresses"
    return None, f"not on the network: {mac.get('mdns')} did not resolve and {hw} did not answer among {n} addresses " \
                 f"(is it on, awake, and on this Wi-Fi?)"


# ── talking to it ───────────────────────────────────────────────────────────

def ssh_argv(mac, address, command=None, batch=True):
    argv = [exe("ssh"), "-o", "ConnectTimeout=8", "-o", f"HostKeyAlias={mac['host_key_alias']}",
            "-o", "IdentitiesOnly=yes", "-i", os.path.expanduser(mac["key"])]
    if batch:
        argv[1:1] = ["-o", "BatchMode=yes"]
    argv.append(f"{mac['user']}@{address}")
    if command:
        argv.append(command)
    return argv


def remote_line(words):
    """The line the Mac's shell runs. ONE argument is already a shell line ('a; b | c') and goes as it is; several
    are separate words and are quoted so each stays one word. (2026-10-22: quoting a single 'pgrep ...; ls ...'
    made the Mac look for a program of that whole name: exit 127.)"""
    return words[0] if len(words) == 1 else shlex.join(words)


def scp_argv(mac, address, remote, local, upload=False):
    """Fetch remote -> local, or with upload=True send local -> remote."""
    ends = [f"{mac['user']}@{address}:{remote}", local]
    return [exe("scp"), "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", f"HostKeyAlias={mac['host_key_alias']}",
            "-o", "IdentitiesOnly=yes", "-i", os.path.expanduser(mac["key"])] + (ends[::-1] if upload else ends)


def take_shot(mac, address, remote, out, runner=None, tries=3):
    """screencapture on the Mac, then fetch it. A home Wi-Fi drops SSH for seconds at a time (seen twice on
    2026-10-22): each step is tried up to three times before it counts as failed."""
    # caffeinate -u wakes a sleeping display; then the file must EXIST and be non-empty: with the display asleep
    # screencapture exits 0 and writes nothing (2026-10-22, the Air after 15 min idle)
    capture = (f"caffeinate -u -t 3 & sleep 2; screencapture -x {remote} && test -s {remote} "
               f"|| {{ echo 'screencapture wrote nothing (display asleep or locked?)'; exit 3; }}")
    for step, argv in (("capture", ssh_argv(mac, address, capture)),
                       ("fetch", scp_argv(mac, address, remote, out))):
        for attempt in range(tries):
            rc, msg = _run(argv, timeout=60, runner=runner)
            if rc == 0:
                break
        else:
            print(f"FAILED {step}: {msg}")
            return 1
    _run(ssh_argv(mac, address, f"rm -f {remote}"), timeout=20, runner=runner)
    print(f"{out}  (the Mac's screen; next: fieldkit shot grid {out})")
    return 0


INFO = ("sw_vers -productVersion; sysctl -n hw.model; sysctl -n machdep.cpu.brand_string; sysctl -n hw.memsize; "
        "df -h / | tail -1 | awk '{print $3 \" used of \" $2 \", \" $4 \" free\"}'; uptime")


def info(mac, address, runner=None):
    rc, out = _run(ssh_argv(mac, address, INFO), timeout=30, runner=runner)
    if rc != 0:
        return None, out
    lines = out.splitlines()
    keys = ("macos", "model", "cpu", "memory_bytes", "disk", "uptime")
    d = dict(zip(keys, lines))
    try:
        d["memory"] = f"{int(d.pop('memory_bytes')) / 2**30:.0f} GB"
    except (KeyError, ValueError):
        pass
    return d, ""


# ── the ssh alias ───────────────────────────────────────────────────────────

def alias_block(text, name):
    """(start, end) of the 'Host NAME' block in an ssh config, comments directly above it included; None if absent."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(rf"^\s*Host\s+(.*\s)?{re.escape(name)}(\s|$)", line):
            start = i
            while start > 0 and lines[start - 1].lstrip().startswith("#"):
                start -= 1
            end = i + 1
            while end < len(lines) and not re.match(r"^\s*(Host|Match)\s", lines[end]):
                end += 1
            return sum(len(l) for l in lines[:start]), sum(len(l) for l in lines[:end])
    return None


def new_alias(name, mac, hostname):
    return (f"# {mac.get('note') or name}: written by fieldkit mac fix-alias {datetime.date.today()}.\n"
            f"# HostKeyAlias keeps the host key valid whatever address the Mac has today.\n"
            f"Host {name}\n    HostName {hostname}\n    User {mac['user']}\n"
            f"    IdentityFile {os.path.expanduser(mac['key'])}\n    IdentitiesOnly yes\n"
            f"    HostKeyAlias {mac['host_key_alias']}\n\n")


def fix_alias(name, mac, hostname, ssh_config=SSH_CONFIG, trash=TRASH):
    """Replace or add the block; the old file is copied to Trash first. -> path of the kept copy or None."""
    ssh_config = Path(ssh_config)
    text = ssh_config.read_text() if ssh_config.exists() else ""
    kept = None
    if text:
        kept = Path(trash) / f"ssh-config-before-fieldkit-mac-{datetime.datetime.now():%y-%m-%d-%H-%M-%S}"
        kept.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ssh_config, kept)
    span = alias_block(text, name)
    block = new_alias(name, mac, hostname)
    text = text[:span[0]] + block + text[span[1]:] if span else (text.rstrip("\n") + "\n\n" if text else "") + block
    ssh_config.parent.mkdir(mode=0o700, exist_ok=True)
    ssh_config.write_text(text)
    if not WINDOWS:                     # on Windows the file inherits the profile's owner-only ACL
        os.chmod(ssh_config, 0o600)
    return kept


# ── doctor ──────────────────────────────────────────────────────────────────

def key_check(key, runner=None):
    """(ok, line) for the private key's permissions. POSIX: its mode. Windows: its ACL (icacls), since ssh.exe refuses
    a key Everyone, Users or Authenticated Users can read; the line gives the icacls fix."""
    if not WINDOWS:
        mode = key.stat().st_mode & 0o777
        return mode & 0o077 == 0, f"key {key} (mode {mode:o}{', ssh refuses a key others can read' if mode & 0o077 else ''})"
    rc, out = _run(["icacls", str(key)], timeout=10, runner=runner)
    if rc != 0:
        return False, f"key {key}: icacls could not read its permissions ({out.splitlines()[-1] if out else rc})"
    open_to = [who for who in OPEN_ACL if who.lower() in out.lower()]
    if open_to:
        user = os.environ.get("USERNAME", "%USERNAME%")
        return False, (f"key {key} can be read by {', '.join(open_to)}: ssh refuses it; fix: "
                       f"icacls \"{key}\" /inheritance:r /grant:r \"{user}:F\"")
    return True, f"key {key} (ACL: owner only)"


def doctor(name, mac, runner=None, ping=None, ssh_config=SSH_CONFIG):
    """[(ok, line)] for one Mac; every line says what was tested, not what was assumed."""
    rows = []
    key = Path(os.path.expanduser(mac["key"]))
    if not key.exists():
        rows.append((False, f"key {key} is missing"))
    else:
        rows.append(key_check(key, runner))
    address, how = find(mac, runner, ping)
    rows.append((bool(address), f"address {address} ({how})" if address else how))
    if not address:
        return rows
    rc, out = _run(ssh_argv(mac, address, "sw_vers -productVersion"), timeout=20, runner=runner)
    rows.append((rc == 0, f"login as {mac['user']}: macOS {out}" if rc == 0 else f"login failed: {out.splitlines()[-1] if out else rc}"))
    if Path(ssh_config).exists() and alias_block(Path(ssh_config).read_text(), name):
        rc, out = _run([exe("ssh"), "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", name, "true"], timeout=20, runner=runner)
        # A name the network cannot resolve (mDNS off on this Linux, 2026-10-23) is not fixed by writing the same
        # name again: point at today's address instead.
        ip = " --ip --apply" if "resolve hostname" in (out or "") else ""
        rows.append((rc == 0, f"alias 'ssh {name}' works" if rc == 0 else
                     f"alias 'ssh {name}' fails ({out.splitlines()[-1] if out else rc}): {self_cmd()} mac fix-alias {name}{ip}"))
    else:
        rows.append((False, f"no 'Host {name}' in {ssh_config}: {self_cmd()} mac fix-alias {name} --apply"))
    if mac.get("last_ip") and mac["last_ip"] != address:
        rows.append((True, f"note: the address was {mac['last_ip']} last time; {self_cmd()} mac find {name} --save"))
    return rows


# ── command line ────────────────────────────────────────────────────────────

def _mandate_log_run(name, cmd, rc, decide=None):
    """A command run on a Mac a project mandate grants (mac: {full_access: NAME}) is written to that mandate's log
    (2026-10-23, M3): the owner reads one log, not the session. No mandate, or one that does not grant it: nothing."""
    try:
        from ..mandate import mandate as M
        v, why, path = (decide or M.decide)("mac", name=name)
        if v == "allow" and path is not None:
            M.log(path, "mac-run", why, mac=name, cmd=" ".join(cmd)[:300], rc=rc)
            return True
    except Exception:  # noqa: BLE001 - logging must never change what the command returned
        pass
    return False


# sensors.gorilla (github gorillanobakaa-dot/sensors-gorilla, gathered into toolbox/) has a macOS side since 2.2.0
# (2026-10-23): the SMC read through IOKit, no password and nothing installed. One source for a person's dashboard and
# for the harness. The Air has no python3 on the SSH PATH, so the full path is used.
SENSORS = Path(__file__).resolve().parents[2] / "toolbox" / "sensors-gorilla" / "sensors.gorilla"
MAC_PYTHON = "/usr/local/bin/python3"
MAC_SENSORS = "bin/sensors.gorilla"            # relative to the Mac user's home


def sensors(mac, address, json_out=False, open_live=False, runner=None):
    """Put sensors.gorilla on the Mac (only when it differs), then one reading here, or the live view there."""
    if not SENSORS.exists():
        print(f"REFUSED: {SENSORS} is missing; get it with: {self_cmd()} gather --only sensors-gorilla")
        return 2
    want = hashlib.sha256(SENSORS.read_bytes()).hexdigest()
    rc, have = _run(ssh_argv(mac, address, f"shasum -a 256 {MAC_SENSORS} 2>/dev/null | cut -c1-64; "
                                           f"test -x {MAC_PYTHON} || echo NO-PYTHON"), timeout=30, runner=runner)
    if "NO-PYTHON" in have:
        print(f"REFUSED: no {MAC_PYTHON} on the Mac (python.org installer for macOS)")
        return 2
    if have.strip() != want:
        _run(ssh_argv(mac, address, "mkdir -p bin"), timeout=30, runner=runner)
        rc, out = _run(scp_argv(mac, address, MAC_SENSORS, str(SENSORS), upload=True), timeout=60, runner=runner)
        if rc != 0:
            print(f"could not copy sensors.gorilla to the Mac: {out}")
            return 1
    if open_live:
        # A new Terminal window sized to the screen; the 11-inch Air's maximised Terminal is 169 x 33 to 40 rows
        script = (f"osascript -e 'tell application \"Terminal\"' -e 'do script \"clear; {MAC_PYTHON} ~/{MAC_SENSORS}\"' "
                  f"-e 'activate' -e 'end tell'")
        rc, out = _run(ssh_argv(mac, address, script), timeout=60, runner=runner)
        print("live on the Mac's screen (Ctrl+C there to stop)" if rc == 0 else f"could not open Terminal: {out}")
        return rc
    cmd = f"{MAC_PYTHON} {MAC_SENSORS} --json" if json_out else f"stty cols 169 rows 33; {MAC_PYTHON} {MAC_SENSORS} --once"
    argv = ssh_argv(mac, address, cmd)
    if not json_out:
        argv[1:1] = ["-tt"]
    if runner:
        return runner(argv)[0]
    return subprocess.run(argv).returncode


PUBKEY_RE = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]+( [^\n]*)?$")


def public_key_line(text):
    """The one public-key line in a .pub file, or None. A private key (or anything else) is refused, never sent."""
    if "PRIVATE KEY" in text:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return lines[0] if len(lines) == 1 and PUBKEY_RE.match(lines[0]) else None


def authorize_line(pub):
    """The Mac's shell line that adds one public key to ~/.ssh/authorized_keys once (a second run adds nothing)."""
    q = shlex.quote(pub)
    return ("mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
            f"(grep -qxF {q} ~/.ssh/authorized_keys && echo 'already there' || {{ echo {q} >> ~/.ssh/authorized_keys && echo added; }})")


def keygen_hint(key, name="NAME"):
    """How to make the key a Mac entry names, on this system, and the step after it. Prints no key material."""
    k = Path(os.path.expanduser(key))
    return (f"no key at {k} yet. Make one (the private half never leaves this machine):\n"
            f"  {exe('ssh-keygen')} -t ed25519 -N \"\" -C fieldkit-mac -f {k}\n"
            f"then, on a machine that can already log in to the Mac, put the PUBLIC half ({k.name}.pub) on it:\n"
            f"  {self_cmd()} mac authorize {name} {k.name}.pub")


def self_cmd():
    try:
        from fieldkit.core.settings import self_cmd as sc
        return sc()
    except ImportError:
        return f"{sys.executable} {os.path.abspath(__file__)}"


def main(argv=None, prog="macssh.py"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0],
                                 epilog="\n".join(__doc__.split("\n\n")[1].splitlines()),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list", help="the Macs this machine knows")
    a = sp.add_parser("add", help="record a Mac (private file)")
    a.add_argument("name")
    a.add_argument("--user", required=True)
    a.add_argument("--key", required=True)
    a.add_argument("--mdns", required=True, help="its .local name")
    a.add_argument("--hw-addr", help="its Wi-Fi or Ethernet hardware address, to find it when mDNS is silent")
    a.add_argument("--host-key-alias", help="the name its host key is filed under (default: the mDNS name, lower case)")
    a.add_argument("--note", default="")
    d = sp.add_parser("doctor", help="key, address, login, the alias (exit 1 on a fault)")
    d.add_argument("name", nargs="?")
    f = sp.add_parser("find", help="its address now")
    f.add_argument("name")
    f.add_argument("--save", action="store_true", help="record the address as last_ip")
    i = sp.add_parser("info", help="what the Mac is: model, CPU, memory, macOS, disk")
    i.add_argument("name")
    i.add_argument("--json", action="store_true")
    x = sp.add_parser("fix-alias", help="the 'Host NAME' entry in ~/.ssh/config")
    x.add_argument("name")
    x.add_argument("--ip", action="store_true", help="write today's address instead of the mDNS name")
    x.add_argument("--apply", action="store_true")
    sh = sp.add_parser("shot", help="the Mac's screen now, fetched here (then fieldkit shot crop/stack/text-compare)")
    sh.add_argument("name")
    sh.add_argument("--out", help="where to put it (default: ./NAME-screen-<time>.png)")
    au = sp.add_parser("authorize", help="put another machine's PUBLIC key on the Mac (once)")
    au.add_argument("name")
    au.add_argument("pubkey", help="a .pub file (one line); a private key is refused")
    se = sp.add_parser("sensors", help="the Mac's sensors now (sensors.gorilla); --open: live on the Mac's screen")
    se.add_argument("name", nargs="?", default="macair")
    se.add_argument("--json", action="store_true")
    se.add_argument("--open", action="store_true")
    ri = sp.add_parser("release-install", help="install from the GitHub release page as a new user (old copies quarantined)")
    ri.add_argument("name")
    ri.add_argument("--product", default="opencode", help="which program (default: opencode)")
    ri.add_argument("--tag", default="latest", help="vX.Y.Z, or latest (default)")
    ri.add_argument("--launch", action="store_true", help="then double-click the Desktop icon and fetch the screen")
    ri.add_argument("--plan", action="store_true", help="print the script the Mac would run, run nothing")
    r = sp.add_parser("run", help="run a command on the Mac")
    r.add_argument("name")
    r.add_argument("command", nargs=argparse.REMAINDER)
    # 2026-10-23: driving the Mac's screen like a person (fieldkit/mac/macdrive.py)
    ka = sp.add_parser("keep-awake", help="caffeinate on the Mac for a long job")
    ka.add_argument("name")
    ka.add_argument("--minutes", type=float, default=120)
    ka.add_argument("--stop", action="store_true")
    oa = sp.add_parser("open-app", help="open an app, answer its folder dialog, close only new idle windows")
    oa.add_argument("name")
    oa.add_argument("app", help="an app name or path, e.g. '~/Applications/Gorilla OpenCode.app'")
    oa.add_argument("--folder", help="answer its 'choose folder' dialog with this folder")
    oa.add_argument("--process", help="the program it starts, to report its PID, tty and working folder")
    st = sp.add_parser("screen-text", help="the text in the Terminal tab on a tty (no picture)")
    st.add_argument("name")
    st.add_argument("--tty", required=True)
    st.add_argument("--lines", type=int, default=40)
    ky = sp.add_parser("keys", help="raise the tab on a tty and type a key or paste a file (checks it landed)")
    ky.add_argument("name")
    ky.add_argument("--tty", required=True)
    ky.add_argument("--key", help="enter, esc, up, down, tab, or one letter")
    ky.add_argument("--paste", help="a text file, sent as one line then Enter")
    ky.add_argument("--expect", help="type only if this pattern is on the screen first (never into an unknown screen)")
    ts = sp.add_parser("tui-start", help="walk Gorilla OpenCode's start-up screens to the prompt, a key only on a known screen")
    ts.add_argument("name")
    ts.add_argument("--tty", required=True)
    ts.add_argument("--seconds", type=float, default=180)
    wt = sp.add_parser("wait-text", help="wait until the tab shows a pattern")
    wt.add_argument("name")
    wt.add_argument("--tty", required=True)
    wt.add_argument("--for", dest="pattern", required=True)
    wt.add_argument("--seconds", type=float, default=120)
    wd = sp.add_parser("watch-dialogs", help="answer, count and log every permission dialog of a running Gorilla OpenCode")
    wd.add_argument("name")
    wd.add_argument("--tty", required=True)
    wd.add_argument("--answer", choices=["allow", "session", "deny"], default="allow")
    wd.add_argument("--until-file", help="stop when this file exists on the Mac (a report the run writes)")
    wd.add_argument("--seconds", type=float, default=3600)
    wd.add_argument("--every", type=float, default=15)
    wd.add_argument("--log", help="append one JSON line per dialog to this file")
    wd.add_argument("--nudges", type=int, default=2, help="type 'continue' after a turn that ended in an error, at most N times")
    wd.add_argument("--idle-seconds", type=float, default=900, help="stop when the screen has not changed this long")
    o = ap.parse_args(argv)

    if o.cmd == "add":
        macs = json.loads(CONFIG.read_text()).get("macs", {}) if CONFIG.exists() else {}
        hw = (o.hw_addr or "").lower()
        if hw and not MAC_RE.match(hw):
            print(f"REFUSED: {o.hw_addr} is not a hardware address (aa:bb:cc:dd:ee:ff)")
            return 2
        macs[o.name] = {"user": o.user, "key": o.key, "mdns": o.mdns, "hw_addr": hw,
                        "host_key_alias": o.host_key_alias or o.mdns.lower(), "note": o.note}
        save(macs)
        print(f"recorded {o.name} in {CONFIG}; next: {self_cmd()} mac doctor {o.name}")
        if not Path(os.path.expanduser(o.key)).exists():
            print(keygen_hint(o.key, o.name))
        return 0
    macs = load()
    if o.cmd == "list":
        for n, m in sorted(macs.items()):
            print(f"{n}: {m['user']}@{m['mdns']}  {m.get('note', '')}")
        return 0
    if o.cmd == "doctor":
        bad = 0
        for n in [o.name] if o.name else sorted(macs):
            print(f"== {n}")
            for ok, line in doctor(n, get(macs, n)):
                print(f"  {'ok   ' if ok else 'FAULT'} {line}")
                bad += not ok
        return 1 if bad else 0
    mac = get(macs, o.name)
    if o.cmd == "find":
        address, how = find(mac)
        print(f"{o.name}: {address} ({how})" if address else f"{o.name}: NOT FOUND, {how}")
        if address and o.save:
            mac["last_ip"] = address
            save(macs)
        return 0 if address else 1
    if o.cmd == "info":
        address, how = find(mac)
        if not address:
            print(f"NOT FOUND: {how}")
            return 1
        d, err = info(mac, address)
        if d is None:
            print(f"login failed: {err}")
            return 1
        print(json.dumps(d, indent=1) if o.json else "\n".join(f"  {k}: {v}" for k, v in d.items()))
        return 0
    if o.cmd == "fix-alias":
        address, how = find(mac)
        host = address if o.ip else mac["mdns"]
        if o.ip and not address:
            print(f"REFUSED: --ip needs the Mac on the network now ({how})")
            return 1
        print(new_alias(o.name, mac, host), end="")
        if not o.apply:
            print(f"plan only: {self_cmd()} mac fix-alias {o.name}{' --ip' if o.ip else ''} --apply")
            return 0
        kept = fix_alias(o.name, mac, host)
        print(f"written to {SSH_CONFIG}" + (f"; the previous file is kept at {kept}" if kept else ""))
        rc, _ = _run([exe("ssh"), "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", o.name, "true"], timeout=20)
        print(f"ssh {o.name}: {'works' if rc == 0 else 'still fails, run doctor'}")
        return 0 if rc == 0 else 1
    if o.cmd == "shot":
        address, how = find(mac)
        if not address:
            print(f"NOT FOUND: {how}")
            return 1
        remote = f"/tmp/fieldkit-shot-{datetime.datetime.now():%y%m%d%H%M%S}.png"
        out = o.out or f"{o.name}-screen-{datetime.datetime.now():%y-%m-%d-%H-%M-%S}.png"
        return take_shot(mac, address, remote, out)
    if o.cmd in ("keep-awake", "open-app", "screen-text", "keys", "wait-text", "tui-start", "watch-dialogs"):
        from . import macdrive
        address, how = find(mac)
        if not address:
            print(f"NOT FOUND: {how}")
            return 1
        fn = lambda line: ssh_argv(mac, address, line)
        if o.cmd == "keep-awake":
            code, msg = macdrive.keep_awake(fn, o.minutes, o.stop)
        elif o.cmd == "open-app":
            code, rows = macdrive.open_app(fn, o.app, o.folder, o.process)     # "~" is the Mac's home, kept for it
            msg = "\n".join(rows)
        elif o.cmd == "screen-text":
            rows, err = macdrive.screen_text(fn, o.tty, o.lines)
            code, msg = (0, "\n".join(rows)) if rows is not None else (1, err)
        elif o.cmd == "keys":
            if bool(o.key) == bool(o.paste):
                print("keys: give --key or --paste")
                return 2
            text = Path(o.paste).read_text(encoding="utf-8") if o.paste else None
            code, msg = macdrive.keys(fn, o.tty, o.key, text, expect=o.expect)
        elif o.cmd == "tui-start":
            code, rows = macdrive.tui_start(fn, o.tty, o.seconds)
            msg = "\n".join(rows)
        elif o.cmd == "watch-dialogs":
            code, rows = macdrive.watch_dialogs(fn, o.tty, o.answer, o.until_file, o.seconds, o.every, o.log, o.nudges, o.idle_seconds)
            msg = "\n".join(rows)
        else:
            code, msg = macdrive.wait_text(fn, o.tty, o.pattern, o.seconds)
        print(msg)
        return code
    if o.cmd == "authorize":
        pub = public_key_line(Path(o.pubkey).read_text(encoding="utf-8", errors="replace"))
        if not pub:
            print(f"REFUSED: {o.pubkey} is not one public-key line (ssh-ed25519 ... / ssh-rsa ...); nothing sent")
            return 2
        address, how = find(mac)
        if not address:
            print(f"NOT FOUND: {how}")
            return 1
        rc, out = _run(ssh_argv(mac, address, authorize_line(pub)), timeout=30)
        print(f"{o.name}: {out}" if rc == 0 else f"FAILED: {out}")
        return 0 if rc == 0 else 1
    if o.cmd == "sensors":
        address, how = find(mac)
        if not address:
            print(f"NOT FOUND: {how}")
            return 1
        return sensors(mac, address, json_out=o.json, open_live=o.open)
    if o.cmd == "release-install":
        from . import releaseinstall
        if o.plan:
            return releaseinstall.main(o, mac, None, ssh_argv, take_shot)
        address, how = find(mac)
        if not address:
            print(f"NOT FOUND: {how}")
            return 1
        return releaseinstall.main(o, mac, address, ssh_argv, take_shot)
    if o.cmd == "run":
        cmd = o.command[1:] if o.command[:1] == ["--"] else o.command
        if not cmd:
            print("REFUSED: nothing to run (fieldkit mac run NAME -- COMMAND)")
            return 2
        address, how = find(mac)
        if not address:
            print(f"NOT FOUND: {how}", file=sys.stderr)
            return 1
        rc = run_retry(ssh_argv(mac, address, remote_line(cmd), batch=False))   # a Wi-Fi drop retried (main)
        _mandate_log_run(o.name, cmd, rc)                 # under a mandate's mac grant: in its log (this branch)
        return rc
    return 2


def run_retry(argv, tries=4, wait=6, runner=None, sleep=None):
    """Run an ssh command, retrying ONLY a Wi-Fi drop (exit 255 with a connection error). 2026-10-23: the Air dropped
    off Wi-Fi again and again; `mac run` was the one Mac command without a retry (the screen commands had one)."""
    import time
    from .macdrive import DROP
    sleep = sleep or time.sleep
    for attempt in range(1, tries + 1):
        r = (runner or (lambda a: subprocess.run(a, capture_output=True, text=True)))(argv)
        out, err = r.stdout or "", r.stderr or ""
        if r.returncode == 255 and any(d in err for d in DROP) and attempt < tries:
            sleep(wait)
            continue
        sys.stdout.write(out)
        sys.stderr.write(err)
        return r.returncode
    return 255


if __name__ == "__main__":
    sys.exit(main())
