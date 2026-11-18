"""Write and reuse passing test-file receipts from the trusted Fieldkit tests workflow."""

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as xml
from pathlib import Path

INPUTS = Path(".github/test-proof-inputs.json")
COMMON_INPUTS = (".gitattributes", ".github/test-proof-inputs.json", ".github/ci_file_proof.py",
                 ".github/ci_tests.py", ".github/workflows/tests.yml", ".github/workflows/release.yml",
                 "pyproject.toml", "tests/conftest.py")
RELEASE_FIXTURES = {
    ("tests/test_releasecandidate.py", "test_draft_recovers_an_interrupted_upload_without_changing_latest"): "release",
    ("tests/test_emerge_repair.py", "test_reuse_requires_the_same_runtime_dependencies"): "emergency-repair",
}


def command(*args, env=None):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=30, env=env).strip()


def context():
    image = {name: os.getenv(name) for name in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH")}
    if not all(image.values()) or os.getenv("GITHUB_ACTIONS") != "true":
        return None
    packages = sorted((d.metadata["Name"].lower(), d.version) for d in importlib.metadata.distributions()
                      if d.metadata["Name"].lower() != "fieldkit")
    return {"tree": command("git", "rev-parse", "HEAD^{tree}"), "image": image,
            "python": platform.python_version(), "git": command("git", "--version"),
            "packages": hashlib.sha256(json.dumps(packages).encode()).hexdigest()}


def scoped_inputs(files, declarations=None, lookup=None):
    """Opt into cross-tree reuse only for an explicitly scoped test with all declared paths present in Git."""
    if declarations is None:
        declarations = json.loads(INPUTS.read_text(encoding="utf-8"))
    lookup = lookup or (lambda path: command("git", "ls-tree", "HEAD", "--", path))
    fingerprints = {}
    seen = {}
    for name in files:
        extra = declarations.get(name)
        if not isinstance(extra, list):
            continue
        paths = [name, *COMMON_INPUTS, *extra]
        if any(not isinstance(p, str) or not re.fullmatch(r"[\w.-]+(?:/[\w.-]+)*", p)
               or set(p.split("/")) & {".", ".."} for p in paths):
            continue
        paths = sorted(set(paths))
        for path in paths:
            if path not in seen:
                seen[path] = lookup(path)
        if all(seen[path] for path in paths):
            content = json.dumps([(path, seen[path]) for path in paths])
            fingerprints[name] = hashlib.sha256(content.encode()).hexdigest()
    return fingerprints


def passed_files(report):
    passing, incomplete = set(), set()
    for case in report.iter("testcase"):
        filename = case.get("file", "").replace("\\", "/")
        if not filename:
            continue
        if any(case.find(result) is not None for result in ("error", "failure", "skipped")):
            incomplete.add(filename)
        else:
            passing.add(filename)
    return passing - incomplete


def write_receipt(output, report, selected, identity, inputs=None):
    junit = xml.parse(report)
    matched = sorted(set(selected) & passed_files(junit))
    timings = {}
    counts = {}
    for case in junit.iter("testcase"):
        filename = case.get("file", "").replace("\\", "/")
        label = RELEASE_FIXTURES.get((filename, case.get("name")))
        if label and filename in selected and not any(case.find(x) is not None for x in ("error", "failure", "skipped")):
            duration = float(case.get("time", "nan"))
            if math.isfinite(duration) and duration >= 0:
                timings[label] = duration
                counts[label] = {item.get("name").removeprefix("fieldkit_fixture_"): int(item.get("value"))
                                 for item in case.findall("properties/property")
                                 if item.get("name", "").startswith("fieldkit_fixture_")
                                 and item.get("value", "").isdigit()}
    if matched or timings:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"schema": 2, "context": identity, "passed": matched,
                                     "inputs": scoped_inputs(matched) if inputs is None else inputs,
                                     "fixture_seconds": timings, "fixture_counts": counts}), encoding="utf-8")
    print(f"ci-tests: recorded passes for {len(matched)} of {len(selected)} test files", flush=True)


def trusted_run(run, repo, head):
    return (run.get("head_sha") == head and (run.get("head_repository") or {}).get("full_name") == repo
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and run.get("event") in ("pull_request", "push", "workflow_dispatch")
            and run.get("path", "").split("@", 1)[0] == ".github/workflows/tests.yml")


def missing_files(files, identity, receipts, inputs=None):
    remaining = set(files)
    inputs = inputs or {}
    environment = {key: value for key, value in identity.items() if key != "tree"}
    for proof in receipts:
        if (not isinstance(proof, dict) or proof.get("schema") not in (1, 2)
                or not isinstance(proof.get("passed"), list) or not all(isinstance(p, str) for p in proof["passed"])):
            continue
        prior = proof.get("context")
        if prior == identity:
            remaining.difference_update(proof["passed"])
        elif (proof["schema"] == 2 and isinstance(prior, dict) and "tree" in prior
              and {key: value for key, value in prior.items() if key != "tree"} == environment
              and isinstance(proof.get("inputs"), dict)):
            remaining.difference_update(name for name in proof["passed"]
                                        if name in inputs and proof["inputs"].get(name) == inputs[name])
        if not remaining:
            break
    return [name for name in files if name in remaining]


def receipts(repo, heads, system, token=""):
    env = dict(os.environ, GH_TOKEN=token) if token else None
    for head in heads:
        runs = json.loads(command("gh", "api", f"repos/{repo}/actions/workflows/tests.yml/runs?head_sha={head}&per_page=6",
                                  env=env))
        for run in runs["workflow_runs"]:
            if not trusted_run(run, repo, head):
                continue
            run_id, attempt = run["id"], run["run_attempt"]
            if type(run_id) is not int or type(attempt) is not int:
                continue
            with tempfile.TemporaryDirectory(prefix="fieldkit-ci-evidence-") as folder:
                try:
                    command("gh", "run", "download", str(run_id), "-R", repo,
                            "-p", f"test-pass-{system}-*-attempt-{attempt}", "-D", folder, env=env)
                except subprocess.CalledProcessError:
                    continue  # Older successful runs may have no receipts.
                for path in Path(folder).rglob("proof.json"):
                    yield json.loads(path.read_text(encoding="utf-8"))


def reuse(files, token=""):
    started = time.monotonic()
    try:
        identity = context()
        repo = os.getenv("GITHUB_REPOSITORY", "")
        if not identity or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            return files
        heads = list(dict.fromkeys(command("git", "log", "-3", "--format=%H %P").split()))
        if not heads or any(not re.fullmatch(r"[a-f0-9]{40}", head) for head in heads):
            return files
        evidence = receipts(repo, heads, identity["image"]["RUNNER_OS"], token=token)
        pending = missing_files(files, identity, evidence, scoped_inputs(files))
        print(f"ci-tests: reused recorded passes for {len(files) - len(pending)} of {len(files)} test files "
              f"in {time.monotonic() - started:.2f} s", flush=True)
        return pending
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        print("ci-tests: previous file evidence unavailable; running all selected checks", flush=True)
        return files
