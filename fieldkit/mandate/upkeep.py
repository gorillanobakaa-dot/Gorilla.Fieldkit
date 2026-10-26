# Version: 1.0.0 · updated 26-10-26-12-00
"""Keeping the owner-wide mandate working: the doctor, the hooks that honor the gate, renewal and the expiry warning.

    fieldkit mandate doctor [--json]              in force or not, days left, the owner key here, the key trusted, the
                                                  other computer's review key pinned, and every covered repository's
                                                  pre-push hook: honors the gate, needs no gate, or NEEDS the gate
    fieldkit mandate install-hooks [--yes]        the gate-aware hook in every covered clone on THIS computer that has
                                                  a template (fieldkit/mandate/hooks/<repo>/); without --yes a preview;
                                                  old files kept as *.bak-<date>; each one verified after
    fieldkit mandate install-hooks --stick DIR    the templates, a README and the list of clones the other computer
                                                  must check, written to DIR (a stick folder; catchup collect adds it)
    fieldkit mandate renew FILE.yaml [--days 7]   a new period: the same approve screen, one typed yes + passphrase
    fieldkit mandate status                       every mandate here, and the one-line warning WARN_DAYS before expiry

Owner, 2026-10-26 ("fa permanent 1,2,3,4"): the one-approval system becomes part of the harness. Why each piece:
  - the Gorilla.Opencode hook is private to the clone (scripts/ is gitignored there), so it existed on one laptop
    only; its template now lives here, merged and reviewed like any code, and install-hooks puts it back in a clone
    byte for byte (an installed copy that differs from the template is reported, never silently replaced).
  - the mandate expires (owner-wide: 7 days). On the day after, every push needs the owner flag again and twelve
    pasted lines come back. So WARN_DAYS before the end a single line says so in `mandate status`, `mandate start`
    (the command post's board check) and once per session per day in the UserPromptSubmit hook (room.py). One line,
    never repeated within the session's day: no nag loop.
  - renewing is approving: the owner's typed yes and passphrase at a real terminal, the same screen as approve (every
    door, NOT COVERED, the threat model). A stopped mandate is never renewed (a stop is permanent for it). The
    passphrase is checked BEFORE the file changes, and a failed seal puts the old file and seal back, so a renewal
    can never leave the mandate unsealed.
install-hooks and renew are hard line 4 (hardlines.py): an unattended run never changes a hook or a mandate.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

WARN_DAYS = 3
TEMPLATES = Path(__file__).with_name("hooks")
#: a repository whose hook asks the owner flag on purpose for something no mandate covers (owner-only by design)
BY_DESIGN = {
    "gorilla-firefox": "asks the owner flag only when decisions/gorilla_decisions.json changes: the owner's record, "
                       "never covered by a mandate",
}
GATE_CALL = re.compile(r"mandate[\"',\s]+gate\b")
FLAG = "GORILLA_OWNER_APPROVED"


# ---- expiry --------------------------------------------------------------------------------------------------------
def _ends(path):
    from . import mandate as M
    try:
        return date.fromisoformat(str(M.load(path)["expires"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def expiry_lines(today=None, paths=None):
    """One line per mandate in force that ends within WARN_DAYS, or that expired within the last 14 days."""
    from . import mandate as M
    today = today or date.today()
    out = []
    paths = M.mandates() if paths is None else paths
    states = {p: M.verify(p, today) for p in paths}
    any_in_force = any(ok for ok, _ in states.values())
    for p in paths:
        ends = _ends(p)
        if ends is None:
            continue
        ok, why = states[p]
        left = (ends - today).days
        if ok and left <= WARN_DAYS:
            out.append(f"MANDATE {p.stem} ends {ends} ({left} day(s) left): the owner renews it with "
                       f"fieldkit mandate renew {p} (typed yes + passphrase)")
        # an old mandate that expired while another is in force is history, not news
        elif not ok and not any_in_force and why.startswith("expired") and -14 <= left < 0:
            out.append(f"MANDATE {p.stem} EXPIRED on {ends}: pushes need the owner flag again until the owner runs "
                       f"fieldkit mandate renew {p}")
    return out


def warning(today=None, paths=None):
    """The whole warning as ONE line ("" when nothing ends soon)."""
    return " | ".join(expiry_lines(today, paths))


def session_warning(session_id, today=None, seen_dir=None, paths=None):
    """The warning once per session per day (the UserPromptSubmit hook): "" when already shown today."""
    today = today or date.today()
    line = warning(today, paths)
    if not line or not session_id:
        return ""
    if seen_dir is None:
        from ..autopilot import room
        seen_dir = room.ROOM_DIR / "mandate-warned"
    f = Path(seen_dir) / (re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id)) + ".txt")
    try:
        if f.is_file() and f.read_text(encoding="utf-8").strip() == today.isoformat():
            return ""
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(today.isoformat(), encoding="utf-8")
    except OSError:
        return ""                                         # cannot remember it: say nothing rather than nag
    return line


# ---- hooks ---------------------------------------------------------------------------------------------------------
def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=60)


def git_here(platform=None, which=shutil.which, run=subprocess.run):
    """-> (ok, why): a real git on this computer. macOS (2026-10-26): /usr/bin/git without Apple's developer tools is a
    stub that opens an "install the command line developer tools" dialog instead of running, and a Mac installed from
    the zip has no git on purpose (core/requirements.json: git optional_when zip). Never run the stub."""
    platform = platform or sys.platform
    exe = which("git")
    if not exe:
        return False, "git is not installed here: no clone on this computer to push from (normal on a Mac installed " \
                      "from the zip)"
    if platform == "darwin" and str(exe) == "/usr/bin/git":
        try:
            r = run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            r = None
        if r is None or r.returncode != 0:
            return False, "/usr/bin/git is Apple's stub (no developer tools): no clone on this computer to push from"
    return True, f"git: {exe}"


def _norm(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def covered_repos(paths=None):
    """-> {name: mandate stem} from every mandate here, then every repository of the packaged owner-wide.yaml (the
    owner's whole list, approved here or not: a session never hand-pushes any of them)."""
    from . import mandate as M
    out = {}
    for p in M.mandates() if paths is None else paths:
        try:
            for name in (M.load(p).get("repos") or {}):
                out.setdefault(name, p.stem)
        except (OSError, ValueError):
            continue
    for name in (M.load(Path(M.__file__).with_name("owner-wide.yaml")).get("repos") or {}):
        out.setdefault(name, "owner-wide (the packaged list)")
    return out


def find_clones(roots=None):
    """-> {repo name: [main clone folders]} on this computer: catchup's walk (Documents and the Antigravity scratch,
    its exclusions), worktrees left out (they share the main clone's hooks)."""
    from . import proofs
    if roots is None and not git_here()[0]:
        return {}                                         # no real git: no clone, and never the macOS stub
    if roots is None:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "fk_catchup", Path(__file__).resolve().parents[2] / "kits" / "catchup" / "catchup.py")
            cu = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cu)
            cfg = cu.default_config()
            found = cu.find_repos(cfg["roots"], cfg["repo_exclude"])
        except (OSError, AttributeError, ImportError):    # an install without kits/: the sync list only
            from ..core import sync
            found = sync.repos()
    else:
        found = [Path(r) for r in roots]
    out = {}
    for d in found:
        if not (Path(d) / ".git").is_dir():               # a worktree's .git is a file
            continue
        try:
            name = proofs.where(d)[0]
        except OSError:
            continue
        if name:
            out.setdefault(name, []).append(Path(d))
    return out


