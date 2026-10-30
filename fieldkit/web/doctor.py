"""fieldkit web doctor: what the browser fallback needs, what is missing, the exact commands to fix it."""
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path

from . import browser

PINS = json.loads(Path(__file__).with_name("pins.json").read_text(encoding="utf-8"))
FIREFOX = "Gorilla Firefox"


def asset_key(system=None, machine=None):
    system, machine = system or sys.platform, (machine or platform.machine()).lower()
    arm = machine in ("arm64", "aarch64")
    if system.startswith("win"):
        return None if arm else "windows-x64"
    if system == "darwin":
        return "macos-arm64" if arm else "macos-x64"
    return "linux-arm64" if arm else "linux-x64"


def tools_dir():
    base = Path.home() / ("AppData/Local/fieldkit" if sys.platform.startswith("win") else ".local/share/fieldkit")
    return base / "tools" / "geckodriver" / PINS["geckodriver"]["version"]


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_url(asset):
    pin = PINS["geckodriver"]
    return f"{pin['base']}/{pin['version']}/{asset['file']}"


def install_plan(asset):
    return (f"download {archive_url(asset)} (sha256 {asset['sha256']}), verify, unpack into {tools_dir()}, then set "
            f'"browser_fetch": {{"geckodriver": "{tools_dir() / asset["exe"]}"}} in fieldkit.local.json')


def check(cfg=None, has_module=None, run=subprocess.run, key=None):
    """-> list of {"item", "ok", "detail", "fix"}. fix is the exact command or step, "" when ok."""
    cfg = browser.config() if cfg is None else cfg
    has_module = has_module or (lambda n: importlib.util.find_spec(n) is not None)
    key = key or asset_key()
    pin = PINS["geckodriver"]
    asset = pin["assets"].get(key or "")
    rows = []
    ok = has_module("selenium")
    rows.append({"item": "python package selenium", "ok": ok, "detail": f"pinned {PINS['selenium']}",
                 "fix": "" if ok else f'"{sys.executable}" -m pip install --user selenium=={PINS["selenium"]}'})
    ff = cfg.get("firefox")
    ok = bool(ff) and Path(ff).is_file()
    rows.append({"item": FIREFOX, "ok": ok, "detail": ff or "not set",
                 "fix": "" if ok else 'put "browser_fetch": {"firefox": FULL_PATH_TO_firefox} in fieldkit.local.json'})
    gd = cfg.get("geckodriver")
    if gd and Path(gd).is_file():
        try:
            ver = run([gd, "--version"], capture_output=True, text=True, timeout=20).stdout.splitlines()[0]
        except Exception as e:
            ver = f"cannot run: {e}"
        ok = pin["version"].lstrip("v") in ver
        rows.append({"item": "geckodriver", "ok": ok, "detail": f"{ver}; file sha256 {sha256_of(gd)} (the archive's "
                     f"sha256 is checked at install; pinned {pin['version']})",
                     "fix": "" if ok or not asset else install_plan(asset)})
    else:
        rows.append({"item": "geckodriver", "ok": False,
                     "detail": (gd or "not set") + ("" if asset else f" (no pinned build for {key or 'this CPU'})"),
                     "fix": install_plan(asset) if asset else f"no pinned geckodriver for {key or 'this CPU'}"})
    return rows


def _download(url, dest):
    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)


def install_geckodriver(key=None, download=_download, say=print):
    """Download the pinned archive, check its sha256, unpack. -> the driver path. Raises browser.BrowserError."""
    import tarfile
    import tempfile
    import zipfile
    key = key or asset_key()
    asset = PINS["geckodriver"]["assets"].get(key or "")
    if not asset:
        raise browser.BrowserError(f"no pinned geckodriver for {key}")
    dest = tools_dir()
    with tempfile.TemporaryDirectory() as tmp:
        arc = Path(tmp) / asset["file"]
        download(archive_url(asset), arc)
        got = sha256_of(arc)
        if got != asset["sha256"]:
            raise browser.BrowserError(
                f"sha256 mismatch for {asset['file']}: got {got}, pinned {asset['sha256']}: not installed")
        if asset["file"].endswith(".zip"):
            with zipfile.ZipFile(arc) as z:
                data = z.read(asset["exe"])
        else:
            with tarfile.open(arc) as t:
                data = t.extractfile(asset["exe"]).read()
    dest.mkdir(parents=True, exist_ok=True)
    exe = dest / asset["exe"]
    exe.write_bytes(data)
    exe.chmod(0o755)
    say(f"installed {exe}")
    return exe


def install(rows, key=None, isatty=None, ask=input, say=print, download=_download, run=subprocess.run):
    """Show what is missing with the exact commands, ask y/n at a terminal, install. -> (installed, refusal text)."""
    missing = [r for r in rows if not r["ok"] and r["item"] != FIREFOX]
    if not missing:
        return [], ""
    tty = sys.stdin.isatty() if isatty is None else isatty
    if not tty:
        return [], "REFUSED: installing needs a person at a terminal; nothing was installed. Missing: " + "; ".join(
            f"{r['item']} -> {r['fix']}" for r in missing)
    done = []
    for r in missing:
        say(f"{r['item']}: {r['fix']}")
        if ask("install this? [y/N] ").strip().lower() not in ("y", "yes"):
            continue
        if r["item"].startswith("python package"):
            run([sys.executable, "-m", "pip", "install", "--user", f"selenium=={PINS['selenium']}"], check=True)
        else:
            exe = install_geckodriver(key, download, say)
            say(f'now add to fieldkit.local.json: "browser_fetch": {{"geckodriver": "{exe}"}}')
        done.append(r["item"])
    return done, ""
