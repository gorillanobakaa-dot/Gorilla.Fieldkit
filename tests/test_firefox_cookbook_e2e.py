"""P38 (owner order 2026-11-16): a toy Firefox through the whole cookbook chain, one test per seam that broke on 160.

A tmp upstream git repo with tag FIREFOX_1_0_RELEASE, a toy patch set (config/patch_policy.json, a shared group, a
.linux group and a group export-hand wrote but nobody registered), a bare origin with HEAD=master. The chain:
port -> export-hand -> register (seam 1) -> build-verify journal (2) -> closed steps (3) -> base branch (4) -> stage
device (5) -> upstream base (6) -> published (7) -> handover package -> each OS's apply step.
Each seam is broken on purpose by tests/firefox_cookbook_mutations.yaml (fieldkit mutate)."""
import json
import subprocess
from pathlib import Path

import pytest

from fieldkit.core import concepts

TAG = "FIREFOX_1_0_RELEASE"


def git(repo, *a):
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=master",
                        "-c", "commit.gpgsign=false", *a], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def w(path, text):
    """Bytes as written (no CRLF on Windows): the patches must apply on every OS."""
    Path(path).write_bytes(text.encode())


def patch_for(upstream, path, new):
    """A real `git diff` patch that changes `path` to `new` on the upstream tree."""
    git(upstream, "checkout", "-q", TAG)
    old = (Path(upstream) / path).read_text()
    w((Path(upstream) / path), new)
    d = git(upstream, "diff", "--", path) + "\n"
    w((Path(upstream) / path), old)
    return d


@pytest.fixture
def toy(tmp_path):
    up = tmp_path / "upstream"
    up.mkdir()
    git(up, "init", "-q")
    git(up, "config", "core.autocrlf", "false")
    w((up / "browser.js"), "pref('telemetry', true);\n")
    w((up / "linux.cpp"), "int gtk = 0;\n")
    git(up, "add", "-A")
    git(up, "commit", "-qm", "firefox 1.0")
    git(up, "tag", TAG)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
    for k, v in (("core.autocrlf", "false"),):
        subprocess.run(["git", "-C", str(origin), "config", k, v], check=True)
    ps = tmp_path / "patchset"
    subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "-q", str(origin), str(ps)], check=True, capture_output=True)
    git(ps, "config", "core.autocrlf", "false")
    (ps / "config").mkdir()
    (ps / "patches" / "10.PRIVACY").mkdir(parents=True)
    (ps / "patches" / "23.REBASE.1.linux").mkdir(parents=True)
    w((ps / "patches" / "10.PRIVACY" / "0001-telemetry-off.patch"), 
        patch_for(up, "browser.js", "pref('telemetry', false);\n"))
    w((ps / "patches" / "23.REBASE.1.linux" / "0001-gtk.patch"), patch_for(up, "linux.cpp", "int gtk = 1;\n"))
    w((ps / "config" / "patch_policy.json"), json.dumps({"patchset_root": "patches", "groups": {
        "10.PRIVACY": {"status": "enabled"}, "23.REBASE.1.linux": {"status": "enabled"}}}, indent=2))
    git(ps, "checkout", "-q", "-b", "master")
    git(ps, "add", "-A")
    git(ps, "commit", "-qm", "patch set")
    git(ps, "push", "-q", "-u", "origin", "master")
    git(ps, "remote", "set-head", "origin", "--auto")
    return {"up": up, "origin": origin, "ps": ps, "tmp": tmp_path}


# -- seam 1: export-hand writes a group; it is registered, never silently skipped ---------------------------------
def test_seam1_unregistered_group_is_found_and_registered(toy):
    from fieldkit.buildh import replay
    g = toy["ps"] / "patches" / "21.PORT.FIXES.1"
    g.mkdir()
    w((g / "0001-port.patch"), patch_for(toy["up"], "browser.js", "pref('telemetry', false); // port\n"))
    assert replay.unregistered_groups(toy["ps"]) == [("21.PORT.FIXES.1", 1)]
    assert replay.register_groups(toy["ps"], say=lambda *_: None) == ["21.PORT.FIXES.1"]
    assert replay.unregistered_groups(toy["ps"]) == []
    pol = json.loads((toy["ps"] / "config" / "patch_policy.json").read_text())
    assert list(pol["groups"])[-1] == "21.PORT.FIXES.1" and pol["groups"]["21.PORT.FIXES.1"]["status"] == "enabled"


