"""e2e: the generated platform files match their generators on this OS (plan F4.3, FINDINGS 8.5): cmdtree.json had a
drift check, platform_index.json and docs/PLATFORMS.md had none, so a card's `platforms` could change while the gate
and the table still showed the old answer."""
import json
import subprocess
import sys

from fieldkit.core import platform_gate as pg

want = pg.build_index(pg.public_tools())
have = json.loads(pg.INDEX.read_text(encoding="utf-8"))
assert have == want, ("platform_index.json is stale: run `python -m fieldkit.core.platform_gate --write`",
                      sorted(set(have) ^ set(want))[:10])
r = subprocess.run([sys.executable, "-m", "fieldkit", "parity", "os", "--check"], capture_output=True, text=True,
                   timeout=300)
print(r.stdout, r.stderr)
assert r.returncode == 0, ("docs/PLATFORMS.md is stale: run `python -m fieldkit parity os --write`", r.returncode)
print("E2E OK: platform_index.json and docs/PLATFORMS.md current on", sys.platform)
