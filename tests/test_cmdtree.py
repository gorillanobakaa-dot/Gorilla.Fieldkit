"""No command reaches a person, a file or a shell unless it exists (plan P9, 2026-11-12): the live command tree and
`fieldkit cmd check`."""
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from fieldkit.autopilot import inbox, toolrouter
from fieldkit.core import cmdtree
from fieldkit.lifecycle import brieflint

ROOT = Path(__file__).resolve().parents[1]
INCIDENT = "gorilla ALL=(root) NOPASSWD: /opt/fk/.venv/bin/python -m fieldkit leakgate *"


def test_the_incident_sudoers_line_fails_with_the_real_command():
    f = cmdtree.check(INCIDENT)
    assert len(f) == 1 and f[0]["kind"] == "verb" and f[0]["word"] == "leakgate"
    assert "fieldkit build-harness leakgate" in f[0]["suggest"]
    assert "did you mean: fieldkit build-harness leakgate" in cmdtree.describe(f[0])


def test_real_commands_pass_in_every_shape():
    ok = [
        "fieldkit build-harness leakgate TASK",
        "gorilla ALL=(root) NOPASSWD: /opt/fk/.venv/bin/python -m fieldkit build-harness leakgate *",
        "python3 -m fieldkit task start BRIEF.md --on peer",
        "sudo -n .venv/bin/python -m fieldkit doctor --lean",
        "bash -c 'cd /x && fieldkit emerge status firefox'",
        "& 'C:\\fk\\fieldkit.exe' sync --apply",
        "C:\\fk\\.venv\\Scripts\\python.exe -m fieldkit cmd check --file x",
        "$ fieldkit inbox add TITLE WHY \"fieldkit tools find words\"",
        "fieldkit peers link send text --to X",          # hand-routed sub-verb: not in the parser, never judged
        "fieldkit --version",
    ]
    for line in ok:
        assert cmdtree.check(line) == [], line


def test_unknown_things_fail():
    bad = {
        "fieldkit nosuchverb x": "verb",
        "fieldkit task strat X": "choice",
        "bash -c 'fieldkit nonesuch'": "verb",
        "fieldkit inbox add T W 'fieldkit nonesuch go'": "verb",
        "\"C:\\fk\\fieldkit.exe\" leakgate": "verb",
    }
    for line, kind in bad.items():
        f = cmdtree.check(line)
        assert f and f[0]["kind"] == kind, line
    assert cmdtree.check("fieldkit doctor --nosuchflag")[0]["kind"] == "flag"


def test_prose_is_not_a_command_and_a_wrong_example_may_say_so():
    assert cmdtree.check("Gorilla fieldkit is a set of tools; the fieldkit tools run offline") == []
    assert cmdtree.check("`fieldkit leakgate` does not exist", skip_negated=True) == []
    assert cmdtree.check("`fieldkit leakgate` does not exist") != []


def test_the_committed_tree_is_the_live_one():
    live = cmdtree.render(cmdtree.generate())
    assert cmdtree.TREE_FILE.read_text(encoding="utf-8") == live, \
        "fieldkit/core/cmdtree.json is stale: python -m fieldkit.core.cmdtree write"


def test_check_is_fast():
    import time
    cmdtree._CACHE.clear()
    t0 = time.perf_counter()
    cmdtree.check(INCIDENT)
    assert time.perf_counter() - t0 < 0.1


def test_the_docs_rules_skills_cards_and_generated_lines_name_real_commands():
    files = subprocess.run(["git", "ls-files", "docs", "rules", "skills", "README.md", "INSTALL.md", "AGENTS.md",
                            "fieldkit/desk/tools.yaml", "fieldkit"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    bad = []
    for f in files:
        if not f.endswith((".md", ".yaml", ".yml", ".py")) or f.startswith("fieldkit/") and not (
                f.endswith(".py") or f == "fieldkit/desk/tools.yaml"):
            continue
        for x in cmdtree.scan_file(ROOT / f):
            bad.append(f"{f}:{x['line']}: {cmdtree.describe(x)}")
    assert not bad, "\n".join(bad[:30])


def test_every_card_entry_is_a_real_command():
    cards = yaml.safe_load((ROOT / "fieldkit/desk/tools.yaml").read_text(encoding="utf-8"))["tools"]
    bad = []
    for c in cards:
        e = c.get("entry") or []
        if "-m" in e and "fieldkit" in e[e.index("-m") + 1:e.index("-m") + 2]:
            line = " ".join(["fieldkit"] + [str(x) for x in e[e.index("-m") + 2:]])
            bad += [f"{c['id']}: {cmdtree.describe(f)}" for f in cmdtree.check(line, flags=False)]
    assert not bad, "\n".join(bad[:20])


def test_inbox_add_refuses_a_command_that_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "inbox")
    r = inbox.add("T", "why", INCIDENT, kind="hardline")
    assert r["routed"] == "refused" and "build-harness leakgate" in r["why"]
    assert not (tmp_path / "inbox").exists() or not list((tmp_path / "inbox").glob("*.json"))


def test_the_tool_router_refuses_an_unknown_verb_and_allows_a_real_one():
    r = toolrouter.route("fieldkit leakgate foo")
    assert r and r["route"] == "unknown-fieldkit-command" and "build-harness leakgate" in r["use"]
    assert toolrouter.route("fieldkit build-harness leakgate foo") is None
    assert toolrouter.route("git commit -m 'the fieldkit nosuch thing'") is None      # a quoted message is no command
    assert toolrouter.route("git commit -m \"fieldkit nosuch thing\"") is None      # the quoted text starts with the word
    assert toolrouter.route("bash -c 'fieldkit nosuch thing'")                        # but bash -c is one


def test_a_brief_with_an_unknown_command_is_refused():
    found = brieflint.lint("PLAN ITEM: P9\nrun `fieldkit leakgate TASK` first", root=str(ROOT))
    assert any(sev == "refuse" and "leakgate" in m for sev, m in found)
    quoted = brieflint.lint("PLAN ITEM: P9\n`fieldkit leakgate` does not exist (the incident)", root=str(ROOT))
    assert not any("does not exist" in m and sev == "refuse" and "cmd check" in m for sev, m in quoted)


def test_the_command_line_exit_codes():
    def run(*a):
        return subprocess.run([sys.executable, "-m", "fieldkit", "cmd", *a], cwd=ROOT, capture_output=True, text=True)
    bad = run("check", INCIDENT)
    assert bad.returncode == 3 and "build-harness leakgate" in bad.stdout
    assert run("check", "fieldkit doctor --lean").returncode == 0
    j = json.loads(run("check", "--json", INCIDENT).stdout)
    assert j["ok"] is False and j["findings"][0]["word"] == "leakgate"
