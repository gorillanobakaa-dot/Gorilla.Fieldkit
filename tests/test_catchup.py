"""catchup (kits/catchup/catchup.py), run as a user would: subprocess, real git repositories in a temp folder.

Covers doctor, repos, collect (unpushed commits as patches, uncommitted diff, untracked files, a bundle for a
repository with no remote), check (a secret fails it), pack (verified zip, never overwrites) and public-check
(Romanian text and private terms found, binaries and allow-marked lines skipped).
"""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "kits" / "catchup" / "catchup.py"
pytestmark = [pytest.mark.skipif(not TOOL.is_file(), reason="kits/catchup missing"),
              pytest.mark.skipif(not shutil.which("git"), reason="git not installed (catchup doctor)")]

# Romanian sample: a deliberate fixture for the detector, marked so public-check skips this line
RO_LINE = "Acesta este un ghid pentru studen" + chr(0x21B) + "i " + chr(0x219) + "i profesori."  # privacy-scan: allow


def run(*args, cwd=None):
    r = subprocess.run([sys.executable, "-I", str(TOOL), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300, cwd=cwd)
    return r.returncode, r.stdout + r.stderr


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",  # privacy-scan: allow
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """remote.git (bare) <- pushed/ with one unpushed commit, an edit and an untracked file; local/ with no remote."""
    d = tmp_path_factory.mktemp("cu")
    roots = d / "roots"
    remote = d / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    pushed = roots / "pushed"
    pushed.mkdir(parents=True)
    git(pushed, "init", "-q", "-b", "main")
    (pushed / "a.txt").write_text("one\n", encoding="utf-8")
    git(pushed, "add", "a.txt")
    git(pushed, "commit", "-q", "-m", "first")
    # a file:// remote counts as "no GitHub copy" for the bundle rule, so give it an http-looking URL for
    # reporting and push through a second remote name
    git(pushed, "remote", "add", "origin", "https://example.invalid/pushed.git")
    git(pushed, "remote", "add", "bare", str(remote))
    git(pushed, "push", "-q", "bare", "main")
    git(pushed, "branch", "-q", "--set-upstream-to=bare/main")
    (pushed / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    git(pushed, "commit", "-q", "-am", "second, not pushed")
    (pushed / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")  # uncommitted
    (pushed / "new.txt").write_text("untracked\n", encoding="utf-8")
    local = roots / "local"
    local.mkdir()
    git(local, "init", "-q", "-b", "main")
    (local / "n.txt").write_text("notes\n", encoding="utf-8")
    git(local, "add", "n.txt")
    git(local, "commit", "-q", "-m", "local only")
    mem = d / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("- [a](a.md) - a fact\n", encoding="utf-8")
    cfg = d / "catchup.json"
    cfg.write_text(json.dumps({"roots": [str(roots)], "repo_exclude": [],
                               "sets": {"memory": [str(mem)], "instructions": []}}), encoding="utf-8")
    return {"d": d, "roots": roots, "cfg": cfg, "pushed": pushed, "local": local}


def test_doctor_reports_git_and_destination(tmp_path):
    code, out = run("doctor", "--dest", tmp_path)
    assert code == 0, out
    assert "git" in out and "GB free" in out
    code, out = run("doctor", "--dest", tmp_path / "missing")
    assert code == 1 and "MISSING" in out


def test_repos_reports_ahead_and_dirty(world):
    code, out = run("repos", world["roots"], "--json", "--config", world["cfg"])
    assert code == 0, out
    states = {Path(s["path"]).name: s for s in json.loads(out)}
    assert states["pushed"]["ahead"] == 1
    assert states["local"]["upstream"] is None


def test_collect_carries_everything_github_lacks(world):
    out_dir = world["d"] / "bundle"
    code, out = run("collect", "--out", out_dir, "--config", world["cfg"], "--since", "2000-01-01")
    assert code == 0, out
    work = out_dir / "unpushed-work"
    pushed = next(p for p in work.iterdir() if p.name.endswith("pushed"))
    assert (pushed / "uncommitted.diff").read_text(encoding="utf-8").count("+three") == 1
    assert len(list((pushed / "unpushed-commits").glob("*.patch"))) == 1
    assert (pushed / "untracked" / "new.txt").is_file()
    local = next(p for p in work.iterdir() if p.name.endswith("local"))
    bundle = local / "local.bundle"
    assert bundle.is_file()
    clone = world["d"] / "from-bundle"
    subprocess.run(["git", "clone", "-q", str(bundle), str(clone)], check=True)
    assert (clone / "n.txt").read_text(encoding="utf-8") == "notes\n"
    assert any(p.name == "MEMORY.md" for p in (out_dir / "memory").rglob("*"))
    for name in ("README-FIRST.txt", "HANDOFF-SKELETON.md", "MANIFEST.json", "repos.md"):
        assert (out_dir / name).is_file(), name
    code, out = run("collect", "--out", out_dir, "--config", world["cfg"])
    assert code != 0 and "not empty" in out  # never overwrites


def test_check_fails_on_a_secret_and_pack_refuses(world, tmp_path):
    src = tmp_path / "b"
    src.mkdir()
    (src / "README-FIRST.txt").write_text("x\n", encoding="utf-8")
    (src / "ok.txt").write_text("nothing here\n", encoding="utf-8")
    code, out = run("check", src)
    assert code == 0, out
    (src / "sandbox.env").write_text("T=" + "ghp" + "_FAKE" + "x" * 32 + "\n", encoding="utf-8")
    code, out = run("check", src)
    assert code == 0, out  # a planted fake in an evaluation sandbox is not a secret
    (src / "leak.txt").write_text("token " + "ghp" + "_" + "A" * 36 + "\n", encoding="utf-8")
    code, out = run("check", src)
    assert code == 3 and "github-token" in out and "A" * 36 not in out  # masked
    code, out = run("pack", src, tmp_path / "dest")
    assert code != 0 and "fail closed" in out
    assert not (tmp_path / "dest" / "b.zip").exists()


def test_pack_writes_a_verified_zip_and_never_overwrites(world, tmp_path):
    src = world["d"] / "bundle"
    dest = tmp_path / "stick"
    code, out = run("pack", src, dest)
    assert code == 0, out
    z = dest / "bundle.zip"
    with zipfile.ZipFile(z) as f:
        assert f.testzip() is None
        assert "bundle/README-FIRST.txt" in f.namelist()
    line = (dest / "bundle.zip.sha256").read_bytes()
    assert line.endswith(b"  bundle.zip\n") and b"\r" not in line  # sha256sum -c on Debian reads it
    assert not list(dest.glob("*.part"))
    code, out = run("pack", src, dest)
    assert code == 0, out
    assert (dest / "bundle_2.zip").is_file() and z.is_file()


def test_patch_carries_staged_work_and_proves_it(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "b.bin").write_bytes(bytes(range(256)))
    git(repo, "add", "a.txt", "b.bin")
    msg = tmp_path / "msg.txt"
    msg.write_text("the work\n", encoding="utf-8")
    out = tmp_path / "carry"
    code, text = run("patch", repo, "--out", out, "--message", msg)
    assert code == 0 and "every file identical" in text, text
    assert {p.name for p in out.iterdir()} == {"staged.diff", "BASE-COMMIT.txt", "COMMIT-MESSAGE.txt", "HOW-TO-APPLY.txt"}
    assert not (tmp_path / ".carry-proof").exists()                 # the proof worktree is gone
    code, text = run("patch", repo, "--out", out)
    assert code != 0 and "not empty" in text


def test_manifest_and_pack_replace(world, tmp_path):
    src = world["d"] / "bundle"
    (src / "HANDOFF.md").write_text("added by hand\n", encoding="utf-8")
    code, out = run("manifest", src)
    assert code == 0, out
    assert any(r["path"] == "HANDOFF.md" for r in json.loads((src / "MANIFEST.json").read_text(encoding="utf-8")))
    dest = tmp_path / "stick"
    (dest).mkdir()
    (dest / "keep.txt").write_text("someone else's\n", encoding="utf-8")
    assert run("pack", src, dest)[0] == 0
    code, out = run("pack", src, dest, "--replace")
    assert code == 0, out
    assert (dest / "bundle.zip").is_file() and not (dest / "bundle_2.zip").exists() and (dest / "keep.txt").is_file()


def test_public_check_finds_romanian_and_respects_allow_and_binaries(tmp_path):
    repo = tmp_path / "pub"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "clean.md").write_text("Plain English only. Merge the branch, then test.\n", encoding="utf-8")
    code, out = run("public-check", "--no-terms", repo)
    assert code == 0, out
    (repo / "allowed.md").write_text(RO_LINE + "  privacy-scan: allow\n", encoding="utf-8")
    (repo / "pic.png").write_bytes(b"\x89PNG\x00\x00" + RO_LINE.encode("utf-8"))
    code, out = run("public-check", "--no-terms", repo)
    assert code == 0, out
    (repo / "notes.md").write_text("intro\n" + RO_LINE + "\n", encoding="utf-8")
    (repo / ".gitignore").write_text("private/\n", encoding="utf-8")
    (repo / "private").mkdir()
    (repo / "private" / "x.md").write_text(RO_LINE + "\n", encoding="utf-8")  # ignored: never published
    code, out = run("public-check", "--no-terms", repo)
    assert code == 3, out
    assert "notes.md:2" in out and "private" not in out.split("finding(s)")[0].replace("private term", "")


def test_public_check_history_finds_what_the_tree_no_longer_shows(tmp_path):
    repo = tmp_path / "h"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.md").write_text("intro\n" + RO_LINE + "\n", encoding="utf-8")
    git(repo, "add", "a.md")
    git(repo, "commit", "-q", "-m", "add notes")
    (repo / "a.md").write_text("intro\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "remove notes")
    code, out = run("public-check", "--no-terms", repo)
    assert code == 0, out  # the tree is clean now
    code, out = run("public-check", "--no-terms", "--history", repo)
    assert code == 3 and "a.md" in out and out.count("romanian ") == 1, out  # the removal is not a second hit


def test_public_check_two_words_needed_without_letters(tmp_path):
    f = tmp_path / "f.md"
    f.write_text("This guide is in English: ghid is one word only.\n", encoding="utf-8")
    code, out = run("public-check", "--no-terms", f)
    assert code == 0, out
    f.write_text("Acum este gata.\n", encoding="utf-8")  # privacy-scan: allow
    code, out = run("public-check", "--no-terms", f)
    assert code == 3, out


# -- verify and the question ledger (2026-10-19) ------------------------------------------------------------------
def _bundle(tmp_path):
    """A packed bundle without git: a folder with README-FIRST.txt, two files and MANIFEST.json, then `pack`."""
    b = tmp_path / "bundle-x"
    (b / "memory").mkdir(parents=True)
    (b / "README-FIRST.txt").write_text("readme\n", encoding="utf-8")
    (b / "memory" / "MEMORY.md").write_text("- a note\n", encoding="utf-8")
    assert run("manifest", b)[0] == 0
    dest = tmp_path / "stick"
    rc, out = run("pack", b, dest)
    assert rc == 0, out
    return b, dest / "bundle-x.zip"


def test_verify_passes_a_good_copy_and_names_what_is_wrong(tmp_path):
    folder, z = _bundle(tmp_path)
    rc, out = run("verify", z)
    assert rc == 0 and "VERIFY OK" in out, out
    assert run("verify", folder)[0] == 0
    # a changed file, a missing one and one the manifest does not list, in the unpacked folder
    (folder / "memory" / "MEMORY.md").write_text("- changed\n", encoding="utf-8")
    (folder / "extra.txt").write_text("x\n", encoding="utf-8")
    (folder / "README-FIRST.txt").unlink()
    rc, out = run("verify", folder)
    assert rc == 3 and "differs: memory/MEMORY.md" in out and "missing: README-FIRST.txt" in out \
        and "not in the manifest: extra.txt" in out, out


def test_verify_refuses_a_zip_whose_sha256_file_disagrees_or_is_missing(tmp_path):
    _, z = _bundle(tmp_path)
    side = z.with_name(z.name + ".sha256")
    side.write_bytes(b"0" * 64 + b"  bundle-x.zip\n")
    rc, out = run("verify", z)
    assert rc == 3 and "a partial or changed copy" in out, out
    side.unlink()
    rc, out = run("verify", z)
    assert rc == 3 and "no bundle-x.zip.sha256 beside the zip" in out, out


def test_questions_are_carried_until_answered(tmp_path):
    ledger = tmp_path / "q.json"
    rc, out = run("ask", "Did the Linux leak gate selftest pass?", "--ledger", ledger)
    assert rc == 0 and "Q-" in out, out
    qid = out.split()[0]
    rc, out = run("ask", "Which kernel booted?", "--to", "linux", "--ledger", ledger)
    rc, out = run("questions", "--ledger", ledger)
    assert "2 open" in out, out
    assert run("answer", qid, "yes, READY", "--ledger", ledger)[0] == 0
    rc, out = run("questions", "--ledger", ledger)
    assert "1 open" in out and qid not in out, out
    rc, out = run("questions", "--all", "--ledger", ledger)
    assert "yes, READY" in out
    assert run("answer", qid, "again", "--ledger", ledger)[0] != 0          # an answered question stays answered
    rows = json.loads(ledger.read_text(encoding="utf-8"))
    assert [r["status"] for r in rows] == ["answered", "open"]


def test_collect_puts_the_open_questions_in_the_bundle(world, tmp_path):
    ledger = tmp_path / "q.json"
    run("ask", "Is the VAIO still on kernel 7.2.9?", "--ledger", ledger)
    out_dir = tmp_path / "out"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"roots": [str(world["roots"])], "sets": {}}), encoding="utf-8")
    rc, out = run("collect", "--out", out_dir, "--config", cfg, "--ledger", ledger)
    assert rc == 0, out
    page = (out_dir / "QUESTIONS-OPEN.md").read_text(encoding="utf-8")
    assert "Is the VAIO still on kernel 7.2.9?" in page and "day(s) open" in page
    assert "QUESTIONS-OPEN.md" in [r["path"] for r in json.loads((out_dir / "MANIFEST.json").read_text(encoding="utf-8"))]
