"""fieldkit pycache: Python compiled once on the hot path (plan P9, idea I-17, 2026-11-12).

The stale detector, the warm, the bytecode .pth, the audit's exit codes, the cold-vs-warm timing, the integration row
"hot path compiled", and the worktree hooks that warm a new tree. Every rule here has a mutation in
tests/pycache_mutations.yaml.
"""
import json
import os
import py_compile
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fieldkit.integration import core as icore
from fieldkit.lifecycle.worktrees import make as real_make                     # conftest stubs worktrees.make per test
from fieldkit.pycache import audit as audit_mod, core, profile as profile_mod

real_warm_background = core.warm_background            # conftest stubs the side effects per test: keep the real ones
real_ensure_pth = core.ensure_pth


@pytest.fixture
def prefix(tmp_path, monkeypatch):
    """A bytecode cache of the test's own, for this process and its children."""
    p = str(tmp_path / "pyc")
    monkeypatch.setattr(sys, "pycache_prefix", p)
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", p)
    return p


def fake_repo(tmp_path, body="VALUE = 1\n"):
    r = tmp_path / "repo"
    (r / "fieldkit").mkdir(parents=True)
    (r / "fieldkit" / "__init__.py").write_text("", encoding="utf-8")
    (r / "fieldkit" / "m.py").write_text(body, encoding="utf-8")
    return r


def test_state_missing_then_fresh_then_stale_when_the_source_changes(tmp_path, prefix):
    src = tmp_path / "a.py"
    src.write_text("x = 1\n", encoding="utf-8")
    assert core.state(src) == "missing"
    py_compile.compile(str(src), cfile=core.cache_file(src))
    assert core.state(src) == "fresh"
    src.write_text("x = 22222\n", encoding="utf-8")          # a different size: stale whatever the clock says
    assert core.state(src) == "stale"
    Path(core.cache_file(src)).write_bytes(b"junk")
    assert core.state(src) == "stale"                        # a cache that is not a .pyc is never fresh
    py_compile.compile(str(src), cfile=core.cache_file(src))
    pyc = Path(core.cache_file(src))
    pyc.write_bytes(b"\x00\x00\x00\x00" + pyc.read_bytes()[4:])  # a full header with another Python's magic number
    assert core.state(src) == "stale"


def test_state_follows_the_mtime_of_the_source(tmp_path, prefix):
    src = tmp_path / "a.py"
    src.write_text("x = 1\n", encoding="utf-8")
    py_compile.compile(str(src), cfile=core.cache_file(src))
    os.utime(src, (src.stat().st_atime, src.stat().st_mtime + 100))   # same size, newer source
    assert core.state(src) == "stale"


def test_warm_compiles_everything_and_is_idempotent(tmp_path, prefix):
    r = fake_repo(tmp_path)
    before, after, ok = core.warm(r, workers=1)
    assert ok and before == {"fresh": 0, "stale": 0, "missing": 2} and after == {"fresh": 2, "stale": 0, "missing": 0}
    cache = Path(core.cache_file(r / "fieldkit" / "m.py"))
    stamp = cache.stat().st_mtime_ns
    time.sleep(0.02)
    before, after, _ = core.warm(r, workers=1)
    assert before["fresh"] == 2 and cache.stat().st_mtime_ns == stamp   # nothing recompiled


def test_pth_install_status_and_a_tampered_file(tmp_path):
    cfg = core.hotpath()
    assert core.pth_status(cfg, tmp_path)[0] is False
    p = core.pth_install(cfg, tmp_path)
    assert p.name == "zz-fieldkit-bytecode.pth" and core.pth_status(cfg, tmp_path)[0] is True
    p.write_text("import sys\n", encoding="utf-8")
    assert core.pth_status(cfg, tmp_path)[0] is False        # a .pth that no longer turns writing on is not installed


