"""P37 one song sheet (fieldkit/rules_song.py): inventory, duplicate / conflict / budget lint, sync, hook bytes.
Fixture homes only; the repository's own AGENTS.md is checked for the removed duplicate."""
import copy
import json
from pathlib import Path

import pytest

from fieldkit import rules_song as S

ROOT = Path(S.__file__).resolve().parents[1]
SAME = "Always run the covering tests before every single commit on your own branch."


@pytest.fixture
def pol():
    p = copy.deepcopy(S.policy(ROOT))
    p["projects"] = ["proj"]
    return p


def mk(tmp_path, glob="", proj="", gemini=None, agents=None):
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "CLAUDE.md").write_text(glob, encoding="utf-8")
    (tmp_path / "proj").mkdir(exist_ok=True)
    if proj is not None:
        (tmp_path / "proj" / "CLAUDE.md").write_text(proj, encoding="utf-8")
    if gemini is not None:
        (tmp_path / ".gemini").mkdir(exist_ok=True)
        (tmp_path / ".gemini" / "GEMINI.md").write_text(gemini, encoding="utf-8")
    if agents is not None:
        (tmp_path / "proj" / "AGENTS.md").write_text(agents, encoding="utf-8")
    return tmp_path


def kinds(found):
    return sorted(f["kind"] for f in found)


def test_inventory_lists_files_tokens_loader_and_tracking(tmp_path, pol):
    home = mk(tmp_path, glob="x" * 400, proj="y" * 80, agents="z" * 40)
    sk = home / "proj" / "skills" / "a"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: a\ndescription: d\n---\n" + "body " * 500, encoding="utf-8")
    rows = S.inventory(home, pol=pol, git=lambda p: False)
    by = {Path(r["path"]).name + r["kind"]: r for r in rows}
    assert by["CLAUDE.mdglobal"]["tokens"] == 100 and by["CLAUDE.mdglobal"]["agents"] == ["claude-code"]
    assert by["CLAUDE.mdproject"]["tracked"] is False and "session start" in by["CLAUDE.mdproject"]["loads"]
    assert by["AGENTS.mdproject"]["agents"] == ["claude-code", "codex"]
    assert by["SKILL.mdskill"]["loaded_bytes"] < 60 < by["SKILL.mdskill"]["bytes"]          # frontmatter only


def test_duplicate_between_global_and_project_is_found_with_the_cut(tmp_path, pol):
    home = mk(tmp_path, glob=SAME + "\n", proj="# P\n" + SAME + "\nIts own rule about the kernel config only.\n")
    found = S.lint(home=home, pol=pol)
    d = [f for f in found if f["kind"] == "duplicate"]
    assert len(d) == 1 and d[0]["file"].endswith("proj/CLAUDE.md") and "delete it from" in d[0]["proposal"]


def test_two_agents_global_files_sharing_the_core_are_not_a_duplicate(tmp_path, pol):
    home = mk(tmp_path, glob=SAME + "\n", proj="", gemini=SAME + "\n")
    assert "duplicate" not in kinds(S.lint(home=home, pol=pol))


def test_a_sentence_twice_in_one_file_is_a_duplicate(tmp_path, pol):
    home = mk(tmp_path, glob="", proj=SAME + "\nother text that is long enough to count as one sentence.\n" + SAME)
    assert "duplicate" in kinds(S.lint(home=home, pol=pol))


def test_known_conflict_pair_and_always_never_polarity(tmp_path, pol):
    home = mk(tmp_path, glob="Ask the owner first before any step.\n", proj="Never ask anything of anyone.\n")
    assert "conflict" in kinds(S.lint(home=home, pol=pol))
    home2 = mk(tmp_path / "b", glob="Always push the branch to origin.\n", proj="Never push the branch to origin.\n")
    f = S.lint(home=home2, pol=pol)
    assert any(x["kind"] == "conflict" and "push the branch to origin" in x["evidence"] for x in f)


