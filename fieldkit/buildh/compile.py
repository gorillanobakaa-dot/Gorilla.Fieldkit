"""The compile gate. The harness does not run the compile (that changes the machine's power scheme and takes
hours: it is the owner's action, through Gorilla.firefox's own orchestrator). It decides, with evidence,
(1) whether the ported source tree is fit to be compiled, and (2) whether what came out really is that tree's build.

  build-gate    BEFORE: every step done, final checks passed and nothing changed since, no hand edits, no skips,
                no copied answer keys, journal chain intact. On success it records exactly which tree and which
                mozconfig the owner is about to build, and prints the owner's commands.
  build-verify  AFTER: the tree and mozconfig are unchanged since the gate; the installer and zip are NEWER than
                the gate (the owner's own harness once recorded a seven-month-old dist); a real size; the built
                firefox.exe reports the pinned version; hashes are written to build-result.json.
                Then the browser that came out (dist/bin), before any install (2026-10-08): its BuildID is from this
                build and Help > About shows it (buildstamp.py, D-157-38), and every about: page is read and judged
                (aboutpages.py). build-result.json records the BuildID; post-install holds the install to it.
"""
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from ..core import settings
from . import audit, task

MIN_ARTIFACT_MB = 50


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(wd, *args):
    return subprocess.run(["git", "-C", str(wd), *args], capture_output=True, text=True, errors="replace").stdout.strip()


def mozconfig_path(harness_root=None):
    return Path(harness_root or settings.expand("${LOCAL:firefox.root}")) / "config" / "mozconfig.win64"


def objdir(mozconfig):
    m = re.search(r"MOZ_OBJDIR=(\S+)", Path(mozconfig).read_text(encoding="utf-8", errors="replace"))
    return Path(m.group(1)) if m else None


def _record_path(task_id):
    return task.STATE / task_id / "build-record.json"


def gate(task_id, harness_root=None, write=True):
    """-> rows [{check, ok, evidence}]; writes build-record.json only when every row passes."""
    t = task.load(task_id)
    wd, rows = Path(t["workdir"]), []

    def row(name, ok, evidence):
        rows.append({"check": name, "ok": bool(ok), "evidence": evidence})

    left = [s["id"] for s in t["steps"] if s["status"] not in ("done", "obsolete") and not s.get("post_build")]
    row("every step is done", not left, "all done" if not left else f"{len(left)} not done, first: {left[0]}")
    post = [s["id"] for s in t["steps"] if s.get("post_build") and s["status"] not in ("done", "obsolete")]
    row("owner checks that need an objdir are listed for build-verify (not a reason to hold the gate)", True,
        "none" if not post else f"{len(post)}: {[p.replace('owner-preflight-', '') for p in post][:4]}")
    obs = [s["id"] for s in t["steps"] if s["status"] == "obsolete"]
    row("obsolete changes listed for review (upstream removed their target; nothing was ported)", True,
        "none" if not obs else f"{len(obs)}: {[o.split('-', 1)[1][-50:] for o in obs][:6]}")
    dropped = [s["id"] for s in t["steps"] if s.get("dropped_by_owner")]
    row("every dropped change was a briefed decision", all(s.get("drop_fingerprint") for s in t["steps"] if s.get("dropped_by_owner")),
        f"{len(dropped)} dropped by the owner after an explanation: {[d.split('-', 1)[1][:50] for d in dropped][:4]}" if dropped else "none dropped")
    jp = task.STATE / task_id / "journal.jsonl"
    ev = [json.loads(l) for l in jp.read_text(encoding="utf-8").splitlines() if l.strip()] if jp.is_file() else []
    last_final = max((i for i, e in enumerate(ev) if e.get("event") == "script-done" and e.get("step") == "final-checks"), default=-1)
    later = [e for e in ev[last_final + 1:] if e.get("event") in ("submit", "auto-done", "revert", "unblock")]
    row("final checks passed, and nothing changed after them", last_final >= 0 and not later,
        "passed, nothing since" if last_final >= 0 and not later else
        ("final-checks never passed" if last_final < 0 else f"{len(later)} change(s) after the last pass"))
    for name, ok, evidence in audit.journal_checks(ev):
        row(name, ok, evidence)
    problems, count, _ = task.verify_journal(task_id)
    row("journal hash chain intact", not problems, f"{count} lines" if not problems else "; ".join(problems[:2]))
    dirty = _git(wd, "status", "--porcelain")
    row("working copy: no hand edits", not dirty, "clean" if not dirty else f"uncommitted: {dirty.splitlines()[:3]}")
    from . import firefox
    stray = [p.name for p in firefox.leftovers(wd)] if wd.exists() else []
    row("no .rej / .orig leftovers", not stray, "none" if not stray else f"{len(stray)}: {stray[:3]}")
    lost = firefox.unexplained_deletions(t) if wd.exists() else []
    row("no pristine file missing without a patch that deletes it", not lost,
        "none missing" if not lost else f"{len(lost)} missing, e.g. {lost[:3]}")
    from . import verify as vf
    for name, ok, evidence in vf.problems(vf.verify(task_id)):
        row(name, ok, evidence)
    # the IDEAS behind Gorilla's fixes (fieldkit/buildh/techniques.py): a lost lock, or a new waiter / download path
    # in a later Firefox, stops the build before it compiles (2026-10-03: the blank new tab, T-157-31-A)
    from . import techniques
    # the UI rules (fieldkit/buildh/uicheck.py): certain mistakes in the lines Gorilla added, before compiling
    from . import uicheck
    for name, ok, evidence in uicheck.gate_rows(wd) if (wd / "browser/config/version.txt").is_file() else []:
        row(name, ok, evidence)
    for name, ok, evidence in techniques.gate_rows(wd) if (wd / "browser/config/version.txt").is_file() else []:
        row(name, ok, evidence)
    moz = mozconfig_path(harness_root)
    row("mozconfig present", moz.is_file(), str(moz))
    od = objdir(moz) if moz.is_file() else None
    row("object directory named in the mozconfig", od is not None, str(od))
    ok = all(r["ok"] for r in rows)
    if ok and write:
        rec = {"task": task_id, "at": time.time(), "head": _git(wd, "rev-parse", "HEAD"),
               "tree": _git(wd, "rev-parse", "HEAD^{tree}"), "mozconfig_sha256": _sha(moz), "objdir": str(od),
               "version": t["meta"]["upstream"]["version"], "workdir": str(wd)}
        _record_path(task_id).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rows


