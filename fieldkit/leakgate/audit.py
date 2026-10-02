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
# a global fetch( only: `keywords.fetch(`, `this.#fetch(` and `async fetch({` methods are not network calls (02 Oct: 9 false hits)
NET_API = re.compile(r"(?<![.\w#])(?<!async )fetch\((?!\{)(?![^)\n]*\)\s*\{)|new XMLHttpRequest|new WebSocket|newChannel\(|NetUtil\.newChannel|asyncOpen\(|sendBeacon|"
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


# ---------------------------------------------------------------------------------------------- executables / PE
NET_DLLS = ("wininet.dll", "winhttp.dll", "urlmon.dll", "webio.dll", "dnsapi.dll", "ws2_32.dll", "iphlpapi.dll", "bits.dll")
EXPECTED_NET_IMPORTS = {"xul.dll": {"ws2_32.dll", "dnsapi.dll", "iphlpapi.dll"}, "nss3.dll": {"ws2_32.dll"}}


def executables(open_member, names):
    """-> {relpath: {"imports": [network dlls imported]}} for every .exe/.dll."""
    import pefile
    out = {}
    for rel in names:
        if not rel.lower().endswith((".exe", ".dll")):
            continue
        try:
            pe = pefile.PE(data=open_member(rel), fast_load=True)
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                                   pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
            imps = set()
            for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
                for e in getattr(pe, attr, []) or []:
                    imps.add(e.dll.decode("ascii", "replace").lower())
            out[rel] = {"imports": sorted(i for i in imps if i in NET_DLLS)}
        except Exception as ex:
            out[rel] = {"imports": [], "error": str(ex)[:120]}
    return out


def executable_audit(install_dir, previous_zip, dispositions):
    inst = Path(install_dir)
    names = [p.relative_to(inst).as_posix() for p in inst.rglob("*") if p.suffix.lower() in (".exe", ".dll")]
    now = executables(lambda rel: (inst / rel).read_bytes(), names)
    prev_names = []
    if previous_zip and Path(previous_zip).is_file():
        z = zipfile.ZipFile(previous_zip)
        prev_names = [n for n in z.namelist() if n.lower().endswith((".exe", ".dll"))]
    new = sorted(n for n in now if prev_names and n not in prev_names)
    unexpected_net = {}
    for rel, info in now.items():
        extra = set(info["imports"]) - EXPECTED_NET_IMPORTS.get(Path(rel).name.lower(), set())
        if extra and not (dispositions.get("pe:" + rel) or {}).get("approval"):
            unexpected_net[rel] = sorted(extra)
    unapproved_new = [n for n in new if not (dispositions.get("exe:" + n) or {}).get("approval")]
    return {"binaries": len(now), "new": new, "unapproved_new": unapproved_new, "network_imports": {k: v["imports"] for k, v in now.items() if v["imports"]},
            "unexpected_network_imports": unexpected_net}


# ---------------------------------------------------------------------------------------------- Rust dependencies
NET_CRATES = ("hyper", "reqwest", "ureq", "viaduct", "h2", "h3", "quinn", "neqo-transport", "neqo-http3", "tokio", "mio",
              "socket2", "async-std", "surf", "isahc", "curl", "rustls", "native-tls", "tungstenite", "websocket")


def cargo_packages(text):
    out, name = {}, None
    for line in text.splitlines():
        if line.startswith("name = "):
            name = line.split('"')[1]
        elif line.startswith("version = ") and name:
            out.setdefault(name, set()).add(line.split('"')[1])
            name = None
    return out


def dependency_audit(tree, previous_lock_text, dispositions):
    now = cargo_packages((Path(tree) / "Cargo.lock").read_text(encoding="utf-8"))
    prev = cargo_packages(previous_lock_text or "")
    new = sorted(n for n in now if prev and n not in prev)
    changed = sorted(n for n in now if n in prev and now[n] != prev[n])
    net_new = [n for n in new if n in NET_CRATES or n.startswith(("hyper", "reqwest", "neqo", "h2", "h3"))]
    unapproved = [n for n in new if not (dispositions.get("crate:" + n) or {}).get("approval")]
    return {"crates": len(now), "new": new, "removed": sorted(n for n in prev if n not in now), "version_changed": len(changed),
            "network_capable_new": net_new, "unapproved_new": unapproved}
