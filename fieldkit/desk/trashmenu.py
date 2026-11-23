"""P41: "Empty with Gorilla Trash..." in the Windows 11 first right-click menu (owner decision 2026-11-23).

The modern menu lists only commands of a package with identity. This module builds a signed sparse MSIX (manifest +
logo only; the files stay in %LOCALAPPDATA%\\Fieldkit\\bin) whose manifest registers the IExplorerCommand DLL built from
rust/gorilla-trash-menu. The certificate is self-signed and generated here: the private key stays in the current user's
certificate store (never a file, never committed); only the PUBLIC .cer is imported into LocalMachine\\TrustedPeople,
which is what lets the package install on any other PC (that import needs administrator rights: run `install --admin`
through the elevate tool, or the installer script).

    python -m fieldkit.desk.trashmenu build  --dll DLL [--bindir DIR] [--types Directory,*]
    python -m fieldkit.desk.trashmenu install-admin     (elevated: trust the public certificate)
    python -m fieldkit.desk.trashmenu register          (register the package for the current user)
    python -m fieldkit.desk.trashmenu status
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
import zlib
from pathlib import Path

from fieldkit import proc as fkproc

CLSID = "7C3A1F52-9D4B-4E8A-B2C6-1F0E5A9D3B71"  # the same value as CLSID_TRASH_MENU in rust/gorilla-trash-menu
SUBJECT = "CN=Fieldkit Gorilla Trash"
PACKAGE = "Fieldkit.GorillaTrashMenu"
VERSION = "1.0.1.0"
DLL_NAME = "gorilla-trash-menu.dll"
KITS = r"C:\Program Files (x86)\Windows Kits\10\bin"

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"
  xmlns:com="http://schemas.microsoft.com/appx/manifest/com/windows10"
  xmlns:desktop4="http://schemas.microsoft.com/appx/manifest/desktop/windows10/4"
  xmlns:desktop5="http://schemas.microsoft.com/appx/manifest/desktop/windows10/5"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap uap10 com desktop4 desktop5 rescap">
  <Identity Name="{name}" Publisher="{subject}" Version="{version}" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>Gorilla Trash menu</DisplayName>
    <PublisherDisplayName>Fieldkit</PublisherDisplayName>
    <Logo>logo.png</Logo>
    <uap10:AllowExternalContent>true</uap10:AllowExternalContent>
  </Properties>
  <Resources><Resource Language="en-us" /></Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Capabilities><rescap:Capability Name="runFullTrust" /><rescap:Capability Name="unvirtualizedResources" /></Capabilities>
  <Applications>
    <Application Id="GorillaTrash" Executable="gorilla-trash.exe" uap10:TrustLevel="mediumIL" uap10:RuntimeBehavior="win32App">
      <uap:VisualElements AppListEntry="none" DisplayName="Gorilla Trash" Description="Empty the Recycle Bin with a list first"
        BackgroundColor="transparent" Square150x150Logo="logo.png" Square44x44Logo="logo.png" />
      <Extensions>
        <desktop4:Extension Category="windows.fileExplorerContextMenus">
          <desktop4:FileExplorerContextMenus>
{items}
          </desktop4:FileExplorerContextMenus>
        </desktop4:Extension>
        <com:Extension Category="windows.comServer">
          <com:ComServer>
            <com:SurrogateServer DisplayName="Gorilla Trash menu">
              <com:Class Id="{clsid}" Path="{dll}" ThreadingModel="STA" />
            </com:SurrogateServer>
          </com:ComServer>
        </com:Extension>
      </Extensions>
    </Application>
  </Applications>
</Package>
"""


def manifest(types=("Directory",)) -> str:
    items = "\n".join(
        f'            <desktop5:ItemType Type="{t}">\n'
        f'              <desktop5:Verb Id="GorillaTrash" Clsid="{CLSID}" />\n'
        f'            </desktop5:ItemType>' for t in types)
    return MANIFEST.format(name=PACKAGE, subject=SUBJECT, version=VERSION, clsid=CLSID, dll=DLL_NAME, items=items)


def _png(size=44) -> bytes:
    """A flat dark square: the menu icon comes from GetIcon, this only satisfies the manifest."""
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\x22\x22\x22\xff" * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def state_dir() -> Path:
    d = Path(__file__).resolve().parents[2] / "state" / "trashmenu"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bindir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Fieldkit" / "bin"


