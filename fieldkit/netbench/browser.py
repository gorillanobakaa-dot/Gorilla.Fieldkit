"""The browser side: a throwaway copy of the install, throwaway profiles, a headless launch, and its processes.

- The install is never run in place: it is copied (only while no browser runs from it) into a marked throwaway
  folder (buildh/throwaway.py), and only the copy gets distribution/policies.json with the run's throwaway
  certificate authority.
- Every launch uses a fresh throwaway profile (B1's restart visit reuses the profile of the same repetition, which
  is the point of it). The user's own profiles are never opened.
- The proxy prefs point every request at the relay; `network.dns.forceResolve` sends any name the browser would
  resolve itself to 127.0.0.1, and `network.proxy.failover_direct` is off, so nothing can leave the machine.
- Only the processes this module started are stopped: the launched PID with taskkill /T, then any descendant it
  recorded that is still alive (checked by PID and start time). Nothing is ever stopped by name.
"""
import configparser
import json
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import psutil

from ..buildh import throwaway

# prefs every run gets: plumbing, not tuning (recorded in the result)
HARNESS_PREFS = {
    "browser.shell.checkDefaultBrowser": False,
    "browser.aboutwelcome.enabled": False,
    "browser.startup.homepage_override.mstone": "ignore",
    "startup.homepage_welcome_url": "",
    "startup.homepage_welcome_url.additional": "",
    "browser.startup.page": 0,
    "browser.sessionstore.resume_from_crash": False,
    "toolkit.startup.max_resumed_crashes": -1,
    "datareporting.policy.dataSubmissionPolicyBypassNotification": True,
    "network.proxy.type": 1,
    "network.proxy.http": "127.0.0.1",
    "network.proxy.ssl": "127.0.0.1",
    "network.proxy.share_proxy_settings": False,
    "network.proxy.no_proxies_on": "",
    "network.proxy.allow_hijacking_localhost": True,
    "network.proxy.failover_direct": False,
    "network.dns.forceResolve": "127.0.0.1",
    "network.dns.disableIPv6": True,
    "network.trr.mode": 5,
    "network.connectivity-service.enabled": False,
    "network.captive-portal-service.enabled": False,
}

# the install's own values of the prefs this study is about (read from omni.ja, recorded with each result)
STUDY_PREFS = (
    "network.buffer.cache.size", "network.buffer.cache.count", "network.http.http2.send-buffer-size",
    "network.http.max-persistent-connections-per-server", "network.http.max-connections",
    "network.http.response.timeout", "network.http.connection-timeout", "network.http.tls-handshake-timeout",
    "network.http.connection-retry-timeout", "network.tcp.keepalive.enabled", "network.tcp.keepalive.idle_time",
    "network.tcp.keepalive.retry_interval", "network.tcp.keepalive.probe_count",
    "network.http.tcp_keepalive.short_lived_connections", "network.http.tcp_keepalive.short_lived_idle_time",
    "network.http.tcp_keepalive.short_lived_time", "network.http.tcp_keepalive.long_lived_connections",
    "network.http.tcp_keepalive.long_lived_idle_time", "network.http.http3.enable", "network.http.http3.cc_algorithm",
    "browser.cache.disk.enable", "browser.cache.memory.enable", "browser.cache.memory.capacity",
    "network.http.pacing.requests.enabled", "network.http.pacing.requests.min-parallelism",
    "network.http.pacing.requests.hz", "network.http.pacing.requests.burst", "network.dnsCacheExpiration",
    "media.autoplay.default", "media.preload.default", "media.preload.auto", "gfx.downloadable_fonts.enabled",
    "permissions.default.image", "network.process.enabled", "privacy.resistFingerprinting",
)


def build_info(install_dir):
    """application.ini [App] -> {"build_id", "version", "codename", "install_dir"}."""
    cp = configparser.ConfigParser()
    ini = Path(install_dir) / "application.ini"
    cp.read(ini, encoding="utf-8")
    if not cp.has_section("App"):
        raise FileNotFoundError(f"no application.ini [App] in {install_dir}")
    app = cp["App"]
    return {"build_id": app.get("BuildID"), "version": app.get("Version"), "codename": app.get("CodeName"),
            "install_dir": str(install_dir)}


