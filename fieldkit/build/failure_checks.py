"""Stored failure checks: the deterministic half of a triage signature (install and release kinds).

A signature in build/signatures/<kind>.yaml names a check; the check looks at the disk (a HOME, a repo, a folder)
and returns findings. Every check is pure file reading, so it runs the same on Windows, Linux and macOS and is
tested on a fake HOME. Each check also builds its own broken and clean fixtures: `triage self-test` uses them to
prove the check fires on the damage and stays quiet on a good tree.

    fieldkit triage checks --kind install|release [--home DIR] [--root DIR] [--branch NAME]
    fieldkit triage self-test --kind install|release
"""
import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path

ICON_EXT = (".png", ".svg", ".xpm", ".ico")
TEXT_EXT = (".sh", ".ps1", ".yaml", ".yml", ".md", ".txt", ".json", ".rb", ".py", ".desktop", ".toml", ".cmd", ".bat")
ROLLING = re.compile(r"releases/download/(latest|nightly|rolling|continuous|edge)/", re.I)
ALLOWED_BRANCH = ("feat/", "fix/", "release-", "night/")


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _data_dirs(ctx):
    home = Path(ctx["home"])
    env = os.environ.get("XDG_DATA_HOME") if ctx.get("use_env") else None
    user = Path(env) if env else home / ".local" / "share"
    system = Path(ctx.get("system_share") or "/usr/share")
    return user, system


def _icons(base):
    root = base / "icons"
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file() and p.suffix in ICON_EXT}


def icon_shadow(ctx):
    user, system = _data_dirs(ctx)
    mine, theirs = _icons(user), _icons(system)
    return [f"{p} shadows {theirs[rel]} (different bytes; the user copy wins)"
            for rel, p in sorted(mine.items()) if rel in theirs and _sha(p) != _sha(theirs[rel])]


def icon_cache(ctx):
    user, system = _data_dirs(ctx)
    out = []
    for base in (user, system):
        icons = base / "icons"
        if not icons.is_dir():
            continue
        for theme in sorted(d for d in icons.iterdir() if d.is_dir()):
            cache = theme / "icon-theme.cache"
            files = [p for p in theme.rglob("*") if p.is_file() and p.suffix in ICON_EXT]
            if cache.is_file() and files and max(p.stat().st_mtime for p in files) > cache.stat().st_mtime:
                out.append(f"{cache} is older than the newest icon in {theme} "
                           f"(run: gtk-update-icon-cache -f -t {theme})")
    return out


def _exec_target(line):
    m = re.match(r"\s*Exec=\s*(\"[^\"]+\"|\S+)", line)
    return m.group(1).strip('"') if m else None


def desktop_exec(ctx):
    user, _ = _data_dirs(ctx)
    apps = user / "applications"
    out = []
    for f in sorted(apps.glob("*.desktop")) if apps.is_dir() else []:
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            t = _exec_target(line)
            if t and os.path.isabs(t) and not Path(t).exists():
                out.append(f"{f} has Exec={t}, which does not exist")
                break
    return out


def rolling_url(ctx):
    root = Path(ctx.get("root") or ".")
    out = []
    for p in sorted(root.rglob("*")) if root.is_dir() else []:
        if ".git" in p.parts or not p.is_file() or p.suffix.lower() not in TEXT_EXT or p.stat().st_size > 1 << 20:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if ROLLING.search(line):
                out.append(f"{p}:{n} downloads from a rolling tag: {line.strip()[:120]}")
    return out


def _current_branch(root):
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                           text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def branch_mandate(ctx):
    b = ctx.get("branch")
    if b is None:
        b = _current_branch(ctx.get("root") or ".")
    if not b:
        return []           # not a git tree: nothing to push
    if not b.startswith(ALLOWED_BRANCH):
        return [f"branch {b!r} is not pushable under the mandate (allowed: feat/ fix/ release- night/); "
                f"commit on a feat/ branch and use fieldkit ship push --auto"]
    return []


