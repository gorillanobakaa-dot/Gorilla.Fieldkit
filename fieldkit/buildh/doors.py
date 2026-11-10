# Version: 1.0.0 · updated 26-11-09-12-00
"""P11: the DevTools debugger server's remote door stays shut, proven on the installed build.

    fieldkit build-harness post-install TASK --only doors      the five rows

Owner, 2026-10-31 (idea I-08, test I-03): Unleashed 158 opened a no-prompt debugger on 127.0.0.1:6000 when
distribution/policies.json unlocked the devtools prefs and `-start-debugger-server 6000` was passed. The build now
carries a physical lock in code (patch 22.EGRESS.LOCKDOWN.158/047, like Marionette's 23.REBASE.158/0188). Each row
starts the build headless from a THROWAWAY COPY (its policies.json and the registry are never left changed) with one
way of asking for the server, then looks for a listener of the browser's process tree and tries the debugger
protocol on the port. REFUSED = nothing listened and nothing answered. A way that cannot be tried is SKIPPED-as-FAIL,
never passed (the port busy before the start, the copy not made).

    d2   prefs in a fresh profile's user.js + -start-debugger-server
    d3   everything at once: prefs, -marionette, --remote-debugging-port, -start-debugger-server, MOZ_MARIONETTE
    e1   policies.json locking the devtools prefs + the flags of d3
    e3   policies.json locking only devtools.debugger.remote-enabled + -start-debugger-server
    reg  the same locked prefs as a registry policy (HKCU\\Software\\Policies\\Mozilla\\Firefox, Windows only)
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PORT = 6000
WAIT = 25
DEVTOOLS_PREFS = {"devtools.debugger.remote-enabled": True, "devtools.chrome.enabled": True,
                  "devtools.debugger.prompt-connection": False, "remote.experimental.dynamicstart.enabled": True}
USER_JS = "".join(f'user_pref("{k}", {json.dumps(v)});\n' for k, v in DEVTOOLS_PREFS.items()) \
    + 'user_pref("browser.shell.checkDefaultBrowser", false);\n'
FLAG = ["-start-debugger-server", str(PORT)]
ALL_FLAGS = ["-marionette", "--remote-debugging-port", "9222"] + FLAG
REG_PATH = r"Software\Policies\Mozilla\Firefox"

#: id -> (what, browser arguments, policy prefs or None, registry policy?, extra environment)
DOORS = {
    "d2": ("prefs in user.js + -start-debugger-server", FLAG, None, False, {}),
    "d3": ("all flags, prefs and MOZ_MARIONETTE together", ALL_FLAGS, None, False, {"MOZ_MARIONETTE": "1"}),
    "e1": ("policies.json locking the devtools prefs + all flags", ALL_FLAGS, DEVTOOLS_PREFS, False, {"MOZ_MARIONETTE": "1"}),
    "e3": ("policies.json locking only remote-enabled + -start-debugger-server", FLAG,
           {"devtools.debugger.remote-enabled": True}, False, {}),
    "reg": ("registry policy locking the devtools prefs + -start-debugger-server", FLAG, DEVTOOLS_PREFS, True, {}),
}


def policy_json(prefs):
    return json.dumps({"policies": {"Preferences": {k: {"Value": v, "Status": "locked"} for k, v in prefs.items()}}}, indent=2)


def answers(port=None, timeout=2.0):
    """True when something on 127.0.0.1:port speaks first or answers `getRoot` (the DevTools protocol greeting)."""
    try:
        with socket.create_connection(("127.0.0.1", port or PORT), timeout=timeout) as s:
            s.settimeout(timeout)
            try:
                return bool(s.recv(256))
            except OSError:
                return True                       # connected and held open: a listener accepted us
    except OSError:
        return False


def listening(pid):
    """The LISTEN ports of the process tree of `pid` (psutil), or None when it cannot be read."""
    try:
        import psutil
        tree = [psutil.Process(pid)] + psutil.Process(pid).children(recursive=True)
        ports = set()
        for p in tree:
            for c in p.net_connections(kind="inet"):
                if c.status == psutil.CONN_LISTEN:
                    ports.add(c.laddr.port)
        return ports
    except Exception:
        return None


def judge(door, listened, connected, started):
    """-> (ok, evidence). Pure: REFUSED needs the browser to have STARTED and nothing to listen or answer."""
    if not started:
        return False, f"{door}: the browser did not start, nothing was proven"
    if listened or connected:
        return False, f"{door}: OPEN, listening {sorted(listened) or '-'}, answered={connected}"
    return True, f"{door}: refused, {WAIT} s headless, nothing listening, port {PORT} silent"


def _env(extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("MOZ_")}
    env.update(extra)
    return env


def _registry(prefs, on):
    """Write (on) or remove the registry policy under HKCU: one REG_MULTI_SZ value `Preferences` holding the JSON
    (the form Mozilla's own policy templates use). Windows only; -> True when it was written."""
    import winreg
    if not on:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, "Preferences")
        except OSError:
            pass
        for path in (REG_PATH, r"Software\Policies\Mozilla"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)          # only when empty: the owner's own keys stay
            except OSError:
                break
        return False
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as k:
        body = {n: {"Value": v, "Status": "locked"} for n, v in prefs.items()}
        winreg.SetValueEx(k, "Preferences", 0, winreg.REG_MULTI_SZ, [json.dumps(body)])
    return True