def _pref_values(text, names):
    out = {}
    for m in re.finditer(r'^\s*(?:pref|user_pref|sticky_pref)\(\s*"([^"]+)"\s*,\s*(.+?)\s*(?:,\s*(?:locked|sticky))?\)\s*;',
                         text, re.M):
        if m.group(1) in names:
            raw = m.group(2).strip()
            try:
                out[m.group(1)] = json.loads(raw)
            except ValueError:
                out[m.group(1)] = raw
    return out


def install_prefs(install_dir, names=STUDY_PREFS):
    """The default values the install ships for `names` (greprefs.js, then browser firefox.js, later wins).
    A pref not set in either file keeps its compiled-in default and is listed as such."""
    names = set(names)
    found = {}
    for omni, members in ((Path(install_dir) / "omni.ja", ("greprefs.js",)),
                          (Path(install_dir) / "browser" / "omni.ja", ("defaults/preferences/firefox.js",))):
        try:
            with zipfile.ZipFile(omni) as z:
                for m in members:
                    if m in z.namelist():
                        found.update(_pref_values(z.read(m).decode("utf-8", "replace"), names))
        except (OSError, zipfile.BadZipFile):
            continue
    return {n: found.get(n, "(compiled-in default)") for n in sorted(names)}


def running_from(install_dir):
    """PIDs of processes whose executable lives in `install_dir`."""
    root = os.path.normcase(str(Path(install_dir).resolve())) + os.sep
    pids = []
    for p in psutil.process_iter(["pid", "exe"]):
        exe = p.info.get("exe")
        if exe and os.path.normcase(exe).startswith(root):
            pids.append(p.info["pid"])
    return pids


def wait_not_running(install_dir, max_wait=3600, poll=30, say=print, sleep=time.sleep):
    """Wait until no browser runs from the install. -> True when free, False when still running after max_wait."""
    waited = 0
    while True:
        pids = running_from(install_dir)
        if not pids:
            return True
        if waited >= max_wait:
            return False
        say(f"  the browser is running from {install_dir} (PIDs {pids[:6]}); waiting for it to close "
            f"({waited // 60} of {max_wait // 60} min)")
        sleep(poll)
        waited += poll


def copy_install(install_dir, ca_pem, say=print):
    """A throwaway copy of the install, trusting the run's CA through policies.json. -> (marked root, copy dir)."""
    root = throwaway.profile("gnetbench_", user_js=None)
    dest = root / "browser"
    say(f"  copying {install_dir} -> {dest}")
    shutil.copytree(install_dir, dest)
    ca = root / "netbench-ca.pem"
    shutil.copy2(ca_pem, ca)
    pol = dest / "distribution" / "policies.json"
    data = {"policies": {}}
    if pol.is_file():
        data = json.loads(pol.read_text(encoding="utf-8"))
    data.setdefault("policies", {}).setdefault("Certificates", {}).setdefault("Install", []).append(str(ca))
    pol.parent.mkdir(exist_ok=True)
    pol.write_text(json.dumps(data, indent=1), encoding="utf-8")
    install_quit_hook(dest)
    return root, dest


# A clean shutdown on request (2026-10-04). Browser.stop() used to end the browser with taskkill /F only; a forced
# kill can leave the disk cache index unwritten, so B1's restart visit at 5 KB/s found an empty cache while the
# same build kept it on the Starlink link. A person closes the browser; so does the bench now: the copy carries an
# autoconfig script that quits normally when QUIT_FILE appears in the profile, and stop() waits for that before it
# falls back to the forced kill.
QUIT_FILE = "netbench-quit"
QUIT_CFG = """// fieldkit netbench: quit normally when the bench asks (a file in the profile), like a person closing the window
const { setInterval: __nbI } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const __nbQuit = Services.dirsvc.get("ProfD", Ci.nsIFile);
__nbQuit.append("%s");
__nbI(() => { if (__nbQuit.exists()) { Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit); } }, 250);
""" % QUIT_FILE


def install_quit_hook(app_dir):
    (Path(app_dir) / "defaults" / "pref").mkdir(parents=True, exist_ok=True)
    (Path(app_dir) / "defaults" / "pref" / "gnetbench-autoconfig.js").write_bytes(
        b'pref("general.config.filename", "gnetbench.cfg");\npref("general.config.obscure_value", 0);\n'
        b'pref("general.config.sandbox_enabled", false);\n')
    (Path(app_dir) / "gnetbench.cfg").write_bytes(QUIT_CFG.encode("utf-8"))


