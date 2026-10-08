"""Static source audit and binary audit (spec: STATIC SOURCE AUDIT, STATIC NETWORK API AUDIT, ELF AND BINARY AUDIT,
SOURCE DIFF TEST, DESTINATION INVENTORY).

source   For every network-relevant component of the spec, every non-test source file that calls a network API is
         inventoried (file, component, APIs, Gorilla markers). Each needs a disposition in
         Gorilla.firefox/leakgate/dispositions.json approved by the owner; files new since release N-1 are marked.
binary   Every hostname embedded in the shipped omni.ja archives and xul.dll is extracted and diffed against
         release N-1 (the backup of the previous install). Each NEW host needs a disposition.
vendor   Every https:// or wss:// host in the packaged omni.ja / browser/omni.ja that belongs to a vendor service
         domain (VENDOR_DOMAINS) must be decided: an approved disposition `host:<name>` (globs allowed) or an
         approved allowlist entry. Unknown ones fail BINARY_POLICY (vendor_host_check).

Inventory widening, 2026-10-02 (an independent audit found files the inventory missed). Two causes:
  1. directories: toolkit/actors (NetErrorParent MITM priming), toolkit/components/reputationservice
     (ApplicationReputation remote verdicts), toolkit/components/doh (DoH heuristics/rollout), the WebExtension
     storage.sync Kinto client (toolkit/components/extensions; the audit report placed it under services/sync) and
     services/common (the Kinto/REST clients) were in no COMPONENTS prefix, so they were never scanned.
  2. patterns NET_API did not recognise (EXTRA_NET): an XHR whose request is opened with a method
     (`request.open("HEAD", url)`) or a helper fetch called with `method: "HEAD"`; C++ `ios->NewChannel(` and
     `AsyncOpen(` (only lower-case JS `newChannel(`/`asyncOpen(` and `NS_NewChannel` were known); DNS lookups
     (`Services.dns.asyncResolve(`, C++ `AsyncResolve(`); the Region fetch (`Region._fetchRegion(`); Kinto clients;
     remote asset URLs on *.cdn.mozilla.net in message definitions (ASRouter FeatureCalloutMessages images).
     .json files are scanned too (message JSON).
A newly inventoried file with no approved disposition makes SOURCE_POLICY fail: the maintainer decides it.
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
    # widened 2026-10-02 (see the module docstring)
    "Network error pages/MITM priming": ["toolkit/actors"],
    "Application reputation (download verdicts)": ["toolkit/components/reputationservice"],
    "DoH heuristics/rollout": ["toolkit/components/doh"],
    "WebExtension storage.sync (Kinto)": ["toolkit/components/extensions/ExtensionStorageSyncKinto.sys.mjs",
                                          "toolkit/components/extensions/ExtensionStorageSync.sys.mjs", "services/common"],
}
# a global fetch( only: `keywords.fetch(`, `this.#fetch(` and `async fetch({` methods are not network calls (02 Oct: 9 false hits)
NET_API = re.compile(r"(?<![.\w#])(?<!async )fetch\((?!\{)(?![^)\n]*\)\s*\{)|new XMLHttpRequest|new WebSocket|newChannel\(|NetUtil\.newChannel|asyncOpen\(|sendBeacon|"
                     r"ServiceRequest|\bDownloader\b|RemoteSettings\(|NS_NewChannel|nsIHttpChannel|PR_Connect|PR_OpenTCPSocket|"
                     r"PR_OpenUDPSocket|CreateTransport|viaduct::|reqwest::|hyper::|ureq::|WinHttp|InternetOpen|HttpClient")
# (label, pattern): network use NET_API does not recognise; the label is what the inventory records
EXTRA_NET = (
    ("open(METHOD)", re.compile(r"\.open\(\s*[\"'](?:GET|HEAD|POST|PUT|DELETE|PATCH|OPTIONS)[\"']")),
    ("method: HEAD/POST/PUT", re.compile(r"\bmethod\s*:\s*[\"'](?:HEAD|POST|PUT|DELETE|PATCH)[\"']")),
    ("NewChannel( (C++)", re.compile(r"(?:->|\.|::|\b)NewChannel\w*\(")),
    ("AsyncOpen( (C++)", re.compile(r"\bAsyncOpen\d?\(")),
    ("asyncResolve( (DNS)", re.compile(r"\b[aA]syncResolve\w*\(")),
    ("Region fetch", re.compile(r"\bRegion\._?fetch\w*\(")),
    ("Kinto client", re.compile(r"KintoHttpClient|kinto-http-client|\bnew (?:lazy\.)?Kinto\(")),
)
REMOTE_ASSET = re.compile(r"https?://((?:[a-z0-9-]+\.)*cdn\.mozilla\.net)/", re.I)
SOURCE_EXT = (".mjs", ".js", ".jsm", ".cpp", ".cc", ".h", ".rs", ".py", ".json")


def network_apis(text):
    """-> sorted labels of every network use found in `text` (NET_API matches, EXTRA_NET labels, remote asset hosts)."""
    apis = {m.group(0) for m in NET_API.finditer(text)}
    apis |= {label for label, rx in EXTRA_NET if rx.search(text)}
    apis |= {"remote asset URL " + m.group(1).lower() for m in REMOTE_ASSET.finditer(text)}
    return sorted(apis)
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
                apis = network_apis(text)
                if not apis:
                    continue
                lock = "PHYSICAL LOCK" in text
                out[rel] = {"component": comp, "apis": apis, "gorilla": text.count("GORILLA"), "physical_lock": lock,
                            "suggestion": "locked in source (PHYSICAL LOCK present)" if lock else "unreviewed"}
    return out


def static_audit(root, dispositions, n_minus_1_root=None):
    """-> {"inventory", "count", "unapproved", "undecided", "new_since_previous"}. `undecided` = inventoried files with
    no disposition at all (typically newly inventoried); they are part of `unapproved`, which fails SOURCE_POLICY."""
    inv = inventory(root)
    prev = set(inventory(n_minus_1_root)) if n_minus_1_root else set()
    unapproved = sorted(f for f in inv if not (dispositions.get(f) or {}).get("approval"))
    return {"inventory": inv, "count": len(inv), "unapproved": unapproved,
            "undecided": sorted(f for f in inv if f not in dispositions),
            "new_since_previous": sorted(f for f in inv if prev and f not in prev)}


HOST_IN_BYTES = re.compile(rb"(https?|wss?)://([a-z0-9][a-z0-9.-]{1,200}\.[a-z]{2,24})", re.I)
VENDOR_DOMAINS = ("mozilla.org", "mozilla.com", "mozilla.net", "mozilla.cloudflare-dns.com", "googleapis.com", "google.com",
                  "gstatic.com", "microsoft.com", "live.com", "windows.net", "cloudflare-dns.com")
TEXT_EXT = (".js", ".mjs", ".json", ".ftl", ".properties", ".xhtml", ".html", ".css", ".manifest", ".txt", ".xml")


def embedded_hosts(open_member, omni_names=("omni.ja", "browser/omni.ja"), dll="xul.dll", schemes=None):
    """-> {host: [where]}. `open_member(relpath)` returns bytes of a file of the install. `schemes`, when a dict, is
    filled with {host: {scheme}} for the hosts found in the omni.ja archives (vendor_host_check uses it)."""
    out = {}
    for ja in omni_names:
        with zipfile.ZipFile(io.BytesIO(open_member(ja))) as z:
            for n in z.namelist():
                if n.endswith(TEXT_EXT):
                    for m in HOST_IN_BYTES.finditer(z.read(n)):
                        host = m.group(2).decode().lower()
                        out.setdefault(host, set()).add(f"{ja}:{n}")
                        if schemes is not None:
                            schemes.setdefault(host, set()).add(m.group(1).decode().lower())
    try:
        data = open_member(dll)
        for m in HOST_IN_BYTES.finditer(data):
            out.setdefault(m.group(2).decode().lower(), set()).add(dll)
    except (OSError, KeyError):
        pass
    return {h: sorted(w)[:5] for h, w in out.items()}


def vendor_domain(host):
    return any(host == d or host.endswith("." + d) for d in VENDOR_DOMAINS)


def host_decided(host, dispositions, allow=None):
    """-> "approved" | "pending" | None: an approved disposition `host:<name>` (fnmatch globs allowed in the key) or an
    approved dest/dns allowlist entry of any scenario covers it; "pending" = only unapproved ones do."""
    import fnmatch
    seen = None
    for k, d in dispositions.items():
        if k.startswith("host:") and fnmatch.fnmatch(host, k[5:].lower()):
            if (d or {}).get("approval"):
                return "approved"
            seen = "pending"
    for e in (allow or {}).get("entries", []):
        if e.get("kind") in ("dest", "dns") and any(fnmatch.fnmatch(host, v.lower()) for v in e.get("values", [])):
            if e.get("approval"):
                return "approved"
            seen = "pending"
    return seen


def vendor_host_check(schemes, where, dispositions, allow=None):
    """HOST INVENTORY: every https/wss host of a vendor service domain in the packaged omni.ja archives must be
    decided. -> {"vendor_hosts": [...], "vendor_unlisted": [...], "vendor_pending": [...], "vendor_where": {...}}."""
    vend = sorted(h for h, sch in schemes.items() if sch & {"https", "wss"} and vendor_domain(h))
    unlisted, pending = [], []
    for h in vend:
        st = host_decided(h, dispositions, allow)
        if st != "approved":
            unlisted.append(h)
            if st == "pending":
                pending.append(h)
    return {"vendor_hosts": vend, "vendor_unlisted": unlisted, "vendor_pending": pending,
            "vendor_where": {h: where.get(h, []) for h in unlisted}}


def host_context(install_dir, hosts, where=None, width=160, per_member=8):
    """Every occurrence of each host in the text members of omni.ja and browser/omni.ja, read straight from the
    archives, with jar:member:line and `width` characters either side: the evidence a disposition cites
    ("browser/omni.ja:chrome/.../X.sys.mjs:644"). Born 2026-10-04: 107 vendor hosts of build 26 were reviewed from
    extracted omni folders with a throwaway script; a disposition must point at the shipped text, so this reads the
    install's own archives. Every text member is searched (the audit's `where` lists stop at five places);
    `where`: {host: ["jar:member" | "xul.dll", ...]} (binary-hosts.json `where`/`vendor_where`) only adds the
    places that are not archive text (xul.dll), reported as binary, never searched as text.
    -> {host: [{"where", "line", "text"}] | [{"where", "binary": True}] | []} (an empty list: not found in any text)"""
    inst = Path(install_dir)
    rx = {h: re.compile(r"(?<![\w.-])" + re.escape(h) + r"(?![\w-]|\.[\w-])", re.I) for h in hosts}   # not a longer name
    out = {h: [] for h in hosts}
    for h in hosts:
        for w in (where or {}).get(h, []):
            if ":" not in w:
                out[h].append({"where": w, "binary": True})
    for ja in ("omni.ja", "browser/omni.ja"):
        if not (inst / ja).is_file():
            continue
        with zipfile.ZipFile(inst / ja) as z:
            for n in z.namelist():
                if not n.endswith(TEXT_EXT):
                    continue
                spec = f"{ja}:{n}"
                text = z.read(n).decode("utf-8", "replace")
                low = text.lower()
                for h in hosts:
                    if h.lower() not in low:
                        continue
                    found = 0
                    for i, line in enumerate(text.split("\n"), 1):
                        for m in rx[h].finditer(line):
                            found += 1
                            if found > per_member:
                                break
                            s = max(0, m.start() - width)
                            out[h].append({"where": spec, "line": i, "text": line[s:m.end() + width].strip()})
                        if found > per_member:
                            break
    return out


def hosts_to_review(data):
    """A hosts list -> (hosts, where). Accepts a run's binary-hosts.json (the hosts still needing a decision:
    unapproved new ones and unlisted vendor ones), a list of {"host", "where"} rows, or a list of names."""
    if isinstance(data, dict):
        hosts = list(dict.fromkeys(list(data.get("unapproved_new") or []) + list(data.get("vendor_unlisted") or [])))
        where = {**(data.get("where") or {}), **(data.get("vendor_where") or {})}
        return hosts, {h: where.get(h, []) for h in hosts}
    hosts = [r["host"] if isinstance(r, dict) else str(r) for r in data]
    return hosts, {r["host"]: r.get("where") or [] for r in data if isinstance(r, dict)}


def context_lines(ctx):
    out = []
    for h, rows in ctx.items():
        out.append(f"===== {h}")
        if not rows:
            out.append("  ?? not found in any text member of omni.ja / browser/omni.ja")
        for r in rows:
            out.append(f"  -- {r['where']} (binary: not searched as text)" if r.get("binary")
                       else f"  {r['where']}:{r['line']}: ...{r['text']}...")
    return out


def binary_audit(install_dir, previous_zip, dispositions, allow=None):
    inst = Path(install_dir)
    schemes = {}
    now = embedded_hosts(lambda rel: (inst / rel).read_bytes(), schemes=schemes)
    prev = {}
    if previous_zip and Path(previous_zip).is_file():
        z = zipfile.ZipFile(previous_zip)
        prev = embedded_hosts(lambda rel: z.read(rel))
    new = sorted(h for h in now if prev and h not in prev)
    unapproved = [h for h in new if not (dispositions.get("host:" + h) or {}).get("approval")]
    return {"hosts": len(now), "previous": len(prev), "new": new, "removed": sorted(h for h in prev if h not in now),
            "unapproved_new": unapproved, "where": {h: now[h] for h in new},
            **vendor_host_check(schemes, now, dispositions, allow)}


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
