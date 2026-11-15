"""The Linux leak gate on a GitHub runner (fieldkit/leakgate/ci.py, .github/workflows/leakgate-linux.yml, P27)."""
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

from fieldkit.leakgate import ci

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "leakgate-linux.yml"
POLICIES = ("NETWORK_POLICY", "SOURCE_POLICY", "DEPENDENCY_POLICY")


def fake_inventory(root):
    return {"netwerk/a.cpp": {"apis": ["connect"]}} if (Path(root) / "netwerk" / "a.cpp").is_file() else {}


@pytest.fixture
def world(tmp_path):
    deb = tmp_path / "x_1_amd64.deb"
    deb.write_bytes(b"deb-bytes")
    owner = tmp_path / "owner"
    (owner / "leakgate").mkdir(parents=True)
    (owner / "leakgate" / "allow.json").write_text('{"version": 1, "entries": []}', encoding="utf-8")
    tree = tmp_path / "tree"
    (tree / "netwerk").mkdir(parents=True)
    (tree / "netwerk" / "a.cpp").write_text("connect()", encoding="utf-8")
    (tree / "netwerk" / "unrelated.cpp").write_text("nothing", encoding="utf-8")
    (tree / "Cargo.lock").write_text('[[package]]\nname = "a"\nversion = "1"\n', encoding="utf-8")
    return deb, owner, tree, tmp_path


def packed(world, **kw):
    deb, owner, tree, tmp = world
    return ci.pack(deb, owner, tree, {"version": "160.0"}, tmp / "out", inventory=fake_inventory, **kw)


def test_pack_carries_only_the_files_the_audits_read_and_never_the_deb(world):
    man = packed(world, previous_lock="old lock")
    with tarfile.open(man["context"]) as tf:
        names = sorted(n.lstrip("./") for n in tf.getnames() if n != ".")
    assert "tree/netwerk/a.cpp" in names and "tree/Cargo.lock" in names and "prev/Cargo.lock" in names
    assert "tree/netwerk/unrelated.cpp" not in names, "only inventoried files travel"
    assert not [n for n in names if n.endswith(".deb")]
    assert man["deb"]["sha256"] == ci.sha256(world[0]) and man["owner_files"]["allow.json"]
    assert man["owner_files"]["dispositions.json"] is None, "an absent file is said absent, never invented"


def test_pack_refuses_a_tree_without_cargo_lock(world):
    (world[2] / "Cargo.lock").unlink()
    with pytest.raises(RuntimeError, match="Cargo.lock"):
        packed(world)


def test_unpack_refuses_a_path_outside_the_folder(tmp_path):
    bad = tmp_path / "bad.tar.gz"
    src = tmp_path / "f"
    src.write_text("x", encoding="utf-8")
    with tarfile.open(bad, "w:gz") as tf:
        tf.add(src, arcname="../escape.txt")
    with pytest.raises(RuntimeError, match="refused"):
        ci.unpack(bad, tmp_path / "dest")
    assert not (tmp_path / "escape.txt").exists()


def test_run_refuses_another_binary_and_runs_the_unchanged_gate_on_the_right_one(world):
    deb, owner, tree, tmp = world
    man = packed(world)
    other = tmp / "other.deb"
    other.write_bytes(b"different")
    with pytest.raises(RuntimeError, match="not the same binary"):
        ci.run(man["context"], other, tmp / "run1", gate_run=lambda *a, **k: pytest.fail("the gate must not run"))
    seen = {}

    def gate(path, owner_root, tree_root, upstream, workroot, **kw):
        seen.update(path=path, owner=Path(owner_root), tree=Path(tree_root), kw=kw, up=upstream)
        return {"FINAL_RESULT": "PASS"}, [], {}, {}
    result, _ = ci.run(man["context"], deb, tmp / "run2", gate_run=gate)
    assert result["FINAL_RESULT"] == "PASS" and seen["up"] == {"version": "160.0"}
    assert (seen["owner"] / "leakgate" / "allow.json").is_file() and (seen["tree"] / "Cargo.lock").is_file()
    assert seen["kw"]["n_minus_1_tree"] is None and seen["kw"]["quick"] is True