# -- seam 2: build-verify journals the event the S5 gate reads (tests/test_buildh_cli_fixes.py holds the CLI side) --
def test_seam2_gate_reads_build_verified_after_build_start():
    from fieldkit.migrate import plan

    class M:
        tid = "toy"

        def build_result(self):
            return {"tree": "T", "verified_at": "now"}

        def tree(self):
            return "T"

        def events(self):
            return [{"event": "build-start"}, {"event": "build-verified", "ok": True, "t": "now"}]

        owner = None
    items = {i["id"]: i for i in plan.gate_S5(M())}
    assert items["S5.packaging"]["ok"] and items["S5.build"]["ok"]


# -- seam 3: one definition of a closed step -----------------------------------------------------------------------
def test_seam3_closed_step_is_one_definition():
    from fieldkit.buildh import rebase
    steps = [{"id": "a", "status": "proposed"}, {"id": "b", "status": "done"}, {"id": "c", "status": "obsolete"},
             {"id": "final-build", "status": "pending"}]
    assert [s["id"] for s in concepts.open_steps(steps, final=False)] == ["a"]
    assert rebase.unfinished(steps) == ["a", "final-build"]
    assert not concepts.closed_step({"status": "proposed"})


# -- seam 4: the base branch is origin/HEAD, never assumed main ------------------------------------------------------
def test_seam4_default_branch_is_master_for_the_patch_set(toy):
    assert concepts.default_branch(toy["ps"]) == "master"


# -- seam 5: a plan whose every stage is offloaded measures the device, not this machine ----------------------------
def test_seam5_all_offloaded_is_measured_on_the_device():
    from fieldkit.emerge import runner
    rows = [{"action": "offload", "device": "l15"}, {"action": "skip"}]
    assert concepts.measured_on(rows) == (False, ["l15"])
    assert concepts.measured_on([{"action": "run"}, {"action": "offload", "device": "l15"}])[0] is True
    out = runner.own_checks(None, "run", offloads=[], rows_planned=rows)
    assert out and out[0][0] == "machine" and "l15" in out[0][2]


# -- seam 6: the upstream base is the release tag of the version, never a pinned commit ------------------------------
def test_seam6_upstream_from_version_and_lint_refuses_a_pinned_base(toy):
    from fieldkit.emerge import lint
    assert concepts.upstream_tag("1.0") == TAG and concepts.upstream_tag("160") == "FIREFOX_160_0_RELEASE"
    assert concepts.upstream_tag("140.5.0esr") == "FIREFOX_140_5_0esr_RELEASE"
    sha, how = concepts.upstream_ref("1.0", toy["up"])
    assert sha == git(toy["up"], "rev-parse", TAG) and TAG in how
    assert concepts.upstream_ref("2.0", toy["up"])[0] is None
    spec = {"latest": {"version": "x"}, "sources": [{"id": "upstream", "git": "s", "base": "3c71aeacdb29"}]}
    assert any(k == "BASE" for k, *_ in lint.lint_spec("toy", spec))
    spec["sources"][0]["base"] = "upstream"
    assert not any(k == "BASE" for k, *_ in lint.lint_spec("toy", spec))


# -- seam 7: the patch set is committed and pushed before the other OS builds from it -------------------------------
def test_seam7_published_needs_commit_and_push(toy):
    from fieldkit.migrate import plan
    m = plan.Migration("toy", t={"steps": [], "workdir": str(toy["up"])}, owner=toy["ps"], tree="T", find_install=False)
    m.events = lambda: [{"event": "replay-proof", "ok": True, "tree": "T", "t": "now"}]
    m.tree = lambda: "T"
    pub = lambda: {i["id"]: i for i in plan.gate_patchset_kept(m)}["S5.published"]  # noqa: E731
    assert pub()["ok"], pub()
    w((toy["ps"] / "patches" / "10.PRIVACY" / "0002-more.patch"), "x\n")
    assert not pub()["ok"]
    git(toy["ps"], "add", "-A")
    git(toy["ps"], "commit", "-qm", "more")
    assert not pub()["ok"] and "not pushed" in pub()["evidence"]
    git(toy["ps"], "push", "-q")
    assert pub()["ok"]


# -- the handover package and each OS's apply step ------------------------------------------------------------------
@pytest.mark.parametrize("to,gtk", [("windows", "int gtk = 0;\n"), ("mac", "int gtk = 0;\n")])
def test_handover_package_applies_on_each_os(toy, to, gtk):
    from fieldkit.firefox import handover
    pkg, man, problems = handover.build("1.0", to, toy["ps"], "toy", toy["up"], toy["tmp"] / "out",
                                        replay_proof={"ok": True, "tree": "T"}, lessons={"waiting": []})
    assert problems == [] and handover.verify(pkg) == []
    assert man["upstream"]["tag"] == TAG and man["patchset"]["base_branch"] == "master" and man["patchset"]["on_remote"]
    assert man["product"] == "Gorilla Unleashed 1.0"
    assert man["patches"] == ["001-10.PRIVACY--0001-telemetry-off.patch"]       # the .linux group stays home
    git(toy["up"], "checkout", "-q", TAG)
    for p in sorted((pkg / "patches").glob("*.patch")):
        git(toy["up"], "apply", str(p))
    assert (toy["up"] / "browser.js").read_text() == "pref('telemetry', false);\n"
    assert (toy["up"] / "linux.cpp").read_text() == gtk
    w((pkg / "patches" / man["patches"][0]), "tampered\n")
    assert handover.verify(pkg)


