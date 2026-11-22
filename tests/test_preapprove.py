"""F0.7: the one-per-job grant becomes each client's allow-list, and never opens a hard line."""
import json

import pytest

from fieldkit.autopilot import preapprove

G = {"job": "J1", "bash": ["git status", "python -m fieldkit"], "tools": ["Edit"], "peer": ["fieldkit size find"]}


def test_claude_allow_list_has_the_grant_and_no_deny():
    p = preapprove.claude_settings(G)["permissions"]
    assert "Bash(git status:*)" in p["allow"] and "Edit" in p["allow"] and "deny" not in p


def test_a_peer_step_goes_over_the_cable():
    assert preapprove.peer_steps(G) == ["fieldkit peers link run -- fieldkit size find"]
    assert "Bash(fieldkit peers link run -- fieldkit size find:*)" in preapprove.claude_settings(G)["permissions"]["allow"]


def test_opencode_denies_outside_the_grant_never_asks():
    b = preapprove.opencode_config(G)["permission"]["bash"]
    assert b["git status*"] == "allow" and b["*"] == "deny" and "ask" not in b.values()


@pytest.mark.parametrize("cmd", ["git push origin main", "gh release create v1", "git tag -f v1", "gh pr merge 1"])
def test_a_hard_line_cannot_be_granted(cmd):
    with pytest.raises(ValueError):
        preapprove.claude_settings({"job": "J", "bash": [cmd]})
    with pytest.raises(ValueError):
        preapprove.opencode_config({"job": "J", "peer": [cmd]})


def test_a_grant_names_its_job():
    with pytest.raises(ValueError):
        preapprove.claude_settings({"bash": ["ls"]})


def test_write_merges_and_keeps_existing_deny(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"deny": ["Bash(git push:*)"]}}))
    preapprove.write(G, tmp_path)
    d = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
    assert d["permissions"]["deny"] == ["Bash(git push:*)"] and "Edit" in d["permissions"]["allow"]
    assert json.loads((tmp_path / "opencode.json").read_text())["permission"]["bash"]["*"] == "deny"
