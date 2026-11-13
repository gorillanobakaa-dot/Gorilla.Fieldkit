# Version: 1.0.0 · updated 26-11-13
"""`fieldkit appcapture`: put a capture proxy in front of a REAL desktop app and read what it really sends.

    fieldkit appcapture start APP [--exe PATH] [--kind K] [--port N] [--json]
                                    mitmdump in front of the app (HTTPS_PROXY, SSL_CERT_FILE bundle = system CAs + the
                                    mitm CA, NODE_EXTRA_CA_CERTS, Electron --proxy-server); the running app is
                                    restarted by its recorded PID (+ creation time), never by name
    fieldkit appcapture show APP [--host H] [--path P] [--bodies] [--json]
                                    endpoints, or request/response bodies; tokens redacted (ya29., 1//, Authorization...)
    fieldkit appcapture stop APP [--recycle]   stop the proxy and the proxied app by PID; --recycle recycles the flows
    fieldkit appcapture recycle APP            flows to the Recycle Bin through `fieldkit size recycle`
    fieldkit appcapture status [APP]

WHY (2026-11-13): an agent faked model ids for a desktop client, probed with guessed requests and recompiled twice;
the real app, behind a proxy, showed the true request in minutes (docs/APP-TRAFFIC-FIRST.md). Observe the real client
first; this is the procedure as a tool.

Flows live in the scratch root (appcapture-APP/), never in a repo. mitmproxy runs on its own venv interpreter with -I.
Works on Linux, Windows, macOS; the app profile (apps.yaml) names the install path per system."""
import argparse
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import psutil
import yaml

from . import redact

HERE = Path(__file__).parent
CT_TOL = 2.0
WIN = sys.platform == "win32"


def profiles():
    return yaml.safe_load((HERE / "apps.yaml").read_text(encoding="utf-8"))


def workdir(app, root=None):
    if root is None:
        from fieldkit import scratch
        root = scratch.root()
    return Path(root) / f"appcapture-{app}"


# ------------------------------------------------------------------------------------------------ proxy environment
def proxy_env(port, bundle):
    """The environment a proxied app (and its Go/Node children) needs."""
    url = f"http://127.0.0.1:{port}"
    b = str(bundle)
    return {"HTTPS_PROXY": url, "HTTP_PROXY": url, "https_proxy": url, "http_proxy": url,
            "ALL_PROXY": url, "NO_PROXY": "127.0.0.1,localhost",
            "SSL_CERT_FILE": b, "REQUESTS_CA_BUNDLE": b, "NODE_EXTRA_CA_CERTS": b, "CURL_CA_BUNDLE": b}


def app_args(kind, port, k=None):
    k = (k or profiles()["kinds"])
    spec = k.get(kind) or k["generic"]
    out = [a.format(port=port) for a in spec.get("args", [])]
    if spec.get("chromium_ca_flag"):
        out.append(spec["chromium_ca_flag"])
    return out


def system_cafile():
    p = ssl.get_default_verify_paths()
    for c in (p.cafile, p.openssl_cafile, "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
              "/etc/ssl/cert.pem"):
        if c and Path(c).is_file():
            return Path(c)
    try:
        import certifi
        return Path(certifi.where())
    except ImportError:
        return None


def build_bundle(mitm_ca, out, system=None):
    """system CAs + the mitm CA in one PEM: the app still trusts everything it trusted, plus the proxy."""
    system = system or system_cafile()
    parts = [Path(system).read_text(encoding="utf-8", errors="replace")] if system else []
    parts.append(Path(mitm_ca).read_text(encoding="utf-8"))
    Path(out).write_text("\n".join(parts), encoding="utf-8")
    return Path(out)


