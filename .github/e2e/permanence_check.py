"""e2e: the real `fieldkit permanence check` entry point on this OS: a repository with an uncommitted change is
reported UNCOMMITTED (exit 3); the check reads, it never commits or pushes."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def fk(*args, cwd=None, env=None):
    r = subprocess.run([sys.executable, "-m", "fieldkit", *args], capture_output=True, text=True, timeout=300,
                       encoding="utf-8", errors="replace", cwd=cwd, env=dict(os.environ, **(env or {})))
    print(r.stdout[-3000:], r.stderr[-3000:])
    assert "Traceback" not in r.stderr, r.stderr
    return r.returncode, r.stdout


def last_json(out):
    """The JSON document the command printed (it may be preceded by progress lines)."""
    i = out.find("{") if out.lstrip().startswith("{") else min(x for x in (out.find("{"), out.find("[")) if x >= 0)
    return json.loads(out[i:])


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=e2e", "-c", "user.email=e2e", *a],
                   check=True, capture_output=True)

with tempfile.TemporaryDirectory() as d:
    repo = Path(d) / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "f.txt").write_text("a", encoding="utf-8")
    git(repo, "add", "f.txt")
    git(repo, "commit", "-qm", "first")
    (repo / "f.txt").write_text("b", encoding="utf-8")
    rc, out = fk("permanence", "check", "--repo", str(repo), "--json")
    assert rc == 3, rc
    res = last_json(out)
    mine = [f for f in res["findings"] if Path(f["root"]).exists() and os.path.samefile(f["root"], repo)]
    assert any(f["kind"] == "UNCOMMITTED" for f in mine), res
    assert (repo / "f.txt").read_text(encoding="utf-8") == "b"
print("E2E OK: permanence check on", sys.platform)