def hook_path(repo):
    p = _git(repo, "rev-parse", "--git-path", "hooks/pre-push").stdout.strip()
    return (Path(repo) / p) if p and not Path(p).is_absolute() else Path(p or Path(repo) / ".git/hooks/pre-push")


def template_dir(name):
    """fieldkit/mandate/hooks/<repo name, lower case, dots as dashes>: Gorilla.Opencode -> gorilla-opencode."""
    return TEMPLATES / name.lower().replace(".", "-")


def _install_targets(repo, name):
    """Template file -> where it goes in the clone."""
    t = template_dir(name)
    if not t.is_dir():
        return {}
    if name == "Gorilla.Opencode":
        return {t / "hooks.py": Path(repo) / "scripts" / "git-hooks" / "hooks.py",
                t / "pre-push": Path(repo) / "scripts" / "git-hooks" / "pre-push",
                t / "pre-push#git": hook_path(repo)}
    return {}


def _src(f):
    return Path(str(f).split("#", 1)[0])


def classify(repo, name):
    """-> {"state", "ok", "why"}: none | no-flag | by-design | honors | template-old | NEEDS-GATE."""
    hp = hook_path(repo)
    if not hp.is_file():
        return {"state": "none", "ok": True, "why": "no pre-push hook: git asks nothing, there is no flag to honor"}
    if not runnable(hp):
        return {"state": "not-executable", "ok": False,
                "why": f"{hp} is not executable, so git on this system SKIPS it (its scans and its flag check never "
                       "run): fieldkit mandate install-hooks --yes, or chmod +x it"}
    text = hp.read_text(encoding="utf-8", errors="replace")
    for ref in re.findall(r"[\w./-]+\.py", text):         # the script the wrapper runs (scripts/git-hooks/hooks.py)
        for base in (Path(repo), hp.parent):
            f = base / ref
            if f.is_file():
                text += "\n" + f.read_text(encoding="utf-8", errors="replace")
                break
    targets = _install_targets(repo, name)
    if targets:
        stale = [str(dst) for src, dst in targets.items()
                 if not dst.is_file() or _norm(dst.read_bytes()) != _norm(_src(src).read_bytes())]
        if stale:
            honors = FLAG in text and GATE_CALL.search(text)
            return {"state": "template-old", "ok": bool(honors),
                    "why": ("honors the gate, but differs from the Fieldkit template: " if honors else
                            "NEEDS THE GATE (the template is not installed): ") + ", ".join(stale)
                           + " -> fieldkit mandate install-hooks --yes"}
        return {"state": "honors", "ok": True, "why": "the Fieldkit template, byte for byte: asks the mandate gate"}
    if FLAG not in text:
        return {"state": "no-flag", "ok": True, "why": "its hook never asks the owner flag (scans only)"}
    if name in BY_DESIGN:
        return {"state": "by-design", "ok": True, "why": BY_DESIGN[name]}
    if GATE_CALL.search(text):
        return {"state": "honors", "ok": True, "why": "asks the mandate gate when FIELDKIT_SHIP_PUSH=1"}
    return {"state": "NEEDS-GATE", "ok": False,
            "why": f"{hp} wants {FLAG} for every push and never asks `fieldkit mandate gate`: add a template under "
                   f"fieldkit/mandate/hooks/{template_dir(name).name}/ (shape: gorilla-opencode/hooks.py, mandate_gate)"}


