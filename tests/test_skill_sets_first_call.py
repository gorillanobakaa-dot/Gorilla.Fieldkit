"""O1: fieldkit skills sets (a skill set per project, written into skillOverrides) and fieldkit cost firstcall (the
first call's tokens by part). No real claude call, no real settings file, no cable."""
import json
import subprocess
import types

import pytest

from fieldkit.skills import sets
from fieldkit.tokens import first_call as fc

DATA = {"outside": "user-invocable-only", "roots": ["~/Documents"],
        "everywhere": ["fieldkit-tools"], "projects": {"Proj": ["fieldkit-x", "mcp-builder"], "Gone": ["fieldkit-y"]},
        "others": ["mcp-builder", "anthropic-skills:docx"], "user_settings": {"disableClaudeAiConnectors": True},
        "deny_tools": ["NotebookEdit", "Artifact"]}

SETTINGS = ('{\r\n  "includeCoAuthoredBy": false,\r\n  "hooks": {\r\n    "Stop": [\r\n      {\r\n        "hooks": [\r\n'
            '          {\r\n            "type": "command",\r\n            "command": "x"\r\n          }\r\n        ]\r\n'
            '      }\r\n    ]\r\n  },\r\n  "permissions": {\r\n    "deny": [\r\n      "Artifact"\r\n    ]\r\n  }\r\n}')


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_bytes(SETTINGS.encode())
    proj = tmp_path / "Documents" / "Proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.local.json").write_text('{"skillOverrides": {"mine": "off"}, "x": 1}',
                                                          encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    return tmp_path


def test_user_settings_get_the_outside_state_and_a_project_its_own_set(home):
    rows = sets.plan(DATA, home=home, own=["fieldkit-tools", "fieldkit-x", "fieldkit-y"])
    assert [r[0] for r in rows] == [home / ".claude" / "settings.json",
                                    home / "Documents" / "Proj" / ".claude" / "settings.local.json"]
    done = sets.apply(rows)
    assert [w.split(";")[0] for _, w in done] == ["written", "written"]
    user = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert user["skillOverrides"] == {s: "user-invocable-only" for s in
                                      ("fieldkit-x", "fieldkit-y", "mcp-builder", "anthropic-skills:docx")}
    assert "fieldkit-tools" not in user["skillOverrides"]                        # everywhere: left on
    assert user["permissions"]["deny"] == ["Artifact", "NotebookEdit"]           # added once, nothing removed
    assert user["disableClaudeAiConnectors"] is True
    raw = (home / ".claude" / "settings.json").read_bytes().decode()
    assert sets._hooks_text(raw) == sets._hooks_text(SETTINGS)                   # the hooks text, byte for byte
    assert "\r\n" in raw and not raw.endswith("\n")                              # the file's own style
    proj = json.loads((home / "Documents" / "Proj" / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert proj == {"skillOverrides": {"mine": "off", "fieldkit-x": "on", "mcp-builder": "on"}, "x": 1}
    ignored = subprocess.run(["git", "-C", str(home / "Documents" / "Proj"), "check-ignore", "-q",
                              ".claude/settings.local.json"])
    assert ignored.returncode == 0                                               # kept out of git
    assert all(w == "current" for _, w in sets.apply(sets.plan(DATA, home=home, own=["fieldkit-x"])))


def test_a_write_that_would_change_the_hooks_text_is_refused(home):
    odd = SETTINGS.replace('"command": "x"', '"command":   "x"')                # not json.dumps' spacing
    (home / ".claude" / "settings.json").write_bytes(odd.encode())
    done = sets.apply(sets.plan(DATA, home=home, own=[]))
    assert done[0][1].startswith("REFUSED")
    assert (home / ".claude" / "settings.json").read_bytes().decode() == odd     # untouched


def test_dry_run_changes_nothing_and_says_what_would(home, tmp_path, capsys):
    before = (home / ".claude" / "settings.json").read_bytes()
    (tmp_path / "data.yaml").write_text(json.dumps(DATA), encoding="utf-8")
    assert sets.main(["--data", str(tmp_path / "data.yaml"), "--home", str(home)]) == 1
    assert (home / ".claude" / "settings.json").read_bytes() == before
    assert "disableClaudeAiConnectors: None -> True" in capsys.readouterr().out


def test_the_real_data_is_consistent():
    d = sets.load()
    assert d["outside"] in ("on", "name-only", "user-invocable-only", "off")
    every = set(d["everywhere"])
    for name, skills in d["projects"].items():
        assert not every & set(skills), name                                     # a project set adds, never repeats
    assert "Fieldkit" in d["projects"] and "Gorilla.firefox" in d["projects"]


def _stream(tokens, skills=("a", "b"), usd=0.1):
    init = {"type": "system", "subtype": "init", "tools": ["Bash", "Read"], "skills": list(skills),
            "mcp_servers": [{"name": "claude.ai Claude Docs"}], "model": "claude-opus-5-5"}
    asst = {"type": "assistant", "message": {"usage": {"input_tokens": 2, "cache_creation_input_tokens": tokens - 102,
                                                       "cache_read_input_tokens": 100, "output_tokens": 4}}}
    return "\n".join([json.dumps(init), "not json", json.dumps(asst),
                      json.dumps({"type": "result", "total_cost_usd": usd})])


def test_firstcall_sums_input_cache_creation_and_cache_read_of_the_first_call():
    r = fc.parse(_stream(30697).splitlines())
    assert r["tokens"] == 30697 and r["skills"] == ["a", "b"] and r["mcp"] == ["claude.ai Claude Docs"]
    assert fc.parse(["{}"])["tokens"] is None


def test_firstcall_parts_by_switching_one_thing_off(tmp_path):
    sizes = {"full": 30000, "--strict-mcp-config": 28000, "--disable-slash-commands": 19000, "--tools": 18000}
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd)
        key = next((k for k in sizes if k in cmd), "full")
        assert "--no-session-persistence" in cmd                                 # no transcript left behind
        return types.SimpleNamespace(stdout=_stream(sizes[key]), stderr="", returncode=0)

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("x" * 400, encoding="utf-8")
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("y" * 800, encoding="utf-8")
    r = fc.measure(proj, parts=True, runner=runner, home=tmp_path)
    assert len(seen) == 4
    files = fc.rules_files(proj, tmp_path)
    assert files[:2] == [tmp_path / ".claude" / "CLAUDE.md", proj / "AGENTS.md"]  # global first, then cwd upwards
    est = sum(f.stat().st_size for f in files) // 4
    assert r["parts"] == {"mcp": 2000, "skills": 11000, "tools": 12000, "rules (est)": est,
                          "rest (system, hooks; parts overlap)": 30000 - 25000 - est}
    assert "first call 30,000 tokens" in fc.render(r)
    one = fc.measure(proj, runner=runner, home=tmp_path)
    assert list(one["calls"]) == ["full"] and "parts" not in one


def test_firstcall_reports_a_call_that_was_not_made(tmp_path):
    r = fc.measure(tmp_path, runner=lambda cmd, **kw: types.SimpleNamespace(stdout="", stderr="not signed in",
                                                                              returncode=1))
    assert r["tokens"] is None and "not signed in" in fc.render(r)
