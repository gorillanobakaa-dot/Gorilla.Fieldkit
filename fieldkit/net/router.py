"""Learn what a router is and what it will tell you, WITHOUT risking a lockout.

    fieldkit net router identify   what this box is, from read-only signals
    fieldkit net router probe      which endpoints answer, GET only, never auth
    fieldkit net router risk       what a failed login costs BEFORE you make one

# WHY THIS EXISTS

On 2026-10-13 the owner asked for his hub to be troubleshot and handed over the
admin password. Before using it, reading the login page's own JavaScript showed:

    "Every subsequent failed attempt should lock out for 120 seconds"
    fail_5_time = true   after 5 failures

Automating that login meant reimplementing bcrypt plus AES-CCM from their
scripts, and every wrong guess costs the owner access to his own hub. The work
stopped there and the session said so.

That decision is the valuable part, and it only happened because somebody looked
at the lockout policy FIRST. This module makes that the default order.

# ============================================================================
# THE ORDER. DO NOT REARRANGE IT.
# ============================================================================
#
# 1. IDENTIFY, read-only. HTTP headers, asset names, page text. A vendor gives
#    itself away: "ee-style.css" and "id.ee.co.uk" said EE before anything else
#    did. The MAC's OUI says who built it.
#
# 2. CLASSIFY THE WAN. A page full of "SIM", "LTE", "5G", "signal" and "band" is
#    a CELLULAR hub, and that changes every speed conclusion you will draw
#    afterwards: a 10x throughput swing is normal there and a fault on a fixed
#    line. Measured on this hub: SIM 56 mentions, signal 56, lte 33, 5G 30.
#
# 3. FIND THE API, by reading the page's own JavaScript rather than guessing
#    paths. Theirs declared `prefix: 'cgi-bin/'` in common.js.
#
# 4. PROBE READ-ONLY. GET only. A GET that is refused is not a failed login and
#    cannot lock anybody out. "session_id_error" means the endpoint exists and
#    needs a session, which is useful to know and costs nothing.
#
# 5. READ THE LOCKOUT POLICY. Only now, and BEFORE any credential is used.
#
# 6. DECIDE WHETHER TO AUTOMATE AT ALL. If the login needs reimplementing their
#    crypto, the honest answer is often "ask the owner to log in and screenshot
#    the page", which costs thirty seconds and zero risk. A tool that gambles
#    somebody's router access to save them a screenshot has the trade backwards.
#
# NEVER: POST to a login endpoint to "see what happens". That IS the attempt.
# ============================================================================
"""
import json
import re
import shutil
import subprocess

CELLULAR_WORDS = ("sim", "lte", "5g", "rsrp", "rsrq", "sinr", "imei", "apn",
                  "cellular", "mobile", "band", "signal")
# Endpoint names worth GETting. Read-only, common across consumer routers.
COMMON_PATHS = ("GetToken", "GetDeviceInfo", "GetWanInfo", "GetSimStatus",
                "GetSystemInfo", "GetNetworkInfo", "GetStatusInfo", "GetVersionInfo",
                "status", "status.json", "api/status", "api/device/signal")


def _get(url, timeout=15, head=False):
    if not shutil.which("curl"):
        return None, None
    args = ["curl", "-s", "-m", str(timeout), "-o", "-", "-w", "\n%{http_code}", url]
    if head:
        args = ["curl", "-s", "-I", "-m", str(timeout), url]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 10)
    except Exception:
        return None, None
    if head:
        return r.stdout, None
    body, _, code = r.stdout.rpartition("\n")
    return body, code.strip()


def gateway():
    """The default gateway's IP and MAC, without assuming it is 192.168.1.1."""
    try:
        r = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=10)
        m = re.search(r"^default via (\S+)", r.stdout, re.M)
        ip = m.group(1) if m else None
    except Exception:
        ip = None
    mac = None
    if ip:
        try:
            r = subprocess.run(["ip", "neigh", "show", ip], capture_output=True, text=True, timeout=10)
            mm = re.search(r"lladdr\s+(\S+)", r.stdout)
            mac = mm.group(1) if mm else None
        except Exception:
            pass
    return {"ip": ip, "mac": mac, "oui": mac[:8].upper() if mac else None}


