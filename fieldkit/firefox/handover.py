"""The Linux -> Windows / Mac handover as a tool (P38 item D, owner order 2026-11-16).

Before: a model wrote the handover brief by hand every version (state/briefs/firefox-160-windows-recipe-...). Now:

    fieldkit firefox handover VERSION --to windows|mac [--patchset DIR] [--task ID] [--upstream SRC] [--send]

builds state/firefox/handover/VERSION-OS/ with
    manifest.json   patch-set repo, commit, pushed (git branch -r --contains), base branch (origin/HEAD), upstream tag and
                    commit (core/concepts, never pinned), the last replay proof of the Linux task, lessons and their
                    rules, the per-OS steps from cookbook.yaml
    patches/NNN-group--name.patch   the groups that apply on that OS, in policy order, minus exclude / exclude_on.OS
    APPLY.txt       the one apply step (git apply each patch in order onto the upstream tag)
    SHA256SUMS      every file above
and checks it. A failed check -> exit 3 and nothing is sent. --send: windows over the cable (peers link put + a note to
the other side's Zero); mac by scp to the ssh alias `macair` (SSH only, never the cable).
"""
import hashlib
import json
import os
import shutil
import subprocess
from fieldkit import proc as fkproc
from pathlib import Path

import yaml

from ..core import concepts

HERE = Path(__file__).resolve().parent
TARGETS = ("windows", "mac")


def cookbook():
    return yaml.safe_load((HERE / "cookbook.yaml").read_text(encoding="utf-8"))


def default_patchset():
    return Path(os.environ.get("GORILLA_FIREFOX", Path.home() / "Documents" / "FIREFOX.WORK" / "gorilla-firefox"))


def out_root():
    from ..core import settings
    return settings.state_root() / "state" / "firefox" / "handover"


def task_id(version):
    return f"firefox-{str(version).split('.')[0]}-linux"


def step_values(version, task):
    return {"v": version, "task": task, "batch_tag": concepts.batch_tag(version),
            "batch_title": concepts.batch_title(version)}


