# Version: 1.0.0 · updated 26-10-26-17-30
"""2026-10-26: every PR that added a tool card conflicted in platform_index.json and at the end of tools.yaml, and
each hand resolution cost a ~25-minute pre-commit suite (test-parity #122 twice). Two branches adding cards must
merge with no conflict and give the index the generator gives; two edits of ONE card must still conflict."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from fieldkit.core import platform_gate as gate
from fieldkit.method import mergedriver as md

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "fieldkit/desk/tools.yaml"
INDEX = ROOT / "fieldkit/desk/platform_index.json"
GROUPS = ROOT / "docs/groups.yaml"


def git(w, *a, check=True):
    r = subprocess.run(["git", "-C", str(w), *a], capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(f"git {' '.join(a)}: {r.stdout}{r.stderr}")
    return r


def card(cid, cmd, platforms="[windows, linux]"):
    return (f"- id: {cid}\n  title: A test card {cid}\n  harness: method\n  entry: [python, -m, fieldkit, {cmd}]\n"
            f"  platforms: {platforms}\n  test: null\n")


def index_of(w):
    return md.dump_index(gate.build_index(yaml.safe_load((w / "fieldkit/desk/tools.yaml").read_text("utf-8"))["tools"]))


def regen(w):
    (w / "fieldkit/desk/platform_index.json").write_bytes(index_of(w).encode("utf-8"))


@pytest.fixture
def repo(tmp_path):
    """The real tools.yaml, index and groups.yaml in a fresh clone, the driver installed the way a clone gets it."""
    w = tmp_path / "r"
    for src in (TOOLS, INDEX, GROUPS, ROOT / ".gitattributes", ROOT / md.SCRIPT):
        dst = w / src.relative_to(ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    git(tmp_path, "init", "-q", "-b", "main", str(w))
    git(w, "config", "user.email", "t@t")
    git(w, "config", "user.name", "t")
    git(w, "config", "core.autocrlf", "false")
    regen(w)                         # the shipped index IS the generator's output; start from it either way
    git(w, "add", "-A")
    git(w, "commit", "-qm", "base")
    assert md.install(w, py=sys.executable)["ok"]
    return w


def branch(w, name, edit, base="main"):
    git(w, "checkout", "-q", "-b", name, base)
    edit(w)
    git(w, "add", "-A")
    git(w, "commit", "-qm", name)
    git(w, "checkout", "-q", base)


def append(text):
    def edit(w):
        p = w / "fieldkit/desk/tools.yaml"
        p.write_bytes((p.read_text("utf-8") + "\n" + text).encode("utf-8"))
        regen(w)
    return edit


def test_two_branches_adding_cards_merge_with_no_conflict_and_the_index_the_generator_gives(repo):
    branch(repo, "a", append(card("zz-alpha", "alpha run")))
    branch(repo, "b", append("# -- a section of its own ------\n" + card("zz-beta", "beta run", "[linux]")))
    git(repo, "merge", "-q", "--no-edit", "a")
    r = git(repo, "merge", "--no-edit", "b", check=False)
    assert r.returncode == 0, r.stdout + r.stderr                      # without the driver: CONFLICT in both files
    tools = (repo / "fieldkit/desk/tools.yaml").read_text("utf-8")
    assert "<<<<<<<" not in tools and tools.index("id: zz-alpha") < tools.index("id: zz-beta")
    ids = [t["id"] for t in yaml.safe_load(tools)["tools"]]
    assert ids.count("zz-alpha") == ids.count("zz-beta") == 1 and len(ids) == len(set(ids))
    shipped = (repo / "fieldkit/desk/platform_index.json").read_text("utf-8")
    assert shipped == index_of(repo)                                   # the drift guard of tests/test_platform_gate.py
    assert json.loads(shipped)["beta run"]["platforms"] == ["linux"]
    assert git(repo, "status", "--porcelain").stdout == ""


def test_the_same_card_changed_two_ways_still_conflicts(repo):
    def retitle(t):
        def edit(w):
            p = w / "fieldkit/desk/tools.yaml"
            p.write_bytes(p.read_text("utf-8").replace("id: office\n  title: Read,", f"id: office\n  title: {t} Read,",
                                                       1).encode("utf-8"))
        return edit
    branch(repo, "a", retitle("One"))
    branch(repo, "b", retitle("Two"))
    git(repo, "merge", "-q", "--no-edit", "a")
    r = git(repo, "merge", "--no-edit", "b", check=False)
    assert r.returncode != 0 and "CONFLICT" in r.stdout
    assert "<<<<<<< ours" in (repo / "fieldkit/desk/tools.yaml").read_text("utf-8")


def test_two_new_doc_groups_at_the_top_merge(repo):
    def group(name):
        def edit(w):
            p = w / "docs/groups.yaml"
            p.write_bytes(p.read_text("utf-8").replace(
                "groups:\n", f"groups:\n  - name: {name}\n    title: t\n    sources: [{name}/*.py]\n", 1).encode("utf-8"))
        return edit
    branch(repo, "a", group("zalpha"))
    branch(repo, "b", group("zbeta"))
    git(repo, "merge", "-q", "--no-edit", "a")
    assert git(repo, "merge", "--no-edit", "b", check=False).returncode == 0
    names = [g["name"] for g in yaml.safe_load((repo / "docs/groups.yaml").read_text("utf-8"))["groups"]]
    assert {"zalpha", "zbeta"} <= set(names) and len(names) == len(set(names))


def test_a_branch_from_before_the_driver_merges_main_through_the_clones_copy(repo):
    # a branch cut before this change: no script, no .gitattributes lines; the clone's info/attributes and its copy of
    # the script carry the first merge of main
    def old(w):
        (w / md.SCRIPT).unlink()
        (w / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8", newline="\n")
        append(card("zz-old", "old run"))(w)
    branch(repo, "old", old)
    branch(repo, "new", append(card("zz-new", "new run")))
    git(repo, "checkout", "-q", "old")
    r = git(repo, "merge", "--no-edit", "new", check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (repo / "fieldkit/desk/platform_index.json").read_text("utf-8") == index_of(repo)


def test_install_is_idempotent_and_check_names_what_is_missing(repo, tmp_path):
    assert md.install(repo, py=sys.executable)["changed"] is False
    assert md.check(repo)["ok"]
    bare = tmp_path / "bare"
    git(tmp_path, "init", "-q", str(bare))
    shutil.copyfile(ROOT / ".gitattributes", bare / ".gitattributes")   # attributes named, driver not configured
    r = md.check(bare)
    assert not r["ok"] and "fieldkit merge-driver install" in r["why"]


def test_index_rows_both_sides_changed_merge_as_sets_and_removal_against_a_change_conflicts():
    base = {"x": {"platforms": ["linux", "windows"], "why": "", "ids": ["x"]}}
    ours = {"x": {"platforms": ["linux"], "why": "", "ids": ["x"]}}             # ours dropped windows
    theirs = {"x": {"platforms": ["darwin", "linux", "windows"], "why": "", "ids": ["x", "x2"]}}
    assert md.merge_index(base, ours, theirs) == {"x": {"platforms": ["darwin", "linux"], "why": "", "ids": ["x", "x2"]}}
    with pytest.raises(md.Conflict):
        md.merge_index(base, {}, theirs)
    assert md.merge_index(base, {}, base) == {}


def test_gitattributes_sends_every_driver_file():
    assert md.attributes_ok(ROOT) == []
