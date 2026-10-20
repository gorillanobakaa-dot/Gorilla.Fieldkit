"""`fieldkit privacy scan <checkout>` scans what git would publish (2026-10-19: `privacy scan .` in Fieldkit walked the
ignored state/ folder - leak-gate build copies, packet captures - for three hours at a full core). --all walks
everything; a folder that is not a checkout is walked as before."""
import json
import subprocess
import sys


def _scan(*args, cwd):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "privacy", "scan", *args, "--json"], cwd=cwd,
                       capture_output=True, text=True)
    return json.loads(r.stdout[r.stdout.index("{"):]) if "{" in r.stdout else {}


def test_a_checkout_is_scanned_as_git_would_publish_it(tmp_path):
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("state/\n", encoding="utf-8")
    (repo / "ok.txt").write_text("nothing here\n", encoding="utf-8")
    secret = "ghp_" + "B" * 36                                             # privacy-scan: allow
    (repo / "state" / "leak.txt").write_text(secret + "\n", encoding="utf-8")
    import fieldkit
    root = str(__import__("pathlib").Path(fieldkit.__file__).resolve().parents[1])
    default = _scan(str(repo), cwd=root)
    everything = _scan(str(repo), "--all", cwd=root)
    flat = lambda r: json.dumps(r)
    assert "leak.txt" not in flat(default)                                 # ignored: not what git would publish
    assert "leak.txt" in flat(everything)                                  # --all still finds it
