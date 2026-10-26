# Version: 1.0.0 · updated 26-10-26-15-00
"""fieldkit peers link heal: why a device does not answer, layer by layer, and the declared ways to bring it back.

    fieldkit peers link heal [--device NAME] [--json]        the ladder, read-only; one VERDICT line and NEXT
    fieldkit peers link heal --revive [--device NAME]        the ladder, then each DECLARED revive path in order,
                                                             then the ladder again; nothing works -> one inbox item
    fieldkit peers link heal declare NAME rescue,ssh,wol [--mac MAC] [--ssh-user U] [--ssh-key FILE]
                                                             which revive paths this device has (household.json)

2026-10-26 13:4x, the incident it is built from: `orchestrate devices --check` said the VAIO gave NO ANSWER
(WinError 10061). By hand: ping 3/3, ARP 02-00-5e-10-00-01, TCP 47077 and 22 REFUSED, the last VAIO line in link.log
at 00:37. The cable and the VAIO's kernel were up; nothing listened; there was no second door. The owner (a mile-long
warehouse of devices must never need a person walking to a screen): why is the connection refused, can we read the
logs, is the link up, can we revive it, how do we keep it alive. Each rung below answers one of those with evidence.

The rungs: 1 interface (this computer holds its cable address), 2 neighbor (ARP / ip neigh), 3 ping (count, loss,
rtt), 4 TCP to the link port (OPEN, REFUSED = host up and nothing listening, TIMEOUT = host or path down or a
firewall dropping, UNREACHABLE = no route), 5 the app's signed hello, 6 logs (here: link.log, serve.log,
watchdog.log; on the peer, through its rescue door when that answers).
Revive paths, each only if the device declares it: rescue (the rescue door, link_rescue.py), ssh (key-only, forced
command, cable address), wol (a magic packet on the cable). Then the owner's inbox, naming the machine and command.
Exit 0 the device answers, 3 it does not (VERDICT names the layer), 2 bad usage.
"""
import errno
import json
import re
import socket
import subprocess
import sys
import time

from . import link

OK, PROBLEM, SKIP = "OK", "PROBLEM", "SKIP"
REVIVE = ("rescue", "ssh", "wol")

REFUSED_CODES = {errno.ECONNREFUSED, 10061}
TIMEOUT_CODES = {errno.ETIMEDOUT, 10060}
UNREACH_CODES = {errno.EHOSTUNREACH, errno.ENETUNREACH, 10065, 10051, 1231}


def rung(name, verdict, detail, **data):
    return {"rung": name, "verdict": verdict, "detail": detail, **data}


# ---- parsers (pure: the tests feed them the 2026-10-26 output) ----------------------------------------------------
def parse_ping(text):
    """-> {sent, received, loss, rtt_ms} from Windows `ping -n` or Linux/macOS `ping -c` output (None if unreadable)."""
    t = text or ""
    m = re.search(r"Sent = (\d+), Received = (\d+), Lost = \d+ \((\d+)% loss\)", t)
    if m:
        avg = re.search(r"Average = (\d+)ms", t)
        return {"sent": int(m[1]), "received": int(m[2]), "loss": int(m[3]),
                "rtt_ms": float(avg[1]) if avg else None}
    m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received.*?([\d.]+)% packet loss", t)
    if m:
        avg = re.search(r"(?:rtt|round-trip) [\w/]+ = [\d.]+/([\d.]+)/", t)
        return {"sent": int(m[1]), "received": int(m[2]), "loss": round(float(m[3])),
                "rtt_ms": float(avg[1]) if avg else None}
    return None


def parse_neighbor(text, ip):
    """-> {mac, state} for ip from `arp -a` (Windows) or `ip neigh show` (Linux); state REACHABLE|STALE|FAILED|...
    A Windows 00-00-00-00-00-00 entry is the stale entry of 2026-10-22: state FAILED."""
    for line in (text or "").splitlines():
        f = line.split()
        if not f or f[0] != ip:
            continue
        if len(f) >= 3 and re.fullmatch(r"([0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2}", f[1]):
            mac = f[1].lower().replace("-", ":")
            return {"mac": mac, "state": "FAILED" if mac == "00:00:00:00:00:00" else f[2].upper()}
        if "lladdr" in f:
            return {"mac": f[f.index("lladdr") + 1].lower(), "state": f[-1].upper()}
        return {"mac": None, "state": f[-1].upper()}
    return None


