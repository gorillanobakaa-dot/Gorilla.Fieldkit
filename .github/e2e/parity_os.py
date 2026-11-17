"""e2e: the real `fieldkit parity os` entry point on this OS: the record of what each OS has, read from the cards,
modules, stages and skills; exit 0 with no gap, 3 while gaps are listed (read only, nothing written)."""
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

rc, out = fk("parity", "os", "--json")
assert rc in (0, 3), rc
d = last_json(out)
s = d["summary"]
assert s["records"] > 0 and "gaps" in s and "problems" in s, s
assert (rc == 3) == bool(s["gaps"] or s["problems"]), (rc, s)
print("E2E OK: parity os on", sys.platform)
