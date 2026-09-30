"""release - prove the published thing is the tested thing, and that its claims are true.

Generalised from Gorilla.firefox publish_gate.py (2026-09-13/14): a build reached
users with a broken URL bar and inverted prefs, and release notes said "calls now
work" when no call had been placed. The rules carried over:

  - no --force: a failing gate means fix the release or fix the check
  - the file published must be the file tested (hashes, not names)
  - a claim about behaviour needs a measurement of behaviour

    fieldkit release check releases/<project>.yaml [--json]

A spec (YAML):

    name: pfind
    repo: owner/name                 # GitHub (read with the gh CLI)
    local: path to the git checkout
    tag: v2.2.0
    tests:                           # run on a CLEAN export of the tag, not the working tree
      - [python, -m, pytest, -q, tests]
    artifacts:                       # published copy must equal the tested copy
      - {path: pfind.py}             # source file at the tag on GitHub vs the tag locally
      - {asset: "*.exe", local: dist/setup.exe}   # a release asset vs the tested file
    privacy: true                    # fieldkit privacy scan of the exported tag
    claims:                          # every claim found in the notes needs a passing proof
      - claim: "27 tests"
        find: "\\b27 tests\\b"       # regex searched in the release notes
        proof: {command: [python, -m, pytest, -q, --collect-only, tests], output_contains: "27 tests collected"}
      - claim: "runs on Windows and Linux"
        find: "Windows and Linux"
        proof: {evidence: [windows, linux]}   # a passing run recorded ON each platform

Result: CLEAR, or DO NOT PUBLISH with every failed gate and why.

A claim about another machine cannot be proven by a command run here. For those,
the other machine records evidence of the SAME tagged tree (git tree id, which is
identical everywhere, unlike file bytes after line-ending conversion):

    fieldkit release prove releases/<project>.yaml     # on Debian: runs the spec's tests
                                                       # on the exported tag, writes
                                                       # releases/evidence/<name>-<tag>-linux.json
Bring that file back; `check` accepts it only if its platform, tree id and result hold.
"""
import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .core import privacy, settings


def _sh(cmd, cwd=None, timeout=900, input=None):
    cmd = [sys.executable if c in ("python", "python3") else str(c) for c in cmd]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, input=input)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 99, f"could not run: {e}"


def _sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def export_tag(local, tag, dest):
    """The tag's exact tree, via git archive - uncommitted work cannot leak in."""
    rc, out = _sh(["git", "-C", local, "rev-parse", "--verify", f"{tag}^{{commit}}"])
    if rc != 0:
        raise ValueError(f"tag {tag} not found in {local}")
    zpath = Path(dest) / "tag.zip"
    # core.autocrlf off: with it on (the default on many Windows machines, including
    # GitHub's runners) git archive writes CRLF while GitHub serves the stored LF blob,
    # and identical code is reported as "published != tested" (found 2026-09-30).
    rc, out = _sh(["git", "-C", local, "-c", "core.autocrlf=false", "archive", "--format=zip", "-o", str(zpath), tag])
    if rc != 0:
        raise ValueError(f"git archive failed: {out[:200]}")
    tree = Path(dest) / "tree"
    with zipfile.ZipFile(zpath) as z:
        z.extractall(tree)
    return tree


def tree_id(local, tag):
    """The tag's git tree id: the same on every machine for the same content."""
    rc, out = _sh(["git", "-C", local, "rev-parse", f"{tag}^{{tree}}"])
    if rc != 0:
        raise ValueError(f"tag {tag} not found in {local}")
    return out.strip()


def evidence_path(spec_path, spec, platform):
    return Path(spec_path).resolve().parent / "evidence" / f"{spec['name']}-{spec['tag']}-{platform}.json"


def prove(spec_path):
    """Run the spec's tests on the exported tag HERE and record the result as evidence for this platform."""
    from .core.host import host
    spec = settings.load(spec_path)
    platform = host()["system"].lower()
    work = Path(tempfile.mkdtemp(prefix="fieldkit-prove-"))
    try:
        tree = export_tag(spec["local"], spec["tag"], work)
        runs = []
        for cmd in spec.get("tests") or []:
            rc, out = _sh(cmd, cwd=tree)
            runs.append({"command": [str(c) for c in cmd], "exit": rc, "tail": out.strip().splitlines()[-15:]})
        ev = {"name": spec["name"], "tag": spec["tag"], "tree": tree_id(spec["local"], spec["tag"]),
              "platform": platform, "host": host(), "python": sys.version.split()[0],
              "when": __import__("time").strftime("%Y-%m-%d %H:%M:%S"), "runs": runs,
              "passed": bool(runs) and all(r["exit"] == 0 for r in runs)}
        out = evidence_path(spec_path, spec, platform)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ev, indent=1), encoding="utf-8")
        return ev, out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def evidence_gate(spec_path, spec, platforms):
    """(ok, detail) for a claim proven by recorded runs on each named platform."""
    want = tree_id(spec["local"], spec["tag"])
    missing, bad = [], []
    for plat in platforms:
        f = evidence_path(spec_path, spec, plat)
        if not f.is_file():
            missing.append(plat)
            continue
        ev = json.loads(f.read_text(encoding="utf-8"))
        if ev.get("platform") != plat:
            bad.append(f"{plat}: file says {ev.get('platform')}")
        elif ev.get("tree") != want:
            bad.append(f"{plat}: tested tree {str(ev.get('tree'))[:12]} is not {spec['tag']} ({want[:12]})")
        elif not ev.get("passed"):
            bad.append(f"{plat}: tests failed there ({ev.get('when')})")
    if missing or bad:
        return False, "; ".join(bad + [f"no evidence from {m} - run `fieldkit release prove` there" for m in missing])
    return True, "passing runs recorded on " + ", ".join(platforms) + f" for tree {want[:12]}"