def test_collect_uploads_small_text_files_never_the_package(tmp_path):
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    (run / "dns.observed").write_text("a", encoding="utf-8")
    (run / "huge.log").write_bytes(b"x" * 5_000_001)
    (run / "x.deb").write_bytes(b"deb")
    art = ci.collect(tmp_path, {"FINAL_RESULT": "FAIL", "ARTIFACTS": str(run)}, [{"kind": "dest"}])
    assert sorted(p.name for p in art.iterdir()) == ["dns.observed", "events.jsonl", "test-results.json"]


def artifact(tmp, deb_sha, **result):
    art = tmp / "art"
    art.mkdir()
    res = {"BUILD": "1", "VERSION": "160.0", "BINARY_SHA256": {"libxul.so": "ab"}, "FINAL_RESULT": "FAIL",
           **{p: "PASS" for p in POLICIES}, "NETWORK_POLICY": "FAIL"}
    res.update(result)
    (art / "test-results.json").write_text(json.dumps(res), encoding="utf-8")
    (art / "runner.json").write_text(json.dumps({"deb_sha256": deb_sha, "run_url": "u"}), encoding="utf-8")
    return art


def test_record_keeps_the_verdict_as_the_gate_gave_it(tmp_path):
    owner = tmp_path / "o"
    owner.mkdir()
    rec = ci.record(artifact(tmp_path, "s1"), "s1", "160.0", POLICIES, owner)
    assert rec["FINAL_RESULT"] == "FAIL" and rec["policies"]["NETWORK_POLICY"] == "FAIL" and rec["ran_on"].startswith("github")
    assert json.loads((owner / "state" / "leakgate_result.json").read_text(encoding="utf-8"))["FINAL_RESULT"] == "FAIL"


@pytest.mark.parametrize("sha,ver,drop,why", [("zzz", "160.0", None, "sha256"), ("s1", "159.0", None, "Firefox 160.0"),
                                              ("s1", "160.0", "SOURCE_POLICY", "never a PASS by omission")])
def test_record_refuses_what_it_cannot_vouch_for(tmp_path, sha, ver, drop, why):
    owner = tmp_path / "o"
    owner.mkdir()
    art = artifact(tmp_path, "s1")
    if drop:
        res = json.loads((art / "test-results.json").read_text(encoding="utf-8"))
        del res[drop]
        (art / "test-results.json").write_text(json.dumps(res), encoding="utf-8")
    with pytest.raises(RuntimeError, match=why):
        ci.record(art, sha, ver, POLICIES, owner)
    assert not (owner / "state" / "leakgate_result.json").exists()


def test_the_verb_is_registered_and_says_its_usage():
    r = subprocess.run([sys.executable, "-m", "fieldkit", "build-harness", "leakgate-ci"], capture_output=True, text=True,
                       cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": str(ROOT)})
    assert "usage: leakgate-ci pack TASK" in (r.stdout + r.stderr)


# ---- the workflow lint ----

@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def test_workflow_runs_by_hand_only_with_least_permissions(wf):
    on = wf.get("on") or wf.get(True)
    assert list(on) == ["workflow_dispatch"], "never on a push or pull request: it reads a private draft release"
    assert wf["permissions"] == {"contents": "write"}
    assert set(on["workflow_dispatch"]["inputs"]) == {"release", "deb"}


def test_workflow_pins_mitmproxy_and_checks_veth_before_the_gate(wf):
    text = WF.read_text(encoding="utf-8")
    assert 'mitmproxy==12.3.1' in text
    steps = wf["jobs"]["gate"]["steps"]
    names = [s.get("name", s.get("uses", "")) for s in steps]
    veth = next(i for i, n in enumerate(names) if "veth" in n)
    gate = next(i for i, n in enumerate(names) if n.startswith("the gate"))
    assert veth < gate and "type veth" in steps[veth]["run"]


def test_workflow_uploads_the_result_only_never_the_package(wf):
    ups = [s for s in wf["jobs"]["gate"]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert len(ups) == 1 and ups[0]["with"]["path"] == "out/artifact/"
    assert not re.search(r"upload-artifact[\s\S]*\.deb", WF.read_text(encoding="utf-8").split("upload-artifact", 1)[1])


def test_workflow_calls_only_a_verb_that_exists(wf):
    from fieldkit import cli
    text = WF.read_text(encoding="utf-8")
    verbs = set(re.findall(r"build-harness (leakgate[\w-]*)", text))
    assert verbs == {"leakgate-ci"}
    src = (ROOT / "fieldkit" / "cli.py").read_text(encoding="utf-8")
    assert all(f'"{v}"' in src for v in verbs) and cli
