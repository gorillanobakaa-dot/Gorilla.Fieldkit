"""2026-11-16 (Firefox 160): two exported patch groups sat on disk unregistered for five days and five days of patch
work were never committed. The replay fails on an unregistered group, export-hand registers its groups, and the
migration's S5 gate wants the replay proven and the set published."""
import json
import subprocess
from types import SimpleNamespace

from fieldkit.buildh import replay
from fieldkit.migrate import plan


def _owner(tmp_path, groups, on_disk):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "patch_policy.json").write_text(json.dumps(
        {"patchset_root": "patches", "groups": {g: {"status": s} for g, s in groups.items()}}), encoding="utf-8")
    for g in on_disk:
        d = tmp_path / "patches" / g
        d.mkdir(parents=True)
        (d / "001-x.patch").write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
        (d / "README.md").write_text(f"# {g}\n\nCuts made on the tree.\n", encoding="utf-8")
    return tmp_path


def test_an_unregistered_group_is_named_and_a_disabled_one_is_a_decision(tmp_path):
    o = _owner(tmp_path, {"01.A": "enabled", "02.OFF": "disabled"}, ["01.A", "02.OFF", "22.NEW"])
    assert replay.unregistered_groups(o) == [("22.NEW", 1)]


def test_register_groups_names_them_enabled_with_the_readme_reason(tmp_path):
    o = _owner(tmp_path, {"01.A": "enabled"}, ["01.A", "22.NEW"])
    assert replay.register_groups(o, say=lambda m: None) == ["22.NEW"]
    g = json.loads((o / "config" / "patch_policy.json").read_text(encoding="utf-8"))["groups"]
    assert list(g) == ["01.A", "22.NEW"] and g["22.NEW"]["status"] == "enabled"
    assert "Cuts made on the tree." in g["22.NEW"]["reason"]
    assert replay.unregistered_groups(o) == []


def _m(owner, events, tree="T1"):
    return SimpleNamespace(tid="t", owner=owner, tree=lambda: tree, events=lambda: events)


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


def test_s5_wants_the_replay_proven_on_this_tree(tmp_path):
    rows = {r["id"]: r for r in plan.gate_patchset_kept(_m(tmp_path, []))}
    assert not rows["S5.replay"]["ok"]
    ev = [{"event": "replay-proof", "t": "x", "ok": True, "tree": "T0"}]
    assert not {r["id"]: r for r in plan.gate_patchset_kept(_m(tmp_path, ev))}["S5.replay"]["ok"]       # older tree
    ev.append({"event": "replay-proof", "t": "y", "ok": True, "tree": "T1"})
    assert {r["id"]: r for r in plan.gate_patchset_kept(_m(tmp_path, ev))}["S5.replay"]["ok"]


def test_s5_wants_the_set_committed_and_pushed(tmp_path):
    remote, o = tmp_path / "remote.git", tmp_path / "owner"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    o.mkdir()
    _owner(o, {"01.A": "enabled"}, ["01.A"])
    _git(o, "init", "-q")
    _git(o, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A")
    _git(o, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "x")
    _git(o, "remote", "add", "origin", str(remote))
    _git(o, "push", "-q", "-u", "origin", "HEAD")
    pub = lambda: {r["id"]: r for r in plan.gate_patchset_kept(_m(o, []))}["S5.published"]
    assert pub()["ok"], pub()
    (o / "patches" / "22.NEW").mkdir()
    (o / "patches" / "22.NEW" / "001.patch").write_text("x", encoding="utf-8")
    assert not pub()["ok"] and "uncommitted" in str(pub())
    _git(o, "add", "-A")
    _git(o, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "y")
    assert not pub()["ok"] and "not pushed" in str(pub())