def hook_rows(covered=None, clones=None):
    covered = covered_repos() if covered is None else covered
    clones = find_clones() if clones is None else clones
    rows = []
    for name in sorted(covered, key=str.lower):
        here = clones.get(name) or []
        if not here:
            rows.append({"repo": name, "clone": None, "state": "not-here", "ok": True,
                         "why": "not cloned on this computer (check it where it is: fieldkit mandate doctor)"})
        for c in here:
            rows.append(dict(repo=name, clone=str(c), **classify(c, name)))
    return rows


def runnable(p, platform=None, access=os.access):
    """Does git run this hook? On Linux and macOS only when it is executable: git skips any other hook in silence
    (2026-10-26: the template was committed 100644, so on Ubuntu CI the pushed branch went through unchecked). Git for
    Windows runs a hook whatever its mode."""
    return (platform or sys.platform) == "win32" or access(p, os.X_OK)


def _verify_installed(repo):
    """Run the installed hook as git would, twice, with no push lines: without the marker it must refuse with the
    owner-flag reason; with FIELDKIT_SHIP_PUSH=1 it must reach the gate (which refuses: no push lines). First: git
    must run it at all (executable on Linux and macOS)."""
    hp = hook_path(repo)
    if not runnable(hp):
        return False, f"{hp} is not executable: git on this system skips it"
    sh = shutil.which("sh") or shutil.which("bash")
    if not sh:
        return False, "no sh to run the hook with"
    import os
    base = {k: v for k, v in os.environ.items() if k not in (FLAG, "FIELDKIT_SHIP_PUSH")}
    a = subprocess.run([sh, str(hp), "origin", "x"], cwd=str(repo), input="", capture_output=True, text=True,
                       env=base, timeout=300)
    b = subprocess.run([sh, str(hp), "origin", "x"], cwd=str(repo), input="", capture_output=True, text=True,
                       env=dict(base, FIELDKIT_SHIP_PUSH="1"), timeout=300)
    if a.returncode == 0 or FLAG not in a.stderr:
        return False, f"without the marker the hook did not refuse for the owner flag (exit {a.returncode})"
    if b.returncode == 0 or "no push lines" not in b.stderr:
        return False, f"with FIELDKIT_SHIP_PUSH=1 the hook did not reach the gate (exit {b.returncode}): {b.stderr[-300:]}"
    return True, "verified: refuses without the owner flag, asks the gate through fieldkit ship push"


