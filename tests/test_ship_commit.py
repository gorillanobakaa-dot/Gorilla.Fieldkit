# Version: 1.0.0 · updated 26-10-23-16-17
"""fieldkit ship commit: the commit loop (2026-10-23: five attempts for one change, each diagnosed by hand). Known hook
refusals are fixed and tried again; the same one twice, or an unknown one, stops; never --no-verify."""
import subprocess

from fieldkit.method import shipcommit as C

PRIVACY = "pre-commit: privacy findings in files git would publish - commit refused\ntests/x.py\n   line 4   email   (shortened)\n"
TESTS = ("pre-commit: tests failed - commit refused (python -m pytest to see why)\n"
         "tests/test_videokit.py::test_doctor_reports_and_passes_here tests/test_buildh_decision.py::test_plain\n")


def test_the_classes_read_from_what_the_hook_prints():
    assert C.classify("pre-commit: COULD NOT RUN THE TESTS - no interpreter with pytest - commit refused")[0] == "no-pytest"
    assert C.classify("pre-commit: a new module could not be put in a documentation group - commit refused")[0] == "docs-group"
    cls, shown = C.classify(PRIVACY)
    assert cls == "privacy" and any("line 4" in s for s in shown)
    assert C.classify("pre-commit: tests failed - commit refused\ntests/test_platform_gate.py::test_index\n")[0] == \
        "platform-index"
    cls, failed = C.classify(TESTS, passes_alone=lambda t: True)
    assert cls == "env-tests" and failed == ["tests/test_videokit.py::test_doctor_reports_and_passes_here",
                                             "tests/test_buildh_decision.py::test_plain"]
    assert C.classify(TESTS, passes_alone=lambda t: False)[0] == "test-failure"


class Git:
    """git commit answered by a script of hook outputs; everything else runs for real."""

    def __init__(self, outputs):
        self.outputs, self.commits, self.envs = list(outputs), 0, []

    def __call__(self, cmd, **kw):
        if cmd[:1] == ["git"] and "commit" in cmd:
            self.commits += 1
            self.envs.append(dict(kw.get("env") or {}))
            out = self.outputs.pop(0)
            return subprocess.CompletedProcess(cmd, 0 if out == "ok" else 1, out if out != "ok" else "", "")
        if cmd[:1] == ["git"]:
            return subprocess.run(cmd, **{k: v for k, v in kw.items() if k != "env"})
        return subprocess.CompletedProcess(cmd, 0, "", "")             # pytest alone, platform_gate: pass


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path / "r")], check=True)
    msg = tmp_path / "msg.txt"
    msg.write_text("a change\n")
    return tmp_path / "r", msg


def test_a_known_refusal_is_fixed_and_tried_again(tmp_path):
    repo, msg = _repo(tmp_path)
    g = Git([TESTS, "ok"])
    r = C.commit(repo, msg, run=g, env={"PATH": "/usr/bin"})
    assert r["ok"] and g.commits == 2 and r["attempts"][0]["class"] == "env-tests"
    assert g.envs[1]["TMPDIR"] == str(C.FKTEMP) and "commit" not in str(C.FKTEMP).lower()   # no jargon in the path
    assert (repo / "state" / "ship-commit" / "attempts.jsonl").is_file()


def test_the_same_refusal_twice_stops_never_a_third_try(tmp_path):
    repo, msg = _repo(tmp_path)
    g = Git([TESTS, TESTS, "ok"])
    r = C.commit(repo, msg, run=g, env={"PATH": "/usr/bin"})
    assert not r["ok"] and g.commits == 2 and "again after its fix" in r["stopped"]


def test_privacy_and_real_failures_are_reported_not_fixed(tmp_path):
    repo, msg = _repo(tmp_path)
    g = Git([PRIVACY])
    r = C.commit(repo, msg, run=g, env={"PATH": "/usr/bin"})
    assert not r["ok"] and g.commits == 1 and r["stopped"].startswith("privacy")
    assert "--no-verify" not in str(g.envs) and all("--no-verify" not in a for a in ["commit"])
    text = "\n".join(C.lines(r))
    assert "NOT COMMITTED" in text and "line 4" in text and "whole output" in text


def test_never_no_verify(tmp_path):
    repo, msg = _repo(tmp_path)
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return Git(["ok"])(cmd, **kw)
    C.commit(repo, msg, run=run, env={"PATH": "/usr/bin"})
    assert not any("--no-verify" in c or "-n" in c for c in seen if "commit" in c)
