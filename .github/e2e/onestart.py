"""e2e: the real `fieldkit onestart check` entry point on this OS: every owner-only prerequisite a job may need is
listed as PRESENT, MISSING or UNKNOWN (read only; `ask` would change things and is not run here)."""
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
    i = min(x for x in (out.find("{"), out.find("[")) if x >= 0)
    return json.JSONDecoder().raw_decode(out[i:])[0]   # a NEXT: line may follow the document


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=e2e", "-c", "user.email=e2e", *a],
                   check=True, capture_output=True)

rc, out = fk("onestart", "check")
assert rc == 0, rc
rows = [l.split()[0] for l in out.splitlines() if l.strip() and l.split()[0] in ("PRESENT", "MISSING", "UNKNOWN")]
assert rows, out
assert "NEXT:" in out, out
print("E2E OK: onestart on", sys.platform)
