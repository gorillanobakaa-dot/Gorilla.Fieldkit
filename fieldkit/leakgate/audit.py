"""Static source audit and binary audit (spec: STATIC SOURCE AUDIT, STATIC NETWORK API AUDIT, ELF AND BINARY AUDIT,
SOURCE DIFF TEST, DESTINATION INVENTORY).

source   For every network-relevant component of the spec, every non-test source file that calls a network API is
         inventoried (file, component, APIs, Gorilla markers). Each needs a disposition in
         Gorilla.firefox/leakgate/dispositions.json approved by the owner; files new since release N-1 are marked.
binary   Every hostname embedded in the shipped omni.ja archives and xul.dll is extracted and diffed against
         release N-1 (the backup of the previous install). Each NEW host needs a disposition.
A model proposes dispositions (`suggestion`); only the owner approves them, at a real terminal.
"""
import io
import json
import re
import zipfile
from pathlib import Path

COMPONENTS = {
    "Telemetry": ["toolkit/components/telemetry"], "Glean/FOG": ["toolkit/components/glean"],
    "Crash reporting": ["toolkit/crashreporter", "toolkit/components/crashes"], "Remote Settings": ["services/settings"],
    "Normandy": ["toolkit/components/normandy"], "Nimbus/experiments": ["toolkit/components/nimbus"],
    "Messaging/ASRouter": ["browser/components/asrouter", "toolkit/components/messaging-system"],
    "Update": ["toolkit/mozapps/update"], "Background tasks": ["toolkit/components/backgroundtasks"],
    "Push": ["dom/push"], "Safe Browsing": ["toolkit/components/url-classifier", "browser/components/safebrowsing"],
    "Pocket/newtab": ["browser/components/pocket", "browser/extensions/newtab/lib"], "Sync": ["services/sync"],
    "Firefox Accounts": ["services/fxaccounts"], "WebDriver/BiDi/Marionette": ["remote"],
    "Search/suggest/Merino": ["browser/components/urlbar", "toolkit/components/search"],
    "Translations": ["toolkit/components/translations"], "ML/AI": ["toolkit/components/ml", "browser/components/genai"],
    "Region/geolocation": ["toolkit/modules/Region.sys.mjs", "dom/geolocation"],
    "Captive portal/connectivity": ["toolkit/components/captivedetect", "netwerk/base/NetworkConnectivityService.cpp"],
    "Add-ons": ["toolkit/mozapps/extensions"], "DoH/TRR": ["netwerk/dns"], "OCSP/CRLite": ["security/manager/ssl"],
    "Background downloads": ["toolkit/components/bitsdownload", "toolkit/mozapps/downloads"],
    "Ping sender": ["toolkit/components/telemetry/pingsender"],
}
NET_API = re.compile(r"\bfetch\(|new XMLHttpRequest|new WebSocket|newChannel\(|NetUtil\.newChannel|asyncOpen\(|sendBeacon|"
                     r"ServiceRequest|\bDownloader\b|RemoteSettings\(|NS_NewChannel|nsIHttpChannel|PR_Connect|PR_OpenTCPSocket|"
                     r"PR_OpenUDPSocket|CreateTransport|viaduct::|reqwest::|hyper::|ureq::|WinHttp|InternetOpen|HttpClient")
SOURCE_EXT = (".mjs", ".js", ".jsm", ".cpp", ".cc", ".h", ".rs", ".py")
SKIP = re.compile(r"/(test|tests|gtest|docs|example|examples|bench|benches|fuzz|fuzzing)/|_test\.|\.test\.")


def _files(root, prefix):
    p = Path(root) / prefix
    if p.is_file():
        return [p]
    return [f for f in p.rglob("*") if f.is_file() and f.suffix in SOURCE_EXT] if p.is_dir() else []


def inventory(root):
    """-> {rel: {component, apis, gorilla, physical_lock, suggestion}}."""
    root = Path(root)
    out = {}
    for comp, prefixes in COMPONENTS.items():
        for pre in prefixes:
            for f in _files(root, pre):
                rel = f.relative_to(root).as_posix()
                if SKIP.search("/" + rel) or rel in out:
                    continue
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                apis = sorted({m.group(0) for m in NET_API.finditer(text)})
                if not apis:
                    continue
                lock = "PHYSICAL LOCK" in text
                out[rel] = {"component": comp, "apis": apis, "gorilla": text.count("GORILLA"), "physical_lock": lock,
                            "suggestion": "locked in source (PHYSICAL LOCK present)" if lock else "unreviewed"}
    return out


def static_audit(root, dispositions, n_minus_1_root=None):
    """-> {"inventory": n, "unapproved": [...], "new_since_previous": [...]}."""
    inv = inventory(root)
    prev = set(inventory(n_minus_1_root)) if n_minus_1_root else set()
    unapproved = sorted(f for f in inv if not (dispositions.get(f) or {}).get("approval"))
    return {"inventory": inv, "count": len(inv), "unapproved": unapproved,
            "new_since_previous": sorted(f for f in inv if prev and f not in prev)}


HOST_IN_BYTES = re.compile(rb"https?://([a-z0-9][a-z0-9.-]{1,200}\.[a-z]{2,24})", re.I)
TEXT_EXT = (".js", ".mjs", ".json", ".ftl", ".properties", ".xhtml", ".html", ".css", ".manifest", ".txt", ".xml")


def embedded_hosts(open_member, omni_names=("omni.ja", "browser/omni.ja"), dll="xul.dll"):
    """-> {host: [where]}. `open_member(relpath)` returns bytes of a file of the install."""
    out = {}
    for ja in omni_names:
        with zipfile.ZipFile(io.BytesIO(open_member(ja))) as z:
            for n in z.namelist():
                if n.endswith(TEXT_EXT):
                    for m in HOST_IN_BYTES.finditer(z.read(n)):
                        out.setdefault(m.group(1).decode().lower(), set()).add(f"{ja}:{n}")
    try:
        data = open_member(dll)
        for m in HOST_IN_BYTES.finditer(data):
            out.setdefault(m.group(1).decode().lower(), set()).add(dll)
    except (OSError, KeyError):
        pass
    return {h: sorted(w)[:5] for h, w in out.items()}


def binary_audit(install_dir, previous_zip, dispositions):
    inst = Path(install_dir)
    now = embedded_hosts(lambda rel: (inst / rel).read_bytes())
    prev = {}
    if previous_zip and Path(previous_zip).is_file():
        z = zipfile.ZipFile(previous_zip)
        prev = embedded_hosts(lambda rel: z.read(rel))
    new = sorted(h for h in now if prev and h not in prev)
    unapproved = [h for h in new if not (dispositions.get("host:" + h) or {}).get("approval")]
    return {"hosts": len(now), "previous": len(prev), "new": new, "removed": sorted(h for h in prev if h not in now),
            "unapproved_new": unapproved, "where": {h: now[h] for h in new}}
