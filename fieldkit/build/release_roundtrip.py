# Version: 1.0.0 · updated 26-10-16-02-10
"""The published download is the thing people get: remove the installed browser, download the release from GitHub
the way a person would, check it, install it, and prove what is installed is what was published.

Born 2026-10-16. The owner: "delete the old install here, download the new one from the GitHub and make sure it
installs ... this procedure should as always be made into a deterministic tool with its cards, checks and all that".
A build that passes on the machine that made it proves nothing about the file on the release page: a wrong upload,
a truncated asset or a stale .deb under the right name all look fine locally.

    fieldkit build-harness release-roundtrip <task> tag=<tag> [repo=owner/name] [asset=<file.deb>] [keep=1]

Rows (each one a card: check, ok, evidence), in order; the first failure stops the run:
  1. release   the tag exists on GitHub and carries the .deb and SHA256SUMS
  2. download  the .deb comes over plain HTTPS from the public download address (no gh login: a student has none)
  3. checksum  its SHA-256 equals the line for it in the release's SHA256SUMS
  4. package   deb_check: name, version, identity, icons, launcher, logo (the same checks as the build's package stage)
  5. remove    the installed package is removed (apt-get remove: the person's profile in ~/.mozilla is untouched)
  6. absent    dpkg says it is gone and its install folder is empty or absent
  7. install   the DOWNLOADED file is installed (deb_install.install, sudo -n: never asks for a password)
  8. verify    deb_install.verify_install, and the installed version equals the .deb's version
Linux only (a .deb); on Windows the row says so.
"""
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

DEFAULT_REPO = "gorillanobakaa-dot/gorilla-firefox"
PKG = "gorilla-unleashed"


def _run(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def _get(url, dest=None, timeout=1800):
    req = urllib.request.Request(url, headers={"User-Agent": "fieldkit-release-roundtrip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if dest is None:
            return resp.read()
        h = hashlib.sha256()
        with open(dest, "wb") as fh:
            while True:
                b = resp.read(1 << 20)
                if not b:
                    break
                h.update(b)
                fh.write(b)
        return h.hexdigest()


def release_assets(repo, tag, get=_get):
    """-> {name: browser_download_url} of the release, from GitHub's public API (no login)."""
    data = json.loads(get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}"))
    return {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}


def sums_for(text, name):
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name:
            return parts[0].lower()
    return None


def run(tag, workdir, src, repo=DEFAULT_REPO, asset=None, keep=False, say=print, get=_get, deb_check=None,
        deb_install=None, sh=_run, installed=None):
    """-> {"ok", "rows": [...], "deb": path}. `workdir`: an EMPTY folder for the download (only this .deb in it);
    `src`: the port's work tree (its branding folder names the menu entry, D-158-05)."""
    from types import SimpleNamespace
    rows = []

    def row(check, ok, evidence):
        rows.append({"check": f"roundtrip: {check}", "ok": bool(ok), "evidence": evidence})
        say(f"  [{'ok' if ok else 'FAIL'}] {check}: {evidence}")
        return ok

    if sys.platform == "win32":
        row("platform", False, "a .deb round trip is Linux only; on Windows the release is the installer and is "
                               "checked by the Windows release flow")
        return {"ok": False, "rows": rows}
    if deb_check is None:
        from . import deb_check
    if deb_install is None:
        from . import deb_install
    try:
        assets = release_assets(repo, tag, get)
    except Exception as e:
        row("release", False, f"{repo} {tag}: {type(e).__name__}: {e}")
        return {"ok": False, "rows": rows}
    debs = [n for n in assets if n.endswith(".deb") and (asset is None or n == asset)]
    if not row("release", debs and "SHA256SUMS" in assets,
               f"{repo} {tag}: {', '.join(sorted(assets)) or 'no assets'}"):
        return {"ok": False, "rows": rows}
    name = debs[0]
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    deb = work / name
    try:
        got = get(assets[name], deb)
    except Exception as e:
        row("download", False, f"{assets[name]}: {type(e).__name__}: {e}")
        return {"ok": False, "rows": rows}
    row("download", deb.is_file() and deb.stat().st_size > 0, f"{deb} ({deb.stat().st_size:,} bytes) from {assets[name]}")
    want = sums_for(get(assets["SHA256SUMS"]).decode("utf-8", "replace"), name)
    if not row("checksum", want and want == got, f"sha256 {got} {'equals' if want == got else 'differs from'} "
                                                  f"SHA256SUMS {want or '(no line for ' + name + ')'}"):
        return {"ok": False, "rows": rows, "deb": str(deb)}
    chk = deb_check.check(deb, Path(src) / "browser" / "branding" / "gorilla")
    if not row("package", not chk.get("problems"), "DEB-CHECK OK" if not chk.get("problems") else "; ".join(chk["problems"][:3])):
        return {"ok": False, "rows": rows, "deb": str(deb)}
    version = sh(["dpkg-deb", "-f", str(deb), "Version"])[1].strip()
    ctx = SimpleNamespace(vars={"release": str(work), "src": str(src), "version": version.split("-")[0]})
    if not keep:
        rc, out = sh(["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "/usr/bin/apt-get", "remove", "-y", PKG])
        if not row("remove", rc == 0, f"apt-get remove {PKG} exited {rc}" + ("" if rc == 0 else f": {out[-200:]}; a person "
                   f"runs: sudo /usr/bin/apt-get remove {PKG}")):
            return {"ok": False, "rows": rows, "deb": str(deb)}
        rc, out = sh(["dpkg-query", "-W", "-f=${Status}", PKG])
        gone = rc != 0 or "installed" not in out.split()[-1:]
        lib = Path(installed or f"/usr/lib/{PKG}")
        left = list(lib.glob("*")) if lib.is_dir() else []
        if not row("absent", gone and not left, f"dpkg status: {out or 'not known'}; /usr/lib/{PKG}: "
                   f"{len(left)} item(s) left in {lib}"):
            return {"ok": False, "rows": rows, "deb": str(deb)}
    ins = deb_install.stage_install(ctx)
    if not row("install", ins.get("ok"), ins.get("detail", "")):
        return {"ok": False, "rows": rows, "deb": str(deb)}
    ver = deb_install.verify_install(ctx)
    now = sh(["dpkg-query", "-W", "-f=${Version}", PKG])[1].strip()
    row("verify", ver.get("ok") and now == version, f"{ver.get('detail', '')}; installed {now}, release {version}")
    return {"ok": all(r["ok"] for r in rows), "rows": rows, "deb": str(deb)}