# ------------------------------------------------------------------------------------------------ mitmproxy binaries
def mitm_tools(which=shutil.which):
    """-> (mitmdump argv prefix, interpreter for dump_flows). The mitmproxy venv's own python, always with -I."""
    exe = which("mitmdump")
    if not exe:
        return None, None
    exe = Path(exe)
    if WIN:
        py = next((c for c in (exe.parent / "python.exe", exe.parent.parent / "python.exe") if c.is_file()), None)
        return [str(exe)], (str(py) if py else None)
    try:
        first = exe.read_text(errors="replace").splitlines()[0]
    except OSError:
        first = ""
    py = first[2:].strip().split()[0] if first.startswith("#!") else None
    if py and py.endswith("/env"):
        py = None
    py = py or str(exe.resolve().parent / "python")
    return [py, "-I", str(exe)], py


# ------------------------------------------------------------------------------------------------ processes (by PID)
def _same(pid, ctime):
    try:
        p = psutil.Process(pid)
        return p.status() != psutil.STATUS_ZOMBIE and abs(p.create_time() - float(ctime)) <= CT_TOL
    except (psutil.Error, TypeError, ValueError):
        return False


def stop_pid(pid, ctime, tree=False, grace=5.0):
    """Stop PID only while its creation time still matches (a reused number is not ours). -> list of stopped PIDs."""
    if not _same(pid, ctime):
        return []
    p = psutil.Process(pid)
    procs = (p.children(recursive=True) if tree else []) + [p]
    for x in procs:
        try:
            x.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=grace)
    for x in alive:
        try:
            x.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=grace)
    return [x.pid for x in procs]


def find_app(exe_paths):
    """Running processes whose executable is one of `exe_paths` or lives in the same folder. -> [(pid, ctime, argv)]."""
    dirs = {str(Path(e).resolve().parent) for e in exe_paths}
    out = []
    for p in psutil.process_iter(["pid", "exe", "cmdline", "create_time", "ppid"]):
        try:
            exe = p.info["exe"]
            if exe and str(Path(exe).resolve().parent) in dirs:
                out.append((p.info["pid"], p.info["create_time"], p.info["cmdline"] or [exe], p.info["ppid"]))
        except (psutil.Error, OSError):
            pass
    return out


def main_process(found):
    """The process of the app none of the others started: its launcher (the one to restart)."""
    pids = {f[0] for f in found}
    roots = [f for f in found if f[3] not in pids]
    return min(roots or found, key=lambda f: f[1]) if found else None


def resolve_exe(app, exe=None, plat=None):
    if exe:
        return [Path(os.path.expandvars(os.path.expanduser(exe)))]
    plat = plat or {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    prof = profiles()["apps"].get(app, {})
    return [Path(os.path.expandvars(os.path.expanduser(c))) for c in prof.get("exe", {}).get(plat, [])]


# ------------------------------------------------------------------------------------------------ state
def state_path(app, root=None):
    return workdir(app, root) / "state.json"


def load_state(app, root=None):
    p = state_path(app, root)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def save_state(app, st, root=None):
    p = state_path(app, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=1), encoding="utf-8")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _detached(argv, env=None, log=None, cwd=None):
    kw = {"stdin": subprocess.DEVNULL, "stdout": log or subprocess.DEVNULL, "stderr": subprocess.STDOUT,
          "env": env, "cwd": cwd, "close_fds": True}
    if WIN:
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000       # DETACHED | NEW_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(argv, **kw)


def _ct(pid):
    return psutil.Process(pid).create_time()


