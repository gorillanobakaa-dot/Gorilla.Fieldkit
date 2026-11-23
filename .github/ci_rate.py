"""Print the GITHUB_TOKEN core REST budget (a free call: `gh api rate_limit` does not count) so every job log shows
what it used. `start` prints the budget; `end` prints it again and the calls this job used since `start`.
Never fails a job: an unreadable budget prints a note and exits 0 (it is a measurement, not a check)."""
import json
import os
import subprocess
import sys
from pathlib import Path


def core():
    out = subprocess.run(["gh", "api", "rate_limit", "--jq", ".resources.core"], capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None


def main(argv):
    mark = Path(os.environ.get("RUNNER_TEMP", ".")) / "gh-rate-start.json"
    now = core()
    name = os.environ.get("GITHUB_JOB", "?")
    if not now:
        print(f"RATE {name}: budget unreadable")
        return 0
    if argv and argv[0] == "start":
        mark.write_text(json.dumps(now))
        print(f"RATE {name} start: used={now['used']} remaining={now['remaining']} limit={now['limit']}")
        return 0
    start = json.loads(mark.read_text()) if mark.exists() else None
    delta = f" job_used={now['used'] - start['used']}" if start and start.get("reset") == now.get("reset") else ""
    print(f"RATE {name} end: used={now['used']} remaining={now['remaining']} limit={now['limit']}{delta}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
