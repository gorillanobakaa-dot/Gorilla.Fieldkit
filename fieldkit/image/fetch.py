"""Pinned codec downloads: only the URLs in pins.json, each checked against its sha256 before anything is unpacked."""
import hashlib
import io
import json
import os
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from . import tools

PINS = Path(__file__).with_name("pins.json")


class FetchError(Exception):
    pass


def install_dir():
    return Path(os.environ.get("FIELDKIT_IMAGE_TOOLS") or Path.home() / ".fieldkit" / "image-tools")


def pins():
    return json.loads(PINS.read_text(encoding="utf-8"))


def download(url):
    with urllib.request.urlopen(url, timeout=120) as r:      # the URL is a pin, never user input
        return r.read()


def _extract(blob, member, dest):
    """Write the one program named MEMBER (bare name or .exe) from the archive to DEST."""
    names = {member, member + ".exe"}
    if zipfile.is_zipfile(io.BytesIO(blob)):
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for n in z.namelist():
                if Path(n).name in names and not n.endswith("/"):
                    dest.write_bytes(z.read(n))
                    return dest
    else:
        with tarfile.open(fileobj=io.BytesIO(blob)) as t:
            for m in t.getmembers():
                if m.isfile() and Path(m.name).name in names:
                    dest.write_bytes(t.extractfile(m).read())
                    return dest
    raise FetchError(f"{member} is not inside the archive")


def fetch_one(name, platform=None, dl=None, table=None, into=None):
    """-> {tool, version, path, sha256}. A hash mismatch raises and writes nothing."""
    table = table or pins()
    entry = table.get(name)
    if not entry:
        raise FetchError(f"{name}: no pinned download (install it as `fieldkit image doctor` says)")
    pin = entry.get(tools.platform_key(platform))
    if not pin:
        raise FetchError(f"{name}: no pinned download for this system")
    blob = (dl or download)(pin["url"])
    got = hashlib.sha256(blob).hexdigest()
    if got != pin["sha256"]:
        raise FetchError(f"{name}: sha256 mismatch (pinned {pin['sha256'][:12]}, got {got[:12]}): nothing installed")
    d = Path(into or install_dir())
    d.mkdir(parents=True, exist_ok=True)
    exe = ".exe" if tools.platform_key(platform) == "windows" else ""
    path = _extract(blob, entry["member"], d / (name + exe))
    if not exe:
        path.chmod(0o755)
    return {"tool": name, "version": entry["version"], "path": str(path), "sha256": got}