def verify(task_id, run_binary=True):
    """-> rows. Writes build-result.json (hashes) when every row passes."""
    rows = []

    def row(name, ok, evidence):
        rows.append({"check": name, "ok": bool(ok), "evidence": evidence})

    p = _record_path(task_id)
    if not p.is_file():
        row("a passed build gate exists", False, "run build-gate first: nothing says which tree was built")
        return rows
    rec = json.loads(p.read_text(encoding="utf-8"))
    wd = Path(rec["workdir"])
    # 2026-10-04 (build 22): the row failed on uncommitted edits while its evidence said "unchanged since the gate",
    # because the evidence looked at the commit only. Both halves now speak for themselves.
    same_commit = _git(wd, "rev-parse", "HEAD^{tree}") == rec["tree"]
    dirty = [l.strip().split(None, 1)[-1] for l in _git(wd, "status", "--porcelain").splitlines() if l.strip()]
    row("the tree is the one that was gated", same_commit and not dirty,
        "unchanged since the gate" if same_commit and not dirty else
        "; ".join(x for x in ("the committed source changed after the gate" if not same_commit else "",
                              f"{len(dirty)} file(s) edited and not committed: {', '.join(dirty[:4])}" if dirty else "") if x))
    moz = mozconfig_path()
    row("mozconfig unchanged since the gate", moz.is_file() and _sha(moz) == rec["mozconfig_sha256"],
        "unchanged" if moz.is_file() and _sha(moz) == rec["mozconfig_sha256"] else "mozconfig differs from what was gated")
    dist = Path(rec["objdir"]) / "dist"
    found = {}
    for kind, pattern in (("installer", "*.installer.exe"), ("zip", "*.zip")):
        found[kind] = sorted(dist.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)[:1] if dist.is_dir() else []
        if not found[kind]:
            row(f"{kind} was produced", False, f"none in {dist}")
            continue
        f = found[kind][0]
        fresh = f.stat().st_mtime >= rec["at"]
        big = f.stat().st_size >= MIN_ARTIFACT_MB * 2 ** 20
        row(f"{kind} is from THIS build", fresh, f"{f.name} {'newer' if fresh else 'OLDER (stale dist?)'} than the gate")
        row(f"{kind} has a real size", big, f"{f.stat().st_size / 2 ** 20:.0f} MB, need at least {MIN_ARTIFACT_MB}")
    exe = dist / "bin" / "firefox.exe"
    if run_binary:
        if exe.is_file():
            out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60, errors="replace").stdout
            row("built firefox.exe reports the pinned version", rec["version"] in out, out.strip() or "no output")
        else:
            row("built firefox.exe exists", False, str(exe))
    # the icons: the gorilla inside firefox.exe, the crisp logo, the installer's outer icon (icons.py)
    t = task.load(task_id)
    from . import icons, ownercheck
    root = ownercheck._owner_root(t)
    brand_rel = icons.branding_dir(moz.read_text(encoding="utf-8", errors="replace")) if moz.is_file() else None
    brand = wd / brand_rel if brand_rel else None
    if exe.is_file():                                    # (a missing exe already fails its own row above)
        if brand and brand.is_dir():
            for name, hits, total, detail in icons.embedded(exe, brand):
                row(f"branding {name} is embedded in firefox.exe", hits == total and total > 0, f"{hits} of {total} image(s): {detail}")
        else:
            row("branding icons embedded in firefox.exe", False, "no --with-branding dir found from the mozconfig")
    if found.get("installer") and brand and (brand / "firefox.ico").is_file():
        # the owner's verify_installer.py, as a row: the branding icon's pixel data inside the packaged installer
        inst = found["installer"][0]
        rows_ico = [r for r in icons.embedded(inst, brand) if r[0] == "firefox.ico"]
        if rows_ico:
            name, hits, total, detail = rows_ico[0]
            row("branding firefox.ico is embedded in the packaged installer", hits == total and total > 0, f"{hits} of {total} image(s): {detail}")
    if root:
        ok, text = icons.logo_provenance(root)
        row("internal-pages logo is crisp (owner's Crisp Icon Doctrine gates)", bool(ok), (text.splitlines() or ["ok"])[-1][:160]
            if ok is not None else "tool missing: " + text)
        ok, text = icons.installer_stub_check(root)
        row("installer SFX stub carries this build's icon", bool(ok), (text.splitlines() or ["ok"])[0][:160] if ok is not None else "tool missing: " + text)
    # the owner's own preflight, after the build: the checks that needed an objdir must pass now
    if root and run_binary:
        rc, text = ownercheck.run_preflight(root)
        blockers = ownercheck.parse(text) if rc is not None else []
        row("the owner's preflight passes on the built tree", not blockers,
            "no blocker" if not blockers else "; ".join(f"{b['name']}: {b['detail'][:80]}" for b in blockers[:3]))
        if not blockers:
            for s in t["steps"]:
                if s.get("post_build") and s["status"] not in ("done", "obsolete"):
                    s["status"], s["done_by"] = "done", "build-verify"
            task.save(t)
    # after the build, before any install: the browser that came out, read the way post-install reads the installed
    # one (owner 2026-10-08: checks "to be run before or after the build ... preferably both so we can catch the
    # mistakes"): its build stamp, and every about: page (blank pages, missing strings, script errors, requests)
    built_id = None
    if run_binary and exe.is_file():
        from . import aboutpages, buildstamp
        srows, built_id = buildstamp.built_rows(rec["objdir"], rec.get("at"), say=lambda m: None)
        rows.extend(srows)
        rows.extend(aboutpages.run(dist / "bin", built_id, say=lambda m: None)["rows"])
    if all(r["ok"] for r in rows):
        if built_id:
            from . import buildstamp
            buildstamp.remember(task_id, built_id, rec.get("head"), rec["tree"])
        res = {"task": task_id, "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"), "tree": rec["tree"],
               "build_id": built_id,
               "artifacts": {k: {"file": str(v[0]), "sha256": _sha(v[0])} for k, v in found.items() if v}}
        (task.STATE / task_id / "build-result.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return rows


def lines(rows, title):
    out = [f"  {'PASS' if r['ok'] else 'FAIL'}  {r['check']}: {r['evidence']}" for r in rows]
    bad = [r for r in rows if not r["ok"]]
    return out + [f"{title}: {'PASSED' if not bad else 'NOT PASSED - ' + str(len(bad)) + ' check(s) failed'}"]
