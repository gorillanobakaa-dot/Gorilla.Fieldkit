"""Reading and steering which interface the internet goes out of."""
import os
import re
import shutil
import statistics
import subprocess
import sys

PROBE_HOST = "1.1.1.1"


# ============================================================================
# THIS MODULE READS LINUX NETWORK STATE AND SAYS SO RATHER THAN RETURNING NOTHING
# ============================================================================
# It drives `ip` and `nmcli`. On Windows or macOS those are absent, _run()
# swallows the error, and every function returns an empty result.
#
# An empty result is INDISTINGUISHABLE from a real answer: gateways() returning
# [] reads as "this machine has no default route", which is alarming and false.
# Silence and success must never look alike.
#
# Added 2026-10-13 when this harness was about to be shared with a Windows
# machine, where every one of these would have quietly reported nothing.
import platform

SUPPORTED = ("linux",)


def unsupported_here():
    """-> a sentence if this module cannot work on this OS, else None."""
    if platform.system().lower() in SUPPORTED:
        return None
    return (f"fieldkit net reads Linux network state through `ip` and `nmcli`, which "
            f"{platform.system()} does not have. It is not that nothing was found: nothing "
            f"was LOOKED AT. The cellular band maths (fieldkit net antenna) is pure "
            f"arithmetic and works everywhere.")


def _run(args, timeout=30):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except Exception as e:
        return 1, str(e)


def _sysfs(iface, name):
    try:
        with open(f"/sys/class/net/{iface}/{name}") as fh:
            return fh.read().strip()
    except OSError:
        return None


def interfaces():
    """Every real interface, with what the LINK says (not what the routing does)."""
    out = []
    for i in sorted(os.listdir("/sys/class/net")):
        if i == "lo" or i.startswith(("docker", "veth", "br-", "sit")):
            continue
        wireless = os.path.isdir(f"/sys/class/net/{i}/wireless")
        speed = _sysfs(i, "speed")
        out.append({
            "name": i,
            "kind": "wifi" if wireless else "wired",
            "up": _sysfs(i, "operstate") == "up",
            "carrier": _sysfs(i, "carrier") == "1",
            # trap 3: a gigabit port that negotiated 100 looks exactly like a slow line
            "link_mbps": int(speed) if speed and speed.lstrip("-").isdigit() else None,
            "duplex": _sysfs(i, "duplex"),
        })
    return out


def gateways():
    """EVERY default gateway, not just the winning one. -> [] ONLY on Linux.

    Added 2026-10-13 after a real failure: this laptop had two default routes,
    192.168.0.1 over wifi (metric 600) and 192.168.1.1 over the cable
    (metric 900). A router probe followed the default route, reached the WRONG
    BOX, and spent twenty minutes identifying the wrong hub's manufacturer.

    Lower metric wins, so the hub you plugged a cable into is often NOT the one
    a tool talks to. Anything that probes "the router" must say which one.
    """
    if unsupported_here():
        return []
    rc, text = _run(["ip", "route"])
    out = []
    for line in text.splitlines():
        if not line.startswith("default via "):
            continue
        parts = line.split()
        g = {"gateway": parts[2], "iface": None, "metric": None}
        for i, w in enumerate(parts):
            if w == "dev" and i + 1 < len(parts):
                g["iface"] = parts[i + 1]
            if w == "metric" and i + 1 < len(parts):
                g["metric"] = int(parts[i + 1])
        out.append(g)
    out.sort(key=lambda g: (g["metric"] is None, g["metric"]))
    for i, g in enumerate(out):
        g["wins"] = (i == 0)
    return out


def gateway_warning():
    """-> a sentence if there is more than one router, or if we cannot look."""
    blocked = unsupported_here()
    if blocked:
        return blocked
    gw = gateways()
    if len(gw) < 2:
        return None
    w = gw[0]
    others = ", ".join(f"{g['gateway']} on {g['iface']} (metric {g['metric']})" for g in gw[1:])
    return (f"THERE IS MORE THAN ONE ROUTER HERE. Traffic goes to {w['gateway']} on "
            f"{w['iface']} (metric {w['metric']}), because the LOWEST metric wins. Also present: "
            f"{others}. A probe of 'the router' reaches {w['gateway']}, which may not be the box "
            f"you plugged a cable into. Use --ip to choose.")