# ---- fixtures: each builds a broken and a clean tree under a fake HOME ---------------------------------------

def _w(p, text="x", mtime=None):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def _fx_icon_shadow(root, bad):
    _w(root / "usr-share/icons/hicolor/48x48/apps/app.png", "new")
    _w(root / "home/.local/share/icons/hicolor/48x48/apps/app.png", "old" if bad else "new")
    return {"home": root / "home", "system_share": root / "usr-share"}


def _fx_icon_cache(root, bad):
    theme = root / "home/.local/share/icons/hicolor"
    _w(theme / "48x48/apps/app.png", "i", mtime=2_000_000_000 if bad else 1_000_000_000)
    _w(theme / "icon-theme.cache", "c", mtime=1_500_000_000)
    return {"home": root / "home", "system_share": root / "nosystem"}


def _fx_desktop_exec(root, bad):
    exe = _w(root / "bin/app", "#!/bin/sh\n")
    target = root / "bin/gone" if bad else exe
    _w(root / "home/.local/share/applications/app.desktop", f"[Desktop Entry]\nExec={target}\n")
    return {"home": root / "home", "system_share": root / "nosystem"}


def _fx_rolling(root, bad):
    tag = "nightly" if bad else "v1.2.3"
    _w(root / "tree/install.sh", f"curl -L https://github.com/o/r/releases/download/{tag}/app.tar.gz -o app.tgz\n")
    return {"root": root / "tree", "home": root / "home"}


def _fx_branch(root, bad):
    return {"branch": "wip/job" if bad else "feat/job", "home": root / "home"}


CHECKS = {
    "icon-shadow": (icon_shadow, _fx_icon_shadow),
    "icon-cache": (icon_cache, _fx_icon_cache),
    "desktop-exec": (desktop_exec, _fx_desktop_exec),
    "rolling-url": (rolling_url, _fx_rolling),
    "branch-mandate": (branch_mandate, _fx_branch),
}


def run(kind, ctx=None):
    """Run every stored check of one kind on a real context; returns the ones that fire, with cause and fix."""
    from . import triage
    ctx = dict({"home": str(Path.home()), "use_env": True}, **(ctx or {}))
    hits = []
    for s in triage.load_signatures(kind):
        fn = CHECKS.get(s.get("check") or "")
        found = fn[0](ctx) if fn else []
        if found:
            hits.append({"id": s["id"], "check": s["check"], "cause": s["cause"], "fix": s["fix"], "findings": found})
    return hits


def lines(hits):
    out = []
    for h in hits:
        out.append(f"stored check {h['id']}: {h['cause'].strip()}")
        out += [f"  found: {f}" for f in h["findings"]]
        out.append(f"  fix: {h['fix'].strip()}")
    return out


def self_test(kind):
    """Every signature of the kind: its sample line is named by its pattern, its check fires on the broken
    fixture and stays quiet on the clean one. Returns (ok, rows)."""
    from . import triage
    rows = []
    for s in triage.load_signatures(kind):
        row = {"id": s["id"], "ok": True, "why": "pattern and check fire"}
        sample = s.get("sample")
        if not sample or not s["_rx"].search(sample):
            row.update(ok=False, why="sample line is missing or its pattern does not name it")
        entry = CHECKS.get(s.get("check") or "")
        if row["ok"] and not entry:
            row.update(ok=False, why=f"check {s.get('check')!r} is not defined")
        if row["ok"]:
            fn, fx = entry
            with tempfile.TemporaryDirectory(prefix="fk-selftest-") as d:
                bad_ctx, good_ctx = fx(Path(d) / "bad", True), fx(Path(d) / "good", False)
                if not fn(bad_ctx):
                    row.update(ok=False, why="check stayed quiet on the broken fixture")
                elif fn(good_ctx):
                    row.update(ok=False, why="check fired on the clean fixture")
        rows.append(row)
    return bool(rows) and all(r["ok"] for r in rows), rows
