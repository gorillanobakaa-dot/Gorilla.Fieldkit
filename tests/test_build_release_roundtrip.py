# Version: 1.0.0 · updated 26-10-16-02-15
"""release-roundtrip: the published file, not the local build, is what gets installed and proven (owner 2026-10-16)."""
import hashlib
import json
import sys

import pytest

from fieldkit.build import release_roundtrip as rr

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="a .deb round trip is Linux only")
DEB = b"!<arch>\nfake deb bytes"


def _get(sums_ok=True):
    sha = hashlib.sha256(DEB).hexdigest() if sums_ok else "0" * 64
    api = {"assets": [{"name": "g_158.0-1_amd64.deb", "browser_download_url": "https://dl/g.deb"},
                      {"name": "SHA256SUMS", "browser_download_url": "https://dl/SUMS"}]}

    def get(url, dest=None):
        if "api.github.com" in url:
            return json.dumps(api).encode()
        if url.endswith("SUMS"):
            return f"{sha}  g_158.0-1_amd64.deb\n".encode()
        open(dest, "wb").write(DEB)
        return hashlib.sha256(DEB).hexdigest()
    return get


class Check:
    @staticmethod
    def check(deb, branding):
        return {"problems": []}


class Inst:
    calls = []

    @staticmethod
    def stage_install(ctx):
        Inst.calls.append(("install", ctx.vars["release"]))
        return {"ok": True, "detail": "apt-get install exited 0"}

    @staticmethod
    def verify_install(ctx):
        return {"ok": True, "detail": "installed"}


def _sh(cmd, timeout=600):
    if cmd[:2] == ["dpkg-deb", "-f"]:
        return 0, "158.0-1"
    if cmd[0] == "dpkg-query" and "${Status}" in cmd[2]:
        return 1, ""
    if cmd[0] == "dpkg-query":
        return 0, "158.0-1"
    return 0, ""


def test_the_published_file_is_checked_removed_installed_and_verified(tmp_path):
    r = rr.run("v158.0-1", tmp_path / "dl", "/src", get=_get(), deb_check=Check, deb_install=Inst, sh=_sh,
               say=lambda m: None, installed=tmp_path / "gone")
    assert r["ok"] and [x["check"] for x in r["rows"]] == [f"roundtrip: {c}" for c in
                                                         ("release", "download", "checksum", "package", "remove",
                                                          "absent", "install", "verify")]
    assert Inst.calls[-1] == ("install", str(tmp_path / "dl"))           # the DOWNLOADED file, not the local build


def test_a_checksum_mismatch_stops_before_anything_is_removed(tmp_path):
    removed = []
    sh = lambda cmd, timeout=600: (removed.append(cmd) or (0, "")) if "remove" in cmd else _sh(cmd)
    r = rr.run("v158.0-1", tmp_path / "dl", "/src", get=_get(sums_ok=False), deb_check=Check, deb_install=Inst, sh=sh,
               say=lambda m: None)
    assert not r["ok"] and r["rows"][-1]["check"] == "roundtrip: checksum" and removed == []
