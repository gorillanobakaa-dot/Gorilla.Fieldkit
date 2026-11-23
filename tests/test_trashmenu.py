"""P41: the sparse MSIX manifest and the IExplorerCommand cdylib agree (no Windows needed)."""
import re
import xml.dom.minidom
from pathlib import Path

from fieldkit.desk import trashmenu

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_wellformed_and_names_every_type():
    doc = xml.dom.minidom.parseString(trashmenu.manifest(("Directory", "Directory\\Background", "*")))
    types = [n.getAttribute("Type") for n in doc.getElementsByTagName("desktop5:ItemType")]
    assert types == ["Directory", "Directory\\Background", "*"]
    assert doc.getElementsByTagName("desktop5:Verb")[0].getAttribute("Clsid") == trashmenu.CLSID
    assert doc.getElementsByTagName("com:Class")[0].getAttribute("Path") == trashmenu.DLL_NAME


def test_clsid_matches_the_rust_source():
    src = (ROOT / "rust/gorilla-trash-menu/src/lib.rs").read_text(encoding="utf-8")
    m = re.search(r"GUID::from_u128\(0x([0-9a-f_]+)\)", src)
    assert m and m.group(1).replace("_", "").upper() == trashmenu.CLSID.replace("-", "")


def test_png_is_valid():
    assert trashmenu._png().startswith(b"\x89PNG\r\n\x1a\n")


def test_not_this_platform(monkeypatch, capsys):
    monkeypatch.setattr(trashmenu.sys, "platform", "linux")
    assert trashmenu.main(["status"]) == 0
    assert "not-this-platform" in capsys.readouterr().out


def test_default_types_reach_the_virtual_recycle_bin():
    doc = xml.dom.minidom.parseString(trashmenu.manifest())
    types = [n.getAttribute("Type") for n in doc.getElementsByTagName("desktop5:ItemType")]
    assert "CLSID\{645FF040-5081-101B-9F08-00AA002F954E}" in types and "*" not in types
