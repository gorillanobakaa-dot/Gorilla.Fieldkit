"""compare: a finished job held against a person-made reference."""
import subprocess

from fieldkit.buildh import compare


def test_same_differs_and_missing(tmp_path):
    w, ref = tmp_path / "job", tmp_path / "ref"
    for d in (w, ref):
        d.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "a.js").write_text("x\n")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base"], check=True)
    base = subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (w / "a.js").write_text("y\n")
    (w / "b.js").write_text("same\n")
    (w / "c.js").write_text("only in the job\n")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "job"], check=True)
    (ref / "a.js").write_text("z\n")
    (ref / "b.js").write_text("same\n")
    files = compare.changed_by_job(w, base)
    rows = {r["file"]: r for r in compare.compare(w, ref, files, out_dir=tmp_path / "out")}
    assert rows["a.js"]["result"] == "differs" and rows["b.js"]["result"] == "same" and rows["c.js"]["result"] == "missing"
    assert "1 of 3 files are identical" in compare.lines(list(rows.values()))[0]
    assert "-z" in (tmp_path / "out" / "compare.diff").read_text()
