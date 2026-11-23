"""Gate: new code may not call a Windows-only program without an OS branch (owner, 2026-11-23: "no freaking
powershell on linux ... both sides the same capabilities").

A site is a list/tuple literal whose first item is a Windows-only program (powershell, taskkill, schtasks, ...).
It is fine when its function (or an enclosing if/ternary) names the platform: the Linux path sits beside it.
Anything else must be in desk/winonly_baseline.yaml with a reason and its Linux equivalent. The baseline is a
ratchet: a site that is fixed or gone must leave it (stale entries fail), and a new site never enters it.
Second check: a Windows-only card in os_parity.yaml with status PORT may not be added (the list only shrinks).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("winonly_baseline.yaml")
WINDOWS_ONLY = frozenset(
    "powershell pwsh wmic taskkill tasklist schtasks netsh reg sc icacls cmd wevtutil powercfg robocopy cscript "
    "wscript mklink attrib fsutil bcdedit dism certutil diskpart msiexec".split())
GUARD = re.compile(r"sys\.platform|os\.name|platform\.system|_is_win|IS_WIN|is_windows|_win\b|\bWIN\b|WIN32|win32|"
                   r"_windows\(|on_windows|\bON_WIN|IS_WINDOWS|_IS_WIN|\bwindows_only\b|_linux|linux_")


def _prog(node):
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts and isinstance(node.elts[0], ast.Constant) \
            and isinstance(node.elts[0].value, str):
        v = node.elts[0].value.lower().replace("\\", "/").rsplit("/", 1)[-1]
        v = v[:-4] if v.endswith(".exe") else v
        return v if v in WINDOWS_ONLY else None
    return None


def sites(root=None):
    """-> {key: 'path:line'} for every unguarded Windows-only call. key = path::function::program (no line, so it survives edits)."""
    root = Path(root or ROOT)
    out = {}
    for p in sorted((root / "fieldkit").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        lines = src.splitlines()
        rel = p.relative_to(root).as_posix()
        for n in ast.walk(tree):
            prog = _prog(n)
            if not prog:
                continue
            fn, c, guarded = None, n, False
            while c in parents:
                c = parents[c]
                if isinstance(c, (ast.If, ast.IfExp)) and GUARD.search(ast.get_source_segment(src, c.test) or ""):
                    guarded = True
                if fn is None and isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = c
            if fn is not None and GUARD.search("\n".join(lines[fn.lineno - 1:fn.end_lineno])):
                guarded = True
            if not guarded:
                out.setdefault(f"{rel}::{fn.name if fn else '<module>'}::{prog}", f"{rel}:{n.lineno}")
    return out


def load_baseline(path=None):
    return (yaml.safe_load(Path(path or BASELINE).read_text(encoding="utf-8")) or {}).get("sites") or {}


def problems(found=None, baseline=None):
    found = sites() if found is None else found
    base = load_baseline() if baseline is None else baseline
    out = []
    for k, where in sorted(found.items()):
        if k not in base:
            out.append(f"{where}: calls {k.rsplit('::', 1)[1]} with no OS branch - add the Linux path next to it "
                       f"(psutil, /proc, ss, ip, nmcli, systemd, powerprofilesctl) or branch on sys.platform")
    for k, why in sorted(base.items()):
        if k not in found:
            out.append(f"{k}: in winonly_baseline.yaml but no longer an unguarded call - delete the entry (fixed?)")
        elif len(str(why).split()) < 4:
            out.append(f"{k}: baseline needs a reason and its Linux equivalent (got {why!r})")
    return out


def port_problems(rows=None, cards=None, baseline=None):
    """A Windows-only card listed as PORT (Linux missing) must already be in the baseline list: that list only shrinks."""
    from . import osparity, platformcheck
    rows = osparity.load() if rows is None else rows
    cards = platformcheck.cards() if cards is None else cards
    base = set((yaml.safe_load(BASELINE.read_text(encoding="utf-8")) or {}).get("port_cards") or []) \
        if baseline is None else set(baseline)
    win = {c["id"] for c in cards if osparity._single(c) == "windows"}
    now = {cid for cid, r in rows.items() if cid in win and (r or {}).get("status") == "PORT"}
    out = [f"{cid}: Windows-only card marked PORT (Linux missing) and not in the baseline - write the Linux "
           f"implementation instead of listing it" for cid in sorted(now - base)]
    out += [f"{cid}: in winonly_baseline.yaml port_cards but ported or gone - delete the entry" for cid in sorted(base - now)]
    return out
