"""The Linux leak gate on a GitHub ubuntu runner (P27, 2026-11-13): for a machine whose kernel has no veth.

Three steps, each a verb of `fieldkit build-harness leakgate-ci`:
  pack    (the owner's machine)  the files the gate reads, copied as they are, into ONE small context archive: the
          allowlist, dispositions and SPEC of the gate's owner folder, the upstream meta, the real source files the
          static audit inventories (the same function picks them) with Cargo.lock, and the previous release's. The .deb
          is NOT inside: it is hashed here and travels as its own release asset. Nothing is stubbed: the runner's audits
          read real files, so SOURCE_POLICY and DEPENDENCY_POLICY judge the same input they would on this machine.
  run     (the runner)  verify the .deb's sha256 against the context, unpack the context, run the unchanged gate.
  record  (the owner's machine)  read the downloaded result, check it is for THIS .deb, and record it in the task like
          a local run (journal + leakgate_result.json). A result for another binary, or one the context does not vouch
          for, is refused. A verdict is never edited here.

Threat model: GitHub (Microsoft) sees the .deb, the context and every packet log of the run; the repository is PUBLIC,
so the run's log is readable by anyone. Hence: the .deb is a DRAFT release asset (never public), never an artifact;
only the result JSON and events are uploaded.
"""
import hashlib
import io
import json
import re
import shutil
import tarfile
import time
from pathlib import Path

#: the files a task-less gate run reads, relative to the context folder
OWNER, TREE, PREV = "owner", "tree", "prev"
CTX_NAME = "leakgate-context.tar.gz"
MANIFEST = "context.json"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cpu_flags(cpuinfo="/proc/cpuinfo"):
    """-> sorted CPU flags of the first core, or [] where /proc/cpuinfo does not exist."""
    try:
        for line in Path(cpuinfo).read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("flags"):
                return sorted(line.split(":", 1)[1].split())
    except OSError:
        pass
    return []


def _copy_inventory(root, dest, inventory):
    """Copy each inventoried file of tree ROOT to DEST at the same relative path, and Cargo.lock; -> bytes copied."""
    n = 0
    for rel in list(inventory) + ["Cargo.lock"]:
        src = Path(root) / rel
        if src.is_file():
            out = Path(dest) / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out)
            n += src.stat().st_size
    return n


