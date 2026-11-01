"""fieldkit parity: manifest, compare, align, the preflight check - offline, with fixture manifests
(tests/data/parity) and fake processes, downloads and installs. Nothing here reaches GitHub or another machine."""
import copy
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from fieldkit import parity

DATA = Path(__file__).parent / "data" / "parity"
PIN = {"gorilla-opencode": {"version": "v0.1.154", "repo": "o/r"}}


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_PARITY_STATE", str(tmp_path / "state"))
    monkeypatch.delenv(parity.ENV, raising=False)
    monkeypatch.delenv(parity.ENV_COMPARABLE, raising=False)
    monkeypatch.delenv(parity.ENV_DEFENDER, raising=False)
    return tmp_path / "state"


# ---- manifest -------------------------------------------------------------------------------------------------------
def test_the_fixture_hashes_are_the_digest_and_the_time_taken_does_not_count():
    for name in ("windows.json", "linux.json"):
        m = load(name)
        assert parity.digest(m) == m["hash"]
        later = dict(m, taken="2030-01-01 00:00:00")
        assert parity.digest(later) == m["hash"]


def test_the_digest_does_not_depend_on_key_order_but_on_every_value():
    m = load("windows.json")
    shuffled = dict(reversed(list(m.items())))
    assert parity.digest(shuffled) == m["hash"]
    other = copy.deepcopy(m)
    other["tools"]["gorilla-opencode"]["sha256"] = "0" * 64
    assert parity.digest(other) != m["hash"]


@pytest.mark.parametrize("text,want", [("v0.1.154", "0.1.154"), ("gorilla-size 0.3.0", "0.3.0"),
                                       ("go version go1.24.2 windows/amd64", "1.24.2"), ("Python 3.12.10", "3.12.10"),
                                       ("gh version 2.99.0 (2026-10-01)", "2.99.0"), ("", None),
                                       ("v0.1.149-dev.6c9fc15", "0.1.149-dev.6c9fc15")])
def test_versions_are_read_from_each_tool_s_own_words(text, want):
    assert parity.norm_version(text) == want


def test_a_binary_is_hashed_and_started_once_until_it_changes(tmp_path):
    exe = tmp_path / "tool.exe"
    exe.write_bytes(b"one")
    calls, cache = [], {}
    run = lambda cmd, timeout=30: calls.append(cmd) or (0, "tool v1.2.3", "")    # noqa: E731
    a = parity.probe_tool(exe, ["--version"], cache, run)
    b = parity.probe_tool(exe, ["--version"], cache, run)
    assert a == b == {"version": "1.2.3", "sha256": hashlib.sha256(b"one").hexdigest(), "bytes": 3}
    assert len(calls) == 1
    exe.write_bytes(b"two!")
    assert parity.probe_tool(exe, ["--version"], cache, run)["sha256"] == hashlib.sha256(b"two!").hexdigest()
    assert len(calls) == 2
    assert parity.probe_tool(tmp_path / "absent", ["--version"], cache, run) is None