def test_budget_over_the_limit_is_a_finding_and_under_is_not(tmp_path, pol):
    lim = pol["budget"]["project"]["limit"]
    home = mk(tmp_path, glob="", proj="word " * (lim * 4 // 5 + 20))
    assert "budget" in kinds(S.lint(home=home, pol=pol))
    home2 = mk(tmp_path / "ok", glob="", proj="short\n")
    assert kinds(S.lint(home=home2, pol=pol)) == []


def test_the_repository_agents_md_repeats_no_global_text_and_is_in_budget(pol):
    rows = [r for r in S.inventory(ROOT, pol=dict(pol, projects=["."]), git=lambda p: True) if r["kind"] == "project"]
    assert rows and all(r["tokens"] <= pol["budget"]["project"]["limit"] for r in rows), rows
    assert "fieldkit-lifecycle-rule" not in (ROOT / "AGENTS.md").read_text(encoding="utf-8")


def test_hash_ignores_crlf_and_trailing_newline(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"one\r\ntwo\r\n")
    b.write_bytes(b"one\ntwo")
    assert S.sha(a) == S.sha(b)
    b.write_bytes(b"one\ntwo!")
    assert S.sha(a) != S.sha(b)


def test_compare_names_a_missing_and_a_differing_file():
    f = S.compare({"p/A.md": "1" * 64, "p/B.md": "2" * 64, "p/C.md": "3" * 64}, {"p/A.md": "1" * 64, "p/B.md": "9" * 64,
                                                                                 "p/D.md": "4" * 64})
    assert sorted((x["file"], x["evidence"].split(":")[0]) for x in f) == [
        ("p/B.md", "differs"), ("p/C.md", "here only"), ("p/D.md", "the other laptop only")]
    assert S.compare({"a": "1"}, {"a": "1"}) == []


def test_private_store_keeps_untracked_files_and_restores_them(tmp_path, pol):
    home = mk(tmp_path, glob="g", proj="private rules\r\n")
    rows = S.inventory(home, pol=pol, git=lambda p: False)
    store = tmp_path / "store"
    assert S.store_apply(rows, store=store) == ["stored proj/CLAUDE.md"]
    assert S.store_apply(rows, store=store) == []                                     # same again: nothing to do
    (home / "proj" / "CLAUDE.md").unlink()
    assert S.store_apply(rows, store=store, restore=True, home=home) == [f"restored {home / 'proj' / 'CLAUDE.md'}"]
    assert (home / "proj" / "CLAUDE.md").read_text(encoding="utf-8") == "private rules\n"
    tracked = S.inventory(home, pol=pol, git=lambda p: True)                          # a tracked file is not stored
    assert S.store_apply(tracked, store=tmp_path / "s2") == []


def test_hook_bytes_recorded_summarised_and_capped(tmp_path, pol):
    log = tmp_path / "h.jsonl"
    for n in (100, 100, 100, 900):
        S.record("UserPromptSubmit", n, "s", path=log)
    st = S.hook_stats(log, pol)["UserPromptSubmit"]
    assert st["calls"] == 4 and st["total"] == 1200 and st["max"] == 900 and st["over"] == 1
    cap = pol["hooks"]["cap_bytes"]["UserPromptSubmit"]
    text = "NET: keep this one whatever its size\nEAR NOT RUNNING: keep\n" + "\n".join(f"hog: note {chr(97 + i % 26)}{chr(97 + i // 26)} " + "x" * 80 for i in range(30))
    out = S.apply_cap(text, "UserPromptSubmit", pol)
    assert "NET: keep" in out and "EAR NOT RUNNING: keep" in out and "B of hook notes cut" in out
    assert len(out.encode()) < cap + 300
    assert S.apply_cap("small note", "UserPromptSubmit", pol) == "small note"


def test_cli_lint_exit_codes_and_inventory(tmp_path, pol, capsys, monkeypatch):
    home = mk(tmp_path, glob=SAME + "\n", proj=SAME + "\n")
    monkeypatch.setattr(S, "policy", lambda root=None: pol)
    assert S.main(["lint", "--files", "--home", str(home)]) == 3
    assert "duplicate" in capsys.readouterr().out
    clean = mk(tmp_path / "c", glob="a\n", proj="b\n")
    assert S.main(["lint", "--files", "--home", str(clean)]) == 0
    capsys.readouterr()
    assert S.main(["inventory", "--home", str(clean), "--json"]) == 0
    assert json.loads(capsys.readouterr().out.split("NEXT:")[0])[0]["kind"] == "global"
