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
            "WEBRTC_POLICY", "PROXY_POLICY", "IPV6_POLICY", "CANARY_POLICY", "SHUTDOWN_POLICY", "REPRODUCIBILITY_POLICY",
            "SOURCE_POLICY", "BINARY_POLICY", "ALLOWLIST_POLICY")


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
        "PROFILE_CONFIGURATION": "fresh throwaway profile per scenario and mode; user.js: no default-browser check, no about:welcome, downloads into the profile",
        "NETWORK_CONFIGURATION": "Windows host network (NOT the spec's namespace); direct and mitmproxy-pinned runs",
    }


def ensure_ca(work):
    conf = Path(work) / "mitm-conf"
    conf.mkdir(parents=True, exist_ok=True)
    ca = conf / "mitmproxy-ca-cert.pem"
    if not ca.exists():
        mitmdump = shutil.which("mitmdump") or str(Path(sys.executable).parent / "Scripts" / "mitmdump.exe")
        p = subprocess.Popen([mitmdump, "--listen-port", str(se.free_port()), "--set", f"confdir={conf}", "-q"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while not ca.exists() and time.time() - t0 < 30:
            time.sleep(0.5)
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
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
    sensors_seen = {e["sensor"] for e in events}

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


# ---------------------------------------------------------------------------------------------- run
def run(zip_path, owner_root, workdir_tree, upstream, workroot, repeat=1, quick=True, only=None, release=False,
        previous_zip=None, n_minus_1_tree=None, say=print):
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
    try:
        for name, target, secs, args, hosts, watch in sc.SCENARIOS:
            if only and name not in only:
                continue
            scen_hosts[name] = hosts
            secs = sc.QUICK.get(name, secs) if quick else secs
            watch = min(watch, 10) if quick else watch
            for rep in range(repeat):
                for mode, bdir in (("direct", direct), ("proxied", proxied)):
                    say(f"  {name} [{mode}] run {rep + 1}/{repeat}: {secs} s + {watch} s after shutdown")
                    if linux:
                        allowed = [v for e in allow.get("entries", []) if e.get("kind") in ("dns", "dest") for v in e.get("values", [])] + list(hosts)
                        ev, art = lx.run_one(bdir, server.url(target), secs, args, work / "runs",
                                             f"{name}-r{rep}", mode, canaries, port, watch, allowed, say=say)
                    else:
                        ev, art = se.run_one(bdir, server.url(target), secs, args, work / "runs", f"{name}-r{rep}", mode, canaries,
                                             proxy_port=port, watch=watch, packets=packets and mode == "direct", say=say)
                    for e in ev:
                        e["scenario"], e["rep"] = name, rep
                    events.extend(ev)
                    artifacts[f"{name}-r{rep}-{mode}"] = art
                    if name == "page" and mode == "proxied" and not any(e["sensor"] == "mitm" and "anthropic" in e["value"] for e in ev):
                        events.append({"scenario": name, "mode": mode, "sensor": "mitm", "kind": "proxy-bypass", "value": "proxy did not take",
                                       "detail": "the page itself never reached mitmproxy"})
    finally:
        server.close()
        if linux:
            lx.netns_down()

    fail, lists = judge(events, allow, scen_hosts, [direct, proxied], server.results, repeat, packets, quick)
    if repeat >= 3:
        fail["REPRODUCIBILITY_POLICY"].extend(reproducibility(events, repeat))
    for p in al.problems(allow):
        fail["ALLOWLIST_POLICY"].append(p)
    if not allow.get("entries"):
        fail["ALLOWLIST_POLICY"].append(f"no allowlist at {allow_path}")
    say("  static source audit ...")
    st = audit.static_audit(workdir_tree, dispositions, n_minus_1_tree)
    if st["unapproved"]:
        fail["SOURCE_POLICY"].append(f"{len(st['unapproved'])} of {st['count']} network-capable source files have no approved disposition"
                                     + (f"; {len(st['new_since_previous'])} new since release N-1" if st["new_since_previous"] else ""))
    say("  binary audit ...")
    bi = audit.binary_audit(direct, previous_zip, dispositions)
    if not previous_zip:
        fail["BINARY_POLICY"].append("no previous release to diff against")
    if bi["unapproved_new"]:
        fail["BINARY_POLICY"].append(f"{len(bi['unapproved_new'])} embedded host(s) new since release N-1 without an approved disposition: {bi['unapproved_new'][:8]}")

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
    return result, events, st, bi