def route_for(host=PROBE_HOST):
    """WHICH interface the internet actually leaves by. The only honest answer (trap 1)."""
    rc, out = _run(["ip", "route", "get", host])
    dev = re.search(r"\bdev\s+(\S+)", out)
    src = re.search(r"\bsrc\s+(\S+)", out)
    # every default route and its metric, so the reason is visible, not just the result
    rc2, out2 = _run(["ip", "route"])
    defaults = []
    for line in out2.splitlines():
        if not line.startswith("default"):
            continue
        d = re.search(r"\bdev\s+(\S+)", line)
        m = re.search(r"\bmetric\s+(\d+)", line)
        defaults.append({"dev": d.group(1) if d else "?",
                         "metric": int(m.group(1)) if m else 0})
    defaults.sort(key=lambda x: x["metric"])
    return {"using": dev.group(1) if dev else None,
            "source_ip": src.group(1) if src else None,
            "defaults": defaults,
            # the explanation, not just the fact
            "why": ("lowest metric wins: " +
                    ", ".join(f"{d['dev']}={d['metric']}" for d in defaults)) if defaults else ""}


# --------------------------------------------------------------------------
# WAY ONE: prove it without touching anything.
# --------------------------------------------------------------------------
def compare(url=None, bytes_=20_000_000, timeout=120):
    """The same download forced down EACH interface, changing no configuration.

    curl --interface binds the socket to a source address, so traffic leaves by
    that interface whatever the routing table prefers. Nothing is reconfigured,
    nothing needs undoing, and an interface that is up but unused is measured
    anyway. This is the safe way to settle "is the cable actually faster".
    """
    url = url or f"https://speed.cloudflare.com/__down?bytes={bytes_}"
    if not shutil.which("curl"):
        return {"error": "curl is not installed"}
    results = []
    for i in interfaces():
        if not i["up"]:
            continue
        rc, out = _run(["ip", "-4", "addr", "show", i["name"]])
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
        if not m:
            continue
        ip = m.group(1)
        rc, out = _run(["curl", "-s", "--interface", ip, "-o", "/dev/null",
                        "-w", "%{speed_download} %{time_total}", url], timeout=timeout)
        parts = out.split()
        mbps = None
        if rc == 0 and parts and parts[0].replace(".", "").isdigit():
            mbps = round(float(parts[0]) * 8 / 1_000_000, 1)
        results.append({"interface": i["name"], "kind": i["kind"], "source_ip": ip,
                        "mbps": mbps, "link_mbps": i["link_mbps"],
                        "note": None if mbps else "no result (interface up but not usable)"})
    return {"method": "curl --interface, nothing reconfigured", "results": results}


# --------------------------------------------------------------------------
# WAY TWO: change which one wins, reversibly.
# --------------------------------------------------------------------------
def prefer(iface, metric=50, apply_it=False):
    """Make one interface win by giving its connection a lower route metric.

    Reversible with unprefer(). This CHANGES CONFIGURATION, so it asks first:
    without apply_it it returns the plan and the exact command to undo it.

    Preferred over "just unplug the other one", because unplugging loses the
    comparison: you can no longer measure the one you turned off.
    """
    rc, out = _run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "dev", "status"])
    conn = None
    for line in out.splitlines():
        dev, _, name = line.partition(":")
        if dev == iface and name and name != "--":
            conn = name
            break
    if not conn:
        return {"status": "no-connection", "why": f"{iface} has no active NetworkManager connection"}

    plan = {"interface": iface, "connection": conn, "metric": metric,
            "commands": [["nmcli", "connection", "modify", conn,
                          "ipv4.route-metric", str(metric)],
                         ["nmcli", "connection", "up", conn]],
            "undo": f"fieldkit net prefer {iface} --undo",
            "status": "plan"}
    if not apply_it:
        return plan
    for cmd in plan["commands"]:
        rc, out = _run(cmd, timeout=60)
        if rc != 0:
            return {**plan, "status": "failed", "why": out.strip()[-200:]}
    after = route_for()
    # verify the ARTEFACT: did the traffic actually move?
    plan["status"] = "applied" if after["using"] == iface else "applied-but-not-winning"
    plan["now_using"] = after["using"]
    return plan


