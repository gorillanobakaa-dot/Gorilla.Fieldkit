"""e2e: the real `fieldkit keeper tick --dry-run` entry point on this OS: it reads and says what is open, changes
nothing (no action taken, the dry flag set)."""
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

rc, out = fk("keeper", "tick", "--dry-run", "--json")
assert rc == 0, rc
d = last_json(out)
assert d["dry"] is True and d["actions"] == [], d
assert isinstance(d["findings"], list), d
print("E2E OK: keeper tick on", sys.platform)
