"""The release gate: scenarios x sensors -> events -> fail-closed policies -> the owner's machine-readable result.

A policy PASSes only when (a) every event it judges is matched by an APPROVED allowlist entry (or is required by
the test definition itself, e.g. the real page's own host), and (b) every sensor the policy requires actually
collected. A release run (--release) requires the packet sensor (elevated shell) and 3 repetitions.

On this Windows laptop the environment is not the spec's disposable VM: other programs resolve names and open
sockets too. Wire DNS that no browser sensor saw is reported as `unattributed`; it fails only when it names a
vendor or tracker domain. The Linux network-namespace runner (linux.py) is the authoritative environment.
"""
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import allow as al, audit, scenarios as sc, sensors as se
from ..buildh.proof import VENDOR_HOST, AD_HOSTS

TELEMETRY_HOSTS = ("incoming.telemetry.mozilla.org", "*.telemetry.mozilla.org", "telemetry*.mozilla.org", "*glean*",
                   "crash-reports.mozilla.com", "crash-stats.mozilla.org", "*.crash-reports.mozilla.com")
EXPERIMENT_HOSTS = ("normandy*.mozilla.org", "normandy.cdn.mozilla.net", "*experimenter*", "*nimbus*")
REMOTE_SETTINGS_HOSTS = ("firefox.settings.services.mozilla.com", "firefox-settings-attachments.cdn.mozilla.net",
                         "content-signature-2.cdn.mozilla.net", "*.settings.services.mozilla.com")
UPDATE_HOSTS = ("aus5.mozilla.org", "aus*.mozilla.org", "download.mozilla.org", "*.cdn.mozilla.net", "archive.mozilla.org",
                "versioncheck*.addons.mozilla.org")
TELEMETRY_FILES = ("datareporting/*", "saved-telemetry-pings/*", "crashes/*", "minidumps/*", "ExperimentStoreData.json",
                   "shield-preference-experiments.json", "storage/permanent/chrome/idb/*remote-settings*",
                   "*pending_pings*", "*glean*")
POLICIES = ("NETWORK_POLICY", "TELEMETRY_POLICY", "DNS_POLICY", "PROCESS_POLICY", "FILESYSTEM_POLICY", "SOCKET_POLICY",
            "WEBRTC_POLICY", "LAN_POLICY", "PROXY_POLICY", "IPV6_POLICY", "CANARY_POLICY", "SHUTDOWN_POLICY", "REPRODUCIBILITY_POLICY",
            "SOURCE_POLICY", "BINARY_POLICY", "DEPENDENCY_POLICY", "TLS_POLICY", "REGRESSION_POLICY", "ALLOWLIST_POLICY")


def _fn(host, globs):
    import fnmatch
    return any(fnmatch.fnmatch(host, g) for g in globs)


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _cmd(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=60).stdout.strip().splitlines()[0]
    except Exception as e:
        return f"unavailable ({type(e).__name__})"


def build_manifest(copy_dir, workdir_tree, upstream, allow_path, spec_path):
    import configparser
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(Path(copy_dir) / "application.ini", encoding="utf-8")
    pl = configparser.ConfigParser(interpolation=None)
    pl.read(Path(copy_dir) / "platform.ini", encoding="utf-8")
    clang = Path.home() / ".mozbuild" / "clang" / "bin" / "clang-cl.exe"
    fk = Path(__file__).resolve().parents[2]
    return {
        "BUILD": cp.get("App", "BuildID", fallback=None), "VERSION": cp.get("App", "Version", fallback=None),
        "SOURCE": {"tree_head": _cmd(["git", "-C", str(workdir_tree), "rev-parse", "HEAD"]), "upstream": upstream,
                   "platform_source_stamp": pl.get("Build", "SourceStamp", fallback=None)},
        "BINARY_SHA256": {n: _sha(Path(copy_dir) / n) for n in ("firefox.exe", "xul.dll", "omni.ja", "browser/omni.ja") if (Path(copy_dir) / n).is_file()},
        "OS": platform.platform(), "KERNEL": platform.version(), "ARCHITECTURE": platform.machine(),
        "TOOLCHAIN": {"clang-cl": _cmd([str(clang), "--version"]) if clang.is_file() else "not found", "rustc": _cmd(["rustc", "--version"]),
                      "python": sys.version.split()[0]},
        "HARNESS_VERSION": _cmd(["git", "-C", str(fk), "rev-parse", "HEAD"]),
        "ALLOWLIST_SHA256": _sha(allow_path) if Path(allow_path).is_file() else None,
        "SPEC_SHA256": _sha(spec_path) if Path(spec_path).is_file() else None,
        "CONFIGURE": configure_record(),
        "PROFILE_CONFIGURATION": "fresh throwaway profile per scenario and mode; user.js: no default-browser check, no about:welcome, downloads into the profile",
        "NETWORK_CONFIGURATION": "Windows host network (NOT the spec's namespace); direct and mitmproxy-pinned runs",
    }


