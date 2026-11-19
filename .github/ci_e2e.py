"""Select existing E2E jobs before their runners are provisioned."""
import fnmatch
import json
import os
import re
import subprocess
import sys


TRIGGERS = {
    "e2e": (
        "fieldkit/leakgate/**", "fieldkit/integration/**", "fieldkit/capture/**", "fieldkit/zero/**",
        "fieldkit/lifecycle/pool*", "fieldkit/lifecycle/choice*", "fieldkit/core/prepare*", "fieldkit/once.py",
    ),
    "e2e_l15": (
        "fieldkit/buildh/installertest.py", "fieldkit/buildh/cli.py", "fieldkit/lifecycle/keeper.py",
        "fieldkit/lifecycle/onestart.py", "fieldkit/lifecycle/permanence.py", "fieldkit/parity.py",
        "fieldkit/rules.py", "fieldkit/rules_song.py", "fieldkit/rules_lint.py", "fieldkit/method/batch.py",
        "fieldkit/method/train.py", "fieldkit/desk/tools.yaml",
    ),
}
SHARED = (".github/e2e/**", ".github/workflows/e2e*.yml", ".github/workflows/tests.yml",
          ".github/ci_e2e.py", "pyproject.toml", "fieldkit/cli.py")
LEAK_GATE_JOBS = ["e2e-leakgate-linux", "e2e-bh-leakgate-ci-linux", "e2e-bh-leakgate-ci-windows",
                  "e2e-cmd-check-linux", "e2e-cmd-check-windows"]


def matches(paths, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns)


def select(paths):
    """Only narrow explicitly audited changes. Shared files select the full E2E set."""
    if paths is None or matches(paths, SHARED):
        return dict.fromkeys(TRIGGERS, ["*"])
    audited = {"fieldkit/leakgate/gate.py", "fieldkit/leakgate/status.py", "tests/test_leakgate_status.py"}
    if paths and set(paths) <= audited and matches(paths, TRIGGERS["e2e"]):
        return {"e2e": LEAK_GATE_JOBS, "e2e_l15": []}
    return {workflow: ["*"] if matches(paths, patterns) else [] for workflow, patterns in TRIGGERS.items()}


def changed(base):
    if not re.fullmatch(r"[0-9a-f]{40}", base) or base == "0" * 40:
        return None
    try:
        raw = subprocess.check_output(["git", "diff", "--no-renames", "--name-only", "-z", base, "HEAD", "--"],
                                      stderr=subprocess.PIPE)
        return raw.decode("utf-8").split("\0")[:-1]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def main(base):
    choices = dict.fromkeys(TRIGGERS, []) if base == "full" else select(changed(base))
    if choices["e2e"] and choices["e2e"] != ["*"]:
        from pathlib import Path

        import yaml

        workflow = yaml.safe_load(Path(".github/workflows/e2e.yml").read_text(encoding="utf-8"))
        if not set(choices["e2e"]) <= workflow["jobs"].keys():
            choices["e2e"] = ["*"]
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for workflow, jobs in choices.items():
            print(f"{workflow}={json.dumps(jobs, separators=(',', ':'))}", file=output)
            print(f"{workflow}: {', '.join(jobs) if jobs else 'no matching changes'}")


if __name__ == "__main__":
    main(sys.argv[1])
