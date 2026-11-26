"""e2e: unified cross-platform `fieldkit network` and human top-level shortcuts (`show`, `diagnose`, `connect`)."""
import json
import subprocess
import sys

FK = [sys.executable, "-m", "fieldkit"]


def sh(args, expect=(0,)):
    r = subprocess.run(FK + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("$ fieldkit", " ".join(args))
    print(r.stdout[:400], r.stderr[:400])
    assert r.returncode in expect, f"{args}: exit {r.returncode}, wanted {expect}"
    return r


# 1. Help & human examples on `network`, `wlan`, and shortcuts
r = sh(["network", "help"])
assert "fieldkit connect to" in r.stdout and "ALL ALIASES AT A GLANCE" in r.stdout
r = sh(["wlan", "examples"])
assert "fieldkit diagnose" in r.stdout
sh(["network", "--help"])
sh(["show", "--help"])
sh(["diagnose", "--help"])
sh(["diag", "--help"])
sh(["connect", "--help"])
sh(["join", "--help"])
sh(["reconnect", "--help"])

# 2. Pure offline / fast commands on real runner OS (no Wi-Fi hardware required on CI runners)
r = sh(["network", "speed-code", "2401", "--json"])
assert json.loads(r.stdout).get("standard")

r = sh(["network", "antenna", "--bands", "B3+B7+n78", "--json"])
assert json.loads(r.stdout)["must_cover_mhz"] == "1805-3800"

r = sh(["show", "--fast", "--json"], expect=(0, 2, 3))
payload = json.loads(r.stdout)
assert "radios" in payload and "interfaces" in payload and "watchdog" in payload

r = sh(["network", "routes", "--fast", "--json"], expect=(0, 2, 3))
assert "interfaces" in json.loads(r.stdout)

print("PASS: network_cli e2e")