def classify(exc):
    """A connect() failure -> REFUSED | TIMEOUT | UNREACHABLE | ERROR."""
    if exc is None:
        return "OPEN"
    code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    if isinstance(exc, ConnectionRefusedError) or code in REFUSED_CODES:
        return "REFUSED"
    if isinstance(exc, (socket.timeout, TimeoutError)) or code in TIMEOUT_CODES:
        return "TIMEOUT"
    if code in UNREACH_CODES:
        return "UNREACHABLE"
    return "ERROR"


# ---- probes (each one replaceable in the tests) -------------------------------------------------------------------
def _run(argv, timeout=20):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def probe_ping(ip, count=3, run=_run, platform=sys.platform):
    argv = ["ping", "-n", str(count), "-w", "1000", ip] if platform == "win32" else \
        ["ping", "-c", str(count), "-W", "1", ip]
    return run(argv)[1]


def probe_neighbor(ip, run=_run, platform=sys.platform):
    return run(["arp", "-a"] if platform == "win32" else ["ip", "neigh", "show", ip])[1]


def probe_tcp(ip, port, timeout=3, src=None, connect=socket.create_connection):
    t0 = time.time()
    try:
        connect((ip, port), timeout=timeout, source_address=(src, 0) if src else None).close()
        return "OPEN", None, time.time() - t0
    except OSError as e:
        return classify(e), e, time.time() - t0


def last_lines(path, n=5, match=None):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if match:
        lines = [x for x in lines if any(m and m in x for m in match)]
    return lines[-n:]