def unprefer(iface, apply_it=False):
    """Put the route metric back to automatic."""
    rc, out = _run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "dev", "status"])
    conn = None
    for line in out.splitlines():
        dev, _, name = line.partition(":")
        if dev == iface and name and name != "--":
            conn = name
            break
    if not conn:
        return {"status": "no-connection"}
    cmds = [["nmcli", "connection", "modify", conn, "ipv4.route-metric", "-1"],
            ["nmcli", "connection", "up", conn]]
    if not apply_it:
        return {"status": "plan", "connection": conn, "commands": cmds}
    for c in cmds:
        _run(c, timeout=60)
    return {"status": "restored", "connection": conn, "now_using": route_for()["using"]}


# --------------------------------------------------------------------------
# What KIND of line is it? This changes every other answer (trap 4).
# --------------------------------------------------------------------------
def kind(samples=12):
    """Fixed line or cellular, from latency behaviour rather than from the router.

    A fixed line sits within about 1 ms of itself. A cellular WAN schedules every
    packet, so the spread is large and the variance high. Measured on an EE 5G
    hub on 2026-10-13: min 13.6, max 31.5, mdev 5.5 ms.
    """
    if not shutil.which("ping"):
        return {"kind": "unknown", "why": "ping is not installed"}
    rc, out = _run(["ping", "-c", str(samples), "-i", "0.3", PROBE_HOST], timeout=samples * 2 + 20)
    rtts = [float(x) for x in re.findall(r"time=([0-9.]+)\s*ms", out)]
    if len(rtts) < 4:
        return {"kind": "unknown", "why": "not enough replies", "samples": len(rtts)}
    spread = max(rtts) - min(rtts)
    jitter = statistics.pstdev(rtts)
    if jitter >= 3.0 or spread >= 10.0:
        k, why = "cellular-or-shared", (
            "latency wanders a lot, which means each packet waits for a scheduling slot. "
            "Speed on this connection varies by 10x or more within minutes and that is "
            "NORMAL, not a fault. Do not conclude anything from a single measurement.")
    elif jitter < 1.5 and spread < 5:
        k, why = "fixed-line", "latency is steady, so capacity should be steady too"
    else:
        k, why = "uncertain", "between the two; sample again when the line is quiet"
    return {"kind": k, "why": why, "min_ms": round(min(rtts), 1), "max_ms": round(max(rtts), 1),
            "jitter_ms": round(jitter, 2), "spread_ms": round(spread, 1), "samples": len(rtts)}


def lines(d):
    if "using" in d:
        out = [f"internet goes out of: {d['using'] or 'nothing'}  (src {d['source_ip']})"]
        if d["defaults"]:
            out.append("default routes, lowest metric wins:")
            for r in d["defaults"]:
                mark = "  <- in use" if r["dev"] == d["using"] else ""
                out.append(f"  {r['dev']:<16} metric {r['metric']}{mark}")
        return out
    if "results" in d:
        out = [f"method: {d['method']}", ""]
        for r in d["results"]:
            s = f"{r['mbps']} Mbps" if r["mbps"] else r["note"]
            link = f"link {r['link_mbps']} Mbps" if r["link_mbps"] else ""
            out.append(f"  {r['interface']:<16} {r['kind']:<6} {s:<24} {link}")
        return out
    if "kind" in d:
        return [f"connection kind: {d['kind']}",
                f"  latency {d.get('min_ms')} to {d.get('max_ms')} ms, jitter {d.get('jitter_ms')} ms",
                f"  {d['why']}"]
    return [str(d)]


def exit_code(d):
    if "using" in d:
        return 0 if d["using"] else 3
    return 0
