"""Inside an autopilot run gh is hidden on purpose: every place that reports gh status says so, never 'no login'."""
import subprocess
from types import SimpleNamespace

from fieldkit import parity
from fieldkit.core import ghhidden, sync
from fieldkit.method import train

GH_SAYS = "You are not logged into any GitHub hosts."


def _fail_run(*a, **k):
    return SimpleNamespace(returncode=1, stdout="", stderr=GH_SAYS)


def _public_fail(slug, sha):
    raise OSError("offline")


def test_explain_only_inside_autopilot(monkeypatch):
    monkeypatch.setenv("FIELDKIT_AUTOPILOT", "1")
    assert ghhidden.explain("x") == ghhidden.MESSAGE
    monkeypatch.delenv("FIELDKIT_AUTOPILOT")
    assert ghhidden.explain("x") == "x"


def test_ci_green_reports_hidden(monkeypatch):
    monkeypatch.setenv("FIELDKIT_AUTOPILOT", "1")
    ok, why = sync.ci_green("o/r", "a" * 40, run=_fail_run, public=_public_fail)
    assert not ok and ghhidden.MESSAGE in why and "not logged" not in why
    monkeypatch.delenv("FIELDKIT_AUTOPILOT")
    ok, why = sync.ci_green("o/r", "a" * 40, run=_fail_run, public=_public_fail)
    assert "not logged" in why and ghhidden.MESSAGE not in why


def test_train_doctor_reports_hidden(monkeypatch, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setenv("FIELDKIT_AUTOPILOT", "1")
    row = [r for r in train.doctor(str(tmp_path), run=_fail_run) if r[1] == "gh signed in"][0]
    assert not row[0] and row[2] == ghhidden.MESSAGE


def test_parity_doctor_names_hidden_gh(monkeypatch):
    monkeypatch.setenv("FIELDKIT_AUTOPILOT", "1")
    monkeypatch.setattr(parity.shutil, "which", lambda n: "/bin/" + n)
    row = [r for r in parity.doctor() if r[0].startswith("gh")][0]
    assert row[1] and ghhidden.MESSAGE in row[2]
