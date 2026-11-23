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


def test_default_types_are_schema_valid():
    # MakeAppx 10.0.26100 rejects Folder, Drive and CLSID item types: only these three parse.
    assert set(trashmenu.RECYCLE_TYPES) <= {"*", "Directory", "Directory\\Background"}
    doc = xml.dom.minidom.parseString(trashmenu.manifest())
    assert len(doc.getElementsByTagName("desktop5:ItemType")) == len(trashmenu.RECYCLE_TYPES)


def test_recycle_dir_lists_asks_and_never_touches_the_bin(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    calls = []
    assert trashmenu.recycle_dir(tmp_path, ask=lambda _: "n", run=calls.append) == 4 and not calls
    assert trashmenu.recycle_dir(tmp_path, ask=lambda _: "y", run=calls.append) in (0, 3) and len(calls) == 1
    assert trashmenu.recycle_dir("C:\\$Recycle.Bin", ask=lambda _: "y", run=calls.append) == 4
    assert len(calls) == 1


def test_recycle_dir_keeps_desktop_ini_and_thumbs_db(tmp_path, capsys):
    for n in ("a.txt", "Desktop.INI", "thumbs.db"):
        (tmp_path / n).write_text("x")
    scripts = []
    assert trashmenu.recycle_dir(tmp_path, ask=lambda _: "y", run=scripts.append) in (0, 3)
    out = capsys.readouterr().out
    assert "1 item(s)" in out and "Desktop.INI (kept: system file)" in out and "thumbs.db (kept: system file)" in out
    assert "desktop.ini" in scripts[0] and "thumbs.db" in scripts[0]
