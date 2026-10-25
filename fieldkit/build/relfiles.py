# Version: 1.0.0 · updated 26-10-25
"""Release files as evidence: the page's table measured from SHA256SUMS, and the published release checked.

    fieldkit release table SHA256SUMS-v0.1.152.txt [--dir DIR] [--check PAGE.md]
    fieldkit release verify v0.1.152 --repo OWNER/NAME [--dir EMPTY_DIR] [--no-dispatch | --run-id N] [--wait 1800]

WHY (2026-10-25, Gorilla OpenCode v0.1.152, released from Windows)
    The release page's "File | Bytes | SHA-256" table was typed by hand from a terminal; between two builds of the
    .deb its checksum changed three times and only luck kept the page right (typed facts). After publishing, the
    files were downloaded again by hand, checked by hand, and the linux-install workflow was dispatched and watched
    by hand. Both are one command now.

table   every line of SHA256SUMS -> a markdown row with the file's size, after the file in DIR (default: the
        SHA256SUMS folder) is hashed again and found equal. A missing or different file: refused, no table.
        --check PAGE: every row of the page's table must equal SHA256SUMS and the measured size, and every file
        in SHA256SUMS must have a row.
verify  into a NEW empty folder: download every asset of the tag (gh), check each against the published
        SHA256SUMS (every listed file present and equal; GitHub's own digest of each asset too), check the page's
        table against them, then dispatch linux-install.yml for the tag (or read --run-id N), wait for it, and
        report each job (deb-ubuntu, rpm-fedora). Exit 0 only when everything passed.
"""
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

ROW = re.compile(r"^\|\s*`?([^`|]+?)`?\s*\|\s*([\d,]+)\s*\|\s*`?([0-9a-f]{64})`?\s*\|\s*$", re.M)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_sums(path_or_text):
    """{name: sha256} from a sha256sum file ("HASH  name" or "HASH *name"). Raises ValueError on a bad line."""
    text = path_or_text if "\n" in str(path_or_text) or not Path(str(path_or_text)).is_file() \
        else Path(path_or_text).read_text(encoding="utf-8")
    out = {}
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line.rstrip("\r"))
        if not m:
            raise ValueError(f"line {i} is not 'SHA256  name': {line[:80]!r}")
        if m.group(2) in out:
            raise ValueError(f"{m.group(2)} is listed twice")
        out[m.group(2)] = m.group(1).lower()
    return out


def table(sums_path, folder=None):
    """The markdown table, measured: each file hashed again and found equal to SHA256SUMS. Raises ValueError."""
    sums_path = Path(sums_path)
    folder = Path(folder) if folder else sums_path.parent
    rows = ["| File | Bytes | SHA-256 |", "|---|---|---|"]
    for name, sha in parse_sums(sums_path).items():
        p = folder / name
        if not p.is_file():
            raise ValueError(f"{name} is in {sums_path.name} but not in {folder}")
        if sha256(p) != sha:
            raise ValueError(f"{name} in {folder} does not match {sums_path.name}")
        rows.append(f"| `{name}` | {p.stat().st_size:,} | `{sha}` |")
    return "\n".join(rows) + "\n"


def check_table(page, sums, sizes):
    """-> [problems]: rows of the page's table that differ from SHA256SUMS or the measured sizes, files of SHA256SUMS
    with no row. Empty list: the table is true."""
    rows = {m.group(1).strip(): (int(m.group(2).replace(",", "")), m.group(3)) for m in ROW.finditer(page or "")}
    bad = []
    if not rows:
        return ["no 'File | Bytes | SHA-256' table found"]
    for name, (size, sha) in rows.items():
        if name not in sums:
            bad.append(f"{name}: on the page, not in SHA256SUMS")
            continue
        if sha != sums[name]:
            bad.append(f"{name}: page says {sha[:12]}..., SHA256SUMS {sums[name][:12]}...")
        if name in sizes and size != sizes[name]:
            bad.append(f"{name}: page says {size:,} bytes, the file has {sizes[name]:,}")
    bad += [f"{n}: in SHA256SUMS, no row on the page" for n in sums if n not in rows]
    return bad


