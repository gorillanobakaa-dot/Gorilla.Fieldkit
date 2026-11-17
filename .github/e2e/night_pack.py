"""e2e: the real `python -m fieldkit build-harness night-pack` entry point on this OS. Only the safe paths run on a CI
runner: an empty pack is refused, a release pack is prepared from a release-publish plan, status reads it back, and a
scheduled run without the owner's arming does nothing (or is refused as not a person's terminal). Arm needs the
owner's terminal and is never exercised here."""
import json
import subprocess
import sys
import uuid
from pathlib import Path

from fieldkit.buildh import nightpack, task

TID = "e2e-" + uuid.uuid4().hex[:8]


def np(*args):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "build-harness", "night-pack", TID, *args],
                       capture_output=True, text=True, timeout=120)
    print(f"$ night-pack {TID} {' '.join(args)} -> {r.returncode}\n{r.stdout}{r.stderr}")
    return r.returncode, r.stdout + r.stderr


pub = nightpack.DIR.parent / "publish"
pub.mkdir(parents=True, exist_ok=True)
plan = pub / f"{TID}.plan.json"
plan.write_text(json.dumps({"title": "e2e", "gh_repo": "o/r", "assets": {}}), encoding="utf-8")
try:
    rc, out = np("prepare")
    assert rc != 0 or "empty night pack" in out, "an empty pack must be refused"
    rc, out = np("prepare", f"release={TID}", "gate=0")
    assert rc == 0 and "NIGHT PACK PREPARED" in out, out
    rc, out = np("status")
    st = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert rc == 0 and st["pack"]["actions"][0]["kind"] == "release" and st["armed"] is None, st
    rc, out = np("run")
    assert (rc == 0 and "not armed" in out) or (rc == 3 and "REFUSED" in out), out
    assert not nightpack._p(TID, "armed").exists()
finally:
    plan.unlink(missing_ok=True)
    for n in ("pack", "armed"):
        nightpack._p(TID, n).unlink(missing_ok=True)
print("E2E OK: night-pack on", sys.platform, "state", task.STATE)