def gh_json(args):
    rc, out = _sh(["gh", *args], timeout=120)
    if rc != 0:
        raise ValueError(f"gh {' '.join(args)}: {out.strip()[:200]}")
    return json.loads(out)


def published_file_sha(repo, tag, path):
    import base64
    data = gh_json(["api", f"repos/{repo}/contents/{path}?ref={tag}"])
    return _sha_bytes(base64.b64decode(data["content"]))


def release_notes(repo, tag):
    try:
        return gh_json(["release", "view", tag, "-R", repo, "--json", "body"])["body"]
    except ValueError:
        return None


def check(spec_path):
    spec = settings.load(spec_path)
    gates, local, tag, repo = [], spec["local"], spec["tag"], spec.get("repo")

    def gate(name, ok, detail):
        gates.append({"gate": name, "ok": ok, "detail": detail})

    work = Path(tempfile.mkdtemp(prefix="fieldkit-release-"))
    try:
        try:
            tree = export_tag(local, tag, work)
            gate("tag exported", True, f"{tag} extracted clean from {local}")
        except ValueError as e:
            gate("tag exported", False, str(e))
            return _result(spec, gates)

        # Scan BEFORE the tests run: tests write logs and state into the tree, and the
        # question is what gets published, not what a test run left behind (2026-09-30).
        if spec.get("privacy", True):
            found = privacy.scan_path(tree)
            gate("privacy", not found, "clean" if not found else
                 f"{sum(len(v) for v in found.values())} finding(s) in {len(found)} file(s)")

        for cmd in spec.get("tests") or []:
            rc, out = _sh(cmd, cwd=tree)
            last = (out.strip().splitlines() or [""])[-1]
            gate(f"tests: {' '.join(map(str, cmd))[:60]}", rc == 0, last[:160])
        if not spec.get("tests"):
            gate("tests", False, "the spec declares no tests: nothing proves the release works")


        for a in spec.get("artifacts") or []:
            if "path" in a:
                local_sha = _sha_bytes((tree / a["path"]).read_bytes())
                try:
                    pub = published_file_sha(repo, tag, a["path"])
                    gate(f"published {a['path']} == tested", pub == local_sha,
                         "same sha256" if pub == local_sha else f"DIFFERENT: published {pub[:12]} vs tested {local_sha[:12]}")
                except ValueError as e:
                    gate(f"published {a['path']} == tested", False, str(e))
            elif "asset" in a:
                d = work / "assets"
                d.mkdir(exist_ok=True)
                rc, out = _sh(["gh", "release", "download", tag, "-R", repo, "-p", a["asset"], "-D", str(d)], timeout=900)
                got = [p for p in d.iterdir() if fnmatch.fnmatch(p.name, a["asset"])]
                tested = Path(a["local"])
                if rc != 0 or len(got) != 1 or not tested.is_file():
                    gate(f"asset {a['asset']} == tested", False,
                         f"downloaded {len(got)} match(es); tested file {'found' if tested.is_file() else 'missing'}")
                else:
                    same = _sha_bytes(got[0].read_bytes()) == _sha_bytes(tested.read_bytes())
                    gate(f"asset {got[0].name} == tested", same, "same sha256" if same else "DIFFERENT bytes")

        notes = release_notes(repo, tag) if repo else None
        if notes is None:
            gate("release notes", False, "no published release notes found for the tag")
        else:
            gate("release notes", bool(notes.strip()), f"{len(notes)} characters")
            for c in spec.get("claims") or []:
                if not re.search(c["find"], notes, re.I | re.M):
                    continue                                      # the notes do not make this claim
                proof = c.get("proof")
                if not proof:
                    gate(f"claim: {c['claim']}", False, "the notes make this claim and the spec has no proof for it")
                    continue
                if "evidence" in proof:
                    gate(f"claim: {c['claim']}", *evidence_gate(spec_path, spec, proof["evidence"]))
                    continue
                rc, out = _sh(proof["command"], cwd=tree)
                ok = rc == 0 and (not proof.get("output_contains") or re.search(proof["output_contains"], out))
                gate(f"claim: {c['claim']}", bool(ok), (out.strip().splitlines() or [""])[-1][:160])
        return _result(spec, gates)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _result(spec, gates):
    failed = [g for g in gates if not g["ok"]]
    return {"name": spec.get("name"), "tag": spec.get("tag"), "clear": not failed, "gates": gates,
            "verdict": "CLEAR" if not failed else f"DO NOT PUBLISH: {len(failed)} gate(s) failed"}


def lines(r):
    out = [f"{r['name']} {r['tag']}: {r['verdict']}"]
    out += [f"  {'ok  ' if g['ok'] else 'FAIL'} {g['gate']}: {g['detail']}" for g in r["gates"]]
    out.append("NEXT: publish." if r["clear"] else "NEXT: fix each FAIL (the release, or the proof); there is no --force.")
    return out