# ------------------------------------------------------------------------------------------------ start / stop
def start(app, exe=None, kind=None, port=None, root=None, say=print, wait=30.0):
    prof = profiles()["apps"].get(app, {})
    kind = kind or prof.get("kind", "generic")
    cands = resolve_exe(app, exe)
    exe_path = next((c for c in cands if c.exists()), None)
    if exe_path is None:
        return {"ok": False, "error": f"no executable for {app!r}: pass --exe PATH (tried {[str(c) for c in cands]})"}
    mitm, _ = mitm_tools()
    if not mitm:
        return {"ok": False, "error": "mitmdump not found on PATH (the mitmproxy venv): install mitmproxy"}
    old = load_state(app, root)
    if old and _same(old["mitm"]["pid"], old["mitm"]["ctime"]):
        return {"ok": False, "error": f"a capture of {app} is already running (mitm pid {old['mitm']['pid']}): stop it first"}
    wd = workdir(app, root)
    wd.mkdir(parents=True, exist_ok=True)
    try:
        wd.chmod(0o700)          # the raw flows hold tokens: owner only, never printed, recycled by `stop --recycle`
    except OSError:
        pass
    conf, flows = wd / "conf", wd / "flows.mitm"
    port = port or _free_port()
    log = open(wd / "mitmdump.log", "ab")
    mp = _detached(mitm + ["--listen-host", "127.0.0.1", "--listen-port", str(port), "--set", f"confdir={conf}",
                           "-w", str(flows), "-q"], log=log)
    ca = conf / "mitmproxy-ca-cert.pem"
    t0 = time.time()
    while time.time() - t0 < wait:
        if mp.poll() is not None:
            return {"ok": False, "error": f"mitmdump exited early (rc {mp.returncode}); see {wd / 'mitmdump.log'}"}
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                if ca.is_file():
                    break
        except OSError:
            time.sleep(0.3)
    else:
        stop_pid(mp.pid, _ct(mp.pid))
        return {"ok": False, "error": "mitmdump did not listen in time"}
    bundle = build_bundle(ca, wd / "ca-bundle.pem")
    # the running app: restart it by the PID recorded now, never by name
    found = find_app([exe_path])
    launcher = main_process(found)
    argv = list(launcher[2]) if launcher else [str(exe_path)]
    stopped = []
    if launcher:
        stopped = stop_pid(launcher[0], launcher[1], tree=True)
        for pid, ct, _a, _pp in found:                           # helpers the launcher did not parent (detached)
            stopped += [] if pid in stopped else stop_pid(pid, ct)
    env = dict(os.environ, **proxy_env(port, bundle))
    extra = [a for a in app_args(kind, port) if a not in argv]
    ap = _detached(argv + extra, env=env, log=open(wd / "app.log", "ab"))
    st = {"app": app, "kind": kind, "port": port, "flows": str(flows), "bundle": str(bundle), "started": time.time(),
          "mitm": {"pid": mp.pid, "ctime": _ct(mp.pid)}, "app_proc": {"pid": ap.pid, "ctime": _ct(ap.pid), "argv": argv + extra},
          "replaced": stopped}
    save_state(app, st, root)
    say(f"capture of {app} on 127.0.0.1:{port}; app restarted (pid {ap.pid}); flows {flows}")
    return {"ok": True, **st}


def stop(app, recycle_flows=False, root=None):
    st = load_state(app, root)
    if not st:
        return {"ok": False, "error": f"no capture state for {app!r}"}
    done = stop_pid(st["app_proc"]["pid"], st["app_proc"]["ctime"], tree=True)
    done += stop_pid(st["mitm"]["pid"], st["mitm"]["ctime"])
    res = {"ok": True, "stopped": done}
    if recycle_flows:
        res["recycle"] = recycle(app, root)
    return res