def pack(deb, owner_root, workdir_tree, upstream, out_dir, n_minus_1_tree=None, previous_lock=None, inventory=None):
    """Write OUT_DIR/leakgate-context.tar.gz and return the manifest (also inside it). `inventory(root)` is
    audit.inventory unless a test passes another."""
    from . import audit
    inventory = inventory or audit.inventory
    deb, owner_root, out_dir = Path(deb), Path(owner_root), Path(out_dir)
    if not deb.is_file():
        raise RuntimeError(f"leakgate-ci pack: no .deb at {deb}")
    stage = out_dir / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    present = {}
    for name in ("allow.json", "dispositions.json", "SPEC.md"):
        src = owner_root / "leakgate" / name
        if src.is_file():
            dst = stage / OWNER / "leakgate" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            present[name] = sha256(src)
        else:
            present[name] = None                      # absent here: absent on the runner too, the gate treats it the same
    inv = inventory(workdir_tree)
    size = _copy_inventory(workdir_tree, stage / TREE, inv)
    if not (stage / TREE / "Cargo.lock").is_file():
        raise RuntimeError(f"leakgate-ci pack: {workdir_tree}/Cargo.lock is missing: the dependency audit has nothing to read")
    prev_inv = inventory(n_minus_1_tree) if n_minus_1_tree and Path(n_minus_1_tree).is_dir() else {}
    if prev_inv:
        _copy_inventory(n_minus_1_tree, stage / PREV, prev_inv)
    if previous_lock:                                 # what gate.previous_cargo_lock found (git root commit or the file)
        (stage / PREV).mkdir(parents=True, exist_ok=True)
        (stage / PREV / "Cargo.lock").write_text(previous_lock, encoding="utf-8")
    elif (stage / PREV / "Cargo.lock").is_file():
        (stage / PREV / "Cargo.lock").unlink()
    manifest = {"version": 1, "packed": time.strftime("%Y-%m-%dT%H:%M:%S"), "upstream": upstream,
                "deb": {"name": deb.name, "bytes": deb.stat().st_size, "sha256": sha256(deb)},
                "owner_files": present, "source_files": len(inv), "source_bytes": size,
                "previous_files": len(prev_inv), "has_previous_lock": bool(previous_lock),
                "packed_on_cpu_flags": cpu_flags()}
    (stage / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    tgz = out_dir / CTX_NAME
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(stage, arcname=".")
    shutil.rmtree(stage, ignore_errors=True)
    manifest["context_sha256"] = sha256(tgz)
    manifest["context"] = str(tgz)
    return manifest


def unpack(context, dest):
    """Extract the context archive into DEST (never outside it) -> its manifest."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(context, "r:gz") as tf:
        for m in tf.getmembers():
            if not (m.isfile() or m.isdir()) or not (dest / m.name).resolve().is_relative_to(dest):
                raise RuntimeError(f"leakgate-ci: the context holds {m.name!r}: refused (only files and folders inside it)")
        tf.extractall(dest, filter="data")
    return json.loads((dest / MANIFEST).read_text(encoding="utf-8"))


def run(context, deb, out_dir, release=False, say=print, gate_run=None):
    """On the runner: the unchanged gate on the .deb with the context's files -> (result, events). The .deb must be
    the one the context names (sha256). `gate_run` is for tests."""
    from . import gate as lg
    deb = Path(deb)
    work = Path(out_dir) / "ctx"
    man = unpack(context, work)
    got = sha256(deb)
    if got != man["deb"]["sha256"]:
        raise RuntimeError(f"leakgate-ci run: {deb.name} has sha256 {got}, the context was packed for {man['deb']['sha256']}: "
                           "not the same binary, nothing run")
    prev = work / PREV
    prev_tree = prev if (prev / "Cargo.lock").is_file() or any(prev.rglob("*")) else None
    runner = gate_run or lg.run
    result, events, _st, _bi = runner(str(deb), work / OWNER, work / TREE, man["upstream"], Path(out_dir) / "runs",
                                      repeat=3 if release else 1, quick=not release, release=release,
                                      n_minus_1_tree=prev_tree, say=say)
    return result, events


def collect(out_dir, result, events, manifest_path=None):
    """Put the files the owner's machine needs into OUT_DIR/artifact: the result JSON, the events and the gate's run
    folder's small text files; never the .deb. -> the folder."""
    art = Path(out_dir) / "artifact"
    art.mkdir(parents=True, exist_ok=True)
    (art / "test-results.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    (art / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    run_dir = Path(result.get("ARTIFACTS") or "")
    if run_dir.is_dir():
        for p in run_dir.iterdir():
            if p.is_file() and p.name not in ("test-results.json", "events.jsonl") and p.stat().st_size < 5_000_000 \
                    and p.suffix in (".json", ".jsonl", ".observed", ".new", ".txt", ".md", ".log"):
                shutil.copyfile(p, art / p.name)
    if manifest_path and Path(manifest_path).is_file():
        shutil.copyfile(manifest_path, art / "runner.json")
    return art


def record(artifact_dir, deb_sha256, version, policies, owner_root, now=None):
    """Judge nothing; check and write. The artifact must be for the .deb with DEB_SHA256 and the version, a full run
    (every policy present), then return the record dict written to OWNER_ROOT/state/leakgate_result.json."""
    art = Path(artifact_dir)
    res_file = art / "test-results.json"
    if not res_file.is_file():
        raise RuntimeError(f"leakgate-ci record: no test-results.json in {art}")
    result = json.loads(res_file.read_text(encoding="utf-8"))
    shas = set((result.get("BINARY_SHA256") or {}).values())
    runner = art / "runner.json"
    info = json.loads(runner.read_text(encoding="utf-8")) if runner.is_file() else {}
    if info.get("deb_sha256") != deb_sha256:
        raise RuntimeError(f"leakgate-ci record: the run was on a .deb with sha256 {info.get('deb_sha256')}, this task's "
                           f"is {deb_sha256}: refused")
    if not shas:
        raise RuntimeError("leakgate-ci record: the result names no binary hash: refused")
    if version and result.get("VERSION") and result["VERSION"] != version:
        raise RuntimeError(f"leakgate-ci record: the result is for Firefox {result['VERSION']}, the task is {version}: refused")
    missing = [p for p in policies if p not in result]
    if missing:
        raise RuntimeError(f"leakgate-ci record: policies not judged in the result: {missing}: refused (never a PASS by omission)")
    rec = {"build_id": None, "BUILD": result.get("BUILD"), "when": (now or time.strftime("%Y-%m-%dT%H:%M:%S")),
           "release_run": False, "only": None, "ran_on": "github-ubuntu-runner",
           "runner": {k: info.get(k) for k in ("run_url", "os", "kernel", "cpu_flags", "deb_sha256", "context_sha256")},
           "FINAL_RESULT": result["FINAL_RESULT"], "policies": {p: result[p] for p in policies},
           "artifacts": str(art)}
    state = Path(owner_root) / "state"
    state.mkdir(exist_ok=True)
    (state / "leakgate_result.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def runner_info(deb, context_sha256, environ=None, cpuinfo="/proc/cpuinfo"):
    """What the runner says about itself, for the record: CPU flags (a -march=native .deb needs them), kernel, the run."""
    import os
    import platform
    env = environ if environ is not None else os.environ
    url = ""
    if env.get("GITHUB_RUN_ID") and env.get("GITHUB_REPOSITORY"):
        url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
    return {"run_url": url, "os": platform.platform(), "kernel": platform.release(), "cpu_flags": cpu_flags(cpuinfo),
            "deb_sha256": sha256(deb), "context_sha256": context_sha256}


def lines(r):
    """Plain lines for a pack manifest or a record."""
    if "FINAL_RESULT" in r:
        bad = [p for p, v in r["policies"].items() if v != "PASS"]
        return [f"LEAKGATE-CI RECORDED: FINAL_RESULT {r['FINAL_RESULT']} (ran on {r['ran_on']}); failing: {bad or 'none'}"]
    return [f"LEAKGATE-CI PACKED: {r['context']} ({r['source_files']} source files, {r['source_bytes']:,} bytes, "
            f"previous: {r['previous_files']} files, lock {'yes' if r['has_previous_lock'] else 'NO'}); "
            f".deb {r['deb']['name']} sha256 {r['deb']['sha256']}"]


def collect_partial(out_dir, error):
    """The gate crashed: put what the runs already wrote (events, artifacts.json, small text files) plus the error into
    OUT_DIR/artifact, so a late crash never loses the runs done. -> the folder, or None when nothing was written."""
    out = Path(out_dir)
    runs = sorted((p for p in (out / "runs").glob("*") if p.is_dir()), key=lambda p: p.stat().st_mtime) if (out / "runs").is_dir() else []
    art = out / "artifact"
    art.mkdir(parents=True, exist_ok=True)
    (art / "crash.txt").write_text(f"the gate stopped before its verdict: {error}\n", encoding="utf-8")
    if runs:
        for p in runs[-1].iterdir():
            if p.is_file() and p.stat().st_size < 5_000_000 and p.suffix in (".json", ".jsonl", ".observed", ".new", ".txt", ".md", ".log"):
                shutil.copyfile(p, art / p.name)
    return art
