"""e2e: the real `python -m fieldkit cmd` entry point on this OS (plan P9): the committed tree is fresh, a real
command passes, the owner's 2026-11-12 sudoers line (no such verb) fails with exit 3 and a did-you-mean."""
import json
import subprocess
import sys


def run(*args):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "cmd", *args], capture_output=True, text=True, timeout=300)
    print(r.stdout, r.stderr)
    return r


r = run("stale")
assert r.returncode == 0, ("cmdtree.json is stale", r.returncode)
r = run("check", "fieldkit build-harness leakgate TASK", "--json")
assert r.returncode == 0, r.returncode
assert json.loads(r.stdout)["ok"] is True
r = run("check", "gorilla ALL=(root) NOPASSWD: /usr/bin/python3 -m fieldkit leakgate *", "--json")
assert r.returncode == 3, r.returncode
out = json.loads(r.stdout)
assert out["ok"] is False and out["findings"], out
print("E2E OK: cmd check on", sys.platform)