def install_hooks(apply=False, clones=None, covered=None, say=print, today=None):
    """-> number of clones still failing. Only clones with a template are written; everything else is reported."""
    stamp = (today or date.today()).isoformat()
    covered = covered_repos() if covered is None else covered
    clones = find_clones() if clones is None else clones
    bad = 0
    for name in sorted(covered, key=str.lower):
        for c in clones.get(name) or []:
            targets = _install_targets(c, name)
            if not targets:
                row = classify(c, name)
                bad += 0 if row["ok"] else 1
                say(f"  {'ok' if row['ok'] else 'NEEDS'}  {name} ({c}): {row['why']}")
                continue
            change = {src: dst for src, dst in targets.items()
                      if not dst.is_file() or _norm(dst.read_bytes()) != _norm(_src(src).read_bytes())
                      or (dst.name == "pre-push" and not runnable(dst))}
            if not change:
                ok, why = _verify_installed(c)
                bad += 0 if ok else 1
                say(f"  {'ok' if ok else 'FAILED'}  {name} ({c}): the template is installed; {why}")
                continue
            if not apply:
                say(f"  WOULD INSTALL {name} ({c}): " + ", ".join(str(d) for d in change.values())
                    + "  (run again with --yes)")
                bad += 1
                continue
            for src, dst in change.items():
                if dst.is_file():
                    shutil.copy2(dst, dst.with_name(f"{dst.name}.bak-{stamp}"))
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(_norm(_src(src).read_bytes()))
                if dst.name == "pre-push":
                    dst.chmod(0o755)                      # a hook git runs (Linux and macOS skip a 0644 one)
            ok, why = _verify_installed(c)
            bad += 0 if ok else 1
            say(f"  {'INSTALLED' if ok else 'INSTALLED, NOT VERIFIED'}  {name} ({c}): {why}; old files kept as "
                f"*.bak-{stamp}")
    return bad


