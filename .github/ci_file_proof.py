"""Write and reuse passing test-file receipts from the trusted Fieldkit tests workflow."""

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import tempfile
import xml.etree.ElementTree as xml
from pathlib import Path


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=30).strip()


def context():
    image = {name: os.getenv(name) for name in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH")}
    if not all(image.values()) or os.getenv("GITHUB_ACTIONS") != "true":
        return None
    packages = sorted((d.metadata["Name"].lower(), d.version) for d in importlib.metadata.distributions())
    return {"tree": command("git", "rev-parse", "HEAD^{tree}"), "image": image,
            "python": platform.python_version(), "packages": hashlib.sha256(json.dumps(packages).encode()).hexdigest()}


def passed_files(report):
    passing, incomplete = set(), set()
    for case in report.iter("testcase"):
        filename = case.get("file")
        if not filename:
            continue
        if any(case.find(result) is not None for result in ("error", "failure", "skipped")):
            incomplete.add(filename)
        else:
            passing.add(filename)
    return passing - incomplete


def write_receipt(output, report, selected, identity):
    matched = sorted(set(selected) & passed_files(xml.parse(report)))
    if matched:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"schema": 1, "context": identity, "passed": matched}), encoding="utf-8")


def trusted_run(run, repo, head):
    return (run.get("head_sha") == head and (run.get("head_repository") or {}).get("full_name") == repo
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and run.get("event") in ("pull_request", "push", "workflow_dispatch")
            and run.get("path", "").split("@", 1)[0] == ".github/workflows/tests.yml")


def missing_files(files, identity, receipts):
    remaining = set(files)
    for proof in receipts:
        if (isinstance(proof, dict) and proof.get("schema") == 1 and proof.get("context") == identity
                and isinstance(proof.get("passed"), list) and all(isinstance(p, str) for p in proof["passed"])):
            remaining.difference_update(proof["passed"])
    return [name for name in files if name in remaining]


def receipts(repo, heads, system):
    for head in heads:
        runs = json.loads(command("gh", "api", f"repos/{repo}/actions/workflows/tests.yml/runs?head_sha={head}&per_page=6"))
        for run in runs["workflow_runs"]:
            if not trusted_run(run, repo, head):
                continue
            run_id, attempt = run["id"], run["run_attempt"]
            if type(run_id) is not int or type(attempt) is not int:
                continue
            with tempfile.TemporaryDirectory(prefix="fieldkit-ci-evidence-") as folder:
                try:
                    command("gh", "run", "download", str(run_id), "-R", repo,
                            "-p", f"test-pass-{system}-*-attempt-{attempt}", "-D", folder)
                except subprocess.CalledProcessError:
                    continue  # Older successful runs may have no receipts.
                for path in Path(folder).rglob("proof.json"):
                    yield json.loads(path.read_text(encoding="utf-8"))


def reuse(files):
    try:
        identity = context()
        repo = os.getenv("GITHUB_REPOSITORY", "")
        if not identity or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            return files
        heads = command("git", "log", "-1", "--format=%H %P").split()
        if not heads or any(not re.fullmatch(r"[a-f0-9]{40}", head) for head in heads):
            return files
        pending = missing_files(files, identity, receipts(repo, heads, identity["image"]["RUNNER_OS"]))
        print(f"ci-tests: reused recorded passes for {len(files) - len(pending)} of {len(files)} test files", flush=True)
        return pending
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        print("ci-tests: previous file evidence unavailable; running all selected checks", flush=True)
        return files
