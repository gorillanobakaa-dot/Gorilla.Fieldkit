# Version: 1.0.0 · updated 26-11-12
"""fieldkit prepare: every tool and dependency a job needs, found and set up BEFORE the job starts (plan item P9).

    fieldkit prepare JOB|BRIEF.md|RECIPE [--apply] [--use-fallback] [--json]
    fieldkit prepare defect TOOL --job JOB       a tool was missing MID-job: journal it (the table is then fixed)
    fieldkit prepare list                        the jobs and tools of fieldkit/core/prepare.yaml

Owner 2026-11-12: "prepare first, execute second". The Firefox 160 job ran, then stopped on a missing nft, dnsmasq and
mitmdump and asked the owner twice mid-job. The requirements are data (prepare.yaml); this reads them:

  1. what the job needs: a job id, an emerge recipe, or a brief (words matched against each job's `match`), plus the
     machine facts (disk, RAM, root);
  2. what is missing, looked for in the PATH the tool will run under (root's secure_path for a sudo tool: a program
     in ~/.local/bin is invisible to it) and the sbin folders;
  3. what needs no root is set up here, now (pipx or a venv under ~/.local/share/fieldkit-tools; a download only with
     a sha256); what needs root becomes ONE script (state/prepare/JOB-root.sh) and ONE ask (the first line it prints);
  4. READY (exit 0) when nothing is missing, or the owner chose the fallback of each missing item (--use-fallback);
     NOT READY (exit 3) otherwise: the job does not start. Wired into task start, emerge run, runbook run, goal run.

Nothing here probes the network (a probe is a ping out): the hosts a job talks to are listed, not tested.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .host import find_tool, host, tool_dirs
from .settings import state_root

TABLE = Path(__file__).with_name("prepare.yaml")
USER_VENVS = Path.home() / ".local" / "share" / "fieldkit-tools"


def table(path=None):
    import yaml
    return yaml.safe_load(Path(path or TABLE).read_text(encoding="utf-8")) or {}


def family(h=None):
    from .doctor import _family
    return _family(h)


def resolve(target, tbl=None):
    """target: a job id, an emerge recipe, a brief file or its text -> (sorted job ids, what it was)."""
    tbl = tbl or table()
    jobs = tbl.get("jobs") or {}
    if target in jobs:
        return [target], "job"
    found = {n for n, j in jobs.items() if target in (j.get("recipes") or [])}
    if found:
        return sorted(found), "recipe"
    text = target
    try:
        p = Path(target)
        if len(target) < 400 and p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass
    low = text.lower()
    found = {n for n, j in jobs.items() if any(w.lower() in low for w in (j.get("match") or []))}
    return sorted(found), "brief"


def _have_root():
    if os.name == "nt":
        return True
    if os.geteuid() == 0:
        return True
    try:
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def machine(h=None, run_root=None):
    home = os.path.expanduser("~")
    free = round(shutil.disk_usage(home).free / 2**30, 1)
    try:
        from .doctor import memory_gb
        ram = memory_gb()
    except Exception:
        ram = None
    return {"disk_gb": free, "ram_gb": ram, "root": (run_root or _have_root)()}


def _tool_rows(ids, tbl, fam, which):
    rows = []
    for tid in ids:
        t = (tbl.get("tools") or {}).get(tid)
        if not t:
            rows.append({"tool": tid, "ok": False, "detail": "not in prepare.yaml", "step": "unknown", "cmd": ""})
            continue
        runs_as = t.get("runs_as", "user")
        path = next((p for b in t["bins"] if (p := which(b, runs_as))), None)
        row = {"tool": tid, "ok": bool(path), "detail": path or f"not found for {runs_as} in {':'.join(tool_dirs(runs_as))[:80]}",
               "runs_as": runs_as, "step": "", "cmd": "", "fallback": t.get("fallback", "")}
        if not path:
            pkg = (t.get("pkg") or {}).get(fam)
            in_distro = pkg and fam not in (t.get("not_in") or [])
            if in_distro:
                row["step"], row["cmd"] = "root", _pkg_cmd(fam, pkg)
            elif t.get("pip") and runs_as == "root":
                row["step"], row["cmd"] = "root", t["pip_root"]
            elif t.get("pip"):
                row["step"], row["cmd"] = "user", t["pip"]
            elif t.get("download"):
                row["step"], row["cmd"] = "user", "download " + t["download"]["url"]
            else:
                row["step"] = "owner"
                row["cmd"] = f"no recipe for {fam}: " + (t.get("fallback") or "add one to fieldkit/core/prepare.yaml")
        rows.append(row)
    return rows


def _pkg_cmd(fam, pkg):
    return {"debian": f"apt-get install -y {pkg}", "fedora": f"dnf install -y {pkg}", "mac": f"brew install {pkg}",
            "windows": f"winget install --id {pkg} -e"}.get(fam, f"install {pkg}")


def check(target, tbl=None, which=None, h=None, mach=None, fam=None):
    """-> {"ok", "jobs", "kind", "rows", "blockers", "root_cmds"}: nothing is changed."""
    tbl = tbl or table()
    which = which or (lambda b, runs_as="user": find_tool(b, runs_as=runs_as))
    fam = fam or family(h)
    jobs, kind = resolve(target, tbl)
    spec = tbl.get("jobs") or {}
    ids = list(dict.fromkeys(t for j in ["core"] + jobs for t in (spec.get(j) or {}).get("tools", [])))
    rows = _tool_rows(ids, tbl, fam, which)
    mach = mach or machine()
    blockers = []
    need_disk = max([(spec.get(j) or {}).get("disk_gb", 0) for j in jobs] or [0])
    need_ram = max([(spec.get(j) or {}).get("ram_gb", 0) for j in jobs] or [0])
    if need_disk and mach["disk_gb"] < need_disk:
        blockers.append(f"disk: {mach['disk_gb']} GB free, the job needs {need_disk} GB")
    if need_ram and mach["ram_gb"] and mach["ram_gb"] < need_ram:
        blockers.append(f"RAM: {mach['ram_gb']} GB, the job needs {need_ram} GB")
    privs = [p for j in jobs for p in (spec.get(j) or {}).get("privileges", [])]
    if "root" in privs and not mach["root"]:
        rows.append({"tool": "root", "ok": False, "detail": "no root and no passwordless sudo", "step": "root",
                     "cmd": "", "fallback": ""})
    root_cmds = [r["cmd"] for r in rows if not r["ok"] and r["step"] == "root" and r["cmd"]]
    network = sorted({n for j in jobs for n in (spec.get(j) or {}).get("network", [])})
    return {"ok": not blockers and all(r["ok"] for r in rows), "target": target if len(target) < 80 else "brief",
            "jobs": jobs, "kind": kind, "family": fam, "rows": rows, "blockers": blockers, "root_cmds": root_cmds,
            "network": network, "machine": mach}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def install_user(tool, spec, run=None, fetch=None):
    """The no-root step of one missing tool. -> (ok, line). pipx when present, else a venv under USER_VENVS with a
    link in ~/.local/bin; a download only when the table gives its sha256 (a mismatch deletes nothing the user had)."""
    run = run or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=1800))
    if spec.get("download"):
        d = spec["download"]
        if not d.get("sha256"):
            return False, f"{tool}: download has no sha256 in prepare.yaml: refused"
        dest = Path(os.path.expanduser(d["dest"]))
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        (fetch or (lambda u, p: urllib.request.urlretrieve(u, p)))(d["url"], tmp)
        got = _sha256(tmp)
        if got != d["sha256"]:
            tmp.unlink(missing_ok=True)
            return False, f"{tool}: sha256 {got[:12]} is not the table's {d['sha256'][:12]}: not installed"
        tmp.replace(dest)
        dest.chmod(0o755)
        return True, f"{tool}: downloaded and checked sha256 {got[:12]} -> {dest}"
    pkg = spec["pip"]
    if shutil.which("pipx"):
        r = run(["pipx", "install", pkg])
    else:
        venv = USER_VENVS / spec["bins"][0]
        r = run([sys.executable, "-m", "venv", str(venv)])
        if r.returncode == 0:
            r = run([str(venv / "bin" / "pip"), "install", pkg])
            if r.returncode == 0:
                link = Path.home() / ".local" / "bin" / spec["bins"][0]
                link.parent.mkdir(parents=True, exist_ok=True)
                if not link.exists():
                    link.symlink_to(venv / "bin" / spec["bins"][0])
    return r.returncode == 0, f"{tool}: {'installed' if r.returncode == 0 else 'FAILED'} ({pkg}) {(r.stderr or '')[-200:]}"


def root_script(job, cmds, root=None):
    """The ONE script for everything that needs root: apt lines joined into one call, the rest in order."""
    d = state_root(root) / "state" / "prepare"
    d.mkdir(parents=True, exist_ok=True)
    apt = sorted({w for c in cmds if c.startswith("apt-get install -y ") for w in c.split()[3:]})
    rest = [c for c in cmds if not c.startswith("apt-get install -y ")]
    lines = ["#!/bin/sh", "set -e"]
    if apt:
        lines.append("sudo apt-get install -y " + " ".join(apt))
    lines += [c if c.startswith("sudo ") else "sudo " + c for c in rest]
    p = d / f"{job}-root.sh"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def prepare(target, apply=True, use_fallback=False, tbl=None, which=None, run=None, fetch=None, mach=None, fam=None,
            root=None):
    """Check, set up what needs no root, batch the rest. -> the check dict plus status READY | READY-FALLBACK | NOT-READY,
    `done` (lines of what was set up), `ask` (the one owner line) and `script`."""
    tbl = tbl or table()
    r = check(target, tbl, which, mach=mach, fam=fam)
    r["done"] = []
    if apply:
        for row in r["rows"]:
            if not row["ok"] and row["step"] == "user":
                ok, line = install_user(row["tool"], tbl["tools"][row["tool"]], run=run, fetch=fetch)
                r["done"].append(line)
        if r["done"]:
            r = {**check(target, tbl, which, mach=mach, fam=fam), "done": r["done"]}
    open_rows = [x for x in r["rows"] if not x["ok"]]
    r["script"], r["ask"] = "", ""
    if r["root_cmds"]:
        name = (r["jobs"] or ["job"])[0]
        r["script"] = str(root_script(name, r["root_cmds"], root))
        r["ask"] = f"ONE ask for {len(r['root_cmds'])} step(s) needing root: sh {r['script']}"
    chosen = use_fallback and all(x.get("fallback") for x in open_rows) and not r["blockers"]
    r["status"] = "READY" if r["ok"] else "READY-FALLBACK" if chosen else "NOT-READY"
    r["ready"] = r["status"] != "NOT-READY"
    r["next"] = ("fieldkit task start BRIEF.md | fieldkit emerge run RECIPE" if r["ready"] else
                 (r["ask"] + "; then fieldkit prepare " + str(r["target"]) if r["ask"] else "fix the rows above, then fieldkit prepare again")
                 + "  (or --use-fallback when every missing row has one)")
    return r


def lines(r):
    out = [f"PREPARE  {r['kind']} {r['target']} -> jobs {', '.join(r['jobs']) or 'none known (core only)'} on {r['family']}"]
    for x in r["rows"]:
        out.append(f"  {'ok ' if x['ok'] else 'MISSING':<8}{x['tool']:<10}{x['detail']}"
                   + (f"   [{x['step']}] {x['cmd']}" if x["cmd"] and not x["ok"] else ""))
    out += [f"  BLOCKER  {b}" for b in r["blockers"]] + [f"  done     {d}" for d in r.get("done", [])]
    if r["network"]:
        out.append("  network  " + ", ".join(r["network"]) + "  (listed, not probed)")
    if r.get("ask"):
        out.append("  ASK      " + r["ask"])
    out.append(r["status"] if r["ready"] else f"NOT READY - {sum(not x['ok'] for x in r['rows']) + len(r['blockers'])} open")
    out.append("NEXT: " + r["next"])
    return out


def gate(target, out=print):
    """What every job start calls first. -> True when the job may start; prints the rows when it may not."""
    try:
        r = prepare(target)
    except Exception as e:                          # a broken table must not stop work silently: say so, go on
        out(f"PREPARE skipped: {type(e).__name__}: {e}")
        return True
    if not r["ready"]:
        out("\n".join(lines(r)))
    return r["ready"]


def defect(tool, job, root=None):
    """A tool was missing mid-job: journal it; the next prepare run then asks up front."""
    f = state_root(root) / "state" / "prepare" / "defects.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tool": tool, "job": job,
           "known": tool in (table().get("tools") or {})}
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit prepare", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="a job id, an emerge recipe, a brief file; or: defect TOOL | list")
    ap.add_argument("tool", nargs="?")
    ap.add_argument("--job", default="unknown")
    ap.add_argument("--check", action="store_true", help="look only: set nothing up")
    ap.add_argument("--use-fallback", action="store_true", help="the owner chose the fallback of each missing item")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.target:
        ap.print_usage()
        return 2
    if a.target == "list":
        t = table()
        print("jobs: " + ", ".join(t["jobs"]) + "\ntools: " + ", ".join(t["tools"]))
        return 0
    if a.target == "defect":
        row = defect(a.tool or "?", a.job)
        print(json.dumps(row) if a.json else f"DEFECT journaled: {row['tool']} missing mid-job {row['job']}; "
              f"{'it is in the table, check why prepare missed it' if row['known'] else 'add it to fieldkit/core/prepare.yaml'}")
        return 0
    r = prepare(a.target, apply=not a.check, use_fallback=a.use_fallback)
    print(json.dumps(r, indent=1, default=str) if a.json else "\n".join(lines(r)))
    return 0 if r["ready"] else 3


if __name__ == "__main__":
    sys.exit(main())
