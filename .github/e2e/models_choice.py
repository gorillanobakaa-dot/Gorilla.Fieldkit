"""e2e: the real `python -m fieldkit models menu|remember` entry points on this OS (plan P39): the live menu, the data
check, and the owner's recorded choice for headless jobs written where FIELDKIT_LIFECYCLE_DIR says, a stale name refused."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


def fk(*args, env=None):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "models", *args], capture_output=True, text=True,
                       timeout=180, env=env)
    print("$ models", " ".join(args), "->", r.returncode)
    print(r.stdout[-2000:], r.stderr[-2000:])
    return r


r = fk("menu", "--check")
assert r.returncode == 0 and "CHECK OK" in r.stdout, r.returncode
r = fk("menu", "--json")
assert r.returncode == 0, r.returncode
m = json.loads(r.stdout)
assert m["where"] == "this computer" and isinstance(m["agents"], list) and isinstance(m["options"], list), m.keys()

data = yaml.safe_load(Path("fieldkit/lifecycle/choice.yaml").read_text(encoding="utf-8"))
name, agent = next(iter(data["agents"].items()))
provider = agent["providers"][0]
model = next(iter((data["providers"][provider].get("models") or ["some-model"])))
model = model if isinstance(model, str) else model.get("name") or model.get("id")
reasoning = agent["reasoning"][0]
with tempfile.TemporaryDirectory() as d:
    env = dict(os.environ, FIELDKIT_LIFECYCLE_DIR=d)
    r = fk("remember", "e2e-build", name, provider, model, reasoning, env=env)
    assert r.returncode == 0 and "REMEMBERED e2e-build" in r.stdout, r.returncode
    rec = json.loads((Path(d) / "choices.json").read_text(encoding="utf-8"))["e2e-build"]
    assert (rec["agent"], rec["provider"], rec["model"], rec["reasoning"]) == (name, provider, model, reasoning), rec
    if data.get("stale"):
        r = fk("remember", "e2e-stale", name, provider, data["stale"][0], reasoning, env=env)
        assert r.returncode == 2 and "REFUSED" in r.stdout, r.returncode
        assert "e2e-stale" not in json.loads((Path(d) / "choices.json").read_text(encoding="utf-8"))
print("E2E OK: models menu|remember on", sys.platform)
