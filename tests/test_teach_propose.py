"""fieldkit teach propose: a teaching log becomes a pipeline draft, signature stubs and card candidates."""
import yaml

from fieldkit.teach import propose as P

SH_LOG = """# Harness teaching log: x

### 2026-10-14 15:47:42  $ cd ~/Documents/Scripts/Fieldkit && fieldkit where 2>&1 | tail -1
```text
/home/user/Documents/Scripts/Fieldkit
```
exit=0

### 2026-10-14 15:47:43  $ git fetch origin 2>&1 | tail -2
```text
From github: master -> origin/master
```
exit=0

### 2026-10-14 15:47:44  $ git status --short | wc -l
```text
118
```
exit=0

### 2026-10-14 15:48:00  $ bash patches/apply.sh --check /home/user/x
```text
FATAL: source tree not found: /home/user/x
```
exit=1
"""

FK_LOG = """# Teaching log

### 1. 2026-10-14 14:00:00  Bash: Show status
```bash
git status --short
```
```text
 M a.py
```
ok

### 2. 2026-10-14 14:00:02  Bash
```bash
false
```
```text
exit 1
```
FAILED
"""


def test_both_log_shapes_parse_to_the_same_records():
    a = P.parse(SH_LOG)
    assert [r["ok"] for r in a] == [True, True, True, False] and a[3]["rc"] == 1
    assert a[0]["command"].startswith("cd ~/Documents") and "fieldkit where" in a[0]["command"]
    b = P.parse(FK_LOG)
    assert [r["command"] for r in b] == ["git status --short", "false"] and b[1]["ok"] is False


def test_program_skips_cd_and_plumbing():
    assert P.program("cd ~/x && fieldkit where 2>&1 | tail -1") == "fieldkit"
    assert P.program("FOO=1 git fetch origin") == "git"
    assert P.program("echo hi | wc -l") == "echo", "all plumbing: the first word stands"


def test_proposal_groups_stages_by_program_and_stubs_failures(tmp_path):
    d = P.propose(P.parse(SH_LOG), name="t", home="/home/user")
    ids = [s["id"] for s in d["pipeline"]["stages"]]
    assert ids == ["fieldkit-1", "git-2", "git-3"], ids
    git = d["pipeline"]["stages"][1]
    assert "{home}" not in git["run"]["cmd"][-1] or "/home/user" not in git["run"]["cmd"][-1]
    assert git.get("verify_from_log") and git["verify"][0]["output_contains"]
    assert d["counts"]["signatures"] == 1
    sig = d["signatures"]["signatures"][0]
    assert sig["pattern"].startswith("FATAL") and sig["cause"].startswith("TO FILL") and sig["fix"] == "TO FILL"
    assert "/home/user" not in yaml.safe_dump(d["pipeline"]), "the draft is not tied to one machine"


def test_write_makes_four_files_and_refuses_a_missing_log(tmp_path):
    log = tmp_path / "log.md"; log.write_text(SH_LOG, encoding="utf-8")
    r = P.write(str(log), str(tmp_path / "out"), home="/home/user")
    assert r["ok"] and r["stages"] == 3 and r["signatures"] == 1
    for f in ("pipeline-draft.yaml", "signatures-draft.yaml", "cards-draft.yaml", "PROPOSAL.md"):
        assert (tmp_path / "out" / f).is_file(), f
    y = yaml.safe_load((tmp_path / "out" / "pipeline-draft.yaml").read_text(encoding="utf-8"))
    assert y["name"] == "log" and all({"id", "platforms", "run"} <= set(s) for s in y["stages"])
    assert not P.write(str(tmp_path / "nope.md"), str(tmp_path / "out2"))["ok"]