def mac_for(ip):
    """The MAC of THIS address, from the neighbour table. -> (mac, oui) or (None, None).

    TRAP, and a real one: identify() used to report the DEFAULT GATEWAY's MAC no
    matter which --ip was probed. On a laptop with two routers that attributed
    one box's manufacturer to the other, and on 2026-10-13 it sent a diagnosis
    down the wrong path entirely: the EE hub was reported as made by Arris,
    which was the OTHER router on the wifi.
    """
    try:
        text = subprocess.run(["ip", "neigh", "show", str(ip)], capture_output=True,
                              text=True, timeout=10).stdout
    except Exception:
        return None, None
    m = re.search(r"lladdr\s+([0-9a-f:]{17})", text or "", re.I)
    if not m:
        return None, None
    mac = m.group(1).lower()
    return mac, mac[:8].upper()


def identify(ip=None):
    """STEP 1 and 2. What is this box, and is its WAN cellular? All read-only."""
    gw = gateway()
    ip = ip or gw["ip"]
    if not ip:
        return {"error": "no default gateway"}
    base = f"http://{ip}/"
    headers, _ = _get(base, head=True)
    body, code = _get(base)
    body = body or ""

    # a vendor gives itself away in its asset names and outbound links
    assets = sorted(set(re.findall(r'(?:src|href)="([^"]+)"', body)))
    brand_hits = {}
    for a in assets:
        for m in re.findall(r"([a-z0-9][a-z0-9-]{1,20})\.(?:co\.uk|com|net)", a.lower()):
            brand_hits[m] = brand_hits.get(m, 0) + 1
    for a in assets:
        m = re.match(r"css/([a-z0-9]{2,10})-style\.css", a.lower())
        if m:
            brand_hits[m.group(1)] = brand_hits.get(m.group(1), 0) + 5

    low = body.lower()
    cell = {w: low.count(w) for w in CELLULAR_WORDS if low.count(w)}
    cellular = sum(cell.values()) >= 20

    # the MAC must belong to the box actually probed, never to the default
    # gateway: reporting the wrong device's manufacturer is worse than none
    mac, oui = (gw["mac"], gw["oui"]) if ip == gw["ip"] else mac_for(ip)
    return {"ip": ip, "mac": mac, "oui": oui,
            "http_code": code, "server_header": _header(headers, "Server"),
            "brand_hints": sorted(brand_hits.items(), key=lambda kv: -kv[1])[:5],
            "wan_kind": "cellular" if cellular else "probably-fixed",
            "cellular_evidence": dict(sorted(cell.items(), key=lambda kv: -kv[1])[:6]),
            "why": ("the page is full of mobile vocabulary, so the WAN is a SIM. A 10x "
                    "throughput swing is NORMAL on this and is not a fault."
                    if cellular else "no strong mobile vocabulary on the page")}


def _header(raw, name):
    if not raw:
        return None
    m = re.search(rf"^{name}:\s*(.+)$", raw, re.M | re.I)
    return m.group(1).strip() if m else None


def api_prefix(ip=None):
    """STEP 3. Find the API prefix by READING their JavaScript, not by guessing."""
    gw = gateway()
    ip = ip or gw["ip"]
    body, _ = _get(f"http://{ip}/")
    scripts = re.findall(r'src="([^"]+\.js)"', body or "")
    for s in scripts:
        js, _ = _get(f"http://{ip}/{s}")
        if not js:
            continue
        m = re.search(r"prefix\s*:\s*['\"]([^'\"]+)['\"]", js)
        if m:
            return {"prefix": m.group(1), "found_in": s}
    return {"prefix": None, "found_in": None,
            "note": "no prefix declared; probe common paths instead"}


def probe(ip=None, paths=None, prefix=None):
    """STEP 4. GET-only endpoint discovery. CANNOT cause a lockout.

    A GET that is refused is not a failed login. This never POSTs and never
    sends a credential.
    """
    gw = gateway()
    ip = ip or gw["ip"]
    prefix = prefix if prefix is not None else (api_prefix(ip).get("prefix") or "")
    out = []
    for p in (paths or COMMON_PATHS):
        body, code = _get(f"http://{ip}/{prefix}{p}")
        if code != "200":
            continue
        needs_auth = bool(body and "session_id_error" in body)
        data = None
        if body and not needs_auth:
            try:
                data = json.loads(body)
            except Exception:
                data = None
        out.append({"path": f"{prefix}{p}", "http": code,
                    "needs_session": needs_auth,
                    "keys": sorted(data)[:8] if isinstance(data, dict) else None})
    return {"ip": ip, "prefix": prefix, "endpoints": out,
            "note": "GET only. Nothing here can trip a login lockout."}


