"""What on this machine is behind what was published, and the line that fixes it.

# Version: 1.0.0 · updated 26-10-14-11-00

    fieldkit update                 what is behind, from every source
    fieldkit update --only apt      one source at a time
    fieldkit update --apply         do the ones that need no root, verify after
    fieldkit update --json          for another tool

WHY THIS EXISTS
    On 2026-10-14 the owner's desktop showed "Update available" and, checked
    properly, EVERY installed artifact on this machine was behind:

        gorilla-opencode              0.1.132   published 0.1.147
        gorilla-portable-network-hub  0.9.5     source already at 0.11.0
        gorilla-unleashed             155.0-3   patch set already at 157
        claude-desktop                2.31226.1 apt offers 2.37337.1

    The hub was five releases behind its own source. Nothing was broken and
    nothing complained: an old binary does not announce itself, it simply
    cannot offer the new thing, which reads as a missing feature rather than a
    stale install. The session before this one had looked at the git checkouts,
    found them current, and said the machine was fine. Checkouts are not
    installs. This command asks the question the person is actually asking,
    which is about what runs when they type the command.

THE RULE THIS IS BUILT AROUND
    "Could not check" must never look like "up to date".

    Every source returns a status, and `unknown` is a first-class answer with a
    reason attached. A network failure, a missing tool or a repository that
    cannot be read produce UNKNOWN, never OK. This is the same fault that was
    found in the privacy scanner the day before: a report that cannot say what
    it examined is indistinguishable from one that examined nothing.

WHAT IT WILL NOT DO
    It never runs sudo. Installing a Debian package needs root, which is the
    owner's decision at his own terminal, so the exact line is printed instead.
    --apply covers only what can be done without it.

    It never trusts an exit code (directive 2). After anything is applied, the
    artifact is read back: the installed version, or the launcher's own
    --version. `claude update` is recorded on this machine as printing success
    while leaving the old directory in place, which is precisely why.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources.yaml"

OK = "ok"               # installed and current
BEHIND = "behind"       # a newer version exists
UNKNOWN = "unknown"     # could NOT be determined, and why
ABSENT = "absent"       # not installed here at all


def _run(cmd, timeout=120):
    """-> (returncode, text). A missing tool is a failure, never an empty result."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, f"could not run {cmd[0]}: {e}"