def _registry_present():
    import winreg
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH))
        return True
    except OSError:
        return False


def try_door(door, copy_dir, say=lambda m: None, wait=WAIT):
    """Start the copy with the way `door` asks for the server -> {"ok", "evidence"}."""
    from . import layout, throwaway
    _, args, prefs, reg, extra = DOORS[door]
    if answers(timeout=0.5):
        return {"ok": False, "evidence": f"{door}: port {PORT} answered BEFORE the start; free it and run again"}
    dist = Path(copy_dir) / "distribution"
    pol = dist / "policies.json"
    saved = pol.read_bytes() if pol.is_file() else None
    prof = throwaway.profile("gdoors_", USER_JS)
    wrote_reg = False
    proc = None
    listened, connected, started = set(), False, False
    try:
        if prefs and not reg:
            dist.mkdir(exist_ok=True)
            pol.write_text(policy_json(prefs), encoding="utf-8")
        if reg:
            if _registry_present():
                return {"ok": False, "evidence": f"{door}: the registry already holds Mozilla policies; not touched, not proven"}
            wrote_reg = _registry(prefs, True)
        exe = layout.exe(copy_dir)
        proc = layout.start([str(exe), "-profile", str(prof), "-no-remote", "-headless", *args, "about:blank"],
                            env=_env(extra), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < wait:
            time.sleep(1)
            if proc.poll() is not None and time.time() - t0 < 3:
                break
            started = started or proc.poll() is None
            got = listening(proc.pid)
            if got:
                listened |= got & {PORT}
            if answers(timeout=0.5):
                connected = True
            if listened or connected:
                break
        started = started or proc.poll() is None
    finally:
        if proc is not None:
            layout.stop(proc)
        if wrote_reg:
            _registry(prefs, False)
        if prefs and not reg:
            if saved is None:
                pol.unlink(missing_ok=True)
            else:
                pol.write_bytes(saved)
        throwaway.discard(prof)
    ok, ev = judge(door, listened, connected, started)
    return {"ok": ok, "evidence": ev}


def copy_of(install_dir, parent=None):
    """A copy of the install in a new scratch folder -> its path (the real install and its policies stay untouched)."""
    root = Path(parent) if parent else Path(tempfile.mkdtemp(prefix="gdoors_copy_", dir=tempfile.gettempdir()))
    dst = root / "build"
    shutil.copytree(install_dir, dst, symlinks=True)
    return dst


def rows(install_dir, run=None, copy=None, platform=None):
    """One post-install row per door; every one must be REFUSED. `run(door, copy_dir)` is injectable for tests."""
    plat = platform or sys.platform
    want = [d for d in DOORS if d != "reg" or plat == "win32"]
    run = run or try_door
    made = None
    if copy is None:
        made = copy = copy_of(install_dir)
    out = []
    try:
        for d in want:
            r = run(d, copy)
            out.append({"check": f"doors {d}: DevTools server refused ({DOORS[d][0]})", "ok": bool(r["ok"]), "evidence": r["evidence"]})
    finally:
        if made is not None:
            shutil.rmtree(made.parent, ignore_errors=True)       # our own copy, made above in the temp folder
    return out
