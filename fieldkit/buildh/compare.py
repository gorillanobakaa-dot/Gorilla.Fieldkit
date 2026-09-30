"""compare - hold a finished working copy up against a reference result made by a person.

For a replayed job (for example the 154 -> 155.0.1 rebase the owner did by hand), the
owner's own patched source is the answer key. Every file the job changed is compared
with the same file in the reference:

    same       byte-for-byte what the owner produced
    differs    N lines differ (the diff is saved for review)
    missing    the reference has the file but the job does not, or the other way round

    fieldkit build-harness compare [TASK] --reference DIR
"""
import difflib
import json
import subprocess
from pathlib import Path


def changed_by_job(workdir, base_commit):
    out = subprocess.run(["git", "-C", str(workdir), "diff", "--name-only", base_commit, "HEAD"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def compare(workdir, reference, files, out_dir=None):
    rows, diffs = [], []
    for rel in files:
        a, b = Path(workdir) / rel, Path(reference) / rel
        if not a.is_file() or not b.is_file():
            rows.append({"file": rel, "result": "missing", "detail": "job" if not a.is_file() else "reference"})
            continue
        x = a.read_text(encoding="utf-8", errors="replace").splitlines()
        y = b.read_text(encoding="utf-8", errors="replace").splitlines()
        if x == y:
            rows.append({"file": rel, "result": "same"})
            continue
        d = list(difflib.unified_diff(y, x, f"reference/{rel}", f"job/{rel}", lineterm="", n=2))
        n = sum(1 for l in d if l[:1] in "+-" and not l.startswith(("+++", "---")))
        rows.append({"file": rel, "result": "differs", "lines": n})
        diffs += d
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "compare.diff").write_text("\n".join(diffs) + "\n", encoding="utf-8")
        (Path(out_dir) / "compare.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


def lines(rows):
    same = sum(1 for r in rows if r["result"] == "same")
    out = [f"{same} of {len(rows)} files are identical to the reference"]
    out += [f"  {r['result']:8} {r['file']}" + (f"  ({r['lines']} lines)" if r.get("lines") else "")
            for r in rows if r["result"] != "same"]
    return out