def load_sources(path=None):
    import yaml
    return yaml.safe_load(Path(path or SOURCES).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- versions
_NUM = re.compile(r"\d+")


def version_parts(v):
    """Compare versions by their NUMBERS, so 0.1.132 sorts below 0.1.147 and
    firefox-9 does not sort above firefox-155."""
    return [int(n) for n in _NUM.findall(v or "")] or [0]


def is_newer(candidate, installed):
    return version_parts(candidate) > version_parts(installed)


def installed_version(package, run=_run):
    """What dpkg says is installed. None means not installed; it is not an error."""
    if not shutil.which("dpkg-query"):
        return None, "dpkg-query is not on this machine (not a Debian-family system)"
    rc, out = run(["dpkg-query", "-W", "-f=${Version}", package])
    if rc != 0 or not out.strip():
        return None, None
    return out.strip(), None


# ------------------------------------------------------------------- apt
def apt_pending(run=_run, watch=()):
    """Everything apt would upgrade, with the named ones called out.

    `apt-get -s upgrade` simulates and needs no root. A failure here is UNKNOWN:
    an empty list from a broken apt reads exactly like a tidy machine.
    """
    if not shutil.which("apt-get"):
        return {"status": UNKNOWN, "why": "apt-get is not on this machine", "items": []}
    rc, out = run(["apt-get", "-s", "upgrade"])
    if rc != 0:
        return {"status": UNKNOWN, "why": f"apt-get -s upgrade failed: {out.strip()[:200]}",
                "items": []}
    items = []
    for line in out.splitlines():
        if not line.startswith("Inst "):
            continue
        # Inst <name> [<installed>] (<candidate> <origin> [arch])
        m = re.match(r"Inst (\S+) \[([^\]]+)\] \((\S+)\s+([^)\[]*)", line)
        if not m:
            continue
        name, now, cand, origin = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        items.append({"name": name, "installed": now, "candidate": cand,
                      "origin": origin, "security": "Security" in origin,
                      "watched": name in set(watch)})
    return {"status": BEHIND if items else OK,
            "items": items,
            "security_count": sum(1 for i in items if i["security"]),
            "command": "sudo apt update && sudo apt upgrade" if items else ""}


# ---------------------------------------------------------------- github
def github_latest(repo, asset_suffix=None, run=_run):
    """The newest release tag, and for a suffix the newest release carrying such
    an asset. One repository can publish Linux and Windows builds under separate
    tags, and 'latest' is then the wrong answer for one of them."""
    if not shutil.which("gh"):
        return None, "the gh command is not installed, so releases cannot be read"
    if asset_suffix:
        rc, out = run(["gh", "release", "list", "-R", repo, "--limit", "30",
                       "--json", "tagName"])
        if rc != 0:
            return None, f"gh release list failed: {out.strip()[:160]}"
        try:
            tags = [r["tagName"] for r in json.loads(out)]
        except (ValueError, KeyError) as e:
            return None, f"could not read the release list: {e}"
        for tag in tags:
            rc, out = run(["gh", "release", "view", tag, "-R", repo, "--json", "assets"])
            if rc != 0:
                continue
            try:
                assets = json.loads(out).get("assets") or []
            except ValueError:
                continue
            if any(a.get("name", "").endswith(asset_suffix) for a in assets):
                return tag, None
        return None, f"no release in the last {len(tags)} carries a {asset_suffix} asset"
    rc, out = run(["gh", "release", "view", "-R", repo, "--json", "tagName"])
    if rc != 0:
        return None, f"gh release view failed: {out.strip()[:160]}"
    try:
        return json.loads(out)["tagName"], None
    except (ValueError, KeyError) as e:
        return None, f"could not read the release: {e}"


def github_state(entry, run=_run):
    """One published-by-us package: installed against its newest release."""
    pkg = entry["package"]
    have, note = installed_version(pkg, run=run)
    row = {"name": pkg, "what": entry.get("what", ""), "repo": entry["repo"],
           "installed": have, "latest": None, "status": UNKNOWN, "why": "",
           "command": ""}
    if have is None:
        row["status"] = ABSENT
        row["why"] = note or "not installed here"
        return row
    tag, why = github_latest(entry["repo"], entry.get("asset_suffix"), run=run)
    if tag is None:
        row["why"] = why
        return row
    row["latest"] = tag
    if is_newer(tag, have):
        row["status"] = BEHIND
        row["command"] = (f"gh release download {tag} -R {entry['repo']} -p '*{entry.get('asset_suffix') or '.deb'}' "
                          f"-D /tmp && sudo apt install /tmp/*.deb")
    else:
        row["status"] = OK
    return row


# ---------------------------------------------------------------- native
def native_state(entry, run=_run):
    """An unpacked tree with a launcher. Reports what is actually there, and
    every version directory left behind, because a failed update shows up as a
    new directory that nothing points at."""
    root = Path(os.path.expanduser(entry["root"]))
    launcher = Path(os.path.expanduser(entry["launcher"]))
    row = {"name": entry["name"], "what": entry.get("what", ""), "status": UNKNOWN,
           "installed": None, "versions": [], "why": "", "command": ""}
    if not root.exists():
        row["status"] = ABSENT
        row["why"] = f"{root} does not exist"
        return row
    row["versions"] = sorted((p.name for p in root.iterdir() if p.is_dir()),
                             key=version_parts)
    if launcher.is_symlink():
        row["installed"] = os.path.basename(os.path.realpath(launcher))
    elif launcher.exists():
        rc, out = run([str(launcher), "--version"])
        row["installed"] = out.strip().split()[0] if rc == 0 and out.strip() else None
    if not row["installed"]:
        row["why"] = f"{launcher} does not say which version it runs"
        return row
    # Its own updater is the only source of a newer version, and on this machine
    # it is not trusted to report truthfully, so nothing here claims to know the
    # published version. What CAN be said is whether the launcher points at the
    # newest tree on disk.
    newest = row["versions"][-1] if row["versions"] else None
    if newest and newest != row["installed"]:
        row["status"] = BEHIND
        row["why"] = (f"{newest} is unpacked but the launcher still points at "
                      f"{row['installed']}: an update was fetched and never took effect")
        row["command"] = f"ln -sfn {root / newest} {launcher}"
    else:
        row["status"] = OK
        row["why"] = ("the launcher points at the newest tree on disk. Whether a newer "
                      "one exists upstream is not checked here: this updater is recorded "
                      "as reporting success without changing anything, so its word is "
                      "not evidence (directive 2).")
    if not entry.get("trust_its_exit_code", True):
        row["verify_with"] = f"{launcher} --version"
    return row


# ---------------------------------------------------------------- the survey
def survey(sources=None, run=_run):
    src = sources or load_sources()
    apt = apt_pending(run=run, watch=(src.get("apt") or {}).get("watch") or ())
    github = [github_state(e, run=run) for e in (src.get("github") or [])]
    native = [native_state(e, run=run) for e in (src.get("native") or [])]
    behind = ([i for i in apt["items"]] if apt.get("items") else []) \
        + [r for r in github if r["status"] == BEHIND] \
        + [r for r in native if r["status"] == BEHIND]
    unknown = [r for r in github + native if r["status"] == UNKNOWN] \
        + ([apt] if apt["status"] == UNKNOWN else [])
    return {"apt": apt, "github": github, "native": native,
            "behind_count": len(behind), "unknown_count": len(unknown)}


def lines(r):
    """The report a person reads."""
    out = []
    a = r["apt"]
    out.append("FROM APT")
    if a["status"] == UNKNOWN:
        out.append(f"  COULD NOT CHECK: {a['why']}")
        out.append("  That is not 'up to date'. Nothing was read.")
    elif not a["items"]:
        out.append("  nothing pending")
    else:
        for i in a["items"]:
            mark = "!" if i["security"] else ("*" if i["watched"] else " ")
            out.append(f"  {mark} {i['name']:<28} {i['installed']:<22} -> {i['candidate']}")
        if a["security_count"]:
            out.append(f"  ! {a['security_count']} of these are SECURITY updates")
        out.append(f"  run: {a['command']}")

    out.append("")
    out.append("PUBLISHED BY US, WHICH APT CANNOT SEE")
    for row in r["github"]:
        if row["status"] == BEHIND:
            out.append(f"  * {row['name']:<28} {row['installed']:<22} -> {row['latest']}")
            out.append(f"      {row['what']}")
            out.append(f"      run: {row['command']}")
        elif row["status"] == OK:
            out.append(f"    {row['name']:<28} {row['installed']:<22} current")
        elif row["status"] == ABSENT:
            out.append(f"    {row['name']:<28} not installed here")
        else:
            out.append(f"  ? {row['name']:<28} COULD NOT CHECK: {row['why']}")

    out.append("")
    out.append("INSTALLED BY HAND, WHICH NOTHING WATCHES")
    for row in r["native"]:
        head = f"  {row['name']:<28} {row['installed'] or 'unknown'}"
        if row["status"] == BEHIND:
            out.append("  *" + head[2:])
            out.append(f"      {row['why']}")
            out.append(f"      run: {row['command']}")
        else:
            out.append(head)
            if row.get("versions") and len(row["versions"]) > 1:
                out.append(f"      versions on disk: {', '.join(row['versions'])}")
            out.append(f"      {row['why']}")

    out.append("")
    if r["unknown_count"]:
        out.append(f"{r['behind_count']} behind, {r['unknown_count']} COULD NOT BE CHECKED.")
        out.append("An unchecked thing is not a current thing.")
    else:
        out.append(f"{r['behind_count']} behind, everything else checked and current.")
    return out


def apply_plan(r):
    """What --apply would do, and what it refuses to do.

    Anything needing root is refused with its line printed. The owner types sudo
    at his own terminal; a tool that does it for him has taken a decision that
    was not its own.
    """
    doable, needs_root = [], []
    for row in r["native"]:
        if row["status"] == BEHIND and row.get("command"):
            doable.append({"what": row["name"], "command": row["command"],
                           "verify": row.get("verify_with", "")})
    if r["apt"].get("items"):
        needs_root.append({"what": f"{len(r['apt']['items'])} apt package(s)",
                           "command": r["apt"]["command"]})
    for row in r["github"]:
        if row["status"] == BEHIND:
            needs_root.append({"what": row["name"], "command": row["command"]})
    return {"doable": doable, "needs_root": needs_root}
