"""release-publish (owner 2026-10-19: "I should NOT have to do this"): prove first, then one owner action that shows
everything it does, stops at the first failure, and marks the old release superseded only after the new one is live
and byte-identical."""
import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from fieldkit.buildh import releasepublish as rp, task


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state" / "build-harness")
    monkeypatch.setattr(rp, "PLAN_DIR", tmp_path / "state" / "publish")
    a = tmp_path / "app.exe"
    a.write_bytes(b"app")
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(f"{hashlib.sha256(b'app').hexdigest()}  app.exe\n", encoding="utf-8")
    notes = tmp_path / "notes.md"
    notes.write_text("notes", encoding="utf-8")
    return SimpleNamespace(assets=[a, sums], notes=notes, tmp=tmp_path)


def fake(calls, fail=None, digests=None, draft=False):
    def run(cmd, **kw):
        calls.append(cmd)
        joined = " ".join(map(str, cmd))
        rc = 1 if fail and fail in joined else 0
        if "rev-parse --abbrev-ref" in joined:
            return SimpleNamespace(returncode=0, stdout="master\n", stderr="")
        if "rev-parse HEAD" in joined:
            return SimpleNamespace(returncode=0, stdout="b604512aaaa\n", stderr="")
        if "rev-list --count" in joined:
            return SimpleNamespace(returncode=0, stdout="7\n", stderr="")
        if cmd[:3] == ["gh", "release", "view"] and "--json" in cmd and "tagName" in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="release not found")
        if cmd[:3] == ["gh", "release", "view"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"isDraft": draft, "assets": [
                {"name": n, "digest": "sha256:" + d} for n, d in (digests or {}).items()]}), stderr="")
        return SimpleNamespace(returncode=rc, stdout="ok\n", stderr="")
    return run


class Popen:
    def __init__(self, rc):
        self.rc = rc
        self.cmds = []

    def __call__(self, cmd, **kw):
        self.cmds.append((cmd, kw.get("env", {}).get("GORILLA_OWNER_APPROVED")))
        self.stdout = io.StringIO("To github.com\n   d5cce66..b604512  master -> master\n")
        return self

    def wait(self):
        return self.rc


def _prep(env, calls, **kw):
    return rp.prepare("t", env.tmp, "v1-build2", "Title", env.notes, env.assets, gh_repo="o/r",
                      supersede="v1", supersede_title="SUPERSEDED - v1", supersede_notes=env.notes,
                      runner=fake(calls), say=lambda m: None, **kw)


def test_prepare_checks_and_writes_the_plan(env):
    r = _prep(env, [])
    assert r["ok"] and rp.plan_path("v1-build2").is_file()
    assert r["plan"]["assets"]["app.exe"]["sha256"] == hashlib.sha256(b"app").hexdigest()


def test_a_good_send_pushes_releases_verifies_then_supersedes(env):
    _prep(env, [], pushes=["windows-port", "windows-port:main"])
    calls, po, out = [], Popen(0), []
    digests = {n: a["sha256"] for n, a in json.loads(rp.plan_path("v1-build2").read_text())["assets"].items()}
    rc = rp.send("v1-build2", runner=fake(calls, digests=digests), popen=po, say=out.append, confirm=True, owner=lambda: True, speak=lambda t: None)
    assert rc == 0 and "RELEASE LIVE AND VERIFIED" in out
    assert [c[0][-1] for c in po.cmds] == ["windows-port", "windows-port:main"]          # both pushes, in order
    assert all(flag == "1" for _c, flag in po.cmds)                                       # the flag only inside the run
    edit = [c for c in calls if c[:3] == ["gh", "release", "edit"]]
    assert edit and edit[0][3] == "v1"                                                    # superseded last
    assert any("To github.com" in l for l in out)                                          # the push is visible


def test_a_failed_push_stops_before_anything_else(env):
    _prep(env, [])
    calls, out = [], []
    rc = rp.send("v1-build2", runner=fake(calls), popen=Popen(1), say=out.append, confirm=True, owner=lambda: True, speak=lambda t: None)
    assert rc == 3 and not any(c[:2] == ["gh", "release"] for c in calls)
    assert any("STOPPED at: push" in l for l in out)


def test_a_digest_mismatch_never_supersedes_the_old_release(env):
    _prep(env, [])
    calls, out = [], []
    rc = rp.send("v1-build2", runner=fake(calls, digests={"app.exe": "0" * 64}), popen=Popen(0), say=out.append,
                 confirm=True, owner=lambda: True, speak=lambda t: None)
    assert rc == 3 and not any(c[:3] == ["gh", "release", "edit"] for c in calls)


def test_send_refuses_an_agent_a_missing_plan_and_changed_assets(env):
    out = []
    assert rp.send("v1-build2", say=out.append, confirm=True, owner=lambda: False) == 3     # not the owner's terminal
    assert rp.send("v9", say=out.append, confirm=True, owner=lambda: True, speak=lambda t: None) == 3              # nothing prepared
    _prep(env, [])
    env.assets[0].write_bytes(b"changed")
    assert rp.send("v1-build2", say=out.append, confirm=True, owner=lambda: True, speak=lambda t: None) == 3
    assert any("changed since prepare" in l for l in out)


def test_prepare_refuses_wrong_sums(env):
    env.assets[1].write_text("0" * 64 + "  app.exe\n", encoding="utf-8")
    assert not _prep(env, [])["ok"] and not rp.plan_path("v1-build2").is_file()
