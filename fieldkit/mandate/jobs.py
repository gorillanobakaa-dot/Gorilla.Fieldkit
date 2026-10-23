# Version: 1.0.0 · updated 26-10-23-15-10
"""Jobs between one orchestrator and its workers, under the owner's mandate (local/PROJECT-MANDATE-PLAN.md 16.4).

    fieldkit mandate job send REPO SHA [--kind test]   the orchestrator: give the paired worker a job (once per job)
    fieldkit mandate job status                        the ledger: every job, who has it, its result and concerns
    fieldkit mandate job wait ID [--seconds S]         one blocking wait for that job's result (no polling session)

Owner, 2026-10-23: "ONE COMPUTER IS THE BOSS, THE ORCHESTRATOR ... the other ones they help ... they are given parts
of the job, they execute it, they report their concerns ... they offer to fix the problem and continue working ...
they do not object ... you can not have 20 devices being chief." The owner's sealed mandate, held by the orchestrator,
is the only approval. 20 devices = 20 one-time setups (pair, trust pin), never 20 prompts a night.

The shapes (both travel signed by the link's HMAC):
    job     {verb: job, id, kind: test, repo, commit, mandate: <sha256 of the sealed mandate file>}
    result  {verb: result, result: {id, kind, repo, commit, status: pass | fail | not-run, passed?, evidence,
             concerns: [{text, fix_offered}], os, host, at, key, sig}}
The result is also signed by the worker's own Ed25519 result key (fingerprint `key`), over the canonical JSON of every
field but `sig`: PROVENANCE, which device ran what, never an approval. The orchestrator pins each worker's key once, at
pairing (fieldkit mandate trust-reviewer). A worker accepts a job only when it holds a sealed mandate with that sha in
force here and the repository is in it; otherwise it answers why, and the orchestrator reports that as a concern.
Kinds today: test. build and screenshot are the next (16.3 (5)): a stage of release-cycle that is CANNOT HERE.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

KINDS = ("test",)
ID = re.compile(r"[\w.-]{1,120}")
SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
RESULT_FIELDS = ("id", "kind", "repo", "commit", "status", "evidence", "concerns", "os", "host", "at", "key")


def _link():
    from ..peers import link
    return link


def ledger_path():
    return _link().home() / "jobs.json"


def results_dir():
    return _link().home() / "results"


def _ledger():
    try:
        return json.loads(ledger_path().read_text(encoding="utf-8")) if ledger_path().is_file() else {}
    except ValueError:
        return {}


def _save(led):
    ledger_path().parent.mkdir(parents=True, exist_ok=True)
    ledger_path().write_text(json.dumps(led, indent=1), encoding="utf-8")


def job_id(kind, repo, commit, mandate_sha):
    return f"{kind}-{repo}-{commit[:12]}-{mandate_sha[:8]}"


def mandate_for(repo, mandate_sha=None):
    """-> the path of a mandate IN FORCE here that names `repo` (and has that sha, when given), or None."""
    from . import mandate as M
    for p in M.mandates():
        try:
            if mandate_sha and M._sha(p) != mandate_sha:
                continue
            if M.verify(p)[0] and repo in (M.load(p).get("repos") or {}):
                return p
        except (OSError, ValueError):
            continue
    return None


# ---- the orchestrator ------------------------------------------------------------------------------------------------
def send(kind, repo, commit, mandate_path, call=None):
    """Give the worker a job, once: a job already in the ledger is not sent again. -> (id, what happened)."""
    from . import mandate as M
    if kind not in KINDS or not SHA.fullmatch(commit or ""):
        return None, f"a job is one of {', '.join(KINDS)} on a full 40-character sha"
    msha = M._sha(mandate_path)
    jid = job_id(kind, repo, commit, msha)
    led = _ledger()
    if jid in led:
        return jid, f"already sent at {led[jid]['sent']} ({led[jid].get('status', 'sent')})"
    res = (call or _link().call)({"verb": "job", "id": jid, "kind": kind, "repo": repo, "commit": commit,
                                  "mandate": msha})
    if not res.get("ok"):
        return jid, f"not sent: {res.get('error')}"
    led[jid] = {"kind": kind, "repo": repo, "commit": commit, "mandate": msha, "status": "sent",
                "sent": time.strftime("%Y-%m-%d %H:%M:%S"), "worker_said": res.get("error") or "started"}
    _save(led)
    return jid, "sent"


def _canon(r):
    return json.dumps({k: r.get(k) for k in RESULT_FIELDS}, sort_keys=True, separators=(",", ":")).encode()


def verified_results(repo, commit, workers=None, own_key=None):
    """Every stored result for repo@commit whose signature a pinned worker key checks (never this computer's key)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from . import mandate as M
    keys = workers if workers is not None else M.reviewer_keys()
    mine = own_key if own_key is not None else M.review_key()[1]
    out = []
    d = results_dir()
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("repo") != repo or r.get("commit") != commit or r.get("key") == mine or r.get("key") not in keys:
            continue
        try:
            Ed25519PublicKey.from_public_bytes(keys[r["key"]]).verify(bytes.fromhex(str(r.get("sig", ""))), _canon(r))
        except (InvalidSignature, ValueError):
            continue
        out.append(r)
    return out


def handle_result(req):
    """The orchestrator's side of verb result (inside link.handle, after the HMAC check): keep it, update the ledger.
    The worker's signature is checked when the result is USED (verified_results), against the keys pinned then."""
    r = req.get("result")
    if not isinstance(r, dict) or not ID.fullmatch(str(r.get("id", ""))):
        return {"ok": False, "error": "a result needs an id"}
    results_dir().mkdir(parents=True, exist_ok=True)
    (results_dir() / f"{r['id']}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    led = _ledger()
    e = led.setdefault(r["id"], {"kind": r.get("kind"), "repo": r.get("repo"), "commit": r.get("commit"), "sent": "?"})
    e.update(status=r.get("status"), evidence=str(r.get("evidence", ""))[:300], concerns=r.get("concerns") or [],
             worker=r.get("host"), at=r.get("at"))
    _save(led)
    return {"ok": True, "stored": r["id"]}


def wait(jid, seconds=3600, sleep=time.sleep, now=time.monotonic):
    """One blocking wait on this computer (no session polls the worker): -> the ledger entry once it has a result."""
    end = now() + seconds
    while True:
        e = _ledger().get(jid)
        if e and e.get("status") not in (None, "sent"):
            return e
        if now() >= end:
            return e
        sleep(5)


# ---- the worker ------------------------------------------------------------------------------------------------------
def _lock():
    return _link().home() / "job.lock"


def busy(lock, cap):
    try:
        return time.time() - Path(lock).stat().st_mtime < cap + 60
    except OSError:
        return False


def handle_job(req, popen=subprocess.Popen, repos=None, lock=None, find_mandate=None, cap=3600):
    """The worker's side of verb job (inside link.handle, after the HMAC check): no session is asked. Refuses only
    what it CANNOT do (no such mandate here, a repository it does not have, a kind it does not know, a job running);
    each answer goes back to the orchestrator, which reports it."""
    from . import verdict as V
    jid, kind, repo, commit, msha = (str(req.get(k) or "") for k in ("id", "kind", "repo", "commit", "mandate"))
    if not (ID.fullmatch(jid) and SHA.fullmatch(commit) and SHA256.fullmatch(msha) and ID.fullmatch(repo)):
        return {"ok": False, "error": "a job needs id, kind, repo, commit (40 hex) and mandate (sha256)"}
    if kind not in KINDS:
        return {"ok": False, "error": f"this worker does not know the job kind {kind!r} ({', '.join(KINDS)})"}
    mpath = (find_mandate or mandate_for)(repo, msha)
    if mpath is None:
        return {"ok": False, "error": f"no mandate with sha {msha[:12]} in force here naming {repo} "
                                      "(fix: fieldkit mandate share it to this computer)"}
    path = V.find_repo(repo, repos)
    if path is None:
        return {"ok": False, "error": f"{repo} is not checked out here (fix: add it to sync.repos on this computer)"}
    lk = Path(lock) if lock is not None else _lock()
    if busy(lk, cap):
        return {"ok": False, "error": "a job is running here already: one at a time"}
    lk.parent.mkdir(parents=True, exist_ok=True)
    lk.write_text(f"{jid} {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    popen([sys.executable, "-m", "fieldkit", "mandate", "job", "run", jid, str(path), commit, str(mpath),
           "--lock", str(lk)],
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
          **({"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}))
    return {"ok": True, "started": jid}


def sign_result(r):
    from . import mandate as M
    k, fp = M.review_key()
    r = dict(r, key=fp)
    r["sig"] = k.sign(_canon(r)).hex()
    return r


def run_job(jid, repo_path, commit, mandate_path, verdict_fn=None, call=None):
    """The job itself (a detached process on the worker): run, sign the RESULT, send it back. -> the result."""
    import socket
    from . import mandate as M
    from . import proofs, verdict as V
    name = proofs.where(repo_path)[0]
    branches = ((M.load(mandate_path).get("repos") or {}).get(name) or {}).get("push") or []
    v = (verdict_fn or V.verdict)(repo_path, commit, name=name, branches=branches)
    r = sign_result({"id": jid, "kind": "test", "repo": name, "commit": commit, "status": v["status"],
                     "evidence": str(v["evidence"])[:1900], "concerns": v["concerns"], "os": _link().os_tag(),
                     "host": socket.gethostname(), "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    (call or _link().call)({"verb": "result", "result": r,
                            "text": f"RESULT {name} {commit[:10]} {v['status']}"
                                    + (f" with {len(v['concerns'])} concern(s)" if v["concerns"] else "")})
    return r


def lines():
    led = _ledger()
    out = []
    for jid, e in sorted(led.items(), key=lambda kv: str(kv[1].get("sent"))):
        out.append(f"  {e.get('status', 'sent'):8} {jid}  {e.get('evidence', e.get('worker_said', ''))[:120]}")
        out += [f"           CONCERN: {c.get('text')}  FIX OFFERED: {c.get('fix_offered')}" for c in e.get("concerns") or []]
    return out or ["  no job sent from here"]
