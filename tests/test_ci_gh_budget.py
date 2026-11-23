"""The GITHUB_TOKEN installation limit (about 1,000 REST calls an hour per repository): CI reads GitHub through one
snapshot per run, not once per job. These tests keep it that way (owner 2026-11-23: no more hour-long waits)."""
from pathlib import Path

import yaml

from fieldkit.method import train

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

# Jobs that may carry the token: the snapshot job, and each job below with its reason. A new job with a token is a
# new door on the quota: add it here with its reason (and use the snapshot if it reads the open PRs).
ALLOWED = {
    ("tests.yml", "merge-proof"): "reads the previous run of a push, once per push",
    ("tests.yml", "selection"): "the one ci-durations fetch of the run, handed on as an artifact",
    ("tests.yml", "durations"): "fallback fetch only when selection handed none on",
    ("tests.yml", "shard-ubuntu-latest"): "FIELDKIT_CI_READ_TOKEN: reuse-CI evidence reads per shard (next to cut: hand them over in the selection artifact)",
    ("tests.yml", "shard-windows-latest"): "same as the Linux shard",
    ("e2e-l15.yml", "snapshot"): "the one read of the open PRs for the ship-batch jobs",
}
OWN_WORKFLOWS = ("leakgate-linux.yml", "release.yml")       # own workflow, one run a day, not part of the PR run
SNAPSHOT_USERS = [f"e2e-ship-batch-{k}-{o}" for k in ("collect", "select") for o in ("linux", "windows")]
SNAPSHOT_JOB = "snapshot"


def _jobs():
    for f in sorted(WORKFLOWS.glob("*.yml")):
        for name, job in (yaml.safe_load(f.read_text(encoding="utf-8")).get("jobs") or {}).items():
            yield f.name, name, job


def has_token(job):
    text = yaml.safe_dump(job)
    return "github.token" in text or "GITHUB_TOKEN" in text


def unknown_token_jobs(jobs):
    """(file, job) pairs that hold the shared token and are neither allowed nor a snapshot user."""
    return [(f, n) for f, n, job in jobs
            if has_token(job) and (f, n) not in ALLOWED and f not in OWN_WORKFLOWS
            and not (f == "e2e-l15.yml" and (n in SNAPSHOT_USERS or n.startswith("e2e-ship-batch-")))]


def snapshot_problems(name, job):
    text = yaml.safe_dump(job)
    out = []
    if job.get("needs") != SNAPSHOT_JOB:
        out.append("needs: snapshot missing")
    if "name: gh-snapshot" not in text or "FIELDKIT_GH_SNAPSHOT" not in text:
        out.append("does not download and use the snapshot")
    if "continue-on-error: true" not in text:
        out.append("a missing snapshot must read live, never skip")
    return out


def test_only_known_jobs_carry_the_token():
    assert not unknown_token_jobs(list(_jobs())), unknown_token_jobs(list(_jobs()))


def test_the_guard_can_fail():
    bad = [("tests.yml", "coverage", {"steps": [{"run": "gh api x", "env": {"GH_TOKEN": "${{ github.token }}"}}]})]
    assert unknown_token_jobs(bad) == [("tests.yml", "coverage")]
    assert snapshot_problems("x", {"steps": [{"run": "python .github/e2e/ship_batch_collect.py"}]})


def test_ship_batch_read_jobs_use_the_snapshot():
    jobs = {n: j for f, n, j in _jobs() if f == "e2e-l15.yml"}
    for name in SNAPSHOT_USERS:
        assert snapshot_problems(name, jobs[name]) == [], name


def _fake_gh(calls, rc=0, out='[{"number": 1}]'):
    def run(argv, **kw):
        calls.append(argv)
        return type("R", (), {"returncode": rc, "stdout": out, "stderr": "HTTP 403" if rc else ""})()
    gh = train.GH.__new__(train.GH)
    gh.repo, gh.run, gh.slug = ".", run, "o/r"
    return gh


def test_snapshot_replays_reads_and_never_replays_writes(tmp_path, monkeypatch):
    monkeypatch.setenv(train.SNAPSHOT_ENV, str(tmp_path))
    calls = []
    gh = _fake_gh(calls)
    assert gh.prs() == [{"number": 1}] and gh.prs() == [{"number": 1}]
    assert len(calls) == 1                                    # the second read came from the snapshot
    gh.comment(5, "hi")
    gh.comment(5, "hi")
    assert len(calls) == 3                                    # a write is never replayed


def test_a_failed_read_is_not_kept(tmp_path, monkeypatch):
    monkeypatch.setenv(train.SNAPSHOT_ENV, str(tmp_path))
    calls = []
    gh = _fake_gh(calls, rc=1, out="")
    assert gh._gh("pr", "list")[0] == 1 and gh._gh("pr", "list")[0] == 1
    assert len(calls) == 2 and not list(tmp_path.iterdir())


def test_no_snapshot_folder_means_live_reads(monkeypatch):
    monkeypatch.delenv(train.SNAPSHOT_ENV, raising=False)
    assert train._snapshot_read(("pr", "list")) is None