def test_handover_fails_closed(toy):
    from fieldkit.firefox import handover
    g = toy["ps"] / "patches" / "22.EGRESS.1"
    g.mkdir()
    w((g / "0001-egress.patch"), "x\n")
    _pkg, _man, problems = handover.build("1.0", "windows", toy["ps"], "toy", toy["up"], toy["tmp"] / "out",
                                          replay_proof=None, lessons={"waiting": ["a lesson"]})
    text = " | ".join(problems)
    assert "uncommitted" in text and "replay proof" in text and "lesson" in text and "22.EGRESS.1" in text


def test_linux_platform_takes_its_own_group(toy):
    from fieldkit.firefox import handover
    assert [g for g, _ in handover.platform_patches(toy["ps"], "linux")] == ["10.PRIVACY", "23.REBASE.1.linux"]
    assert concepts.platform_of_group("23.REBASE.1.linux") == "linux" and concepts.platform_of_group("10.PRIVACY") is None
    assert concepts.excluded_on({"exclude": ["a"], "exclude_on": {"mac": ["b"]}}, "mac") == {"a", "b"}


def test_cookbook_data_and_lesson_rules_load():
    from fieldkit.buildh import lessonrules
    from fieldkit.firefox import handover
    cb = handover.cookbook()
    assert set(cb["systems"]) == set(concepts.PLATFORMS)
    assert {s["system"] for s in cb["steps"]} >= set(concepts.PLATFORMS)
    assert [s["id"] for s in cb["steps"] if s["id"].startswith("S")] == [f"S{i}" for i in range(10)]
    assert lessonrules.ENTRIES and lessonrules.rule_for("a hunk that failed to apply must always become a step")
    with pytest.raises(ValueError):
        concepts.upstream_tag("not-a-version")


def test_cli_release_prints_the_chain(capsys):
    from fieldkit.firefox import cli
    assert cli.main(["release", "1.0"]) == 0
    out = capsys.readouterr().out
    assert "Gorilla Unleashed 1.0" in out and TAG in out and "handover 1.0 --to windows" in out and "NEXT: " in out


# -- one product name and one scheme on every system (owner 2026-11-16; cases measured on the published releases) ----
def test_one_name_scheme_for_all_three_systems():
    assert concepts.naming()["name"] == concepts.PRODUCT == "Gorilla Unleashed"
    assert concepts.product_name("160.0") == "Gorilla Unleashed 160.0"
    assert concepts.artifact_name("160.0", "windows", "x86_64", "zip") == "Gorilla-Unleashed-160.0-windows-x86_64.zip"
    assert concepts.release_tag("160.0", "mac") == "v160.0-mac"
    assert concepts.release_title("160.0", "mac") == "Gorilla Unleashed 160.0 (macOS)"
    for os_name in concepts.PLATFORMS:
        assert not concepts.name_problems("file", concepts.artifact_name("160.0", os_name, "amd64", "deb"))
        assert not concepts.name_problems("tag", concepts.release_tag("160.0", os_name))
        assert not concepts.name_problems("title", concepts.release_title("160.0", os_name))


@pytest.mark.parametrize("kind,value", [
    ("file", "firefox-158.0.en-US.win64.installer.exe"),      # Windows v158.0-win64-build2: Mozilla's names
    ("file", "firefox-158.0.en-US.win64.zip"),
    ("title", "158.0 build 2"),
    ("tag", "v158.0-win64-build2"),
    ("file", "gorilla-unleashed_158.0-2_amd64.deb"),           # Linux: right name, another form
    ("tag", "v158.0-2"),
    ("tag", "macbook-air"),                                    # Mac: no version
    ("file", "gorilla-air-26-10-23-21-58.zip"),                # a date, not a version
    ("title", "Gorilla Unleashed for old Macs"),
    ("text", "Build Gorilla Firefox on Windows"),
])
def test_published_names_of_today_fail_the_check(kind, value):
    assert concepts.name_problems(kind, value)


def test_fieldkit_firefox_files_use_the_one_name():
    from fieldkit.firefox import cli
    assert cli.name_check() == []
    assert cli.main(["names", "--tag", "v160.0-linux", "--title", "Gorilla Unleashed 160.0 (Linux)"]) == 0
    assert cli.main(["names", "--tag", "macbook-air"]) == 3
