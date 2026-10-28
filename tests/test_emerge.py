"""fieldkit emerge: content-addressed stages, a model only on failure, fixes only as commits, tiers, offload, pins."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from fieldkit.autopilot import hardlines
from fieldkit.emerge import main as emerge_main
from fieldkit.emerge import recipe as R
from fieldkit.emerge import runner as E

PY = sys.executable
OK_CHECKS = [("disk", "ok", "fine")]


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()


def make_repo(d):
    d.mkdir(parents=True, exist_ok=True)
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    return d


def commit_all(d, msg="c"):
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", msg)


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))
    return p


def copy_cmd(src, dst):
    """A stage command: copy src to dst (a 'compile')."""
    return [PY, "-c", "import os,shutil,sys; os.makedirs(os.path.dirname(sys.argv[2]), exist_ok=True); "
                      "shutil.copyfile(sys.argv[1], sys.argv[2])", str(src), str(dst)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_TESTCOST_DIR", str(tmp_path / "tc"))
    monkeypatch.setattr(hardlines, "STOP_FILE", tmp_path / "STOP")
    work = make_repo(tmp_path / "proj")
    write(work / "a.txt", "alpha\n")
    write(work / "b.txt", "beta\n")
    write(work / "README.md", "readme\n")
    write(work / ".gitignore", "out/\nrestored\ngated\n")
    commit_all(work)
    return tmp_path, work


def spec(work, **extra):
    s = {"name": "demo", "vars": {"w": str(work)},
         "sources": [{"id": "w", "git": "{w}", "track": "tree"}],
         "stages": [
             {"id": "one", "run": {"cmd": copy_cmd("{w}/a.txt", "{w}/out/a.bin")},
              "inputs": ["{w}/a.txt"], "outputs": ["{w}/out/a.bin"], "uses": ["inputs"], "needs": []},
             {"id": "two", "run": {"cmd": copy_cmd("{w}/b.txt", "{w}/out/b.bin")},
              "inputs": ["{w}/b.txt"], "outputs": ["{w}/out/b.bin"], "uses": ["inputs"], "needs": []},
             {"id": "pack", "run": {"cmd": [PY, "-c", "open(r'{w}/out/p.zip','w').write('packed')"]},
              "outputs": ["{w}/out/p.zip"], "uses": [], "needs": ["one", "two"]},
         ]}
    s.update(extra)
    return s


def recipe(tmp, s, source="<memory>"):
    return R.Recipe(s, source=source, state_root=tmp / "state", strict=False)


class Fixer:
    def __init__(self, action=None):
        self.prompts, self.action = [], action

    def __call__(self, prompt, cwd=None):
        self.prompts.append(prompt)
        if self.action:
            self.action()


class Inbox:
    def __init__(self):
        self.items = []

    def __call__(self, title, why, command="", kind=""):
        self.items.append((title, why, command))
        return len(self.items)


def emerge(r, **kw):
    kw.setdefault("say", lambda m: None)
    kw.setdefault("guard", False)
    kw.setdefault("checks", OK_CHECKS)
    kw.setdefault("fixer", Fixer())
    kw.setdefault("inbox_add", Inbox())
    kw.setdefault("who", "testhost")
    return E.Emerge(r, **kw)


def statuses(rep):
    return {e["id"]: e["status"] for e in rep["stages"]}


# -- the cache ----------------------------------------------------------------------------------------------------
def test_green_then_everything_cached_and_no_model(env):
    tmp, work = env
    fixer = Fixer()
    rep = emerge(recipe(tmp, spec(work)), fixer=fixer).run()
    assert rep["ok"] and statuses(rep) == {"one": "pass", "two": "pass", "pack": "pass"}
    rep2 = emerge(recipe(tmp, spec(work)), fixer=fixer).run()
    assert statuses(rep2) == {"one": "skip", "two": "skip", "pack": "skip"}
    assert rep["llm_calls"] == rep2["llm_calls"] == 0 and fixer.prompts == []
    ov = json.loads((tmp / "state" / "demo" / "overhead.json").read_text())
    assert {"job", "kind", "start", "end", "wall_s", "bare_s", "bare_parts", "ratio", "max_ratio", "level", "buckets",
            "rework", "runs", "counts", "sessions", "notes"} <= set(ov)
    assert set(ov["buckets"]) == {"bare", "rework", "tests", "tools", "llm", "owner", "idle"}
    assert ov["counts"]["owner_prompts"] == 0 and ov["counts"]["cached"] == 3
    events = [json.loads(l)["event"] for l in (tmp / "state" / "demo" / "journal.jsonl").read_text().splitlines()]
    assert events.count("build-stage-done") == 3 and "build-start" in events and "build-done" in events


def test_changed_input_rebuilds_that_stage_and_its_dependents_only(env):
    tmp, work = env
    emerge(recipe(tmp, spec(work))).run()
    write(work / "a.txt", "alpha 2\n")
    rep = emerge(recipe(tmp, spec(work))).run()
    assert statuses(rep) == {"one": "pass", "two": "skip", "pack": "pass"}


def test_a_changed_output_is_not_trusted(env):
    tmp, work = env
    emerge(recipe(tmp, spec(work))).run()
    write(work / "out" / "b.bin", "tampered\n")
    rep = emerge(recipe(tmp, spec(work))).run()
    assert statuses(rep)["two"] == "pass" and (work / "out" / "b.bin").read_text() == "beta\n"
    write(work / "out" / "b.bin", "BETA\n")             # same size, other bytes
    rep = emerge(recipe(tmp, spec(work))).run()
    assert statuses(rep)["two"] == "pass" and (work / "out" / "b.bin").read_text() == "beta\n"


def test_a_recipe_asking_for_endless_fixes_is_capped(env):
    tmp, work = env
    s = failing_spec(work)
    s["stages"][0]["max_fix"] = 99
    git(work, "checkout", "-q", "-b", "fix/try")
    n = [0]

    def fix():
        n[0] += 1
        write(work / f"note{n[0]}.txt", "tried\n")
        commit_all(work, f"try {n[0]}")
    fixer = Fixer(fix)
    rep = emerge(recipe(tmp, s), fixer=fixer).run()
    assert not rep["ok"] and len(fixer.prompts) == E.MAX_FIXES_PER_RUN


def test_a_declared_output_that_was_not_made_fails_closed(env):
    tmp, work = env
    s = spec(work)
    s["stages"][0]["outputs"] = ["{w}/out/never.bin"]
    inbox = Inbox()
    rep = emerge(recipe(tmp, s), allow_fix=False, inbox_add=inbox).run()
    assert not rep["ok"] and rep["stopped_at"] == "one"
    assert "declared output missing" in inbox.items[0][1]


def test_keys_are_portable_between_machines(env, tmp_path):
    tmp, work = env
    other = make_repo(tmp_path / "elsewhere" / "deeper" / "proj")
    for n in ("a.txt", "b.txt", "README.md"):
        write(other / n, (work / n).read_text())
    commit_all(other)
    k1 = recipe(tmp, spec(work)).keys()
    k2 = R.Recipe(spec(other), state_root=tmp_path / "state2", strict=False).keys()
    assert k1 == k2
    write(other / "a.txt", "different\n")
    k3 = R.Recipe(spec(other), state_root=tmp_path / "state3", strict=False).keys()
    assert k3["one"] != k1["one"] and k3["two"] == k1["two"]


def test_the_chain_default_feeds_each_stage_the_one_before(env):
    tmp, work = env
    s = spec(work)
    for st in s["stages"]:
        st.pop("needs")
    r = recipe(tmp, s)
    assert r.needs("one") == [] and r.needs("two") == ["one"] and r.needs("pack") == ["two"]
    k = r.keys()
    write(work / "a.txt", "changed\n")
    k2 = recipe(tmp, s).keys()
    assert all(k[x] != k2[x] for x in ("one", "two", "pack"))


# -- fix on failure ---------------------------------------------------------------------------------------------
def failing_spec(work):
    """Stage `one` runs fix.py, which fails until it says ok: a fix is a commit changing fix.py."""
    write(work / "fix.py", "import sys; sys.exit(1)\n")
    commit_all(work, "broken")
    s = spec(work)
    s["stages"][0]["run"] = {"cmd": [PY, str(work / "fix.py")]}
    s["stages"][0]["outputs"] = []
    s["stages"][0]["inputs"] = ["{w}/fix.py"]
    return s


def test_a_failure_calls_one_model_run_and_a_committed_fix_resumes(env):
    tmp, work = env
    s = failing_spec(work)
    write(work / "demo.yaml", yaml.safe_dump(s))
    commit_all(work, "recipe")

    def fix():
        git(work, "checkout", "-q", "-b", "fix/emerge-demo-one")
        write(work / "fix.py", "print('ok')\n")
        commit_all(work, "fix")
    fixer = Fixer(fix)
    rep = emerge(R.Recipe(s, source=work / "demo.yaml", state_root=tmp / "state", strict=False), fixer=fixer).run()
    assert rep["ok"] and rep["llm_calls"] == 1 and len(fixer.prompts) == 1
    assert "stage one failed" in fixer.prompts[0] and "fix/emerge-demo-one" in fixer.prompts[0]
    assert rep["fixes"][0]["ok"] and statuses(rep)["one"] == "pass"
    assert statuses(rep)["two"] == "pass"


def test_an_uncommitted_fix_is_refused_and_goes_to_the_inbox(env):
    tmp, work = env
    s = failing_spec(work)
    fixer = Fixer(lambda: write(work / "fix.py", "print('hand edit')\n"))
    inbox = Inbox()
    rep = emerge(recipe(tmp, s), fixer=fixer, inbox_add=inbox).run()
    assert not rep["ok"] and rep["fixes"][0]["ok"] is False
    assert "never a hand edit" in rep["fixes"][0]["why"] and len(inbox.items) == 1
    assert "two" not in statuses(rep)


def test_a_fix_committed_on_main_is_refused(env):
    tmp, work = env
    s = failing_spec(work)

    def fix():
        write(work / "fix.py", "print('ok')\n")
        commit_all(work, "on main")
    rep = emerge(recipe(tmp, s), fixer=Fixer(fix)).run()
    assert not rep["ok"] and "committed on main" in rep["fixes"][0]["why"]


def test_a_fix_that_changes_nothing_stops_the_run(env):
    tmp, work = env
    rep = emerge(recipe(tmp, failing_spec(work)), fixer=Fixer()).run()
    assert not rep["ok"] and "committed nothing" in rep["fixes"][0]["why"]


def test_bounded_retries_then_the_inbox(env):
    tmp, work = env
    s = failing_spec(work)
    s["stages"][0]["max_fix"] = 2
    git(work, "checkout", "-q", "-b", "fix/try")
    n = [0]

    def fix():                   # a commit that does not fix it
        n[0] += 1
        write(work / f"note{n[0]}.txt", "tried\n")
        commit_all(work, f"try {n[0]}")
    fixer, inbox = Fixer(fix), Inbox()
    rep = emerge(recipe(tmp, s), fixer=fixer, inbox_add=inbox).run()
    assert not rep["ok"] and len(fixer.prompts) == 2 and rep["llm_calls"] == 2
    assert len(inbox.items) == 1 and "after 2 fix attempt(s)" in inbox.items[0][1]


def test_fix_false_goes_straight_to_the_inbox(env):
    tmp, work = env
    s = failing_spec(work)
    s["stages"][0]["fix"] = False
    fixer, inbox = Fixer(), Inbox()
    rep = emerge(recipe(tmp, s), fixer=fixer, inbox_add=inbox).run()
    assert not rep["ok"] and fixer.prompts == [] and len(inbox.items) == 1


def test_an_always_stage_runs_after_a_failure(env):
    tmp, work = env
    s = failing_spec(work)
    s["stages"][0]["fix"] = False
    s["stages"].append({"id": "restore", "always": True, "always_run": True, "uses": [], "needs": [],
                        "run": {"cmd": [PY, "-c", "open(r'%s','w').write('x')" % (work / "restored")]}})
    rep = emerge(recipe(tmp, s)).run()
    assert not rep["ok"] and (work / "restored").exists()


def test_an_advisory_failure_is_reported_and_the_run_goes_on(env):
    tmp, work = env
    s = spec(work)
    s["stages"].insert(1, {"id": "repro", "advisory": True, "uses": [], "needs": [], "run": {"cmd": [PY, "-c", "raise SystemExit(4)"]}})
    fixer = Fixer()
    rep = emerge(recipe(tmp, s), fixer=fixer).run()
    assert rep["ok"] and rep["advisories"][0]["stage"] == "repro" and fixer.prompts == []


# -- tiers and docs-only ------------------------------------------------------------------------------------------
def test_tiers_follow_the_mode(env):
    tmp, work = env
    s = spec(work)
    s["stages"].append({"id": "gate", "tier": "release", "uses": [], "needs": ["pack"],
                        "run": {"cmd": [PY, "-c", "open(r'%s','w').write('g')" % (work / "gated")]}})
    s["stages"].append({"id": "postinst", "tier": "install", "uses": [], "needs": ["pack"],
                        "run": {"cmd": [PY, "-c", "pass"]}})
    rep = emerge(recipe(tmp, s)).run()
    assert statuses(rep)["gate"] == "tier-skip" and statuses(rep)["postinst"] == "tier-skip"
    assert not (work / "gated").exists()
    rep = emerge(recipe(tmp, s), mode="install").run()
    assert statuses(rep)["postinst"] == "pass" and statuses(rep)["gate"] == "tier-skip"
    rep = emerge(recipe(tmp, s), mode="release").run()
    assert statuses(rep)["gate"] == "pass" and statuses(rep)["one"] == "skip"


def test_a_docs_only_change_runs_only_the_privacy_stages(env):
    tmp, work = env
    s = spec(work, docs_only={"source": "w"})
    for st in s["stages"]:
        st["uses"] = ["sources"]
    s["stages"].append({"id": "privacy", "privacy": True, "uses": ["sources"], "needs": [],
                        "run": {"cmd": [PY, "-c", "pass"]}})
    emerge(recipe(tmp, s)).run()
    write(work / "README.md", "only docs changed\n")
    commit_all(work, "docs")
    rep = emerge(recipe(tmp, s)).run()
    st = statuses(rep)
    assert st["privacy"] == "pass" and st["one"] == st["two"] == st["pack"] == "docs-skip"
    write(work / "a.txt", "code\n")
    rep = emerge(recipe(tmp, s)).run()
    assert statuses(rep)["one"] == "pass"


# -- offload and reproducibility -----------------------------------------------------------------------------------
class FakeOffloader:
    def __init__(self, tmp, ok=True, sha=None):
        self.tmp, self.ok, self.sha, self.sent = tmp, ok, sha, []

    def dispatch(self, recipe, stage, key):
        self.sent.append(stage["id"])
        return {"stage": stage["id"]}

    def collect(self, h):
        if not self.ok:
            return {"ok": False, "why": "the cable is down"}
        f = write(self.tmp / "received" / f"{h['stage']}.deb", "deb from the vaio\n")
        return {"ok": True, "host": "vaio", "outputs": [{"name": f.name, "sha256": R.sha256_file(f),
                                                         "bytes": f.stat().st_size, "path": str(f)}]}


def offload_spec(work):
    s = spec(work)
    s["stages"].append({"id": "deb", "platforms": ["nowhere"], "uses": ["inputs"], "inputs": ["{w}/a.txt"],
                        "needs": [], "offload": {"device": "vaio"}, "run": {"cmd": [PY, "-c", "pass"]}})
    return s


def test_a_stage_for_the_other_os_is_offloaded_first_and_its_artefacts_cached(env):
    tmp, work = env
    off = FakeOffloader(tmp)
    rep = emerge(recipe(tmp, offload_spec(work)), offloader=off).run()
    assert rep["ok"] and statuses(rep)["deb"] == "pass" and off.sent == ["deb"]
    rec = E.Store(tmp / "state" / "demo" / "cache").get("deb", recipe(tmp, offload_spec(work)).keys()["deb"])
    assert "vaio" in rec["hosts"]
    off2 = FakeOffloader(tmp)
    rep = emerge(recipe(tmp, offload_spec(work)), offloader=off2).run()
    assert statuses(rep)["deb"] == "skip" and off2.sent == []


def test_a_failed_offload_is_not_given_to_a_model(env):
    tmp, work = env
    fixer, inbox = Fixer(), Inbox()
    rep = emerge(recipe(tmp, offload_spec(work)), offloader=FakeOffloader(tmp, ok=False), fixer=fixer,
                 inbox_add=inbox).run()
    assert not rep["ok"] and fixer.prompts == [] and "cable is down" in inbox.items[0][1]


def test_a_stage_with_no_offload_for_another_os_stops(env):
    tmp, work = env
    s = spec(work)
    s["stages"].append({"id": "mac", "platforms": ["nowhere"], "uses": [], "needs": [], "run": {"cmd": [PY, "-c", "pass"]}})
    rep = emerge(recipe(tmp, s), fixer=Fixer()).run()
    assert not rep["ok"] and statuses(rep)["mac"] == "not-here"


def test_same_key_different_bytes_is_reported(tmp_path):
    st = E.Store(tmp_path)
    assert st.put("build", "k1", "laptop", [{"name": "x.exe", "sha256": "aa"}], 1) == []
    diffs = st.put("build", "k1", "vaio", [{"name": "x.exe", "sha256": "bb"}], 1)
    assert diffs and diffs[0]["laptop"] == "aa" and diffs[0]["vaio"] == "bb"
    assert st.put("build", "k1", "third", [{"name": "x.exe", "sha256": "aa"}], 1)[0]["vaio"] == "bb"


def test_compare_two_machines_exports():
    mine = {"records": [{"stage": "b", "key": "k", "hosts": {"here": [{"name": "x", "sha256": "1"}]}},
                        {"stage": "c", "key": "q", "hosts": {"here": [{"name": "y", "sha256": "2"}]}}]}
    theirs = {"records": [{"stage": "b", "key": "k", "hosts": {"vaio": [{"name": "x", "sha256": "1"}]}},
                          {"stage": "c", "key": "q", "hosts": {"vaio": [{"name": "y", "sha256": "3"}]}}]}
    res = E.compare(mine, theirs)
    assert len(res["same"]) == 1 and res["different"][0]["name"] == "y"


# -- pins and preflight ----------------------------------------------------------------------------------------------
def test_a_file_source_with_the_wrong_sha256_refuses_before_stage_one(env):
    tmp, work = env
    tar = write(work / "src.tar", "upstream bytes\n")
    s = spec(work, sources=[{"id": "tar", "path": str(tar), "sha256": "0" * 64}])
    r = recipe(tmp, s)
    rows = E.own_checks(r, "iterate", [], disk=lambda p: 10 ** 12, ram=lambda: 10 ** 11, longpath_rows=[], thermal=67)
    assert ("pin tar", "stop") == rows[0][:2] and "pinned 0000" in rows[0][2]
    rep = E.Emerge(r, say=lambda m: None, guard=False, checks=rows, inbox_add=Inbox()).run()
    assert not rep["ok"] and rep["refused"] and rep["stages"] == [] and not (work / "out").exists()


def test_an_unpinned_file_source_is_a_stop_and_the_manifest_pins_it(env):
    tmp, work = env
    tar = write(work / "src.tar", "upstream bytes\n")
    s = spec(work, sources=[{"id": "tar", "path": str(tar)}])
    rpath = write(tmp / "recipes" / "demo.yaml", yaml.safe_dump(s))
    r = R.Recipe(s, source=rpath, state_root=tmp / "state", strict=False)
    assert r.pin(s["sources"][0])[0] == "unpinned"
    assert emerge_main(["manifest", str(rpath), "--write"]) == 0
    r = R.Recipe(s, source=rpath, state_root=tmp / "state", strict=False)
    assert r.pin(r.sources[0])[0] == "ok"
    write(work / "src.tar", "someone changed it\n")
    assert r.pin(r.sources[0])[0] == "mismatch"
    assert emerge_main(["manifest", str(rpath), "--write"]) == 3      # a pin moves only by a reviewed commit


def test_git_pins_commit_and_base(env):
    tmp, work = env
    head = git(work, "rev-parse", "HEAD")
    r = recipe(tmp, spec(work))
    assert r.pin({"id": "x", "git": str(work), "commit": head})[0] == "ok"
    assert r.pin({"id": "x", "git": str(work), "commit": "deadbeef"})[0] == "mismatch"
    write(work / "ours.txt", "our patch\n")
    commit_all(work, "ours on top")
    assert r.pin({"id": "x", "git": str(work), "base": head})[0] == "ok"
    other = make_repo(tmp / "other")
    write(other / "z", "z")
    commit_all(other)
    assert r.pin({"id": "x", "git": str(work), "base": git(other, "rev-parse", "HEAD")})[0] == "mismatch"
    inner = work / "sub"
    inner.mkdir()
    assert r.pin({"id": "x", "git": str(inner)})[0] == "absent"      # a folder inside another repository


def test_a_source_fetched_by_a_stage_is_checked_right_after_it(env):
    tmp, work = env
    s = spec(work, sources=[{"id": "tar", "path": str(work / "out" / "a.bin"), "sha256": "1" * 64,
                             "fetched_by": "one"}])
    inbox, fixer = Inbox(), Fixer()
    rep = emerge(recipe(tmp, s), inbox_add=inbox, fixer=fixer).run()
    assert not rep["ok"] and rep["stopped_at"] == "one" and fixer.prompts == []


def test_preflight_disk_stop(env):
    tmp, work = env
    r = recipe(tmp, spec(work, preflight={"disk_gb": 50, "path": str(work)}))
    rows = E.own_checks(r, "iterate", [], disk=lambda p: 2 * 2 ** 30, ram=lambda: 10 ** 11, longpath_rows=[],
                        thermal=67)
    assert dict((n, st) for n, st, _ in rows)["disk"] == "stop"


def test_the_owner_stop_file_halts_between_stages(env):
    tmp, work = env
    hardlines.STOP_FILE.write_text("stop", encoding="utf-8")
    rep = emerge(recipe(tmp, spec(work))).run()
    assert not rep["ok"] and rep["stopped"] and not (work / "out").exists()


def test_unknown_overlay_key_is_an_error(env):
    tmp, work = env
    with pytest.raises(R.RecipeError):
        R.Recipe({"name": "x", "pipeline": "go-app", "stages": {"build": {"ouputs": []}}}, state_root=tmp, strict=False)
    with pytest.raises(R.RecipeError):
        R.Recipe({"name": "x", "pipeline": "go-app", "stages": {"nosuch": {}}}, state_root=tmp, strict=False)


# -- the shipped recipes ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", R.available())
def test_every_shipped_recipe_loads_and_plans(name, tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_TESTCOST_DIR", str(tmp_path / "tc"))
    raw = yaml.safe_load(R.find(name).read_text(encoding="utf-8"))
    base = R.Recipe(raw, source=R.find(name), state_root=tmp_path / "s0", strict=False)
    # every machine path pointed at an empty folder: the plan must work anywhere, fail closed on what is absent
    over = {k: str(tmp_path / "m" / k) for k, v in base.vars.items() if isinstance(v, str) and ("${" in v or R._ABS.match(v))}
    r = R.Recipe(raw, source=R.find(name), overrides=over, state_root=tmp_path / "s1", strict=False,
                 run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "tool 1.0\n", ""))
    pl = E.Emerge(r, checks=OK_CHECKS, who="t").plan()
    assert len(pl["stages"]) == len(r.stages) and all(len(x["key"]) == 64 for x in pl["stages"])
    assert {x["action"] for x in pl["stages"]} <= {"run", "offload", "tier-skip", "not-here", "skip"}
    assert raw.get("baseline_minutes")


def test_the_inventory_and_the_recipes_name_each_other():
    inv = yaml.safe_load((R.HERE / "inventory.yaml").read_text(encoding="utf-8"))["projects"]
    named = set()
    for p in inv.values():
        r = p.get("recipe")
        named.update(r if isinstance(r, list) else [r] if r else [])
    assert named == set(R.available())


def test_firefox_tiers_match_the_audit(tmp_path):
    r = R.Recipe(yaml.safe_load(R.find("firefox-windows").read_text(encoding="utf-8")), state_root=tmp_path,
                 strict=False)
    tier = {s["id"]: s.get("tier", "every") for s in r.stages}
    assert tier["leak-gate"] == "release" and tier["package-checks"] == tier["post-install"] == "install"
    assert r.by_id["leak-gate"].get("fix") is False


def test_cli_lists_and_routes():
    from fieldkit import cli
    assert emerge_main(["list"]) == 0
    assert cli.main(["emerge", "list"]) == 0
    assert emerge_main(["plan"]) == 2