def platform_patches(patchset, platform):
    """-> [(group, Path)] in policy order: the groups that apply on `platform`, minus their excluded patches."""
    pol = json.loads((Path(patchset) / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    root = Path(patchset) / pol.get("patchset_root", "patches")
    out = []
    for g, spec in (pol.get("groups") or {}).items():
        if not concepts.group_applies(g, spec, platform):
            continue
        skip = concepts.excluded_on(spec, platform)
        out += [(g, p) for p in sorted((root / g).glob("*.patch")) if p.name not in skip and p.stem not in skip]
    return out


def _git(repo, *a):
    r = fkproc.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _replay_proof(tid):
    if not tid:
        return None
    try:
        from ..migrate import plan
        rp = [e for e in plan.journal_events(tid) if e.get("event") == "replay-proof"]
    except Exception:  # noqa: BLE001 - no task state on this machine: the check says so
        return None
    return rp[-1] if rp else None


def _lessons(tid):
    try:
        from ..buildh import lessonrules, task
        rows = task.lessons(tid)
    except Exception:  # noqa: BLE001
        return {"lessons": 0, "waiting": [], "rules": []}
    waiting, ruled = lessonrules.split(rows)
    return {"lessons": len(rows), "waiting": [w.get("lesson") for w in waiting],
            "rules": sorted({r["id"] for _, r in ruled}),
            "port_rules": lessonrules.port_rules_text()}


def build(version, to, patchset=None, tid=None, upstream=None, out=None, replay_proof=None, lessons=None):
    """-> (package dir, manifest, problems). `replay_proof` / `lessons` are for tests (else read from the task)."""
    if to not in TARGETS:
        raise ValueError(f"--to must be one of {TARGETS}")
    ps = Path(patchset or default_patchset())
    tid = tid or task_id(version)
    problems = []
    commit = _git(ps, "rev-parse", "HEAD")
    remote = _git(ps, "branch", "-r", "--contains", commit) if commit else None
    dirty = _git(ps, "status", "--porcelain")
    base = concepts.default_branch(ps)
    if not commit:
        problems.append(f"{ps}: not a git repository")
    elif not remote:
        problems.append(f"patch-set commit {commit[:12]} is on no remote branch (push it first: fieldkit ship push --auto)")
    if dirty:
        problems.append(f"patch-set repo has uncommitted changes ({len(dirty.splitlines())} path(s))")
    tag = concepts.upstream_tag(version)
    up_sha, up_how = concepts.upstream_ref(version, upstream) if upstream else (None, "no upstream tree given here: "
                                                                                       "the other side resolves the tag")
    if upstream and not up_sha:
        problems.append(up_how)
    rp = replay_proof if replay_proof is not None else _replay_proof(tid)
    if not rp or not rp.get("ok"):
        problems.append(f"no passing replay proof for {tid} (fieldkit build-harness replay {tid})")
    ls = lessons if lessons is not None else _lessons(tid)
    if ls.get("waiting"):
        problems.append(f"{len(ls['waiting'])} lesson(s) not yet a rule (fieldkit firefox lessons {tid})")
    try:
        pats = platform_patches(ps, to)
    except (OSError, ValueError) as e:
        pats, _ = [], problems.append(f"patch policy unreadable: {e}")
    if not pats:
        problems.append(f"no patch applies on {to}")
    from ..buildh import replay
    lost = replay.unregistered_groups(ps) if (ps / "config" / "patch_policy.json").is_file() else []
    if lost:
        problems.append(f"unregistered patch groups (never silently skipped): {[g for g, _ in lost]}")

    pkg = Path(out or out_root()) / f"{version}-{to}"
    if pkg.exists():
        shutil.rmtree(pkg)
    (pkg / "patches").mkdir(parents=True)
    names = []
    for i, (g, p) in enumerate(pats, 1):
        n = f"{i:03d}-{g}--{p.name}"
        shutil.copyfile(p, pkg / "patches" / n)
        names.append(n)
    cb = cookbook()
    steps = [s for s in cb["steps"] if s["system"] in (to, "all")]
    values = step_values(version, tid)
    man = {"product": concepts.product_name(version), "version": version, "to": to, "system": cb["systems"][to],
           "patchset": {"repo": str(ps), "commit": commit, "on_remote": (remote or "").split(), "base_branch": base},
           "upstream": {"tag": tag, "commit": up_sha, "how": up_how}, "replay_proof": rp, "lessons": ls,
           "patches": names, "steps": [dict(s, run=s["run"].format_map(values), does=s["does"].format_map(values))
                                       for s in steps],
           "problems": problems}
    (pkg / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    (pkg / "APPLY.txt").write_text(
        f"{man['product']} for {to}: pristine upstream {tag}, then every patch in patches/ in name order:\n"
        f"  git -C SRC checkout {tag}\n  for p in patches/*.patch; do git -C SRC apply --whitespace=nowarn \"$PWD/$p\" || break; done\n"
        f"(PowerShell: Get-ChildItem patches\\*.patch | Sort-Object Name | ForEach-Object {{ git -C SRC apply $_.FullName }})\n",
        encoding="utf-8", newline="\n")
    sums = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(pkg).as_posix()}"
            for f in sorted(pkg.rglob("*")) if f.is_file()]
    (pkg / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    return pkg, man, problems


def verify(pkg):
    """-> problems: every file in SHA256SUMS present with its hash, every patch listed."""
    pkg, bad = Path(pkg), []
    for line in (pkg / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        h, _, name = line.partition("  ")
        f = pkg / name
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != h:
            bad.append(f"checksum: {name}")
    return bad


def send(pkg, to, say=print):
    """Send a checked package. -> exit code."""
    pkg = Path(pkg)
    archive = shutil.make_archive(str(pkg), "gztar", pkg.parent, pkg.name)
    sha = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    if to == "windows":
        cmds = [["fieldkit", "peers", "link", "put", archive],
                ["fieldkit", "peers", "link", "send", f"Firefox handover {pkg.name}: {Path(archive).name} sha256 {sha}. "
                 "Unpack, check SHA256SUMS, follow manifest.json steps (APPLY.txt)."]]
    else:
        cmds = [["scp", archive, "macair:"],
                ["ssh", "macair", f"shasum -a 256 {Path(archive).name}"]]
    for c in cmds:
        say("  $ " + " ".join(c))
        r = fkproc.run(c, capture_output=True, text=True)
        if r.returncode:
            say(f"  failed ({r.returncode}): {(r.stderr or r.stdout).strip()[:300]}")
            return 1
        if c[0] == "ssh" and sha not in r.stdout:
            say(f"  the Mac's SHA-256 differs: {r.stdout.strip()[:120]} (sent {sha})")
            return 3
    say(f"  sent {Path(archive).name} sha256 {sha}")
    return 0