def recycle(app, root=None):
    """The capture folder (flows, CA key, bundle, logs) to the Recycle Bin: never a permanent delete."""
    st = load_state(app, root)
    if st and (_same(st["mitm"]["pid"], st["mitm"]["ctime"]) or _same(st["app_proc"]["pid"], st["app_proc"]["ctime"])):
        return {"ok": False, "error": "the capture is still running: stop it first"}
    wd = workdir(app, root)
    if not wd.exists():
        return {"ok": True, "note": "nothing to recycle"}
    r = subprocess.run([sys.executable, "-m", "fieldkit", "size", "recycle", str(wd), "--whole-folder", "--json"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        return {"ok": True, "note": f"recycled {wd}"}
    why = ""
    try:
        why = "; ".join(x.get("why", "") for x in json.loads(r.stdout).get("refused", []) or [])[:300]
    except (ValueError, AttributeError):
        pass
    return {"ok": False, "error": f"fieldkit size recycle refused {wd} (rc {r.returncode}) {why}".strip()}


# ------------------------------------------------------------------------------------------------ show
def read_flows(flows):
    """-> list of raw flow dicts, parsed by the mitmproxy venv's interpreter (-I)."""
    _, py = mitm_tools()
    if not py:
        raise RuntimeError("mitmproxy interpreter not found")
    r = subprocess.run([py, "-I", str(HERE / "dump_flows.py"), str(flows)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:])
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def clean(rec):
    """One flow with every token redacted: the only shape that is ever printed."""
    return {**rec, "path": redact.text(rec["path"]), "req_headers": redact.headers(rec["req_headers"]),
            "resp_headers": redact.headers(rec["resp_headers"]), "req_body": redact.text(rec["req_body"]),
            "resp_body": redact.text(rec["resp_body"])}


def select(recs, host=None, path=None):
    return [r for r in recs if (not host or host in r["host"]) and (not path or path in r["path"])]


def endpoints(recs):
    """[(method, host, path-without-query, status-set, count)] in first-seen order."""
    seen = {}
    for r in recs:
        k = (r["method"], r["host"], r["path"].split("?")[0])
        e = seen.setdefault(k, {"status": set(), "n": 0})
        e["status"].add(r["status"])
        e["n"] += 1
    return [(*k, sorted(v["status"], key=str), v["n"]) for k, v in seen.items()]


def show(app, host=None, path=None, bodies=False, root=None, recs=None):
    st = load_state(app, root)
    if recs is None:
        if not st:
            raise RuntimeError(f"no capture state for {app!r}")
        recs = read_flows(st["flows"])
    sel = [clean(r) for r in select(recs, host, path)]
    return {"flows": len(sel), "endpoints": endpoints(sel), "requests": sel if bodies else []}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit appcapture")
    sp = ap.add_subparsers(dest="verb", required=True)
    s = sp.add_parser("start")
    s.add_argument("app")
    s.add_argument("--exe")
    s.add_argument("--kind")
    s.add_argument("--port", type=int)
    for v in ("show", "stop", "recycle", "status"):
        q = sp.add_parser(v)
        q.add_argument("app", nargs=None if v != "status" else "?")
        if v == "show":
            q.add_argument("--host")
            q.add_argument("--path")
            q.add_argument("--bodies", action="store_true")
        if v == "stop":
            q.add_argument("--recycle", action="store_true")
    for q in sp.choices.values():
        q.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.verb == "start":
            res = start(a.app, a.exe, a.kind, a.port, say=lambda m: None if a.json else print(m))
        elif a.verb == "stop":
            res = stop(a.app, a.recycle)
        elif a.verb == "recycle":
            res = recycle(a.app)
        elif a.verb == "status":
            st = load_state(a.app) if a.app else None
            live = bool(st and _same(st["mitm"]["pid"], st["mitm"]["ctime"]))
            res = {"ok": True, "state": st, "mitm_alive": live}
        else:
            res = {"ok": True, **show(a.app, a.host, a.path, a.bodies)}
    except RuntimeError as e:
        res = {"ok": False, "error": str(e)}
    if a.json:
        print(json.dumps(res, indent=1, default=str))
    elif not res["ok"]:
        print("appcapture: " + res["error"])
    elif a.verb == "show":
        print(f"{res['flows']} request(s)")
        for m, h, p, stt, n in res["endpoints"]:
            print(f"  {n:>3}x {m:<5} {h}{p}  -> {','.join(map(str, stt))}")
        for r in res["requests"]:
            print(f"\n--- {r['method']} {r['host']}{r['path']} -> {r['status']}\nrequest headers: {json.dumps(r['req_headers'])}"
                  f"\nrequest body ({r['req_len']} bytes): {r['req_body'] or '(empty)'}\nresponse body: {r['resp_body'][:4000]}")
    elif a.verb == "start":
        pass
    else:
        print(json.dumps(res, default=str))
    if res["ok"]:
        print("NEXT: " + {"start": f"use the app, then: fieldkit appcapture show {getattr(a, 'app', '')}",
                          "show": "read the real request; copy its shape, never invent it",
                          "stop": "fieldkit appcapture recycle APP when nobody needs the flows",
                          "recycle": "done", "status": "carry on"}[a.verb])
    return 0 if res["ok"] else 1