def _tool(name: str) -> str:
    found = sorted(Path(KITS).glob(f"*/x64/{name}"), reverse=True)
    if not found:
        raise FileNotFoundError(f"{name} not found under {KITS} (install the Windows SDK)")
    return str(found[0])


def _ps(script: str, check=True):
    r = fkproc.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r.stdout.strip()


def ensure_cert() -> tuple[str, Path]:
    """The signing certificate (created once, key in the user store) and its exported PUBLIC .cer."""
    cer = state_dir() / "fieldkit-gorilla-trash.cer"
    thumb = _ps(
        "$c = Get-ChildItem Cert:\\CurrentUser\\My | Where-Object Subject -eq '%s' | Select-Object -First 1;"
        "if (-not $c) { $c = New-SelfSignedCertificate -Type Custom -Subject '%s' -KeyUsage DigitalSignature "
        "-FriendlyName 'Fieldkit Gorilla Trash' -CertStoreLocation Cert:\\CurrentUser\\My -NotAfter (Get-Date).AddYears(10) "
        "-TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3','2.5.29.19={text}') };"
        "Export-Certificate -Cert $c -FilePath '%s' | Out-Null; $c.Thumbprint" % (SUBJECT, SUBJECT, cer))
    return thumb.splitlines()[-1].strip(), cer


def build(dll: Path, types=("Directory",), target: Path | None = None) -> Path:
    """Copy the DLL next to gorilla-trash.exe, pack and sign the sparse MSIX; returns the .msix path."""
    target = Path(target or bindir())
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dll, target / DLL_NAME)
    pkg = state_dir() / "pkg"
    shutil.rmtree(pkg, ignore_errors=True)
    pkg.mkdir(parents=True)
    (pkg / "AppxManifest.xml").write_text(manifest(types), encoding="utf-8")
    (pkg / "logo.png").write_bytes(_png())
    msix = state_dir() / "gorilla-trash-menu.msix"
    msix.unlink(missing_ok=True)
    for cmd in ([_tool("makeappx.exe"), "pack", "/d", str(pkg), "/p", str(msix), "/nv", "/o"],):
        r = fkproc.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stdout + r.stderr)
    thumb, _ = ensure_cert()
    r = fkproc.run([_tool("signtool.exe"), "sign", "/fd", "SHA256", "/sha1", thumb, "/s", "My", str(msix)],
                   capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stdout + r.stderr)
    return msix


def trust_public_cert() -> str:
    """Needs administrator rights: the public certificate into LocalMachine\\TrustedPeople."""
    _, cer = ensure_cert()
    return _ps(f"Import-Certificate -FilePath '{cer}' -CertStoreLocation Cert:\\LocalMachine\\TrustedPeople | "
               "Select-Object -ExpandProperty Thumbprint")


def register(target: Path | None = None) -> str:
    target = Path(target or bindir())
    msix = state_dir() / "gorilla-trash-menu.msix"
    _ps(f"Get-AppxPackage {PACKAGE} | Remove-AppxPackage", check=False)
    _ps(f"Add-AppxPackage -Path '{msix}' -ExternalLocation '{target}'")
    return status()


def status() -> str:
    return _ps(f"$p = Get-AppxPackage {PACKAGE}; if ($p) {{ 'registered ' + $p.PackageFullName }} else {{ 'not registered' }}; "
               "$t = Get-ChildItem Cert:\\LocalMachine\\TrustedPeople | Where-Object Subject -eq '%s'; "
               "if ($t) { 'trusted ' + $t.Thumbprint } else { 'public certificate not trusted' }" % SUBJECT, check=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="trashmenu")
    ap.add_argument("cmd", choices=["build", "install-admin", "register", "status"])
    ap.add_argument("--dll")
    ap.add_argument("--bindir")
    ap.add_argument("--types", default="Directory")
    a = ap.parse_args(argv)
    if sys.platform != "win32":
        print("not-this-platform: the Windows 11 menu exists only on Windows")
        return 0
    if a.cmd == "build":
        print(build(Path(a.dll), tuple(a.types.split(",")), a.bindir))
    elif a.cmd == "install-admin":
        print("trusted", trust_public_cert())
    elif a.cmd == "register":
        print(register(a.bindir))
    else:
        print(status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
