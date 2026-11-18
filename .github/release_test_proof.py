"""Find prior full Fieldkit CI for this exact checkout, independently for each OS."""

import json
import os
import subprocess


OS_NAMES = {"windows": "windows-latest", "linux": "ubuntu-latest"}


def full_suite_runs(runs, repo, commit):
    for run in runs:
        if (run.get("head_sha") == commit and run.get("event") == "workflow_dispatch"
                and run.get("status") == "completed" and run.get("conclusion") == "success"
                and run.get("path", "").split("@", 1)[0] == ".github/workflows/tests.yml"
                and (run.get("head_repository") or {}).get("full_name", "").casefold() == repo.casefold()):
            yield run


def tested_systems(jobs):
    passed = {job.get("name") for job in jobs if job.get("status") == "completed"
              and job.get("conclusion") == "success"}
    return {system for system, runner in OS_NAMES.items()
            if {f"test ({runner})", *(f"shard ({runner}, {i})" for i in range(1, 5))} <= passed}


def gh(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True, timeout=30))


def main():
    repo, commit = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    endpoint = f"repos/{repo}/actions"
    evidence = {}
    try:
        response = gh(f"{endpoint}/workflows/tests.yml/runs?event=workflow_dispatch&head_sha={commit}&per_page=10")
        for run in full_suite_runs(response["workflow_runs"], repo, commit):
            jobs = gh(f"{endpoint}/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")["jobs"]
            for system in tested_systems(jobs):
                evidence.setdefault(system, run["id"])
            if evidence.keys() == OS_NAMES.keys():
                break
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        print("Prior CI proof could not be read. Running checks for any OS without verified evidence.")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for system in OS_NAMES:
            run_id = evidence.get(system)
            print(f"{system}={str(run_id is not None).lower()}", file=output)
            if run_id is not None:
                print(f"{system}: reusing full-suite proof from Actions run {run_id} at {commit}")


if __name__ == "__main__":
    main()