# ---- the published release -----------------------------------------------------------------------------------
def _gh(args, gh="gh", timeout=600):
    r = subprocess.run([gh] + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def check_download(folder, assets):
    """-> {ok, sums, problems, rows}. assets: [{name, size, digest}] from GitHub. Every file in the published
    SHA256SUMS present and equal; every asset's GitHub digest equal to the downloaded bytes; assets outside
    SHA256SUMS named (release notes and the like are allowed, a program is not)."""
    folder = Path(folder)
    problems, rows = [], []
    sumfiles = sorted(folder.glob("SHA256SUMS-*.txt"))
    if len(sumfiles) != 1:
        return {"ok": False, "sums": {}, "rows": [], "problems": [f"{len(sumfiles)} SHA256SUMS file(s), expected one"]}
    sums = parse_sums(sumfiles[0])
    by_name = {a["name"]: a for a in assets}
    for name, sha in sums.items():
        p = folder / name
        if not p.is_file():
            problems.append(f"{name}: in SHA256SUMS, not published")
            continue
        got = sha256(p)
        rows.append({"file": name, "bytes": p.stat().st_size, "sha256": got, "match": got == sha})
        if got != sha:
            problems.append(f"{name}: downloaded bytes do not match SHA256SUMS")
    for a in assets:
        p = folder / a["name"]
        digest = (a.get("digest") or "").removeprefix("sha256:")
        if p.is_file() and digest and sha256(p) != digest:
            problems.append(f"{a['name']}: GitHub's digest differs from the downloaded bytes")
        if a["name"] not in sums and a["name"] != sumfiles[0].name and not a["name"].endswith((".md", ".txt", ".sha256")):
            problems.append(f"{a['name']}: published but not in {sumfiles[0].name}")
    return {"ok": not problems, "sums": sums, "rows": rows, "problems": problems, "sumfile": sumfiles[0].name}


def _new_run(repo, workflow, since, gh="gh", tries=30, pause=4.0):
    """The workflow_dispatch run created at or after `since` (UTC ISO). gh workflow run prints no id."""
    for _ in range(tries):
        runs = json.loads(_gh(["run", "list", "-R", repo, "--workflow", workflow, "--event", "workflow_dispatch",
                               "--limit", "5", "--json", "databaseId,createdAt"], gh) or "[]")
        fresh = [r for r in runs if r["createdAt"] >= since]
        if fresh:
            return min(fresh, key=lambda r: r["createdAt"])["databaseId"]
        time.sleep(pause)
    raise RuntimeError(f"no {workflow} run appeared after dispatching it")


def wait_run(repo, run_id, timeout=1800, gh="gh", pause=20.0, clock=time.monotonic, sleep=time.sleep):
    """-> {status, conclusion, jobs: {name: conclusion}, url} once the run completes, or status 'timeout'."""
    end = clock() + timeout
    while True:
        d = json.loads(_gh(["run", "view", str(run_id), "-R", repo, "--json", "status,conclusion,jobs,url"], gh))
        if d.get("status") == "completed" or clock() >= end:
            return {"status": d.get("status") if d.get("status") == "completed" else "timeout",
                    "conclusion": d.get("conclusion"), "url": d.get("url"),
                    "jobs": {j["name"]: j.get("conclusion") or j.get("status") for j in d.get("jobs", [])}}
        sleep(pause)


def verify(tag, repo, folder, dispatch=True, run_id=None, wait=1800, workflow="linux-install.yml", gh="gh",
           want_jobs=("deb-ubuntu", "rpm-fedora")):
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        raise ValueError(f"{folder} is not empty: downloads go into a new empty folder")
    folder.mkdir(parents=True, exist_ok=True)
    view = json.loads(_gh(["release", "view", tag, "-R", repo, "--json", "assets,body,isDraft"], gh))
    assets = [{"name": a["name"], "size": a.get("size"), "digest": a.get("digest")} for a in view.get("assets", [])]
    _gh(["release", "download", tag, "-R", repo, "-D", str(folder)], gh, timeout=3600)
    d = check_download(folder, assets)
    page = check_table(view.get("body") or "", d["sums"], {r["file"]: r["bytes"] for r in d["rows"]}) if d["sums"] else []
    out = {"tag": tag, "repo": repo, "folder": str(folder), "draft": view.get("isDraft"), "files": d["rows"],
           "problems": d["problems"] + [f"page table: {x}" for x in page], "workflow": None}
    if dispatch or run_id:
        if not run_id:
            since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))
            _gh(["workflow", "run", workflow, "-R", repo, "-f", f"tag={tag}"], gh)
            run_id = _new_run(repo, workflow, since, gh)
        w = wait_run(repo, run_id, wait, gh)
        w["run_id"] = run_id
        out["workflow"] = w
        for j in want_jobs:
            if w["jobs"].get(j) != "success":
                out["problems"].append(f"{workflow} job {j}: {w['jobs'].get(j) or 'absent'}")
        if w["status"] != "completed":
            out["problems"].append(f"{workflow} run {run_id} did not finish in {wait} s")
    else:
        out["problems_not_checked"] = f"{workflow} not run (--no-dispatch)"
    out["ok"] = not out["problems"]
    return out


def lines(r):
    out = [f"{'OK' if r['ok'] else 'FAILED'}  {r['repo']} {r['tag']}: {len(r['files'])} file(s) checked in {r['folder']}"]
    out += [f"  {'=' if f['match'] else 'X'} {f['file']}  {f['bytes']:,} bytes" for f in r["files"]]
    w = r.get("workflow")
    if w:
        out.append(f"  workflow run {w['run_id']}: {w['status']} {w.get('conclusion') or ''}  {w.get('url') or ''}")
        out += [f"    {k}: {v}" for k, v in sorted(w["jobs"].items())]
    elif r.get("problems_not_checked"):
        out.append(f"  NOT checked: {r['problems_not_checked']}")
    out += [f"  PROBLEM {p}" for p in r["problems"]]
    return out
