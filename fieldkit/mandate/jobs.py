# Version: 1.0.0 · updated 26-10-23-15-30
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


def events_path():
    return _link().home() / "ledger.jsonl"


def event(what, **data):
    """The project ledger (plan 17.2 item 1): append-only, one JSON line per thing that happened, on the boss. jobs.json
    is the state worked out from it; a restarted boss reads both (resume)."""
    events_path().parent.mkdir(parents=True, exist_ok=True)
    n = sum(1 for _ in events_path().open(encoding="utf-8")) + 1 if events_path().is_file() else 1
    with open(events_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(n=n, at=time.strftime("%Y-%m-%d %H:%M:%S"), what=what, **data),
                           ensure_ascii=False) + "\n")
    return n


def events(since=0):
    out = []
    for line in events_path().read_text(encoding="utf-8").splitlines() if events_path().is_file() else []:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("n", 0) > since:
            out.append(e)
    return out


def resume(call=None):
    """A restarted boss (plan 17.2 item 2): the jobs still open, and each worker asked once to resend results it could
    not deliver while the boss was down. -> {open: [ids], last: the last event, asked: what the worker said}."""
    led = _ledger()
    open_ = [j for j, e in led.items() if e.get("status") in (None, "sent")]
    last = (events() or [{}])[-1]
    asked = None
    if open_:
        asked = (call or _link().call)({"verb": "resend"})
        event("boss-resumed", open=open_, worker=asked.get("error") or asked.get("resent"))
    return {"open": open_, "last": last, "asked": asked}


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
    event("job-sent", id=jid, kind=kind, repo=repo, commit=commit)
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
    event("result", id=r["id"], status=r.get("status"), worker=r.get("host"),
          concerns=[c.get("text") for c in r.get("concerns") or []])
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
    boss = _bosses(mpath)
    if boss and str(req.get("from_host") or "") not in boss:   # plan 17.1: workers obey the boss the mandate names
        return {"ok": False, "error": f"this job came from {req.get('from_host')}, not the mandate's orchestrator "
                                      f"({', '.join(boss)}): not taken"}
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


def _bosses(mpath):
    """The device names the sealed mandate gives as orchestrator and backup ([] when it names none)."""
    try:
        from . import mandate as M
        m = M.load(mpath)
    except (OSError, ValueError):
        return []
    return [str(m[k]["device"]) for k in ("orchestrator", "backup")
            if isinstance(m.get(k), dict) and m[k].get("device")]


def outbox():
    return _link().home() / "outbox"


def deliver(r, call=None):
    """Send a result to the boss; one that cannot be delivered (the boss is down) waits in the outbox."""
    res = (call or _link().call)({"verb": "result", "result": r,
                                  "text": f"RESULT {r['repo']} {r['commit'][:10]} {r['status']}"
                                          + (f" with {len(r['concerns'])} concern(s)" if r.get("concerns") else "")})
    if not res.get("ok"):
        outbox().mkdir(parents=True, exist_ok=True)
        (outbox() / f"{r['id']}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    return res


def flush(call=None):
    """Resend every result waiting in the outbox (asked by a restarted boss: verb resend). -> how many went."""
    sent = 0
    for p in sorted(outbox().glob("*.json")) if outbox().is_dir() else []:
        r = json.loads(p.read_text(encoding="utf-8"))
        res = (call or _link().call)({"verb": "result", "result": r, "text": f"RESULT {r['repo']} (resent)"})
        if res.get("ok"):
            p.unlink()                                    # delivered: the boss holds it now
            sent += 1
    return sent


def sign_result(r):
    from . import mandate as M
    k, fp = M.review_key()
    r = dict(r, key=fp)
    r["sig"] = k.sign(_canon(r)).hex()
    return r


def run_job(jid, repo_path, commit, mandate_path, verdict_fn=None, call=None, health_fn=None):
    """The job itself (a detached process on the worker): check the device's health, run awake, sign the RESULT, send
    it back. -> the result. Health problems travel as concerns; the job is not run only when it cannot be."""
    import socket
    from . import health
    from . import mandate as M
    from . import proofs, verdict as V
    name = proofs.where(repo_path)[0]
    branches = ((M.load(mandate_path).get("repos") or {}).get(name) or {}).get("push") or []
    can_run, sick, said = (health_fn or health.check)(repo_path)
    if can_run:
        with health.awake(f"fieldkit job {jid}"):
            v = (verdict_fn or V.verdict)(repo_path, commit, name=name, branches=branches)
    else:
        v = {"status": "not-run", "evidence": "the device cannot run it now: " + "; ".join(said), "concerns": []}
    v["concerns"] = sick + v["concerns"]
    r = sign_result({"id": jid, "kind": "test", "repo": name, "commit": commit, "status": v["status"],
                     "evidence": str(v["evidence"])[:1900], "concerns": v["concerns"], "os": _link().os_tag(),
                     "host": socket.gethostname(), "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    deliver(r, call=call)
    return r


def lines():
    led = _ledger()
    out = []
    for jid, e in sorted(led.items(), key=lambda kv: str(kv[1].get("sent"))):
        out.append(f"  {e.get('status', 'sent'):8} {jid}  {e.get('evidence', e.get('worker_said', ''))[:120]}")
        out += [f"           CONCERN: {c.get('text')}  FIX OFFERED: {c.get('fix_offered')}" for c in e.get("concerns") or []]
    return out or ["  no job sent from here"]