def test_providers_are_names_only_and_a_key_value_never_reaches_the_manifest(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    d = tmp_path / ".config" / "gorilla-opencode"
    d.mkdir(parents=True)
    secret = "AIzaSECRET-KEY-VALUE-123"
    (d / "config.json").write_text(json.dumps({
        "providers": {"gemini": {"apiKey": secret, "disabled": False}, "openai": {"apiKey": "", "disabled": False},
                      "groq": {"apiKey": "x", "disabled": True}, "antigravity": {"apiKey": "", "disabled": False}},
        "agents": {"coder": {"model": "gemini-2.5-flash"}}, "mcpServers": {"fieldkit": {"command": "x"}}}))
    (d / "antigravity-oauth.json").write_text("{}")
    got = parity.opencode_config(home=tmp_path)
    assert got == {"providers": ["antigravity", "gemini"], "agents": {"coder": "gemini-2.5-flash"}, "mcp": ["fieldkit"],
                   "config": True}
    assert secret not in json.dumps(got)


def test_mandates_in_force_are_asked_once_per_day_and_file_state(monkeypatch, tmp_path):
    from fieldkit.mandate import mandate
    f = tmp_path / "owner-wide.yaml"
    f.write_text("x")
    asked = []
    monkeypatch.setattr(mandate, "mandates", lambda: [f])
    monkeypatch.setattr(mandate, "in_force", lambda: asked.append(1) or [f])
    cache = {}
    assert parity.mandates_in_force(cache) == ["owner-wide"]
    assert parity.mandates_in_force(cache) == ["owner-wide"]
    assert len(asked) == 1
    f.write_text("changed")
    parity.mandates_in_force(cache)
    assert len(asked) == 2


# ---- compare --------------------------------------------------------------------------------------------------------
def test_the_two_fixture_machines_are_the_same_where_it_matters():
    r = parity.compare(load("windows.json"), load("linux.json"), PIN)
    assert r["verdict"] == "SAME"
    assert {d["what"] for d in r["diffs"]} == {"python", "git", "os", "arch"}
    assert all(d["level"] == parity.INFO for d in r["diffs"])


def test_a_different_opencode_version_is_drift_and_names_both_values():
    theirs = load("linux.json")
    theirs["tools"]["gorilla-opencode"]["version"] = "0.1.151"
    r = parity.compare(load("windows.json"), theirs, PIN)
    assert r["verdict"] == "DRIFT"
    rows = {d["what"]: d for d in r["diffs"]}
    assert rows["gorilla-opencode"]["detail"] == "0.1.154 here, 0.1.151 there"
    assert "vaio-b runs 0.1.151, the pin is 0.1.154" in rows["gorilla-opencode pin"]["detail"]


def test_two_machines_on_different_opencode_versions_drift_even_with_no_pin():
    theirs = load("linux.json")
    theirs["tools"]["gorilla-opencode"]["version"] = "0.1.155"
    r = parity.compare(load("windows.json"), theirs, None)
    assert r["verdict"] == "DRIFT" and [d["what"] for d in r["diffs"] if d["level"] == "drift"] == ["gorilla-opencode"]


@pytest.mark.parametrize("path,value,what", [
    (("capabilities", "skills"), ["fieldkit-hog"], "skills installed"),
    (("capabilities", "cards"), ["hog"], "cards"),
    (("capabilities", "opencode", "providers"), [], "providers"),
    (("capabilities", "opencode", "mcp"), [], "mcp servers"),
    (("capabilities", "mandates"), [], "mandates in force"),
    (("fieldkit", "commit"), "ac70fd4", "fieldkit commit"),
    (("fieldkit", "dirty"), True, "fieldkit dirty"),
    (("tools", "gorilla-size", "version"), "0.2.9", "gorilla-size"),
])
def test_every_capability_that_must_match_is_drift(path, value, what):
    theirs = load("linux.json")
    cur = theirs
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = value
    r = parity.compare(load("windows.json"), theirs, PIN)
    assert r["verdict"] == "DRIFT"
    assert [d["level"] for d in r["diffs"] if d["what"] == what] == [parity.DRIFT]


def test_a_list_difference_names_what_is_only_on_each_side():
    theirs = load("linux.json")
    theirs["capabilities"]["skills"] = ["fieldkit-hog", "fieldkit-voice"]
    d = next(x for x in parity.compare(load("windows.json"), theirs)["diffs"] if x["what"] == "skills installed")
    assert d["detail"] == "only here: fieldkit-peers; only there: fieldkit-voice"


def test_a_short_commit_from_an_older_peer_matches_its_full_sha():
    theirs = load("linux.json")
    theirs["fieldkit"]["commit"] = "db67cbd"
    assert not [d for d in parity.compare(load("windows.json"), theirs)["diffs"] if d["what"] == "fieldkit commit"]


def test_an_older_peer_answers_hello_only_and_the_verdict_is_partial_not_drift():
    ts = {"os": "linux", "python": "3.13.5", "git": "2.47.3", "link_commit": "db67cbd"}
    theirs = parity.from_toolset(ts, "vaio-b")
    r = parity.compare(load("windows.json"), theirs, PIN)
    assert r["verdict"] == "PARTIAL"
    assert "gorilla-opencode" in r["missing"] and "skills installed" in r["missing"]
    assert not [d for d in r["diffs"] if d["what"] == "gorilla-opencode pin"]      # not said is not "runs nothing"


def test_peer_manifest_asks_manifest_then_falls_back_to_hello():
    asked = []

    def old_peer(req, device=None):
        asked.append(req["verb"])
        if req["verb"] == "manifest":
            return {"ok": False, "error": "unknown verb 'manifest'"}
        return {"ok": True, "host": "vaio-b", "toolset": {"os": "linux", "python": "3.13.5"}}
    m, via = parity.peer_manifest("vaio", old_peer)
    assert asked == ["manifest", "hello"] and m["partial"] and "hello only" in via
    new = lambda req, device=None: {"ok": True, "manifest": load("linux.json")}     # noqa: E731
    assert parity.peer_manifest("vaio", new) == (load("linux.json"), "manifest")
    down = lambda req, device=None: {"ok": False, "error": "timed out"}             # noqa: E731
    assert parity.peer_manifest("mac", down) == (None, "timed out")


def test_an_absent_mac_is_reported_and_never_fails_the_verdict(monkeypatch):
    from fieldkit.peers import link
    h = {"me": "win", "devices": [{"name": "win"}, {"name": "vaio", "role": "worker"}, {"name": "air", "os": "darwin"}]}
    monkeypatch.setattr(link, "household", lambda: h)

    def call(req, device=None):
        return {"ok": True, "manifest": load("linux.json")} if device == "vaio" else {"ok": False, "error": "no route"}
    mine = load("windows.json")
    rows = parity.compare_household(mine, PIN, call_fn=call)
    assert [(r["device"], r["state"]) for r in rows] == [("vaio", "ANSWERS"), ("air", "ABSENT")]
    assert parity.verdict(mine, PIN, rows) == ("SAME", 0)
    only_air = parity.compare_household(mine, PIN, peer="air", call_fn=call)
    assert parity.verdict(mine, PIN, only_air, named=True) == ("ALONE", 3)       # a NAMED device that is off fails
    assert parity.verdict(mine, PIN, [r for r in rows if r["device"] == "air"]) == ("ALONE", 0)


def test_this_machine_off_the_pin_is_drift_even_alone():
    mine = load("windows.json")
    mine["tools"]["gorilla-opencode"]["version"] = "0.1.152"
    assert parity.verdict(mine, PIN, []) == ("DRIFT", 3)


def test_the_pin_file_is_committed_and_names_a_release():
    p = parity.pin()
    assert p["gorilla-opencode"]["version"].startswith("v") and "/" in p["gorilla-opencode"]["repo"]


# ---- the preflight check --------------------------------------------------------------------------------------------
PROCS = [{"pid": 10, "name": "go.exe", "cpu": 99.0, "cwd": None, "exe": None},
         {"pid": 11, "name": "python.exe", "cpu": 1.0, "cwd": "/w/repo/sub", "exe": "/py"},
         {"pid": 12, "name": "gorilla-opencode.exe", "cpu": 0.0, "cwd": "/elsewhere", "exe": "/bin/gorilla-opencode"},
         {"pid": 13, "name": "python.exe", "cpu": 95.0, "cwd": "/w/repo", "exe": "/py"},
         {"pid": 14, "name": "node.exe", "cpu": 2.0, "cwd": "/w/repository-two", "exe": "/node"},
         {"pid": 0, "name": "system idle process", "cpu": 800.0, "cwd": None, "exe": None}]


def test_shared_ground_names_every_other_session_s_hold_and_never_this_one():
    lines = parity.shared_ground(PROCS, mine={13}, checkout="/w/repo", binary="/bin/gorilla-opencode")
    assert lines == ["go.exe pid 10 uses 99% of a core",
                     f"python.exe pid 11 works in this checkout ({'/w/repo/sub'})",
                     "gorilla-opencode.exe pid 12 runs the pinned binary"]


def test_a_quiet_machine_has_no_shared_ground():
    quiet = [dict(p, cpu=1.0, cwd="/other") for p in PROCS if p["pid"] in (10, 14)]
    assert parity.shared_ground(quiet, mine=set(), checkout="/w/repo", binary="/bin/x") == []


def test_the_preflight_is_ok_on_the_pin_with_clear_ground_and_stamps_the_hash(monkeypatch):
    import os
    m = load("windows.json")
    st, line = parity.preflight_check({"kind": "test"}, m=m, p=PIN, ground=[], models=[])
    assert st == "ok" and m["hash"][:12] in line
    assert os.environ[parity.ENV] == m["hash"]


def test_the_preflight_warns_for_a_test_and_refuses_a_bench():
    m = load("windows.json")
    m["tools"]["gorilla-opencode"]["version"] = "0.1.152"
    st, line = parity.preflight_check({"kind": "test"}, m=m, p=PIN, ground=["go.exe pid 10 uses 99% of a core"],
                                      models=["qwen3.6"])
    assert st == "warn"
    assert "runs 0.1.152, the pin is 0.1.154" in line and "go.exe pid 10" in line
    assert "model qwen3.6 is loaded and this run did not load it" in line
    st, line = parity.preflight_check({"kind": "bench"}, m=load("windows.json"), p=PIN, ground=[], models=[],
                                      quiet=["not quiet: RAM 91%"])
    assert st == "stop" and "REFUSED for a bench" in line and "RAM 91%" in line


def test_a_model_the_run_loaded_itself_is_not_held_against_it(monkeypatch):
    from fieldkit import hog
    from fieldkit.hog import probe
    monkeypatch.setattr(probe, "processes", lambda top=400: ([], [], []))
    monkeypatch.setattr(probe, "lms", lambda rows, cfg: {"models": [{"id": "qwen"}, {"id": "gemma"}]})
    monkeypatch.setattr(hog, "config", lambda: {})
    assert parity.loaded_models({"models": ["qwen"]}) == ["gemma"]


def test_the_preflight_runs_the_parity_check_on_every_guarded_run():
    from fieldkit.core import preflight
    assert "fieldkit.parity:preflight_check" in preflight.STEPS


def test_testcost_writes_the_manifest_hash_into_each_result(monkeypatch, tmp_path):
    from fieldkit import testcost
    rows = []
    monkeypatch.setattr(testcost, "_append", lambda name, row: rows.append(row))
    monkeypatch.setenv(parity.ENV, "abc123")
    assert testcost.record("suite", "pass", 1.0)["manifest"] == "abc123"
    monkeypatch.delenv(parity.ENV)
    assert "manifest" not in testcost.record("suite", "pass", 1.0)


def test_the_link_answers_manifest_on_any_transport_like_hello(monkeypatch, tmp_path):
    from fieldkit.peers import link
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    monkeypatch.setattr(link, "_log", lambda text: None)
    monkeypatch.setattr(parity, "manifest", lambda: {"hash": "h"})
    k = b"k" * 32
    assert "manifest" not in link.JOB_VERBS
    r = link.handle(link.sign({"verb": "manifest"}, k), link.Verifier(k), via="lan")
    assert r == {"ok": True, "manifest": {"hash": "h"}}


# ---- align ----------------------------------------------------------------------------------------------------------
def test_the_release_asset_for_each_system():
    assert parity.asset_for("windows") == "windows"
    assert parity.asset_for("linux", which=lambda n: n == "dpkg") == "deb"
    assert parity.asset_for("linux", which=lambda n: n == "rpm") == "rpm"
    assert parity.asset_for("linux", which=lambda n: None) == "linux"
    assert parity.ASSETS["deb"].format(v="0.1.154") == "gorilla-opencode_0.1.154_amd64.deb"


def _fake_gh(tmp_path, body=b"NEW", listed=None, digest=None):
    good = hashlib.sha256(body).hexdigest()

    def run(cmd, timeout=30):
        if cmd[:3] == ["gh", "release", "view"]:
            return 0, json.dumps({"assets": [{"name": "gorilla-opencode.exe", "digest": "sha256:" + (digest or good)},
                                             {"name": "SHA256SUMS-v0.1.154.txt"}]}), ""
        if cmd[:3] == ["gh", "release", "download"]:
            d = Path(cmd[cmd.index("-D") + 1])
            (d / "gorilla-opencode.exe").write_bytes(body)
            (d / "SHA256SUMS-v0.1.154.txt").write_text(f"{listed or good}  gorilla-opencode.exe\n")
            return 0, "", ""
        return 1, "", "unexpected"
    return run, good


def test_fetch_checks_sha256sums_and_github_s_digest(tmp_path):
    run, good = _fake_gh(tmp_path)
    f, sha = parity.fetch("v0.1.154", "o/r", "gorilla-opencode.exe", tmp_path / "dl", run=run)
    assert sha == good and f.read_bytes() == b"NEW"
    run, _ = _fake_gh(tmp_path, listed="0" * 64)
    with pytest.raises(RuntimeError, match="is not the SHA256SUMS"):
        parity.fetch("v0.1.154", "o/r", "gorilla-opencode.exe", tmp_path / "dl2", run=run)
    run, _ = _fake_gh(tmp_path, digest="1" * 64)
    with pytest.raises(RuntimeError, match="GitHub's digest"):
        parity.fetch("v0.1.154", "o/r", "gorilla-opencode.exe", tmp_path / "dl3", run=run)
    with pytest.raises(RuntimeError, match="not empty"):
        parity.fetch("v0.1.154", "o/r", "gorilla-opencode.exe", tmp_path / "dl", run=run)


def test_install_keeps_the_old_binary_and_proves_the_new_one(tmp_path):
    dest = tmp_path / "gorilla-opencode.exe"
    dest.write_bytes(b"OLD")
    new = tmp_path / "new.exe"
    new.write_bytes(b"NEW")
    sha = hashlib.sha256(b"NEW").hexdigest()
    run = lambda cmd, timeout=30: (0, "v0.1.154", "")        # noqa: E731
    r = parity.install_windows(new, dest, "0.1.152", sha, run=run, in_use=lambda p: [])
    assert r["ok"] and r["version"] == "0.1.154" and dest.read_bytes() == b"NEW"
    assert (tmp_path / "gorilla-opencode.exe.bak-v0.1.152").read_bytes() == b"OLD"


def test_install_puts_the_old_binary_back_when_the_bytes_are_not_the_release_s(tmp_path):
    dest = tmp_path / "gorilla-opencode.exe"
    dest.write_bytes(b"OLD")
    new = tmp_path / "new.exe"
    new.write_bytes(b"TAMPERED")
    run = lambda cmd, timeout=30: (0, "v0.1.154", "")        # noqa: E731
    r = parity.install_windows(new, dest, "0.1.152", hashlib.sha256(b"NEW").hexdigest(), run=run, in_use=lambda p: [])
    assert not r["ok"] and dest.read_bytes() == b"OLD"


def test_install_is_refused_while_the_binary_runs(tmp_path):
    dest = tmp_path / "gorilla-opencode.exe"
    dest.write_bytes(b"OLD")
    r = parity.install_windows(tmp_path / "x", dest, "0.1.152", "0", in_use=lambda p: [4242])
    assert not r["ok"] and "pid 4242" in r["why"] and dest.read_bytes() == b"OLD"


def test_install_puts_the_old_binary_back_when_the_new_one_does_not_start(tmp_path):
    dest = tmp_path / "gorilla-opencode.exe"
    dest.write_bytes(b"OLD")
    new = tmp_path / "new.exe"
    new.write_bytes(b"NEW")
    run = lambda cmd, timeout=30: (1, "", "not a valid application")       # noqa: E731
    r = parity.install_windows(new, dest, "0.1.152", hashlib.sha256(b"NEW").hexdigest(), run=run,
                               in_use=lambda p: [])
    assert not r["ok"] and "old binary is back" in r["why"] and dest.read_bytes() == b"OLD"


def test_align_plans_without_apply_and_says_aligned_when_on_the_pin(monkeypatch, tmp_path):
    exe = tmp_path / "gorilla-opencode.exe"
    exe.write_bytes(b"CUR")
    monkeypatch.setenv("FIELDKIT_GORILLA_OPENCODE", str(exe))
    monkeypatch.setattr(parity, "pin", lambda path=None: PIN)
    old = lambda cmd, timeout=30: (0, "v0.1.152", "")        # noqa: E731
    assert parity.align(sysname="windows", run=old)["state"] == "PLAN"
    cur = lambda cmd, timeout=30: (0, "v0.1.154", "")        # noqa: E731
    assert parity.align(sysname="windows", run=cur)["state"] == "ALIGNED"


def test_align_apply_on_windows_fetches_installs_and_records(monkeypatch, tmp_path, state):
    exe = tmp_path / "gorilla-opencode.exe"
    exe.write_bytes(b"CUR")
    monkeypatch.setenv("FIELDKIT_GORILLA_OPENCODE", str(exe))
    monkeypatch.setattr(parity, "pin", lambda path=None: PIN)
    fetched = []

    def fetch_fn(tag, repo, name, folder, run=None):
        fetched.append((tag, repo, name))
        folder.mkdir(parents=True)
        (folder / name).write_bytes(b"NEW")
        return folder / name, "s" * 64
    inst = lambda new, dest, old, sha, run=None: {"ok": True, "version": "0.1.154", "backup": str(dest) + ".bak",  # noqa: E731
                                                  "path": str(dest)}
    r = parity.align(apply=True, sysname="windows", run=lambda c, timeout=30: (0, "v0.1.152", ""),
                     fetch_fn=fetch_fn, install_fn=inst)
    assert r["state"] == "DONE" and fetched == [("v0.1.154", "o/r", "gorilla-opencode.exe")]
    assert json.loads((state / "align.jsonl").read_text().splitlines()[-1])["state"] == "DONE"


class _Resp:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        d, self.data = self.data, b""
        return d


def _fake_web(body=b"NEWBIN", listed=None, name="gorilla-opencode-v0.1.154-linux-amd64"):
    good = hashlib.sha256(body).hexdigest()
    seen = []

    def urlopen(url, timeout=0):
        seen.append(url)
        if url.endswith("SHA256SUMS-v0.1.154.txt"):
            return _Resp(f"{listed or good}  {name}\n".encode())
        if url.endswith("/" + name):
            return _Resp(body)
        raise OSError("404")
    return urlopen, good, seen


def test_public_fetch_uses_the_public_url_and_checks_sha256sums(tmp_path):
    web, good, seen = _fake_web()
    f, sha = parity.fetch_public("v0.1.154", "o/r", "gorilla-opencode-v0.1.154-linux-amd64", tmp_path / "a", urlopen=web)
    assert sha == good and f.read_bytes() == b"NEWBIN"
    assert all(u.startswith("https://github.com/o/r/releases/download/v0.1.154/") for u in seen)
    web, _, _ = _fake_web(listed="0" * 64)
    with pytest.raises(RuntimeError, match="is not the SHA256SUMS"):
        parity.fetch_public("v0.1.154", "o/r", "gorilla-opencode-v0.1.154-linux-amd64", tmp_path / "b", urlopen=web)


def test_a_checksum_mismatch_refuses_to_install(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("FIELDKIT_GORILLA_OPENCODE", raising=False)
    monkeypatch.setattr(parity, "pin", lambda path=None: PIN)
    monkeypatch.setattr(parity, "opencode_path", lambda: None)
    monkeypatch.setattr(parity, "state_dir", lambda: tmp_path / "state")
    web, _, _ = _fake_web(listed="0" * 64)
    monkeypatch.setattr("urllib.request.urlopen", web)
    installed = []
    r = parity.align(apply=True, sysname="linux", run=lambda c, timeout=30: (1, "", ""),
                     user_install_fn=lambda *a, **k: installed.append(a) or {"ok": True})
    assert r["state"] == "FAILED" and "is not the SHA256SUMS" in r["why"]
    assert not installed and not (home / ".local" / "bin" / "gorilla-opencode").exists()


def test_align_on_linux_installs_per_user_without_gh_or_sudo(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(parity, "pin", lambda path=None: PIN)
    monkeypatch.setattr(parity, "opencode_path", lambda: None)
    monkeypatch.setattr(parity, "asset_for", lambda s=None: "deb")
    monkeypatch.setattr(parity, "state_dir", lambda: tmp_path / "state")
    web, good, seen = _fake_web()
    monkeypatch.setattr("urllib.request.urlopen", web)
    ran = []

    def run(c, timeout=30):
        ran.append(c)
        return 0, "v0.1.154", ""
    r = parity.align(apply=True, sysname="linux", run=run)
    dest = home / ".local" / "bin" / "gorilla-opencode"
    assert r["state"] == "DONE" and r["asset"] == "gorilla-opencode-v0.1.154-linux-amd64"
    assert dest.read_bytes() == b"NEWBIN" and r["sha256"] == good
    flat = [" ".join(map(str, c)) for c in ran]
    assert not any(w in f.split() for f in flat for w in ("gh", "sudo", "apt", "dnf"))
    assert all("gh" not in u.split("/")[2:3] for u in seen)


def test_the_align_code_never_names_gh_or_sudo_as_a_command():
    src = inspect.getsource(parity.align) + inspect.getsource(parity.fetch_public) + inspect.getsource(parity.install_user)
    assert '"gh"' not in src and "'gh'" not in src and 'sudo ' not in src.replace("(no sudo", "")


def test_install_user_unpacks_the_mac_zip_and_keeps_the_old_binary(tmp_path):
    import zipfile
    z = tmp_path / "m.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("gorilla-opencode-0.1.154/gorilla-opencode", b"MACBIN")
    dest = tmp_path / "bin" / "gorilla-opencode"
    dest.parent.mkdir()
    dest.write_bytes(b"OLD")
    r = parity.install_user(z, dest, "0.1.152", "x", run=lambda c, timeout=30: (0, "v0.1.154", ""))
    assert r["ok"] and dest.read_bytes() == b"MACBIN" and Path(r["backup"]).read_bytes() == b"OLD"


def test_the_command_compares_with_a_saved_manifest(monkeypatch, capsys):
    monkeypatch.setattr(parity, "manifest", lambda **k: load("windows.json"))
    monkeypatch.setattr(parity, "pin", lambda path=None: PIN)
    assert parity.main(["compare", "--with", str(DATA / "linux.json")]) == 0
    out = capsys.readouterr().out
    assert "VERDICT  SAME" in out and "INFO  python: 3.12.10 here, 3.13.5 there" in out


# ---- machine state: Defender ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("out,rc,want", [("True", 0, True), ("False\r", 0, False), ("", 1, None),
                                         ("Get-MpComputerStatus : access denied", 0, None)])
def test_defender_real_time_is_read_never_guessed(out, rc, want):
    got = parity.defender(run=lambda cmd, timeout=30: (rc, out, ""), sysname="windows", excl=None if want else False)
    assert got["realtime"] is want


def test_no_defender_outside_windows():
    called = []
    got = parity.defender(run=lambda cmd, timeout=30: called.append(cmd) or (0, "True", ""), sysname="linux")
    assert got == {"realtime": None, "scratch_exclusion": None} and not called


def test_a_state_off_the_baseline_is_not_comparable(state):
    assert parity.not_comparable({"realtime": True}, None) == []
    parity.set_baseline({"realtime": False, "scratch_exclusion": None})
    base = parity.baseline()
    assert parity.not_comparable({"realtime": False, "scratch_exclusion": None}, base) == []
    lines = parity.not_comparable({"realtime": True, "scratch_exclusion": None}, base)
    assert lines == ["Defender realtime on, baseline off: NOT COMPARABLE with baseline results"]


def test_the_preflight_flags_a_run_off_the_baseline_and_testcost_records_it(monkeypatch):
    import os
    from fieldkit import testcost
    m = load("windows.json")
    m["machine"]["defender"] = {"realtime": True, "scratch_exclusion": None}
    base = {"defender": {"realtime": False, "scratch_exclusion": None}}
    st, line = parity.preflight_check({"kind": "test"}, m=m, p=PIN, ground=[], models=[], base=base)
    assert st == "warn" and "NOT COMPARABLE" in line and os.environ[parity.ENV_COMPARABLE] == "0"
    rows = []
    monkeypatch.setattr(testcost, "_append", lambda name, row: rows.append(row))
    assert testcost.record("suite", "pass", 1.0)["comparable"] is False
    st, _ = parity.preflight_check({"kind": "bench"}, m=m, p=PIN, ground=[], models=[], quiet=[], base=base)
    assert st == "stop"
    m["machine"]["defender"]["realtime"] = False
    st, _ = parity.preflight_check({"kind": "test"}, m=m, p=PIN, ground=[], models=[], base=base)
    assert st == "ok" and os.environ[parity.ENV_COMPARABLE] == "1"
    row = testcost.record("suite", "pass", 1.0)
    assert "comparable" not in row and row["defender"] == {"realtime": False, "scratch_exclusion": None}


def test_without_a_baseline_the_state_is_recorded_and_said(monkeypatch):
    from fieldkit import testcost
    m = load("windows.json")
    m["machine"]["defender"] = {"realtime": False, "scratch_exclusion": None}
    st, line = parity.preflight_check({"kind": "bench"}, m=m, p=PIN, ground=[], models=[], quiet=[], base=None)
    assert st == "ok" and "Defender real-time off, no baseline" in line
    rows = []
    monkeypatch.setattr(testcost, "_append", lambda name, row: rows.append(row))
    row = testcost.record("suite", "pass", 1.0)
    assert row["defender"]["realtime"] is False and "comparable" not in row


def test_two_copies_of_the_same_mandate_are_drift():
    mine, theirs = load("windows.json"), load("linux.json")
    mine["capabilities"]["mandate_files"] = {"owner-wide": {"sha256": "1bc7be7e" + "0" * 56, "expires": "2026-11-02"}}
    theirs["capabilities"]["mandate_files"] = {"owner-wide": {"sha256": "ea2c3f75" + "0" * 56, "expires": "2026-11-01"}}
    r = parity.compare(mine, theirs, PIN)
    assert r["verdict"] == "DRIFT" and [d["what"] for d in r["diffs"] if d["level"] == "drift"] == ["mandate files"]


def test_mandate_files_are_hashed(monkeypatch, tmp_path):
    import hashlib
    from fieldkit.mandate import mandate
    f = tmp_path / "owner-wide.yaml"
    f.write_bytes(b"sealed")
    monkeypatch.setattr(mandate, "mandates", lambda: [f])
    monkeypatch.setattr(mandate, "load", lambda p: {"expires": "2026-11-02"})
    assert parity.mandate_files() == {"owner-wide": {"sha256": hashlib.sha256(b"sealed").hexdigest(),
                                                     "expires": "2026-11-02"}}


def test_mandate_share_sends_every_file_and_says_when_the_peer_is_away(tmp_path, monkeypatch):
    from fieldkit.mandate import mandate
    f = tmp_path / "owner-wide.yaml"
    f.write_text("m")
    monkeypatch.setattr(mandate, "_seal_path", lambda p: tmp_path / "owner-wide.seal.json")
    monkeypatch.setattr(mandate, "_key_paths", lambda: (None, tmp_path / "absent.pub"))
    monkeypatch.setattr(mandate, "_review_paths", lambda: (None, tmp_path / "absent2.pub"))
    (tmp_path / "owner-wide.seal.json").write_text("{}")
    # 2026-10-26: sent is not enough, the far listener must install the pair (mandate-receive)
    installed = lambda req: {"ok": True, "message": "INSTALLED"}          # noqa: E731
    ok, sent = mandate.share(f, put=lambda p: {"ok": True, "saved": [str(p)]}, call=installed)
    assert ok and sent == ["owner-wide.yaml: ok", "owner-wide.seal.json: ok", "installed: INSTALLED"]
    ok, sent = mandate.share(f, put=lambda p: {"ok": True}, call=installed)
    assert not ok                                                         # nothing says where the pair landed
    ok, sent = mandate.share(f, put=lambda p: {"ok": False, "error": "no route to 10.77.0.2"})
    assert not ok and sent[0] == "owner-wide.yaml: no route to 10.77.0.2"


# ---- per-target toolchain pins ------------------------------------------------------------------------------------
GO_PIN = dict(PIN, go={"targets": {"windows": "1.27.0", "linux": "1.24.4"}, "align": "report"})


def test_go_differing_between_machines_is_drift_without_a_pin():
    theirs = load("linux.json")
    theirs["tools"]["go"]["version"] = "1.24.4"
    r = parity.compare(load("windows.json"), theirs, PIN)
    assert r["verdict"] == "DRIFT" and [d["what"] for d in r["diffs"] if d["level"] == "drift"] == ["go"]


def test_a_per_target_go_pin_makes_the_difference_expected_and_judges_each_machine():
    mine, theirs = load("windows.json"), load("linux.json")
    theirs["tools"]["go"]["version"] = "1.24.4"
    r = parity.compare(mine, theirs, GO_PIN)
    assert r["verdict"] == "SAME"
    assert [d["level"] for d in r["diffs"] if d["what"] == "go"] == ["info"]
    theirs["tools"]["go"]["version"] = "1.25.0"
    r = parity.compare(mine, theirs, GO_PIN)
    rows = [d for d in r["diffs"] if d["what"] == "go pin"]
    assert r["verdict"] == "DRIFT" and "the pin is 1.24.4: install go 1.24.4 by hand (report only)" in rows[0]["detail"]


def test_a_pin_prefix_matches_the_patch_level_and_nothing_else():
    assert parity._matches("1.24.4", "1.24") and parity._matches("1.24.4", "1.24.4")
    assert not parity._matches("1.240.0", "1.24") and not parity._matches(None, "1.24")


def test_a_target_the_pin_does_not_name_is_not_judged():
    m = load("windows.json")
    m["machine"]["os"] = "darwin"
    assert not [d for d in parity.against_pin(m, GO_PIN) if d["what"] == "go pin"]


def test_the_committed_pin_keeps_go_per_target_and_report_only():
    go = parity.pin()["go"]
    assert go["align"] == "report" and set(go["targets"]) >= {"windows", "linux"}


def test_card_ids_are_read_once_until_a_card_file_changes(tmp_path, monkeypatch):
    from fieldkit.desk import platformcheck
    f = tmp_path / "tools.yaml"
    f.write_text("tools:\n- id: hog\n", encoding="utf-8")
    calls = []
    real = platformcheck.cards
    monkeypatch.setattr(platformcheck, "cards", lambda files=None: calls.append(1) or real(files))
    cache = {}
    assert parity.card_ids(cache, [f]) == (["hog"], [])
    assert parity.card_ids(cache, [f]) == (["hog"], []) and len(calls) == 1
    f.write_text("tools:\n- id: hog\n- id: size\n", encoding="utf-8")
    assert parity.card_ids(cache, [f]) == (["hog", "size"], []) and len(calls) == 2


def test_a_primed_cpu_sample_is_used_once_and_never_shortens_the_window(monkeypatch):
    slept = []
    monkeypatch.setattr(parity.time, "sleep", slept.append)
    monkeypatch.setattr(parity, "_start_sample", lambda: [])
    clock = [100.0]
    monkeypatch.setattr(parity.time, "monotonic", lambda: clock[0])
    parity.preflight_prime()
    clock[0] = 100.2                                   # 0.2 s of other checks ran since the first reading
    parity.sample_procs(0.5)
    assert slept == [pytest.approx(0.3)]               # the window is still 0.5 s in all
    parity.sample_procs(0.5)                           # the prime is spent: a full window of its own
    assert slept[-1] == pytest.approx(0.5)
    parity.preflight_prime()
    clock[0] = 100.2 + parity.PRIME_MAX_S + 1          # a stale first reading is taken again
    parity.sample_procs(0.5)
    assert slept[-1] == pytest.approx(0.5)


def test_one_process_walk_feeds_both_the_shared_ground_and_the_model_check(monkeypatch):
    walks = []
    monkeypatch.setattr(parity, "sample_procs", lambda sample_s=0.5: walks.append(1) or [])
    monkeypatch.setattr(parity, "manifest", lambda: load("windows.json"))
    seen = {}
    monkeypatch.setattr(parity, "loaded_models", lambda ctx, rows=None: seen.setdefault("rows", rows) and [])
    parity.preflight_check({"kind": "build"}, p=PIN, quiet=[], base=None)
    assert walks == [1] and seen["rows"] == []