def user_js(prefs):
    lines = []
    for k, v in prefs.items():
        lines.append(f"user_pref({json.dumps(k)}, {json.dumps(v)});")
    return "\n".join(lines) + "\n"


def new_profile(prefs):
    return throwaway.profile("gnetbench_", user_js=user_js(prefs))


def role_of(cmdline):
    """'parent' for the main process, else the child type Firefox passes last (tab, socket, gpu, rdd, utility...)."""
    if "-contentproc" not in cmdline:
        return "parent"
    for tok in reversed(cmdline):
        if tok in ("tab", "socket", "gpu", "rdd", "utility", "gmplugin", "vr", "forkserver", "ipdlunittest"):
            return tok
    return "child"


class Browser:
    """One headless launch. Records every descendant it sees, samples memory on request, stops only its own."""

    def __init__(self, exe, profile, url, env_extra=None, size=(1366, 768)):
        env = dict(os.environ, MOZ_CRASHREPORTER_DISABLE="1", MOZ_HEADLESS_WIDTH=str(size[0]),
                   MOZ_HEADLESS_HEIGHT=str(size[1]))
        env.pop("MOZ_LOG", None)
        env.pop("MOZ_LOG_FILE", None)
        env.update(env_extra or {})
        self.proc = subprocess.Popen([str(exe), "-headless", "-no-remote", "-profile", str(profile), url], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.pid = self.proc.pid
        self.profile = Path(profile)
        self.clean_exit = False
        self.started = time.perf_counter()
        self.seen = {}                    # pid -> (create_time, role)
        self.samples = []                 # (t, {pid: (role, rss, private)})
        self._stop = threading.Event()
        self._sampler = None
        self.track()

    def track(self):
        try:
            root = psutil.Process(self.pid)
            procs = [root] + root.children(recursive=True)
        except psutil.Error:
            return []
        for p in procs:
            if p.pid not in self.seen:
                try:
                    self.seen[p.pid] = (p.create_time(), role_of(p.cmdline()))
                except psutil.Error:
                    continue
        return procs

    def sample(self):
        row = {}
        for p in self.track():
            try:
                mi = p.memory_info()
                row[p.pid] = (self.seen.get(p.pid, (0, "?"))[1], mi.rss, getattr(mi, "private", mi.rss))
            except psutil.Error:
                continue
        self.samples.append((time.perf_counter(), row))
        return row

    def start_sampling(self, every=0.5):
        def loop():
            while not self._stop.is_set():
                self.sample()
                self._stop.wait(every)
        self._sampler = threading.Thread(target=loop, daemon=True)
        self._sampler.start()

    def alive(self):
        return self.proc.poll() is None

    def stop(self, clean_wait=30):
        """A normal quit first (QUIT_FILE; the copy's autoconfig acts on it), then taskkill /T on our PID and any
        recorded descendant still alive (same PID AND same start time). -> PIDs that had to be killed."""
        self._stop.set()
        if self._sampler:
            self._sampler.join(5)
        self.track()
        try:
            (self.profile / QUIT_FILE).write_bytes(b"quit\n")
            self.proc.wait(clean_wait)
            self.clean_exit = True
        except (OSError, subprocess.TimeoutExpired):
            self.clean_exit = False
        try:
            (self.profile / QUIT_FILE).unlink()
        except OSError:
            pass
        if not self.clean_exit:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"], capture_output=True)
            else:
                self.proc.kill()
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            pass
        left, ours = [], []
        for pid, (ct, _role) in self.seen.items():
            try:
                p = psutil.Process(pid)
                if abs(p.create_time() - ct) < 0.01:
                    ours.append(p)
                    if p.is_running():
                        p.kill()
                        left.append(pid)
            except psutil.Error:
                continue
        psutil.wait_procs(ours, timeout=15)      # files in the profile are free only once every process is gone
        return left


UNREMOVED = []


def discard(folder):
    """Delete a throwaway folder this run made (refused for anything else, see throwaway.discard). A folder that
    could not be removed is remembered in UNREMOVED so the run can say so."""
    ok = throwaway.discard(folder, tries=15)
    if not ok and Path(folder).exists():
        UNREMOVED.append(str(folder))
    return ok