def configure_record(objdir=Path("C:/gfobj"), mozconfig=Path.home() / "Documents" / "Gorilla.firefox" / "config" / "mozconfig.win64"):
    """Configure arguments (the mozconfig ac_add_options) and the MOZ_* substitutions configure produced."""
    out = {"mozconfig": None, "MOZ": {}}
    if Path(mozconfig).is_file():
        out["mozconfig"] = [l.strip() for l in Path(mozconfig).read_text(encoding="utf-8", errors="replace").splitlines()
                            if l.strip().startswith(("ac_add_options", "mk_add_options", "export "))]
    cs = Path(objdir) / "config.status"
    if cs.is_file():
        import re as _re
        for m in _re.finditer(r"'(MOZ_[A-Z0-9_]+)':\s*'([^']*)'", cs.read_text(encoding="utf-8", errors="replace")):
            out["MOZ"][m.group(1)] = m.group(2)
    return out


def stop_started(p, wait=10):
    """Stop a process THIS gate started, by its PID (with its children on Windows); nothing else is touched.
    taskkill exists only on Windows: the Linux runner terminates its own child and kills it if it lingers."""
    if p.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        return
    p.terminate()
    try:
        p.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(timeout=wait)


def ensure_ca(work):
    conf = Path(work) / "mitm-conf"
    conf.mkdir(parents=True, exist_ok=True)
    ca = conf / "mitmproxy-ca-cert.pem"
    if not ca.exists():
        mitmdump = shutil.which("mitmdump") or str(Path(sys.executable).parent / "Scripts" / "mitmdump.exe" if sys.platform == "win32"
                                                   else Path(sys.executable).parent / "mitmdump")
        p = subprocess.Popen([mitmdump, "--listen-port", str(se.free_port()), "--set", f"confdir={conf}", "-q"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while not ca.exists() and time.time() - t0 < 30:
            time.sleep(0.5)
        stop_started(p)
    return ca


# ---------------------------------------------------------------------------------------------- judging
def judge(events, allow, scenario_hosts, copy_dirs, server_results, repeat, packets, quick):
    """-> (policies {name: {"result", "why", "unexpected": [...]}} , lists for the result)."""
    resolved = {}
    names_by_scenario = {}
    for e in events:
        if e["kind"] in ("dest", "dns", "sni"):
            names_by_scenario.setdefault(e["scenario"], set()).add(e["value"])
    for scen, names in names_by_scenario.items():
        for n in names:
            try:
                for a in socket.getaddrinfo(n, 443):
                    resolved.setdefault(scen, set()).add(a[4][0])
            except OSError:
                pass

    def allowed(kind, value, scen, port=None):
        if kind in ("dest", "dns", "sni") and _fn(value, scenario_hosts.get(scen, [])):
            return "allowed", "test-definition"
        k = {"sni": "dest", "dns-wire": "dns"}.get(kind, kind)
        return al.verdict(allow, k, value, scen, port)

    lists = {k: [] for k in ("UNEXPECTED_DESTINATIONS", "UNEXPECTED_DNS", "UNEXPECTED_PROCESSES", "UNEXPECTED_EXECUTABLES",
                             "UNEXPECTED_FILES", "UNEXPECTED_SOCKETS", "UNEXPECTED_TELEMETRY", "UNEXPECTED_EXPERIMENTS",
                             "UNEXPECTED_REMOTE_SETTINGS", "UNEXPECTED_UPDATES", "UNEXPECTED_CANARIES", "UNATTRIBUTED_WIRE_DNS",
                             "PENDING_APPROVAL")}
    fail = {p: [] for p in POLICIES}
    # a `coverage` event says a sensor ran in one scenario (gate.action_checks); it is not an observation
    sensors_seen = {e["sensor"] for e in events if e["kind"] != "coverage"}

    def note(policy, listname, e, why):
        item = f"[{e['scenario']}/{e['mode']}/{e['sensor']}] {e['kind']} {e['value']}" + (f":{e['port']}" if e.get("port") else "") + f" - {why}"
        fail[policy].append(item)
        if listname:
            lists[listname].append(item)

    copy_prefixes = [str(Path(c)).lower() for c in copy_dirs]
    for e in events:
        k, v, scen = e["kind"], e["value"], e["scenario"]
        if k in ("dest", "sni", "dns", "dns-wire"):
            host = v.lower()
            for globs, lname in ((TELEMETRY_HOSTS, "UNEXPECTED_TELEMETRY"), (EXPERIMENT_HOSTS, "UNEXPECTED_EXPERIMENTS"),
                                 (REMOTE_SETTINGS_HOSTS, "UNEXPECTED_REMOTE_SETTINGS"), (UPDATE_HOSTS, "UNEXPECTED_UPDATES")):
                if _fn(host, globs):
                    note("TELEMETRY_POLICY", lname, e, "telemetry/experiment/remote-settings/update host")
            if k in ("dest", "sni", "dns"):
                pol, lname = ("DNS_POLICY", "UNEXPECTED_DNS") if k == "dns" else ("NETWORK_POLICY", "UNEXPECTED_DESTINATIONS")
                for why in video_rule(host, scen):
                    note(pol, lname, e, why)
            if k == "dns-wire" and host not in names_by_scenario.get(scen, set()):
                if VENDOR_HOST.search(host) or any(host == a or host.endswith("." + a) for a in AD_HOSTS):
                    note("DNS_POLICY", "UNEXPECTED_DNS", e, "vendor/tracker name on the wire that no browser sensor saw (bypass?)")
                else:
                    lists["UNATTRIBUTED_WIRE_DNS"].append(host)
                continue
            verdict, eid = allowed(k, host, scen, e.get("port"))
            if verdict != "allowed":
                pol, lname = ("DNS_POLICY", "UNEXPECTED_DNS") if k in ("dns", "dns-wire") else ("NETWORK_POLICY", "UNEXPECTED_DESTINATIONS")
                note(pol, lname, e, verdict + (f" (entry {eid})" if eid else ""))
                if verdict == "pending":
                    lists["PENDING_APPROVAL"].append(eid)
        elif k == "dest-ip":
            if v in resolved.get(scen, set()):
                continue
            verdict, eid = al.verdict(allow, "ip", v, scen, e.get("port"))
            if verdict != "allowed":
                note("NETWORK_POLICY", "UNEXPECTED_DESTINATIONS", e, "an address no named destination resolves to (" + e.get("detail", "") + ")")
            if ":" in v and verdict != "allowed":
                note("IPV6_POLICY", None, e, "IPv6 destination")
            if scen == "webrtc":
                note("WEBRTC_POLICY", None, e, "the WebRTC page has no ICE servers: nothing may leave")
        elif k == "process":
            path = (e.get("detail") or "").lower()
            if not any(path.startswith(p) for p in copy_prefixes):
                note("PROCESS_POLICY", "UNEXPECTED_EXECUTABLES", e, f"executable outside the build: {e.get('detail')}")
            verdict, eid = al.verdict(allow, "process", v, scen)
            if verdict != "allowed":
                note("PROCESS_POLICY", "UNEXPECTED_PROCESSES", e, verdict)
        elif k == "process-after-shutdown":
            note("SHUTDOWN_POLICY", "UNEXPECTED_PROCESSES", e, "a build process alive after shutdown")
        elif k in ("listener", "udp"):
            verdict, eid = al.verdict(allow, k, v, scen)
            if verdict != "allowed":
                note("SOCKET_POLICY", "UNEXPECTED_SOCKETS", e, verdict)
            if k == "listener" and not v.startswith(("127.", "[::1]", "::1")):
                note("SOCKET_POLICY", "UNEXPECTED_SOCKETS", e, "listener not bound to loopback")
        elif k in ("file", "file-system"):
            if _fn(v, TELEMETRY_FILES):
                note("TELEMETRY_POLICY", "UNEXPECTED_TELEMETRY", e, "telemetry/experiment/crash data on disk")
            verdict, eid = al.verdict(allow, k, v, scen)
            if verdict != "allowed":
                note("FILESYSTEM_POLICY", "UNEXPECTED_FILES", e, verdict)
        elif k == "proxy-bypass":
            note("PROXY_POLICY", "UNEXPECTED_DESTINATIONS", e, "direct connection while a proxy was pinned")
        elif k == "canary":
            note("CANARY_POLICY", "UNEXPECTED_CANARIES", e, e.get("detail", ""))

    # WebRTC page result: no raw local IP in candidates
    for r in server_results:
        for c in (r.get("candidates") or []):
            parts = c.split()
            if len(parts) > 4 and not parts[4].endswith(".local") and (parts[4].startswith(("10.", "192.168.", "172.", "169.254.", "fe80"))):
                fail["WEBRTC_POLICY"].append(f"raw local address in an ICE candidate: {parts[4]}")

    # required sensors (fail closed: a policy nobody measured does not pass)
    need = {"NETWORK_POLICY": {"necko-http", "mitm", "sockets"} | ({"pktmon"} if packets else set()),
            "DNS_POLICY": {"necko-dns"} | ({"pktmon"} if packets else set()),
            "PROCESS_POLICY": {"process-tree"}, "FILESYSTEM_POLICY": {"filesystem"}, "SOCKET_POLICY": {"sockets"},
            "PROXY_POLICY": {"mitm", "sockets"}, "CANARY_POLICY": {"mitm", "necko-http"}, "TELEMETRY_POLICY": {"mitm", "necko-http", "filesystem"},
            "SHUTDOWN_POLICY": {"process-tree"}, "WEBRTC_POLICY": {"sockets"}, "IPV6_POLICY": {"sockets"}}
    for pol, sens in need.items():
        missing = sorted(s for s in sens if s not in sensors_seen)
        if missing:
            fail[pol].append(f"required sensor(s) did not collect: {missing}")
    if not packets:
        for pol in ("NETWORK_POLICY", "DNS_POLICY", "SHUTDOWN_POLICY", "WEBRTC_POLICY", "IPV6_POLICY"):
            fail[pol].append("packet-level sensor not run (needs an elevated shell): wire traffic unverified")
    if repeat < 3:
        fail["REPRODUCIBILITY_POLICY"].append(f"ran {repeat} time(s); the spec requires at least 3 identical runs")
    if quick:
        fail["SHUTDOWN_POLICY"].append("quick mode: idle and post-shutdown windows shorter than the release values")
    return fail, lists


def reproducibility(events, repeat):
    """Host sets per scenario must be identical across repetitions."""
    by = {}
    for e in events:
        if e["kind"] in ("dest", "dns", "sni") and e["mode"] == "direct":
            by.setdefault((e["scenario"], e.get("rep", 0)), set()).add(e["value"])
    out = []
    for scen in {s for s, _ in by}:
        sets = [by.get((scen, r), set()) for r in range(repeat)]
        if any(s != sets[0] for s in sets[1:]):
            diff = set().union(*sets) - set.intersection(*sets)
            out.append(f"{scen}: host set differs between runs: {sorted(diff)[:8]}")
    return out


def video_rule(host, scen):
    """The video compromise (decision D-157-12), whatever the allowlist says: in a video scenario no Mozilla host, and a
    video plugin host (Widevine/OpenH264 makers) only in its own scenario. -> reasons to fail (empty = no objection;
    inside its own scenario a maker host still needs an approved, scenario-scoped allowlist entry)."""
    out = []
    if scen in sc.VIDEO_COMPROMISE and _fn(host, sc.MOZILLA_HOSTS):
        out.append(f"a Mozilla host in the video scenario {scen}: the video compromise never goes through Mozilla (D-157-12)")
    for own, globs in sc.VIDEO_COMPROMISE.items():
        if own != scen and _fn(host, globs):
            out.append(f"a video plugin host outside its scenario {own}: fetched only when a page needs it (D-157-12)")
    return out


def required_coverage(mode, packets, linux=False):
    """Sensors that must show they ran in every user-action scenario run (fail closed per scenario)."""
    if linux:
        return {"strace", "tcpdump"}
    need = {"process-tree", "filesystem", "sockets"}
    if mode == "direct":
        need |= {"necko-http", "necko-dns"} | ({"pktmon"} if packets else set())
    elif mode in ("proxied", "poisoned"):
        need |= {"mitm"}
    return need


def _lan_left(e, closed_ports):
    from . import sensors as _se
    v = str(e.get("value") or "")
    if not _se.lan_address(v):
        return False
    if v.startswith("127.") or v in ("::1", "[::1]"):
        return e.get("port") is not None and int(e["port"]) in closed_ports
    return True


def action_checks(ran, events, results, requests, packets, linux=False):
    """Fail-closed checks of the user-action scenarios (scenarios.ACTION_SCENARIOS) that ran. -> (fail, lists):
    fail {"NETWORK_POLICY": [...], "LAN_POLICY": [...]}, lists {"LAN_REQUESTS_LEFT": [...]}.
      - every mode of every such scenario: each required sensor shows it ran (coverage or any event), else FAIL;
      - a page that must report (scenarios.REPORTING) never POSTed its result: the action never happened, FAIL;
      - a local path the scenario needs (scenarios.SERVED, e.g. the .exe) was never served, FAIL;
      - lan-probe: any socket/packet/necko evidence of a request to a private, link-local or the closed loopback
        address fails LAN_POLICY (the page is public by pref; a headless run has nobody to answer a permission
        prompt, so a request that left went without one), as does a fetch the page saw succeed."""
    fail = {"NETWORK_POLICY": [], "LAN_POLICY": []}
    lists = {"LAN_REQUESTS_LEFT": []}
    results = [r for r in results if isinstance(r, dict)]
    by = {}
    for e in events:
        by.setdefault((e.get("scenario"), e.get("mode")), set()).add(e.get("sensor"))
    for scen in sc.ACTION_SCENARIOS:
        if scen not in ran:
            continue
        modes = sorted(m for (s_, m) in by if s_ == scen)
        if not modes:
            fail["NETWORK_POLICY"].append(f"[{scen}] no sensor collected anything: the scenario did not run")
        for m in modes:
            missing = sorted(required_coverage(m, packets, linux) - by[(scen, m)])
            if missing:
                fail["NETWORK_POLICY"].append(f"[{scen}/{m}] sensor(s) collected nothing in this scenario: {missing}")
        if scen in sc.REPORTING and not any(r.get("scenario") == scen for r in results):
            fail["NETWORK_POLICY"].append(f"[{scen}] the page never reported: the user action was not exercised")
        for path in sc.SERVED.get(scen, ()):
            if not any(str(q.get("path", "")).split("?")[0] == path for q in requests):
                fail["NETWORK_POLICY"].append(f"[{scen}] the local server never served {path}: the user action was not exercised")
    if "lan-probe" in ran:
        reports = [r for r in results if r.get("scenario") == "lan-probe"]
        closed = {int(r["closed"]) for r in reports if str(r.get("closed", "")).isdigit()}
        if not reports:
            fail["LAN_POLICY"].append("the LAN probe page never reported")
        if not any(e.get("scenario") == "lan-probe" and e.get("sensor") in ("sockets", "strace", "pktmon", "tcpdump") for e in events):
            fail["LAN_POLICY"].append("no socket-level sensor collected in lan-probe: whether the requests left is unknown")
        for e in events:
            if e.get("scenario") == "lan-probe" and e.get("kind") in ("dest-ip", "dest-lan") and _lan_left(e, closed):
                item = f"[lan-probe/{e.get('mode')}/{e.get('sensor')}] {e['value']}:{e.get('port')}"
                lists["LAN_REQUESTS_LEFT"].append(item)
                fail["LAN_POLICY"].append(item + " - a public page reached the local network with no permission granted")
        for r in reports:
            for k, t in (r.get("targets") or {}).items():
                if isinstance(t, dict) and t.get("result") == "reached":
                    fail["LAN_POLICY"].append(f"[lan-probe] the page reached {t.get('url', k)}: Local Network Access did not stop it")
    return fail, lists


def source_messages(st):
    """SOURCE_POLICY failures from audit.static_audit: every inventoried file without an approved disposition fails;
    the ones with no disposition at all (newly inventoried) are named, so the maintainer can decide them."""
    out = []
    if st["unapproved"]:
        out.append(f"{len(st['unapproved'])} of {st['count']} network-capable source files have no approved disposition"
                   + (f"; {len(st['new_since_previous'])} new since release N-1" if st["new_since_previous"] else ""))
    und = st.get("undecided") or []
    if und:
        out.append(f"{len(und)} inventoried file(s) with no disposition at all (maintainer to decide): {und[:12]}")
    return out


def vendor_messages(bi):
    """BINARY_POLICY failures of the HOST INVENTORY: vendor https/wss hosts in omni.ja that nothing decided."""
    un = bi.get("vendor_unlisted") or []
    if not un:
        return []
    pend = set(bi.get("vendor_pending") or [])
    return [f"{len(un)} vendor host(s) in omni.ja with no approved disposition or allowlist entry"
            + (f" ({len(pend)} only pending)" if pend else "") + f": {un[:40]}"]


# ---------------------------------------------------------------------------------------------- run
def run(zip_path, owner_root, workdir_tree, upstream, workroot, repeat=1, quick=True, only=None, release=False,
        previous_zip=None, n_minus_1_tree=None, say=print, soak=None, firewall=False):
    owner_root = Path(owner_root)
    allow_path = al.path_for(owner_root)
    disp_path = owner_root / "leakgate" / "dispositions.json"
    spec_path = owner_root / "leakgate" / "SPEC.md"
    allow = al.load(allow_path)
    dispositions = json.loads(disp_path.read_text(encoding="utf-8")) if disp_path.is_file() else {}
    linux = sys.platform.startswith("linux")
    if linux:
        from . import linux as lx
        st0 = lx.selftest()
        if not st0["ok"]:
            raise RuntimeError(f"leakgate (linux): not root or missing required tools {st0['missing_required']}; see selftest")
    packets = True if linux else se.is_admin()
    if release:
        repeat, quick = max(repeat, 3), False
    work = Path(workroot) / time.strftime("%Y%m%d-%H%M%S")
    work.mkdir(parents=True)
    zp = work / "build.zip"
    shutil.copyfile(zip_path, zp)
    if linux:
        import tarfile
        def _tar_copy(dest, policies=None):
            dest = Path(dest)
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True)
            with tarfile.open(zp) as tf:
                tf.extractall(dest / "_x")
            inner = next((dest / "_x").iterdir())
            for item in inner.iterdir():
                shutil.move(str(item), dest / item.name)
            shutil.rmtree(dest / "_x")
            if policies:
                (dest / "distribution").mkdir(exist_ok=True)
                (dest / "distribution" / "policies.json").write_text(json.dumps({"policies": policies}, indent=1), encoding="utf-8")
            return dest
        se.build_copy = lambda z, d, policies=None: _tar_copy(d, policies)
    direct = se.build_copy(zp, work / "build-direct")
    ca = ensure_ca(work)
    port = se.free_port()
    proxied = se.build_copy(zp, work / "build-proxied", {
        "Certificates": {"Install": [str(ca)]},
        "Proxy": {"Mode": "manual", "HTTPProxy": f"127.0.0.1:{port}", "UseHTTPProxyForAllProtocols": True, "Locked": True}})
    from . import extras
    approved_names = [v for e in allow.get("entries", []) if e.get("kind") in ("dns", "dest") and e.get("approval") for v in e.get("values", [])]
    doh = extras.DohServer(work, ca, approved_names + [h for s in sc.SCENARIOS for h in s[4]])
    dnsctl = se.build_copy(zp, work / "build-dns", {
        "Certificates": {"Install": [str(ca)]},
        "DNSOverHTTPS": {"Enabled": True, "ProviderURL": doh.url(), "Locked": True, "Fallback": False}})
    certsrv = extras.CertServers(work, ca)
    fw_name = None
    if firewall and packets:
        fw_name = f"leakgate-{work.name}"
        extras.firewall_block(str(direct / "firefox.exe"), fw_name)
        say(f"  firewall: outbound block for the direct build copy ({fw_name}); removed at the end")
    manifest = build_manifest(direct, workdir_tree, upstream, allow_path, spec_path)
    say(f"leakgate: build {manifest['BUILD']} ({manifest['VERSION']}), packets {'ON' if packets else 'OFF (not elevated)'}, "
        f"repeat {repeat}, {'quick' if quick else 'release'} durations -> {work}")
    canaries = list(sc.CANARIES.items())
    if linux:
        sport = se.free_port()
        lx.netns_up(port, sport)                       # the veth host address exists only after this
        lx.snapshot_namespace(work)
        server = sc.Server(bind=lx.HOST_IP, port=sport)
    else:
        server = sc.Server()
    events, artifacts, scen_hosts = [], {}, {}
    closed_port = se.free_port()                       # nothing listens here: the lan-probe's loopback target
    try:
        for name, target, secs, args, hosts, watch in sc.SCENARIOS:
            if only and name not in only:
                continue
            scen_hosts[name] = hosts
            target = sc.target_for(name, target, certsrv.ports(), closed_port)
            prefs = sc.prefs_for(name, server.bind, server.port)
            secs = sc.QUICK.get(name, secs) if quick else secs
            if name == "startup-idle" and soak:
                secs = int(soak)
            watch = min(watch, 10) if quick else watch
            modes = [("direct", direct), ("proxied", proxied)]
            if name in sc.DNS_CONTROLLED:
                modes.append(("dns-controlled", dnsctl))
            if name in sc.POISONED and not quick:
                modes.append(("poisoned", proxied))
            if name in sc.GRACEFUL:
                modes = [("direct", direct), ("proxied", proxied)]
            for rep in range(repeat):
                for mode, bdir in modes:
                    say(f"  {name} [{mode}] run {rep + 1}/{repeat}: {secs} s + {watch} s after shutdown")
                    if linux:
                        allowed = [v for e in allow.get("entries", []) if e.get("kind") in ("dns", "dest") for v in e.get("values", [])] + list(hosts)
                        ev, art = lx.run_one(bdir, server.url(target), secs, args, work / "runs",
                                             f"{name}-r{rep}", mode, canaries, port, watch, allowed, say=say, prefs=prefs)
                    else:
                        ev, art = se.run_one(bdir, server.url(target), secs, args, work / "runs", f"{name}-r{rep}", mode, canaries,
                                             proxy_port=port, watch=watch, packets=packets and mode == "direct", say=say,
                                             graceful=name in sc.GRACEFUL, poison=mode == "poisoned", prefs=prefs)
                    for e in ev:
                        e["scenario"], e["rep"] = name, rep
                    events.extend(ev)
                    artifacts[f"{name}-r{rep}-{mode}"] = art
                    if name == "page" and mode == "proxied" and not any(e["sensor"] == "mitm" and "anthropic" in e["value"] for e in ev):
                        events.append({"scenario": name, "mode": mode, "sensor": "mitm", "kind": "proxy-bypass", "value": "proxy did not take",
                                       "detail": "the page itself never reached mitmproxy"})
    finally:
        server.close()
        doh.close()
        certsrv.close()
        if fw_name:
            extras.firewall_unblock(fw_name)
        if linux:
            lx.netns_down()

    # the controlled resolver's own log is a sensor: every name the browser asked for, answered or not
    for q in doh.queries:
        events.append({"scenario": "(dns-controlled)", "mode": "dns-controlled", "sensor": "doh-server", "kind": "dns", "value": q["name"],
                       "detail": f"type {q['type']} {'answered' if q['answered'] else 'NXDOMAIN'}", "rep": 0})
    fail, lists = judge(events, allow, scen_hosts, [direct, proxied, dnsctl], server.results, repeat, packets, quick)
    afail, alists = action_checks(set(scen_hosts), events, server.results, server.requests, packets, linux)
    for pol, items in afail.items():
        fail[pol].extend(items)
    lists.update(alists)
    (work / "page-results.json").write_text(json.dumps(server.results, indent=1), encoding="utf-8")
    # fallbacks under poisoned responses: a host seen only when every answer was garbage
    for scen in sc.POISONED:
        normal = {e["value"] for e in events if e["scenario"] == scen and e["mode"] == "proxied" and e["kind"] == "dest"}
        for e in events:
            if e["scenario"] == scen and e["mode"] == "poisoned" and e["kind"] == "dest" and e["value"] not in normal:
                fail["NETWORK_POLICY"].append(f"[{scen}/poisoned] fallback destination under malformed responses: {e['value']}")
    # certificate validation
    certs = next((r["certs"] for r in server.results if isinstance(r, dict) and "certs" in r), None)
    if only is None or "certs" in only:
        if not certs:
            fail["TLS_POLICY"].append("the certificate page never reported")
        else:
            for k in ("expired", "wronghost", "selfsigned"):
                if certs.get(k) == "loaded":
                    fail["TLS_POLICY"].append(f"a {k} certificate was ACCEPTED")
    # plaintext: any http:// request to a non-local host
    for e in events:
        if e["kind"] == "dest" and str(e.get("detail", "")).startswith("http://") and not e["value"].startswith(("127.", "localhost")):
            fail["TLS_POLICY"].append(f"[{e['scenario']}/{e['mode']}] plaintext HTTP to {e['value']}")
    # graceful shutdown
    for e in events:
        if e["kind"] == "shutdown-failed":
            fail["SHUTDOWN_POLICY"].append(e["value"])
    # periodicity and TLS details (reports; vendor periodicity already fails the network rows)
    flows = []
    for key, art in artifacts.items():
        m = Path(art.get("mitm") or "")
        if m.is_file():
            flows += [json.loads(l) for l in m.read_text(encoding="utf-8").splitlines() if l.strip()]
    period = extras.periodicity(flows)
    tls = []
    for key, art in artifacts.items():
        if art.get("pcap"):
            tls += extras.tls_details(art["pcap"], set(art.get("local_ports") or []))
    for t_ in tls:
        if t_.get("server_version") in ("0x301", "0x302", "0x300"):
            fail["TLS_POLICY"].append(f"TLS below 1.2 negotiated with {t_.get('sni')}: {t_['server_version']}")
    (work / "periodicity.json").write_text(json.dumps(period, indent=1), encoding="utf-8")
    (work / "tls.json").write_text(json.dumps(tls, indent=1), encoding="utf-8")
    # regression against the approved baseline of the previous release
    base_path = owner_root / "leakgate" / "baseline.json"
    if base_path.is_file():
        base = json.loads(base_path.read_text(encoding="utf-8"))
        counts = {}
        for f_ in flows:
            if f_.get("kind") == "http":
                counts[f_["host"]] = counts.get(f_["host"], 0) + 1
        for h, n in counts.items():
            b = base.get("request_counts", {}).get(h)
            if b is None:
                fail["REGRESSION_POLICY"].append(f"{h}: not in the baseline of {base.get('release')}")
            elif n > 2 * max(b, 1):
                fail["REGRESSION_POLICY"].append(f"{h}: {n} requests vs {b} in the baseline of {base.get('release')}")
    else:
        fail["REGRESSION_POLICY"].append(NO_BASELINE + ": the maintainer seeds it with leakgate-baseline from a release run that failed only on this")
    if not packets:
        fail["TLS_POLICY"].append("packet sensor not run: negotiated TLS versions unverified")
    if repeat >= 3:
        fail["REPRODUCIBILITY_POLICY"].extend(reproducibility(events, repeat))
    for p in al.problems(allow) + al.scope_problems(allow):
        fail["ALLOWLIST_POLICY"].append(p)
    if not allow.get("entries"):
        fail["ALLOWLIST_POLICY"].append(f"no allowlist at {allow_path}")
    say("  static source audit ...")
    st = audit.static_audit(workdir_tree, dispositions, n_minus_1_tree)
    fail["SOURCE_POLICY"].extend(source_messages(st))
    say("  binary audit ...")
    bi = audit.binary_audit(direct, previous_zip, dispositions, allow)
    fail["BINARY_POLICY"].extend(vendor_messages(bi))
    if not previous_zip:
        fail["BINARY_POLICY"].append("no previous release to diff against")
    if bi["unapproved_new"]:
        fail["BINARY_POLICY"].append(f"{len(bi['unapproved_new'])} embedded host(s) new since release N-1 without an approved disposition: {bi['unapproved_new'][:8]}")

    say("  executable and dependency audits ...")
    ex = audit.executable_audit(direct, previous_zip, dispositions)
    if ex["unapproved_new"]:
        fail["BINARY_POLICY"].append(f"{len(ex['unapproved_new'])} executable/DLL(s) new since release N-1 without an approved disposition: {ex['unapproved_new'][:6]}")
    if ex["unexpected_network_imports"]:
        fail["BINARY_POLICY"].append(f"binaries importing network libraries they should not: {dict(list(ex['unexpected_network_imports'].items())[:4])}")
    prev_lock = None
    if n_minus_1_tree:
        try:
            import subprocess as _sp
            repo = Path(workdir_tree).parent / "155.0.1"
            root = _sp.run(["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.split()[0]
            prev_lock = _sp.run(["git", "-C", str(repo), "show", f"{root}:Cargo.lock"], capture_output=True, text=True, encoding="utf-8").stdout
        except Exception:
            prev_lock = None
    dep = audit.dependency_audit(workdir_tree, prev_lock, dispositions)
    if not prev_lock:
        fail["DEPENDENCY_POLICY"].append("no previous Cargo.lock to compare with")
    elif dep["unapproved_new"]:
        fail["DEPENDENCY_POLICY"].append(f"{len(dep['unapproved_new'])} Rust crate(s) new since release N-1 without an approved disposition"
                                         + (f"; network-capable: {dep['network_capable_new']}" if dep["network_capable_new"] else "") + f": {dep['unapproved_new'][:8]}")
    (work / "executables.json").write_text(json.dumps(ex, indent=1), encoding="utf-8")
    (work / "dependencies.json").write_text(json.dumps(dep, indent=1), encoding="utf-8")
    result = {**{k: manifest[k] for k in ("BUILD", "VERSION", "SOURCE", "BINARY_SHA256", "OS", "KERNEL", "ARCHITECTURE",
                                           "NETWORK_CONFIGURATION", "PROFILE_CONFIGURATION", "HARNESS_VERSION")},
              "TEST_RUN": work.name, "DNS_CONFIGURATION": "system resolver (Windows DNS Client)",
              "FIREWALL_CONFIGURATION": "none (Windows, not the spec's DROP namespace)"}
    for p in POLICIES:
        result[p] = "PASS" if not fail[p] else "FAIL"
    for k, v in lists.items():
        result[k] = sorted(set(v))
    result["WHY"] = {p: fail[p][:40] for p in POLICIES if fail[p]}
    result["FINAL_RESULT"] = "PASS" if all(result[p] == "PASS" for p in POLICIES) else "FAIL"
    result["ARTIFACTS"] = str(work)

    (work / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    (work / "test-results.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    (work / "build-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (work / "source-inventory.json").write_text(json.dumps(st, indent=1), encoding="utf-8")
    (work / "binary-hosts.json").write_text(json.dumps(bi, indent=1), encoding="utf-8")
    (work / "artifacts.json").write_text(json.dumps(artifacts, indent=1, default=str), encoding="utf-8")
    for kind, fname in (("dest", "destinations.observed"), ("dns", "dns.observed"), ("listener", "sockets.observed")):
        (work / fname).write_text("\n".join(sorted({f"{e['value']}\t{e['scenario']}\t{e['sensor']}" for e in events if e["kind"] == kind})) + "\n", encoding="utf-8")
    (work / "destinations.new").write_text("\n".join(result["UNEXPECTED_DESTINATIONS"]) + "\n", encoding="utf-8")
    (work / "dns.new").write_text("\n".join(result["UNEXPECTED_DNS"]) + "\n", encoding="utf-8")
    (work / "sockets.new").write_text("\n".join(result["UNEXPECTED_SOCKETS"]) + "\n", encoding="utf-8")
    result["PERIODIC_HOSTS"] = {h: v for h, v in period.items() if v["regular"]}
    (work / "test-results.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result, events, st, bi


NO_BASELINE = "no approved baseline (leakgate/baseline.json)"


def bootstrap_eligible(results, owner_root):
    """The first baseline. REGRESSION fails while no baseline exists, and a baseline came only from a release PASS,
    so none could ever be made (found 2026-10-02). A release run whose ONLY failure is that missing baseline may
    seed it; every other rule must have passed. Returns (ok, why)."""
    if (Path(owner_root) / "leakgate" / "baseline.json").is_file():
        return False, "a baseline already exists: only a release PASS may replace it"
    if not results.get("release_run"):
        return False, "not a release run"
    failed = [p for p in POLICIES if results.get(p) != "PASS"]
    if failed != ["REGRESSION_POLICY"]:
        return False, "other rules failed too: " + ", ".join(p for p in failed if p != "REGRESSION_POLICY")
    why = (results.get("WHY") or {}).get("REGRESSION_POLICY") or []
    if not why or any(not str(w).startswith(NO_BASELINE) for w in why):
        return False, "REGRESSION failed for a reason other than the missing baseline"
    return True, "a release run in which every rule passed except the missing baseline"


def save_baseline(run_dir, owner_root, release, bootstrap=False, decisions=None):
    """After an approved release PASS: request counts per host become the regression baseline."""
    run_dir = Path(run_dir)
    counts = {}
    for m in (run_dir / "runs").glob("mitm-*.jsonl"):
        for l in m.read_text(encoding="utf-8").splitlines():
            f = json.loads(l)
            if f.get("kind") == "http":
                counts[f["host"]] = counts.get(f["host"], 0) + 1
    out = {"release": release, "from_run": run_dir.name, "bootstrap": bool(bootstrap), "decisions": decisions,
           "request_counts": counts}
    p = Path(owner_root) / "leakgate" / "baseline.json"
    p.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return p
