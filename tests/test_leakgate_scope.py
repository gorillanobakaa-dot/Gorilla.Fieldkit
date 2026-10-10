"""leakgate-scope (2026-10-10): what changed since the last passed gate, sorted into cannot / can reach the network;
pictures, styles and text are harmless only while they add no web address; everything else counts (fail closed)."""
import json
import subprocess

from fieldkit.leakgate import scope


def _git(w, *a):
    subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)


def _repo(tmp_path):
    w = tmp_path / "tree"
    w.mkdir()
    _git(w, "init", "-q")
    _git(w, "config", "user.email", "t@example.com")
    _git(w, "config", "user.name", "t")
    (w / "a.css").write_text("x { color: red }\n", encoding="utf-8")
    (w / "b.js").write_text("let a = 1;\n", encoding="utf-8")
    _git(w, "add", ".")
    _git(w, "commit", "-q", "-m", "one")
    return w


def _tree(w):
    return subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True).stdout.strip()


def test_style_only_keeps_the_last_pass_and_code_or_a_url_does_not(tmp_path):
    w = _repo(tmp_path)
    t0 = _tree(w)
    (w / "a.css").write_text("x { color: blue }\n", encoding="utf-8")
    (w / "pic.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>\n', encoding="utf-8")
    _git(w, "add", ".")
    _git(w, "commit", "-q", "-m", "look")
    r = scope.run(w, tmp_path, "t", tmp_path / "none.jsonl", from_tree=t0)
    assert not r["can"] and len(r["cannot"]) == 2 and r["verdict"].startswith("the last pass still stands")
    (w / "a.css").write_text("x { background: url(https://tracker.example/p.png) }\n", encoding="utf-8")
    (w / "b.js").write_text("let a = 2;\n", encoding="utf-8")
    _git(w, "add", ".")
    _git(w, "commit", "-q", "-m", "code")
    r = scope.run(w, tmp_path, "t", tmp_path / "none.jsonl", from_tree=t0)
    assert sorted(n for n, _ in r["can"]) == ["a.css", "b.js"] and r["verdict"].startswith("full gate")


def test_the_last_pass_is_found_and_without_one_the_full_gate_runs(tmp_path):
    w = _repo(tmp_path)
    assert scope.run(w, tmp_path, "t", tmp_path / "none.jsonl")["verdict"].startswith("full gate: no passed gate")
    log = tmp_path / "t-20261006-100814.log"
    log.write_bytes("leakgate: build 20261004224302 (157.0)\n".encode("utf-16"))
    (tmp_path / "t-20261006-100814.result.json").write_text(json.dumps(
        {"mode": "release", "exit_code": 0, "log": str(log)}), encoding="utf-8")
    lp = scope.last_pass(tmp_path, "t", tmp_path / "none.jsonl")
    assert lp["build_id"] == "20261004224302" and lp["tree"] == scope.KNOWN_TREES["20261004224302"]