def _hosts():
    try:
        return json.loads((link.home() / "heal-hosts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_host(name, host):
    h = _hosts()
    h[name] = host
    link.home().mkdir(parents=True, exist_ok=True)
    (link.home() / "heal-hosts.json").write_text(json.dumps(h, indent=1), encoding="utf-8")


# ---- the ladder ---------------------------------------------------------------------------------------------------
def _target(device=None):
    h = link.household()
    if not h:
        raise ValueError("not paired: no household on this computer")
    dev = link.device_named(h, device)
    mine = link.device(h, h.get("me")) or {}
    return h, dev, mine


def ladder(device=None, probes=None):
    """-> (rows, facts). probes: {has_address, ping, neighbor, tcp, hello, rescue} replace the real ones."""
    p = {"has_address": link._has_address, "ping": probe_ping, "neighbor": probe_neighbor, "tcp": probe_tcp,
         "hello": lambda name: link.call({"verb": "hello"}, timeout=8, device=name),
         "rescue": lambda name: __import__("fieldkit.peers.link_rescue", fromlist=["call"]).call(
             "rescue-status", name, timeout=8)}
    p["own"] = lambda ip, port: probe_tcp(ip, port, 2)[0] == "OPEN"
    p.update(probes or {})
    h, dev, mine = _target(device)
    ip = (dev.get("address") or [None])[0]
    port = int(dev.get("port") or h.get("port", link.PORT))
    src = link._source_for(h, ip) if ip else None
    rows, facts = [], {"device": dev["name"], "address": ip, "port": port, "link": dev.get("link") or "lan"}
    hosts = _hosts()
    # 1 interface
    mine_ok = bool(src) and p["has_address"](src) if (dev.get("link") == "cable") else True
    rows.append(rung("interface", OK if mine_ok else PROBLEM,
                     f"this computer holds {src}" if mine_ok and src else
                     "LAN device: the route is the operating system's" if mine_ok else
                     f"this computer does not hold its cable address {src or '(none in the household)'}"))
    # 1b this end's own listener: 2026-10-26 both ends were deaf, and each side only saw the other one refuse
    own = p["own"](src, int(mine.get("port") or h.get("port", link.PORT))) if src else None
    facts["own_listening"] = own
    rows.append(rung("own", OK if own else PROBLEM if own is False else SKIP,
                     f"this computer's listener on {src} " + ("answers" if own else "is DOWN: the peer cannot reach "
                                                              "us either (fieldkit peers link restart)")
                     if own is not None else "no own address to check"))
    # 2 neighbor, 3 ping
    pg = parse_ping(p["ping"](ip)) if ip else None
    nb = parse_neighbor(p["neighbor"](ip), ip) if ip else None
    facts.update(ping=pg, neighbor=nb)
    good_nb = bool(nb and nb.get("mac") and nb["state"] not in ("FAILED", "INCOMPLETE"))
    rows.append(rung("neighbor", OK if good_nb else PROBLEM,
                     f"{ip} at {nb['mac']} ({nb['state']})" if good_nb else
                     f"{ip}: {nb['state'] if nb else 'no entry'} (no link-layer answer)"))
    alive = bool(pg and pg["received"] > 0)
    rows.append(rung("ping", OK if alive else PROBLEM,
                     f"{pg['received']}/{pg['sent']} answered, {pg['loss']}% loss, rtt {pg['rtt_ms']} ms" if pg else
                     "ping output unreadable"))
    # 4 TCP
    state, exc, took = p["tcp"](ip, port, 3, src)
    facts["tcp"] = state
    rows.append(rung("tcp", OK if state == "OPEN" else PROBLEM,
                     f"{ip}:{port} {state} in {took:.2f} s" + (f" ({exc})" if exc else "")))
    # 5 hello
    if state == "OPEN":
        r = p["hello"](dev["name"])
        facts["hello"] = r
        if r.get("ok") and r.get("host") and hosts.get(dev["name"]) != r["host"]:
            _save_host(dev["name"], r["host"])     # link.log names peers by host name: remember it for later
        rows.append(rung("hello", OK if r.get("ok") else PROBLEM,
                         f"{r.get('host')} {r.get('os')}" if r.get("ok") else f"signed hello refused: {r.get('error')}"))
    else:
        rows.append(rung("hello", SKIP, "no TCP connection to say hello on"))
    # 6 logs: here, and on the peer through its rescue door
    home = link.home()
    names = [dev["name"], ip, hosts.get(dev["name"])]
    seen = [x for x in last_lines(home / "link.log", 50, names) if "  serving on " not in x][-1:]
    facts["last_seen"] = seen[0][:19] if seen else None
    facts["local_logs"] = {n: last_lines(home / n, 5) for n in ("serve.log", "watchdog.log")}
    rs = p["rescue"](dev["name"]) if dev.get("link") == "cable" and alive else {"ok": False, "error": "not tried"}
    facts["rescue"] = rs
    rows.append(rung("logs", OK if rs.get("ok") else SKIP,
                     (f"last line naming {dev['name']} in link.log: {facts['last_seen'] or 'none'}; ") +
                     (f"peer rescue door: listening={rs.get('listening')}, keep-alive missing "
                      f"{[r['item'] for r in rs.get('keepalive', []) if not r.get('ok')] or 'nothing'}"
                      if rs.get("ok") else f"peer logs unreadable: {rs.get('error')}")))
    return rows, facts


def verdict(rows, facts):
    """-> (layer, next action) for the lowest broken rung."""
    v = {r["rung"]: r["verdict"] for r in rows}
    name, ip = facts["device"], facts["address"]
    if v.get("hello") == OK:
        if v.get("own") == PROBLEM:
            return "this computer's own listener", "fieldkit peers link restart (the peer answers; we do not)"
        return "none", "nothing: the device answers"
    if v["interface"] == PROBLEM:
        return "this computer's cable interface", ("check the cable is in; then fieldkit peers link setup "
                                                   "(re-sets the address; one UAC click via the elevate kit)")
    if v["ping"] == PROBLEM and v["neighbor"] == PROBLEM:
        return f"{name} is down or asleep, or the cable is out", (f"fieldkit peers link heal --revive --device "
                                                                   f"{name} (wol if declared), else power it on")
    if v["ping"] == PROBLEM:
        return f"{name}'s network stack drops ping (it has a link-layer address)", \
            f"read {name}'s firewall; fieldkit peers link check neighbor"
    if facts["tcp"] == "REFUSED":
        return f"{name} is up but its listener is down (port {facts['port']} REFUSED)", \
            f"fieldkit peers link heal --revive --device {name} (rescue door / ssh if declared)"
    if facts["tcp"] == "TIMEOUT":
        return f"{name} answers ping but port {facts['port']} times out: a firewall drops it", \
            f"on {name}: fieldkit peers link setup (re-opens the cable rule), or fieldkit peers link check firewall"
    if facts["tcp"] == "UNREACHABLE":
        return f"no route to {ip}", "fieldkit peers link check neighbor; fieldkit peers link setup"
    if facts["tcp"] == "OPEN":
        return f"{name} listens but refuses the signed hello", ("fieldkit peers link check clock (stale requests), "
                                                               "then compare fingerprints: fieldkit peers link doctor")
    return f"TCP {facts['tcp']}", "read the tcp row above"


def report(rows, facts, say=print):
    for r in rows:
        say(f"  {r['verdict']:<8} {r['rung']:<10} {r['detail']}")
    layer, nxt = verdict(rows, facts)
    say(f"VERDICT  {'OK' if layer == 'none' else 'BROKEN: ' + layer}")
    say(f"NEXT     {nxt}")
    if facts.get("own_listening") is False and layer != "this computer's own listener":
        say("ALSO     this computer's own listener is down: fieldkit peers link restart")
    return layer, nxt


def why(device=None, probes=None):
    """One line for callers that are about to give up (orchestrate devices --check, jobs): the broken layer."""
    try:
        rows, facts = ladder(device, probes)
    except (ValueError, OSError) as e:
        return f"heal: {e}"
    layer, nxt = verdict(rows, facts)
    return f"heal: {layer}; next: {nxt}"


# ---- revive: each path only if the device declares it ------------------------------------------------------------
def magic_packet(mac):
    b = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", mac or ""))
    if len(b) != 6:
        raise ValueError(f"not a MAC address: {mac!r}")
    return b"\xff" * 6 + b * 16


def wake(mac, src, prefix=None, sock_factory=socket.socket):
    """A Wake-on-LAN magic packet to the cable's broadcast address, sent FROM the cable address (never the LAN)."""
    import ipaddress
    from . import link_setup
    net = ipaddress.ip_network(f"{src}/{prefix or link_setup.PREFIX}", strict=False)
    s = sock_factory(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind((src, 0))
        s.sendto(magic_packet(mac), (str(net.broadcast_address), 9))
    finally:
        s.close()
    return f"magic packet for {mac} sent from {src} to {net.broadcast_address}:9"


SSH_RESTART = "systemctl --user restart fieldkit-link.service"


def ssh_authorized_line(pubkey, from_ip):
    """The peer's ~/.ssh/authorized_keys line: this key may run ONE command, from the cable address only, with no
    shell, no forwarding and no terminal (sshd(8) AUTHORIZED_KEYS FILE FORMAT: restrict, from=, command=)."""
    if not re.fullmatch(r"ssh-(ed25519|rsa) [A-Za-z0-9+/=]+( \S+)?", pubkey.strip()):
        raise ValueError("not an OpenSSH public key line")
    return f'restrict,from="{from_ip}",command="{SSH_RESTART}" {pubkey.strip()}'


def ssh_argv(dev, src):
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes",
            "-o", "PasswordAuthentication=no", "-i", str(dev["ssh_key"]), "-b", src,
            f"{dev['ssh_user']}@{dev['address'][0]}", "restart"]


def _rescue(dev, facts):
    from . import link_rescue
    r = link_rescue.call("rescue-restart", dev["name"])
    if r.get("ok"):
        return True, "rescue door: " + "; ".join(r.get("lines", []))
    return False, "rescue door: " + (r.get("error") or "; ".join(r.get("lines", [])) or "no answer")


def _ssh(dev, facts):
    if not (dev.get("ssh_user") and dev.get("ssh_key")):
        return False, "ssh declared without ssh_user / ssh_key"
    code, out = _run(ssh_argv(dev, link._source_for(link.household(), dev["address"][0])), timeout=30)
    return code == 0, f"ssh exit {code}: {out.strip()[:160]}"


def _wol(dev, facts):
    mac = dev.get("wol_mac") or (facts.get("neighbor") or {}).get("mac")
    if not mac:
        return False, "wol declared but no MAC known (heal declare NAME wol --mac MAC)"
    return True, wake(mac, link._source_for(link.household(), dev["address"][0]))


ACTIONS = {"rescue": _rescue, "ssh": _ssh, "wol": _wol}


def owner_command(dev):
    """What a person types on that machine when nothing else worked: exact, for its OS."""
    if "windows" in (dev.get("os") or "").lower():
        return f"on {dev['name']} in PowerShell: schtasks /Run /TN fieldkit-link"
    return (f"on {dev['name']} in a terminal: {SSH_RESTART}  (if that says the unit does not exist: "
            "fieldkit peers link restart, then fieldkit peers link keepalive install)")


def _inbox(title, why, command):
    try:
        from ..autopilot.inbox import add
        add(title, why, command, kind="link")
    except Exception:  # noqa: BLE001 - link.log still holds it
        pass


def revive(device=None, probes=None, actions=None, inbox=None, wait=30, sleep=time.sleep, say=print):
    """-> exit code. The ladder; each declared path that fits the broken layer; the ladder again after each."""
    actions = {**ACTIONS, **(actions or {})}
    rows, facts = ladder(device, probes)
    layer, _ = report(rows, facts, say)
    if layer == "none":
        return 0
    _h, dev, _mine = _target(device)
    declared = [x for x in (dev.get("revive") or []) if x in REVIVE]
    host_up = bool(facts.get("ping") and facts["ping"]["received"])
    tried = []
    for path in REVIVE:
        if path not in declared:
            continue
        if (path == "wol") == host_up:            # wol is for a silent host; rescue and ssh need a live one
            tried.append(f"{path}: skipped (host {'up' if host_up else 'silent'})")
            continue
        ok, line = actions[path](dev, facts)
        tried.append(f"{path}: {line}")
        say(f"REVIVE   {path}: {line}")
        if not ok:
            continue
        for _ in range(max(1, int(wait / 3))):
            sleep(3)
            rows, facts = ladder(device, probes)
            if verdict(rows, facts)[0] == "none":
                say(f"REVIVED  {dev['name']} answers again (by {path})")
                link._log(f"heal: {dev['name']} revived by {path}")
                return 0
    if not declared:
        tried.append("no revive path declared (fieldkit peers link heal declare NAME rescue,wol)")
    text = (f"{layer}. Address {facts['address']}, MAC {(facts.get('neighbor') or {}).get('mac') or '?'}, "
            f"last seen {facts.get('last_seen') or '?'}. Tried: {'; '.join(tried)}.")
    (inbox or _inbox)(f"{dev['name']} does not answer on the cable", text, owner_command(dev))
    link._log(f"heal: {dev['name']} NOT revived: {text}")
    say(f"INBOX    {dev['name']}: {owner_command(dev)}")
    return 3


def declare(name, paths, mac=None, ssh_user=None, ssh_key=None):
    h = link.household()
    d = link.device(h, name) if h else None
    if not d or name == h.get("me"):
        raise ValueError(f"{name}: not another device of this household")
    bad = [x for x in paths if x not in REVIVE]
    if bad:
        raise ValueError(f"unknown revive path(s) {bad}: {', '.join(REVIVE)}")
    if "wol" in paths and mac:
        magic_packet(mac)
        d["wol_mac"] = mac.lower()
    if "ssh" in paths and not (ssh_user and ssh_key):
        raise ValueError("ssh needs --ssh-user and --ssh-key (key-only; the peer's line: heal ssh-line)")
    if ssh_user:
        d["ssh_user"], d["ssh_key"] = ssh_user, ssh_key
    d["revive"] = list(paths)
    link.save_household(h)
    return d


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit peers link heal", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rest", nargs="*", help="declare NAME PATHS | ssh-line PUBKEYFILE FROM_IP")
    ap.add_argument("--device")
    ap.add_argument("--revive", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--mac")
    ap.add_argument("--ssh-user")
    ap.add_argument("--ssh-key")
    a = ap.parse_args(argv)
    try:
        if a.rest[:1] == ["declare"] and len(a.rest) == 3:
            d = declare(a.rest[1], [x for x in a.rest[2].split(",") if x], a.mac, a.ssh_user, a.ssh_key)
            print(f"DECLARED {d['name']}: revive {', '.join(d['revive'])}")
            return 0
        if a.rest[:1] == ["ssh-line"] and len(a.rest) == 3:
            with open(a.rest[1], encoding="utf-8") as f:
                print(ssh_authorized_line(f.read(), a.rest[2]))
            return 0
        if a.rest:
            ap.print_usage()
            return 2
        if a.revive:
            return revive(a.device)
        rows, facts = ladder(a.device)
    except (ValueError, OSError) as e:
        print(f"HEAL  {e}")
        return 2
    if a.json:
        layer, nxt = verdict(rows, facts)
        print(json.dumps({"rows": rows, "facts": facts, "layer": layer, "next": nxt}, indent=1, default=str))
        return 0 if layer == "none" else 3
    return 0 if report(rows, facts)[0] == "none" else 3