def test_the_pth_really_defeats_pythondontwritebytecode(tmp_path):
    """The proof Zero ran by hand: with the env var set, an interpreter that loads the .pth writes its .pyc."""
    site = tmp_path / "site"
    site.mkdir()
    core.pth_install(core.hotpath(), site)
    (tmp_path / "mod.py").write_text("V = 1\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPYCACHEPREFIX"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    code = f"import site,sys;site.addsitedir({str(site)!r});sys.path.insert(0,{str(tmp_path)!r});import mod"
    subprocess.run([sys.executable, "-c", code], env=env, check=True, cwd=str(tmp_path), timeout=60)
    assert list((tmp_path / "__pycache__").glob("mod*.pyc"))


def test_audit_fails_on_a_cold_cache_and_passes_once_warm(tmp_path, prefix, capsys):
    repo = core.REPO
    cold = audit_mod.audit(repo, need_pth=False)
    assert cold["hot_files"] > 5 and any("not compiled" in f for f in cold["findings"])
    assert audit_mod.main(["--repo", str(repo), "--no-pth", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["verdict"] == "FINDINGS"
    core.warm(repo, workers=1)
    warm = audit_mod.audit(repo, need_pth=False)
    assert warm["findings"] == [] and warm["hot"]["stale"] == 0 and warm["hot"]["missing"] == 0
    assert audit_mod.main(["--repo", str(repo), "--no-pth", "--json"]) == 0


def test_audit_names_a_missing_pth_and_a_module_that_does_not_import(tmp_path, prefix, monkeypatch):
    monkeypatch.setattr(core, "pth_dir", lambda: tmp_path)
    d = audit_mod.audit(core.REPO, need_pth=True)
    assert any("bytecode .pth is missing" in f for f in d["findings"])
    r = fake_repo(tmp_path, "raise RuntimeError('boom')\n")
    monkeypatch.setattr(core, "hotpath", lambda: {"modules": [{"module": "fieldkit.m"}], "budget": {}, "timing_runs": 1,
                                                  "pth": core.hotpath.__wrapped__["pth"] if hasattr(core.hotpath, "__wrapped__") else
                                                  {"file": "zz.pth", "marker": "x", "line": "x"}})
    d = audit_mod.audit(r, need_pth=False)
    assert any("does not import" in f and "boom" in f for f in d["findings"])


def test_timing_measures_cold_and_warm_and_the_budget_is_enforced(tmp_path, monkeypatch):
    r = fake_repo(tmp_path)
    t = core.timing(r, ["fieldkit.m"], 1, tmp_root=str(tmp_path))
    cold, warm = t["fieldkit.m"]
    assert cold > 0 and warm > 0
    assert not list((r / "fieldkit").rglob("__pycache__"))     # the repo itself is never touched: the copy is timed
    monkeypatch.setattr(core, "timing", lambda *a, **k: {"fieldkit.m": (900.0, 100.0)})
    monkeypatch.setattr(core, "closure", lambda *a, **k: ([], {}))
    monkeypatch.setattr(core, "hotpath", lambda: {"modules": [{"module": "fieldkit.m"}], "timing_runs": 1,
                                                  "budget": {"extra_cold_ms": 500, "warm_ms": 50},
                                                  "pth": {"file": "zz.pth", "marker": "x", "line": "x"}})
    d = audit_mod.audit(r, with_timing=True, need_pth=False)
    assert any("extra when cold" in f for f in d["findings"]) and any("warm (budget 50)" in f for f in d["findings"])
    monkeypatch.setattr(core, "timing", lambda *a, **k: {"fieldkit.m": (300.0, 100.0)})
    monkeypatch.setattr(core, "hotpath", lambda: {"modules": [{"module": "fieldkit.m"}], "timing_runs": 1,
                                                  "budget": {"extra_cold_ms": 500, "warm_ms": 500},
                                                  "pth": {"file": "zz.pth", "marker": "x", "line": "x"}})
    assert audit_mod.audit(r, with_timing=True, need_pth=False)["findings"] == []


def test_env_origin(monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    assert core.env_origin() is None
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    assert "pid" in core.env_origin() or "environment" in core.env_origin() or "process tree" in core.env_origin()


def test_the_integration_row_hot_path_compiled(tmp_path):
    assert icore.hotpath_gap(icore.Repo(core.REPO), "w") is None          # the real repo is clean
    assert "missing" in icore.hotpath_gap(icore.Repo(tmp_path), "w")
    (tmp_path / "fieldkit" / "pycache").mkdir(parents=True)
    (tmp_path / "fieldkit" / "pycache" / "hotpath.yaml").write_text(
        "modules:\n  - {module: fieldkit.ok}\n  - {module: fieldkit.broken}\n  - {module: fieldkit.gone}\n", encoding="utf-8")
    (tmp_path / "fieldkit" / "ok.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "fieldkit" / "broken.py").write_text("def (:\n", encoding="utf-8")
    msg = icore.hotpath_gap(icore.Repo(tmp_path), "w")
    assert "fieldkit.broken (SyntaxError)" in msg and "fieldkit.gone (no source file)" in msg and "fieldkit.ok" not in msg


def test_every_hot_module_exists_in_the_repo():
    for m in core.hotpath()["modules"]:
        rel = m["module"].replace(".", "/")
        assert (core.REPO / (rel + ".py")).is_file() or (core.REPO / rel / "__init__.py").is_file(), m


def test_warm_background_is_fail_soft_and_compiles(tmp_path):
    assert real_warm_background(tmp_path / "nowhere") is False
    assert real_warm_background(tmp_path) is False             # a real folder that is not a Fieldkit tree: no process
    r = fake_repo(tmp_path)
    env_prefix = str(tmp_path / "bg")
    os.environ["PYTHONPYCACHEPREFIX"] = env_prefix
    try:
        assert real_warm_background(r) is True
        deadline = time.time() + 30
        while time.time() < deadline and not list(Path(env_prefix).rglob("m.*.pyc")):
            time.sleep(0.1)
    finally:
        del os.environ["PYTHONPYCACHEPREFIX"]
    assert list(Path(env_prefix).rglob("m.*.pyc"))


def test_a_new_job_worktree_is_warmed(tmp_path, monkeypatch):
    repo = fake_repo(tmp_path)

    def git(*a):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                       capture_output=True)
    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "x")
    seen = []
    monkeypatch.setattr(core, "warm_background", lambda p: seen.append(str(p)))
    path, why = real_make(repo, "pyc-test", root=tmp_path / "wt", fetch=False)
    assert path is not None, why
    assert seen == [str(path)]


def test_profile_of_an_import_and_of_a_function(capsys):
    assert profile_mod.main(["fieldkit.pycache.out", "--top", "3", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["lines"]
    assert profile_mod.main(["fieldkit.pycache.core:hotpath", "--top", "3", "--json"]) == 0
    assert profile_mod.main(["fieldkit.no_such_module:f", "--json"]) == 1


def test_the_cli_knows_the_verbs():
    r = subprocess.run([sys.executable, "-m", "fieldkit", "pycache", "--help"], capture_output=True, text=True,
                       cwd=str(core.REPO), timeout=60)
    assert r.returncode == 0 and "audit" in r.stdout and "compare" in r.stdout


def test_ensure_pth_writes_once_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "pth_dir", lambda: tmp_path)
    assert real_ensure_pth() is True and core.pth_status()[0] is True
    assert real_ensure_pth() is True
    monkeypatch.setattr(core, "pth_dir", lambda: tmp_path / "read-only" / "nowhere")
    assert real_ensure_pth() is False                         # a system Python we cannot write: reported by the audit


def test_sync_run_puts_the_pth_back(monkeypatch):
    from fieldkit.core import sync
    seen = []
    monkeypatch.setattr(core, "ensure_pth", lambda: seen.append(1) or True)
    monkeypatch.setattr(sync, "repos", lambda extra=(): [])
    monkeypatch.setattr(sync, "ROOT", Path("/nonexistent"))
    sync.run()
    assert seen == [1]
