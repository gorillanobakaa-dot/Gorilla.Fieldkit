"""Record the checkout tested by a PR and look for it before provisioning merge CI."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from ci_file_proof import command, trusted_run


def checkout():
    commit, tree, *parents = command("git", "show", "-s", "--format=%H %T %P", "HEAD").split()
    if len(parents) != 2 or any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in (commit, tree, *parents)):
        return None
    return {"schema": 1, "tree": tree, "base": parents[0], "head": parents[1]}


def prior_run(repo, expected, api, download):
    endpoint = f"repos/{repo}/actions/workflows/tests.yml/runs?event=pull_request&head_sha={expected['head']}&per_page=6"
    for run in api(endpoint)["workflow_runs"]:
        if run.get("event") != "pull_request" or not trusted_run(run, repo, expected["head"]):
            continue
        run_id, attempt = run.get("id"), run.get("run_attempt")
        if type(run_id) is not int or type(attempt) is not int or min(run_id, attempt) < 1:
            continue
        if download(run_id, attempt) == expected:
            return run_id
    return None


def evidence(repo, run_id, attempt):
    with tempfile.TemporaryDirectory(prefix="fieldkit-merge-proof-") as directory:
        try:
            command("gh", "run", "download", str(run_id), "-R", repo, "-n", f"tested-tree-attempt-{attempt}", "-D", directory)
            return json.loads((Path(directory) / "checkout.json").read_text(encoding="utf-8"))
        except (ValueError, OSError, subprocess.SubprocessError):
            return None


def main(mode):
    current = checkout()
    if mode == "record":
        if (current and current["base"] == os.environ.get("FIELDKIT_PR_BASE")
                and current["head"] == os.environ.get("FIELDKIT_PR_HEAD")):
            output = Path(".ci-proof/checkout.json")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(current), encoding="utf-8")
        return
    run_id = None
    repo = os.environ["GITHUB_REPOSITORY"]
    if current and current["base"] == os.environ.get("FIELDKIT_BEFORE") and re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        try:
            run_id = prior_run(repo, current, lambda path: json.loads(command("gh", "api", path)),
                               lambda run, attempt: evidence(repo, run, attempt))
        except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
            pass
    print(f"Matched prior PR CI run {run_id} for the exact merge tree" if run_id else "No exact PR CI proof; provision testing")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        print(f"reuse={'true' if run_id else 'false'}", file=output)


if __name__ == "__main__":
    main(sys.argv[1])