def stick(out, rows=None, covered=None):
    """The stick folder for the other computer: the templates, their sha256, the clones it must check, a README."""
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty: choose a new folder (nothing is overwritten)")
    shutil.copytree(TEMPLATES, out / "templates", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    sums = {str(f.relative_to(out)).replace("\\", "/"): hashlib.sha256(_norm(f.read_bytes())).hexdigest()
            for f in sorted((out / "templates").rglob("*")) if f.is_file()}
    covered = covered_repos() if covered is None else covered
    rows = hook_rows(covered) if rows is None else rows
    here = {r["repo"] for r in rows if r["clone"]}
    there = sorted((n for n in covered if n not in here), key=str.lower)
    with_template = sorted(p.name for p in TEMPLATES.iterdir() if p.is_dir() and p.name != "__pycache__")
    (out / "SHA256SUMS.json").write_text(json.dumps(sums, indent=1), encoding="utf-8")
    (out / "CLONES-TO-CHECK.txt").write_text(
        "Covered repositories NOT cloned on the computer that wrote this (check each clone the other computer has):\n"
        + "\n".join(f"  {n}" for n in there) + "\n\nAlso check (a template exists; its hook is private to the clone):\n"
        + "\n".join(f"  {n}" for n in with_template) + "\n", encoding="utf-8")
    (out / "README.txt").write_text("\n".join([
        f"Mandate hooks for the other computer, written {time.strftime('%Y-%m-%d %H:%M')} by fieldkit mandate "
        "install-hooks --stick.",
        "",
        "PRIVATE: hand-carried only (the clone hooks are private to each clone).",
        "",
        "On the other computer, in its Fieldkit checkout (main with the mandate code merged):",
        "  1. fieldkit mandate doctor              every covered clone there: honors the gate, needs none, or NEEDS it",
        "  2. fieldkit mandate install-hooks       a preview: which clones get the template",
        "  3. fieldkit mandate install-hooks --yes installs and verifies (old files kept as *.bak-<date>)",
        "  4. fieldkit mandate doctor              again: every row ok",
        "The templates here are the same bytes as fieldkit/mandate/hooks/ on Fieldkit main (SHA256SUMS.json, LF).",
        "A clone listed NEEDS-GATE with no template is a hook change for the owner (shape: templates/gorilla-opencode).",
        ""]), encoding="utf-8")
    return out


# ---- doctor ----------------------------------------------------------------------------------------------------------
def doctor_facts(today=None, rows=None):
    from . import mandate as M
    today = today or date.today()
    from . import guarded
    facts = {"mandates": [], "key_here": M._key_paths()[0].is_file(), "trusted": sorted(M.trusted_keys()),
             "reviewers": sorted(M.reviewer_keys()), "warning": warning(today),
             "store": str(guarded.root()), "store_ok": guarded.guarded(), "git": git_here()}
    for p in M.mandates():
        ok, why = M.verify(p, today)
        ends = _ends(p)
        seal = {}
        try:
            seal = json.loads(M._seal_path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        facts["mandates"].append({"name": p.stem, "path": str(p), "in_force": ok, "why": why,
                                  "ends": str(ends) if ends else None,
                                  "days_left": (ends - today).days if ends else None,
                                  "key": seal.get("key"), "key_trusted": seal.get("key") in facts["trusted"]})
    facts["hooks"] = hook_rows() if rows is None else rows
    return facts


def doctor_lines(f):
    out = []
    if not f["mandates"]:
        out.append("  NO MANDATE here (~/.fieldkit/mandate/mandates): every push needs the owner flag")
    for m in f["mandates"]:
        out.append(f"  {'IN FORCE' if m['in_force'] else 'not in force':12} {m['name']}: {m['why']}"
                   + (f"; {m['days_left']} day(s) left" if m["in_force"] and m["days_left"] is not None else ""))
    out.append(f"  owner signing key on this computer: {'yes' if f['key_here'] else 'no'}"
               + ("" if f["key_here"] else " (normal on the computer that only verifies)"))
    out.append(f"  owner key(s) trusted here: {', '.join(f['trusted']) or 'NONE (fieldkit mandate trust)'}")
    out.append(f"  other computer's review key pinned here (VAIO): {', '.join(f['reviewers']) or 'no'}")
    if "store" in f:
        ok, why = f["store_ok"]
        out.append(f"  protected trust store ({sys.platform}): {f['store']}: {'guarded' if ok else why}")
    if "git" in f and not f["git"][0]:
        out.append(f"  {f['git'][1]}")
    for r in f["hooks"]:
        out.append(f"  {'ok   ' if r['ok'] else 'NEEDS'} hook {r['repo']}" + (f" ({r['clone']})" if r["clone"] else "")
                   + f": {r['why']}")
    if f["warning"]:
        out.append("  " + f["warning"])
    return out


def doctor_ok(f):
    return any(m["in_force"] for m in f["mandates"]) and bool(f["trusted"]) and all(r["ok"] for r in f["hooks"])


# ---- renew ------------------------------------------------------------------------------------------------------------
def renewed_text(path, days, today=None, source=None):
    """The mandate's text with `expires:` moved to today + days; every other byte the same. With source: the text of
    that file instead (a changed mandate the owner reads on the renew screen, e.g. new machine prerequisites)."""
    text = Path(source or path).read_text(encoding="utf-8")
    new = (today or date.today()) + timedelta(days=int(days))
    out, n = re.subn(r"(?m)^expires:\s*\S+\s*$", f"expires: {new.isoformat()}", text)
    if n != 1:
        raise ValueError("the mandate has no single top-level `expires:` line")
    return out, new


def renew(path, passphrase, confirmed, days=7, today=None, source=None):
    """-> (code, message). The passphrase is checked before the file changes; a failed seal restores the old one."""
    from cryptography.hazmat.primitives import serialization
    from . import mandate as M
    path = Path(path)
    if not confirmed:
        return 1, "not renewed"
    if M.stopped(path):
        return 4, "this mandate was stopped: a stop is permanent for it; write a changed mandate and approve that"
    priv = M._key_paths()[0]
    if not priv.is_file():
        return 2, "no owner signing key here: renew on the computer that holds it"
    try:
        serialization.load_pem_private_key(priv.read_bytes(), password=passphrase.encode())
    except (ValueError, TypeError):
        return 3, "wrong passphrase: not renewed, nothing changed"
    text, new = renewed_text(path, days, today, source)
    seal = M._seal_path(path)
    old_text = path.read_bytes()
    old_seal = seal.read_bytes() if seal.is_file() else None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path.with_name(f"{path.name}.{stamp}.prev").write_bytes(old_text)   # not *.yaml: never read as a mandate
    if old_seal is not None:
        seal.with_name(f"{seal.name}.{stamp}.prev").write_bytes(old_seal)
    path.write_text(text, encoding="utf-8")
    code, msg = M.approve(path, passphrase, True)
    if code != 0:
        path.write_bytes(old_text)
        if old_seal is not None:
            seal.write_bytes(old_seal)
        return code, f"NOT renewed ({msg}); the old mandate and seal are back"
    M.log(path, "renewed", f"until {new}")
    return 0, f"RENEWED {path.stem} until {new}: " + msg


def main(action, r, argv):
    """`fieldkit mandate doctor | install-hooks | renew` (mandate.main hands over here)."""
    import getpass
    from . import mandate as M
    args = list(argv)
    if action == "doctor":
        f = doctor_facts()
        if "--json" in args:
            print(json.dumps(f, indent=1))
        else:
            print("\n".join(doctor_lines(f)))
        return 0 if doctor_ok(f) else 3
    if action == "install-hooks":
        if "--stick" in args:
            i = args.index("--stick")
            if i + 1 >= len(args):
                print("install-hooks --stick DIR")
                return 2
            try:
                out = stick(args[i + 1])
            except FileExistsError as e:
                print(f"REFUSED: {e}")
                return 2
            print(f"STICK FOLDER: {out} (templates, SHA256SUMS.json, CLONES-TO-CHECK.txt, README.txt)")
            return 0
        bad = install_hooks(apply="--yes" in args)
        return 0 if bad == 0 else 3
    if action == "renew":
        if not r:
            print("renew FILE.yaml [--days 7] [--from CHANGED.yaml]")
            return 2
        path = Path(r[0])
        days = int(args[args.index("--days") + 1]) if "--days" in args else 7
        source = Path(args[args.index("--from") + 1]) if "--from" in args and args.index("--from") + 1 < len(args) \
            else None
        try:
            m = M.load(source or path)
            text, new = renewed_text(path, days, source=source)
            old_doors = M.doors(M.load(path)) if source and path.is_file() else None
        except (OSError, ValueError) as e:
            print(f"REFUSED: {e}")
            return 2
        ok, why = M.verify(path)
        print(f"RENEW {m['project']}: now {'in force' if ok else 'not in force'} ({why}); the new period ends {new}.")
        if source is None:
            print("Only the date changes. The same doors as before, for every session on every computer that trusts "
                  "your key:")
        else:
            print(f"The mandate CHANGES to {source}. Doors marked NEW were not in the one you approved before; every "
                  "door, for every session on every computer that trusts your key:")
        for line in M.doors(m):
            fresh = old_doors is not None and line not in old_doors and not line.startswith("ends:")
            print(f"  - {'NEW ' if fresh else ''}{line}")
        print("NOT COVERED: these stay yours, whatever the mandate says (and what enforces each):")
        for line in M.not_covered():
            print(f"  - {line}")
        print("THREAT MODEL:")
        for line in M.threat_model(path):
            print(f"  - {line}")
        if not M._typed_yes(f"Renew until {new}? Type yes: "):
            print("REFUSED: renewing needs the owner typing yes at a real terminal" if not sys.stdin.isatty()
                  else "not renewed")
            return 3
        code, msg = renew(path, getpass.getpass("Owner key passphrase: "), True, days, source=source)
        print(msg)
        if code == 0:
            # 2026-10-26: the VAIO kept the old copy after a renew here and refused a job (parity: mandate files)
            try:
                ok, sent = M.share(path)
            except Exception as e:  # noqa: BLE001
                ok, sent = False, [str(e)[:120]]
            print(("SHARED with the other computer: " if ok else "NOT SHARED (the other computer still has the OLD "
                   f"copy; when it is reachable: fieldkit mandate share {path}): ") + "; ".join(sent or ["no peer"]))
        return code
    return 2
