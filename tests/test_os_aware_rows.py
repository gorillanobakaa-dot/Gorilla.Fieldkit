"""P9 (owner 2026-11-12): a check row that names a one-system signal must declare its platforms (host.os_row).

The lint reads every module under fieldkit/ and finds check-row names: the first string argument of a call to a
function named `row`, or the "check" value of a dict literal. A name that contains a Windows-only or Linux-only
signal (.exe, registry, powershell, winreg, dpkg, .deb, systemctl, /proc) is a finding unless it is the name given to
`os_row` (which declares its platforms). KNOWN holds the rows that predate the rule and are not yet converted: it only
shrinks (a new row can not be added to it; the test also fails when an entry no longer matches)."""
import ast
import re
from pathlib import Path

from fieldkit.core import host

ROOT = Path(__file__).resolve().parent.parent / "fieldkit"
SIGNAL = re.compile(r"\.exe\b|registry|powershell|winreg|dpkg|\.deb\b|systemctl|/proc\b", re.I)

#: "file::row name" of rows that predate the rule (a later job converts them; this list never grows)
KNOWN = set()


def findings(root=ROOT, source=None):
    out = []
    files = [(Path("<src>"), source)] if source is not None else [(p, p.read_text(encoding="utf-8", errors="replace"))
                                                                  for p in sorted(root.rglob("*.py"))]
    for p, text in files:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        names = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                fn = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                if fn == "row" and n.args and isinstance(n.args[0], (ast.Constant, ast.JoinedStr)):
                    names.append(n.args[0])
            elif isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value == "check" and isinstance(v, (ast.Constant, ast.JoinedStr)):
                        names.append(v)
        for node in names:
            txt = ast.get_source_segment(text, node) or ""
            if SIGNAL.search(txt):
                out.append(f"{p.relative_to(root.parent).as_posix() if source is None else p}::{txt.strip(chr(34) + chr(39))}")
    return sorted(set(out))


def test_every_one_system_row_declares_its_platforms():
    found = set(findings())
    assert not (found - KNOWN), "check rows that name a one-system signal without os_row(platforms): " + "; ".join(sorted(found - KNOWN))
    assert not (KNOWN - found), "KNOWN lists rows that no longer exist (shrink it): " + "; ".join(sorted(KNOWN - found))


def test_lint_can_fail():
    bad = 'def v(rows):\n    row("firefox.exe exists", True, "x")\n    rows.append({"check": "uninstall registry entry", "ok": 1})\n'
    assert len(findings(source=bad)) == 2
    good = 'def v(rows):\n    host.os_row(rows, ("windows",), "firefox.exe exists", lambda: (True, "x"))\n'
    assert findings(source=good) == []


def test_os_row_runs_here_and_skips_elsewhere(monkeypatch):
    rows = []
    monkeypatch.setattr(host, "platform_ok", lambda p: "linux" in p)
    monkeypatch.setattr(host, "this_platform", lambda: "linux")
    host.os_row(rows, ("windows",), "w", lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    host.os_row(rows, ("linux",), "l", lambda: (False, "bad"))
    assert rows[0]["ok"] and rows[0]["status"] == "not-this-platform" and "windows" in rows[0]["evidence"]
    assert rows[1]["ok"] is False and rows[1]["evidence"] == "bad"


def test_install_verify_on_linux_reads_firefox_not_the_registry_or_exe(tmp_path, monkeypatch):
    from fieldkit.buildh import install as inst
    real = host.host()
    monkeypatch.setattr(host, "host", lambda: dict(real, system="Linux", is_windows=False, is_linux=True, is_mac=False))
    (tmp_path / "application.ini").write_text("[App]\nVersion=160.0\nBuildID=1\n")
    b = tmp_path / "firefox"
    b.write_text("#!/bin/sh\necho Mozilla Firefox 160.0\n")
    b.chmod(0o755)
    rows = {r["check"]: r for r in inst.verify(str(tmp_path), "160.0")}
    assert rows["installed firefox reports the pinned version"]["ok"]
    assert rows["installed firefox.exe reports the pinned version"]["status"] == "not-this-platform"
    assert rows["the uninstall entry points at this install"]["status"] == "not-this-platform"
    assert all(r["ok"] for r in rows.values())