def risk(ip=None):
    """STEP 5. What does a FAILED login cost? Read this BEFORE using a password.

    This is the whole point of the module. On the owner's hub the login script
    said: lock out after 5 failures, then 120 seconds per attempt. Knowing that
    first turned "automate the login" into "ask for a screenshot instead", which
    was the right call.
    """
    gw = gateway()
    ip = ip or gw["ip"]
    body, _ = _get(f"http://{ip}/")
    scripts = re.findall(r'src="([^"]+\.js)"', body or "")
    findings, crypto = [], []
    for s in scripts:
        js, _ = _get(f"http://{ip}/{s}")
        if not js:
            continue
        for pat, note in (
            (r"lock.{0,20}out|fail_\d+_time|FailTime|WaitSeconds",
             "the login has a lockout after repeated failures"),
            (r"(\d{2,4})\s*seconds", None),
        ):
            if re.search(pat, js, re.I) and note:
                findings.append({"script": s, "finding": note})
        m = re.search(r"should lock out for (\d+) seconds", js, re.I)
        if m:
            findings.append({"script": s, "finding": f"lockout is {m.group(1)} seconds per attempt"})
        m2 = re.search(r">=\s*(\d+)\s*\)\s*\{\s*\n?\s*\w*\.?fail", js, re.I)
        if m2:
            findings.append({"script": s, "finding": f"lockout triggers after {m2.group(1)} failures"})
        for name in ("bcrypt", "sjcl", "CryptoJS", "AES", "RSA"):
            if name.lower() in js.lower() and name not in crypto:
                crypto.append(name)

    automatable = not crypto
    return {"ip": ip, "lockout_findings": findings, "client_side_crypto": crypto,
            "safe_to_automate_login": automatable,
            "advice": ("the password is sent as-is, so a login can be scripted; still read the "
                       "lockout above and allow for ONE attempt"
                       if automatable else
                       "the password is hashed and encrypted in the browser, so automating the "
                       "login means reimplementing their crypto, and every wrong guess costs a "
                       "lockout. ASK THE OWNER TO LOG IN AND SCREENSHOT THE PAGE instead, then "
                       "read it with `fieldkit shot read`. Thirty seconds, no risk."),
            "never": "do not POST to a login endpoint to see what happens. That IS an attempt."}


def lines(d):
    if "brand_hints" in d:
        out = [f"gateway: {d['ip']}  mac {d['mac']}  oui {d['oui']}",
               f"http: {d['http_code']}   server: {d['server_header'] or 'not stated'}",
               f"brand hints: {', '.join(f'{k}({v})' for k, v in d['brand_hints']) or 'none'}",
               f"WAN kind: {d['wan_kind']}"]
        if d["cellular_evidence"]:
            out.append("  evidence: " + ", ".join(f"{k}={v}" for k, v in d["cellular_evidence"].items()))
        out.append("  " + d["why"])
        return out
    if "endpoints" in d:
        out = [f"prefix: {d['prefix'] or '(none)'}", d["note"], ""]
        for e in d["endpoints"]:
            state = "needs a session" if e["needs_session"] else (
                "readable: " + ", ".join(e["keys"] or []) if e["keys"] else "readable")
            out.append(f"  {e['path']:<28} {state}")
        return out
    if "lockout_findings" in d:
        out = ["what a FAILED login costs here:"]
        for f in d["lockout_findings"]:
            out.append(f"  - {f['finding']}")
        if not d["lockout_findings"]:
            out.append("  - nothing found, which is NOT the same as no lockout")
        out += ["", f"client-side crypto: {', '.join(d['client_side_crypto']) or 'none'}",
                f"safe to automate: {d['safe_to_automate_login']}", "", d["advice"], "", d["never"]]
        return out
    return [str(d)]
