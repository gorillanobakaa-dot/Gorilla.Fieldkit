"""e2e: the real `fieldkit ship batch status` entry point on this OS: a repository with no batch yet says so (exit 0,
an empty journal); a folder that is no repository is refused (exit 3, nothing forced)."""
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
    rc, out = fk("ship", "batch", "status", "--repo", str(repo), "--json")
    assert rc == 0, rc
    res = last_json(out)
    assert res["ok"] and res["rows"] == 0 and res["last"] == [], res
    rc, out = fk("ship", "batch", "status", "--repo", d, "--json")
    assert rc == 3, rc
    res = last_json(out)
    assert res["ok"] is False and res["next"] == "nothing was forced", res
print("E2E OK: ship batch status on", sys.platform)
