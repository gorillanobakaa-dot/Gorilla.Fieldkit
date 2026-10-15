# Version: 1.0.0 · updated 26-10-16-01-00
"""platform-steps: the steps for THIS operating system, computed from the machine (owner 2026-10-16: 'a deterministic
tool that tells the AI exactly that: on Windows you have to do this ... on Linux this and this')."""
import json
import subprocess

from fieldkit.buildh import platformsteps as ps


def _owner(tmp_path, groups):
    o = tmp_path / "owner"
    (o / "config").mkdir(parents=True)
    (o / "patches").mkdir()
    (o / "decisions").mkdir()
    (o / "decisions" / "PRODUCT-DECISIONS.yaml").write_text("decisions: []\n")
    (o / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "patches", "groups": groups}))
    subprocess.run(["git", "init", "-q", str(o)], check=True)
    return o


def _task(tmp_path, owner):
    return {"id": "t", "meta": {"harness_root": str(owner), "upstream": {"version": "158.0"}}, "workdir": str(tmp_path / "none")}


def test_windows_without_the_build_script_is_blocked_and_says_where_it_lives(tmp_path):
    t = _task(tmp_path, _owner(tmp_path, {}))
    rows = {r["id"]: r for r in ps.checks(t, "windows")}
    assert rows["windows-build-script"]["state"] == "BLOCKED" and "Gorilla.firefox" in rows["windows-build-script"]["say"]
    assert "windows-build-script" not in {r["id"] for r in ps.checks(t, "linux")}


def test_missing_hook_and_uncarried_set_are_actions_and_go_away_when_done(tmp_path):
    from fieldkit.core import privacy
    o = _owner(tmp_path, {"01.A": {"status": "enabled"}})
    t = _task(tmp_path, o)
    acts = ps.lines(t, "linux", self_cmd="/opt/fk", only_actions=True)
    assert any("privacy install-hook" in a and "/opt/fk privacy" in a for a in acts)
    assert any("rebase-set t write=1" in a for a in acts)
    privacy.install_hook(o, "/opt/fk")
    pol = json.loads((o / "config" / "patch_policy.json").read_text())
    pol["groups"]["23.REBASE.158"] = {"status": "enabled"}
    (o / "config" / "patch_policy.json").write_text(json.dumps(pol))
    assert ps.lines(t, "linux", self_cmd="/opt/fk", only_actions=True) == []


def test_full_listing_names_this_system_first_and_the_other_for_contrast(tmp_path):
    out = "\n".join(ps.lines(_task(tmp_path, _owner(tmp_path, {})), "windows", self_cmd="/opt/fk"))
    assert out.startswith("THIS MACHINE IS WINDOWS")
    assert out.index("ON WINDOWS (here)") < out.index("ON LINUX (the other machine)")
    assert "/opt/fk build-harness build-run t" in out and "/opt/fk pipeline run firefox-linux" in out
