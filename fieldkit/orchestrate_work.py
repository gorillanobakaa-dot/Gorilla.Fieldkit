"""Jobs on a received tree: the boss sends a folder and a command, a worker runs it, the artefacts come back.

    fieldkit orchestrate job run --device W --tree DIR --project REPO [--artefact GLOB ...] [--wall M] [--wait S]
                                 -- FIELDKIT ARGS ...        a Fieldkit command line, run on a copy of DIR there
    fieldkit orchestrate job agent --device W --tree DIR --project REPO --brief FILE [--model ID] [--brain URL]
                                 [--artefact GLOB ...] [--wall M] [--wait S]
                                                              Gorilla OpenCode headless with the brief: the model
                                                              is one the WORKER's Gorilla OpenCode has (default a
                                                              cheap Gemini Flash); --brain adds an OpenAI-compatible
                                                              URL for a worker with no provider of its own
    fieldkit orchestrate job status                           the ledger: every job, its result and concerns
    fieldkit orchestrate work JOB-ID                          (the worker, started by its listener: not for people)

Stage O3 of the household orchestrator (local/PROJECT-MANDATE-PLAN.md section 19). What it adds to the job protocol
of fieldkit/mandate/jobs.py (one boss, many workers; the owner's mandate is the only approval):
  - the material travels with the job: the tree goes first with `peers link put` (stage O2: streamed, sha256 per
    file), and the job names it by the digest both ends compared, so the worker runs exactly those bytes;
  - the worker runs on a COPY of the received tree (received/ is never changed), one job at a time, with a wall
    cap, awake, after a free-space and clock check. It never needs the internet: nothing reaches GitHub;
  - `run` is a Fieldkit command line with no shell; the commands that approve, push, publish or start agents are
    refused (REFUSED). `agent` is a fresh Gorilla OpenCode run with the brief, on the model the job names, through
    the worker's own configured provider (owner, 2026-10-26: cheap Gemini for tests, never a local model for O3);
    the model is pinned in the tree copy's .gorilla-opencode.json, the only thing a project folder may set. With
    --brain (an OpenAI-compatible URL) the run gets a profile that knows only that endpoint, and a model it loaded
    on this computer is unloaded afterwards (owner rule: an idle 43 GB model woke him);
  - every artefact goes back to the boss with put, and its name, size and sha256 are in the result the worker
    signs with its own key; the boss checks the files it got against them (`artefacts_check`).
Jobs travel on the cable only (link.JOB_VERBS), until the mandate gate covers the LAN. Not done yet (security is
for later, section 19): an agent run has the worker's normal Gorilla OpenCode tools, so it can write outside the tree
copy as the worker's user could.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from .mandate import jobs
from .peers import link

REFUSED = {"mandate", "ship", "publish", "release", "release-cycle", "autopilot", "peers", "inbox", "orchestrate",
           "agent", "elevate", "build-harness"}       # approvals, pushes, publishing, sessions, admin, the harness
CMD = re.compile(r"[a-z][a-z0-9-]{0,40}")
MAX_ARTEFACTS = 50
MAX_BRIEF = 32 * 1024
DEFAULT_MODEL = "gemini-flash-lite-latest"            # cheap Gemini, the owner's model for tests (2026-10-26)
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,120}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def work_root():
    return link.home() / "work"


def _job_path(jid):
    return work_root() / jid / "job.json"


def receipt_for(digest):
    """-> the newest receipt of a tree that arrived here with that digest (peers link put), or None."""
    d = link.home() / "receipts"
    for p in sorted(d.glob("*.json"), reverse=True) if d.is_dir() else []:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("digest") == digest and Path(r.get("saved", "")).exists():
            return r
    return None


def check_work(req):
    """-> why this run/agent job cannot be taken, or None."""
    kind = req.get("kind")
    if not SHA256.fullmatch(str(req.get("tree_digest") or "")):
        return "a run or agent job names its tree by the 64-hex digest `peers link put` reported"
    pats = req.get("artefacts") or []
    if not isinstance(pats, list) or len(pats) > 20 or any(
            not isinstance(g, str) or link_transfer_bad(g) for g in pats):
        return "artefacts: at most 20 relative glob patterns inside the tree (no .., no absolute path)"
    try:
        wall = int(60 if req.get("wall_minutes") is None else req["wall_minutes"])
    except (TypeError, ValueError):
        wall = 0
    if not 1 <= wall <= 1440:
        return "wall_minutes: 1 to 1440"
    if kind == "run":
        argv = req.get("argv")
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) and "\x00" not in a for a in argv)):
            return "run: argv is a list of strings, the Fieldkit command line without `fieldkit`"
        if not CMD.fullmatch(argv[0]):
            return f"run: {argv[0]!r} is not a Fieldkit command"
        if argv[0] in REFUSED:
            return f"run: `fieldkit {argv[0]}` is not run as a job (it approves, pushes, publishes or starts agents)"
    elif kind == "agent":
        brief = req.get("brief")
        if not isinstance(brief, str) or not brief.strip() or len(brief) > MAX_BRIEF:
            return f"agent: a brief of 1 to {MAX_BRIEF} characters"
        if req.get("brain") and not re.match(r"^https?://[^\s/]+", str(req.get("brain"))):
            return "agent: brain, when given, is the http(s) URL of an OpenAI-compatible endpoint"
        if not MODEL.fullmatch(str(req.get("model") or DEFAULT_MODEL)):
            return "agent: model is a model id the worker's Gorilla OpenCode knows (e.g. gemini-flash-lite-latest)"
    else:
        return f"{kind!r} is not a job on a tree"
    return None


def link_transfer_bad(pattern):
    from .peers import link_transfer
    return link_transfer.bad_path(pattern.replace("*", "x").replace("?", "x").replace("[", "x").replace("]", "x"))


def accept(req, mpath, lock, cap=3600, popen=subprocess.Popen):
    """The worker's side, inside jobs.handle_job after the mandate and boss checks. -> the answer to the boss."""
    why = check_work(req)
    if why:
        return {"ok": False, "error": why}
    rec = receipt_for(req["tree_digest"])
    if rec is None:
        return {"ok": False, "error": f"no tree with digest {req['tree_digest'][:12]} arrived here (send it first: "
                                      "fieldkit peers link put DIR)"}
    if jobs.busy(lock, cap):
        return {"ok": False, "error": "a job is running here already: one at a time"}
    jid = req["id"]
    d = work_root() / jid
    d.mkdir(parents=True, exist_ok=True)
    job = {k: req.get(k) for k in ("id", "kind", "repo", "tree_digest", "argv", "brief", "brain", "model",
                                   "artefacts", "wall_minutes", "from_host")}
    job.update(mandate_path=str(mpath), tree=rec["saved"], tree_kind=rec.get("kind"), lock=str(lock),
               accepted=time.strftime("%Y-%m-%d %H:%M:%S"))
    _job_path(jid).write_text(json.dumps(job, indent=1, ensure_ascii=False), encoding="utf-8")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{jid} {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    popen([sys.executable, "-P", "-m", "fieldkit", "orchestrate", "work", jid], env=link.link_env(),
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
          **({"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}))
    return {"ok": True, "started": jid}


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _tail(path, n=15, chars=1500):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])[-chars:]


AGENTS = ("coder", "task", "title", "summarizer")


def pin_model(tree, model):
    """The model for this run, in the tree copy's own folder settings (Gorilla OpenCode lets a project folder choose
    its models and nothing else); the worker's provider keys stay in its own config, never copied."""
    (Path(tree) / ".gorilla-opencode.json").write_text(
        json.dumps({"agents": {a: {"model": model} for a in AGENTS}}, indent=1), encoding="utf-8")


def agent_profile(d, brain):
    """--brain: a Gorilla OpenCode config that knows only that endpoint (XDG_CONFIG_HOME for the run)."""
    root = d / "profile"
    c = root / "gorilla-opencode"
    c.mkdir(parents=True, exist_ok=True)
    cfg = {"localEndpoints": [{"name": "brain", "baseURL": brain}], "extrasChoiceMade": True, "mcpServers": {}}
    (c / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    return root


def opencode_exe():
    exe = shutil.which("gorilla-opencode")
    if not exe and os.environ.get("LOCALAPPDATA"):
        cand = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Gorilla OpenCode" / "gorilla-opencode.exe"
        exe = str(cand) if cand.is_file() else None
    return exe


def unload_local_models(brain, run=subprocess.run):
    """Owner rule: a model the job loaded is unloaded when it ends. -> what was done (one line)."""
    host = re.sub(r"^https?://", "", brain).split("/")[0].split(":")[0]
    if host not in ("127.0.0.1", "localhost", "::1"):
        return f"brain {host} is another computer: its models are its owner's to unload"
    lms = shutil.which("lms") or next((str(p) for p in (Path.home() / ".lmstudio" / "bin" / "lms.exe",
                                                       Path.home() / ".lmstudio" / "bin" / "lms") if p.is_file()), None)
    if not lms:
        return "NOT UNLOADED: no lms command here (unload the model in LM Studio)"
    try:
        r = run([lms, "unload", "--all"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"NOT UNLOADED: lms unload --all failed ({e})"
    return "unloaded: lms unload --all" if r.returncode == 0 else \
        f"NOT UNLOADED: lms unload --all exit {r.returncode}: {(r.stderr or r.stdout).strip()[:160]}"


def work(jid, run=subprocess.run, put=None, deliver=None, sign=None, health_fn=None):
    """The job itself (a detached process on the worker). -> the result, delivered to the boss."""
    put = put or link.put
    deliver = deliver or jobs.deliver
    sign = sign or jobs.sign_result
    job = json.loads(_job_path(jid).read_text(encoding="utf-8"))
    d = work_root() / jid
    tree = d / "tree"
    log = d / "job.log"
    concerns, t0, rc = [], time.time(), None
    boss = job.get("from_host") if link.device(link.household(), job.get("from_host")) else None
    try:
        if tree.exists():
            shutil.rmtree(tree)                       # this job's own copy from an earlier start
        src = Path(job["tree"])
        if src.is_dir():
            shutil.copytree(src, tree, symlinks=True)
        else:
            tree.mkdir(parents=True)
            shutil.copy2(src, tree / src.name)
        concerns += (health_fn or _health)()
        wall = int(job.get("wall_minutes") or 60) * 60
        env = link.link_env()
        if job["kind"] == "run":
            argv = [sys.executable, "-P", "-m", "fieldkit", *job["argv"]]
        else:
            exe = opencode_exe()
            if not exe:
                raise RuntimeError("gorilla-opencode is not installed on this worker")
            model = job.get("model") or DEFAULT_MODEL
            if job.get("brain"):
                env["XDG_CONFIG_HOME"] = str(agent_profile(d, job["brain"]))
                model = model if model.startswith("local.") else f"local.{model}"
            pin_model(tree, model)
            env["GORILLA_OPENCODE_HEADLESS_TIMEOUT"] = f"{max(1, wall // 60)}m"
            argv = [exe, "-p", job["brief"], "-c", str(tree)]
        from .mandate import health
        with health.awake(f"fieldkit job {jid}"), open(log, "w", encoding="utf-8") as out:
            out.write(f"$ {' '.join(argv[:6])}{' ...' if len(argv) > 6 else ''}\n")
            out.flush()
            try:
                rc = run(argv, cwd=str(tree), env=env, stdin=subprocess.DEVNULL, stdout=out,
                         stderr=subprocess.STDOUT, timeout=wall).returncode
            except subprocess.TimeoutExpired:
                rc = None
                concerns.append({"text": f"stopped at the wall cap ({wall // 60} min)",
                                 "fix_offered": "send it again with a larger --wall"})
        if job["kind"] == "agent" and job.get("brain"):
            said = unload_local_models(job["brain"], run=run)
            if said.startswith("NOT"):
                concerns.append({"text": said, "fix_offered": "unload the model in LM Studio"})
        arts, missing = _artefacts(tree, job.get("artefacts") or [], put, boss)
        concerns += [{"text": f"no file matched the artefact pattern {g!r}", "fix_offered": "check the pattern"}
                     for g in missing]
        bad = [a for a in arts if not a["sent"]]
        concerns += [{"text": f"artefact {a['name']} did not reach the boss: {a['error']}",
                      "fix_offered": "the file is still in the job folder; send the job again"} for a in bad]
        status = "pass" if rc == 0 and not missing and not bad else "fail"
        evidence = (f"exit {rc} in {time.time() - t0:.0f} s; {len(arts) - len(bad)} of {len(arts)} artefact(s) "
                    f"sent, sha256 in this result\n{_tail(log)}")
    except Exception as e:  # noqa: BLE001 - every failure becomes a result the boss sees, never silence
        status, arts = "not-run", []
        evidence = f"the job could not run here: {type(e).__name__}: {e}"
    finally:
        try:
            Path(job.get("lock") or "").unlink(missing_ok=True)
        except OSError:
            pass
    r = sign({"id": jid, "kind": job["kind"], "repo": job["repo"], "commit": job["tree_digest"], "status": status,
              "evidence": evidence[:1900], "concerns": concerns, "os": link.os_tag(), "host": socket.gethostname(),
              "at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "artefacts": [{k: a[k] for k in ("name", "size", "sha256")} for a in arts]})
    (d / "result.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    deliver(r)
    return r


def _health():
    """Free temp space and the clock (health.py); never the network: a tree job needs no internet."""
    from .mandate import health
    rows = {"temp space": health.free_temp(health.MIN_FREE_GB), "clock": health.clock(subprocess.run)}
    return [{"text": f"{n}: {detail}", "fix_offered": fix} for n, (ok, detail, fix) in rows.items() if ok is False]


def _artefacts(tree, patterns, put, boss):
    out, missing = [], []
    for g in patterns:
        files = sorted(p for p in tree.glob(g) if p.is_file())[:MAX_ARTEFACTS - len(out)]
        if not files:
            missing.append(g)
        for p in files:
            res = put(p, device=boss)
            out.append({"name": p.relative_to(tree).as_posix(), "size": p.stat().st_size, "sha256": _sha(p),
                        "sent": bool(res.get("ok")), "error": "" if res.get("ok") else res.get("error", ""),
                        "boss_sha256": res.get("sha256")})
    return out, missing


# ---- the boss -------------------------------------------------------------------------------------------------------
def job_key(kind, argv=None, brief=None, brain=None, model=None, artefacts=()):
    blob = json.dumps([kind, argv, brief, brain, model, list(artefacts)], sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:8]


def send(kind, device, tree, project, argv=None, brief=None, brain=None, model=None, artefacts=(), wall=60,
         put=None, call=None, find_mandate=None):
    """Send the tree, then the job. -> (job id, what happened, the put answer)."""
    from .mandate import mandate as M
    put = put or link.put
    call = call or link.call
    mpath = (find_mandate or jobs.mandate_for)(project)
    if mpath is None:
        return None, f"no mandate in force here names {project}: fieldkit mandate status", None
    req = {"verb": "job", "kind": kind, "repo": project, "mandate": M._sha(mpath), "artefacts": list(artefacts),
           "wall_minutes": int(wall), "tree_digest": "0" * 64}
    if kind == "run":
        req["argv"] = list(argv or [])
    else:
        req.update(brief=brief, brain=brain, model=model or "")
    why = check_work(req)
    if why:
        return None, why, None
    t = put(tree, device=device)
    if not t.get("ok") or not t.get("digest"):
        return None, f"the tree did not arrive: {t.get('error')}", t
    jid = f"{kind}-{project}-{t['digest'][:12]}-{job_key(kind, argv, brief, brain, model, artefacts)}"
    req.update(id=jid, tree_digest=t["digest"])
    led = jobs._ledger()
    if led.get(jid, {}).get("status") in ("sent", "pass"):
        return jid, f"already {led[jid]['status']} at {led[jid].get('sent')}", t
    res = call(req, device=device)
    if not res.get("ok"):
        jobs.event("job-refused", id=jid, worker=device, why=res.get("error"))
        return jid, f"not taken: {res.get('error')}", t
    led[jid] = {"kind": kind, "repo": project, "commit": t["digest"], "mandate": req["mandate"], "status": "sent",
                "sent": time.strftime("%Y-%m-%d %H:%M:%S"), "worker_name": device, "worker_said": "started"}
    jobs._save(led)
    jobs.event("job-sent", id=jid, kind=kind, repo=project, tree=t["digest"], worker=device)
    return jid, "sent", t


def artefacts_check(result, received=None):
    """-> [(name, ok, why)]: each artefact the worker signed for, against a file that arrived here with that sha256."""
    rec = received or link.home() / "receipts"
    got = {}
    for p in sorted(rec.glob("*.json")) if rec.is_dir() else []:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for _rel, sha in r.get("sha256") or []:
            got.setdefault(sha, r.get("saved"))
    out = []
    for a in result.get("artefacts") or []:
        where = got.get(a.get("sha256"))
        if not where or not Path(where).is_file():
            out.append((a.get("name"), False, "no file with that sha256 arrived here"))
        elif _sha(where) != a.get("sha256"):
            out.append((a.get("name"), False, f"{where} changed since it arrived"))
        else:
            out.append((a.get("name"), True, where))
    return out


def verified_result(jid, project, digest):
    """The stored result for that job whose signature a pinned worker key checks, or None."""
    for r in jobs.verified_results(project, digest):
        if r.get("id") == jid:
            return r
    return None


def main(argv):
    """fieldkit orchestrate job ... and fieldkit orchestrate work ID (from orchestrate.main)."""
    import argparse
    if argv[:1] == ["work"]:
        if len(argv) != 2:
            print("work JOB-ID")
            return 2
        r = work(argv[1])
        print(f"RESULT {r['id']} {r['status']}")
        return 0 if r["status"] == "pass" else 3
    rest = argv[1:] if argv[:1] == ["job"] else argv
    tail = []
    if "--" in rest:
        i = rest.index("--")
        rest, tail = rest[:i], rest[i + 1:]
    ap = argparse.ArgumentParser(prog="fieldkit orchestrate job", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["run", "agent", "status"])
    ap.add_argument("--device", help="the worker (fieldkit peers link devices)")
    ap.add_argument("--tree", help="the folder (or file) the job works on; sent first with peers link put")
    ap.add_argument("--project", default="Gorilla.Fieldkit", help="the repository the owner's mandate names")
    ap.add_argument("--artefact", action="append", default=[], help="a glob inside the tree to send back (repeat)")
    ap.add_argument("--wall", type=int, default=60, help="minutes the job may run")
    ap.add_argument("--wait", type=float, default=0, help="seconds to wait for the result here (one blocking wait)")
    ap.add_argument("--model", help=f"agent: a model id the worker's Gorilla OpenCode has ({DEFAULT_MODEL})")
    ap.add_argument("--brain", help="agent: an OpenAI-compatible URL as the WORKER reaches it, only for a worker "
                                    "with no provider of its own")
    ap.add_argument("--brief", help="agent: a file with the brief")
    a = ap.parse_args(rest)
    if a.kind == "status":
        print("\n".join(jobs.lines()))
        return 0
    if not a.tree or not a.device:
        print("job run|agent needs --device and --tree")
        return 2
    brief = Path(a.brief).read_text(encoding="utf-8") if a.brief else None
    jid, said, t = send(a.kind, a.device, a.tree, a.project, argv=tail, brief=brief, brain=a.brain, model=a.model,
                        artefacts=a.artefact, wall=a.wall)
    if t and t.get("ok"):
        print(f"TREE  {t['files']} file(s), {t['bytes'] / 1e6:,.1f} MB, digest {t['digest'][:12]} on {a.device}")
    print(f"JOB   {jid or '-'}: {said}")
    if said != "sent":
        return 3
    if not a.wait:
        print("NEXT: fieldkit orchestrate job status, or wait: --wait SECONDS")
        return 0
    e = jobs.wait(jid, a.wait)
    if not e or e.get("status") in (None, "sent"):
        print(f"VERDICT  no result within {a.wait:g} s (the job may still run: fieldkit orchestrate job status)")
        return 3
    r = verified_result(jid, a.project, t["digest"])
    if r is None:
        print(f"VERDICT  {e.get('status')} reported, but no result signed by a pinned worker key: not trusted")
        return 3
    print(f"RESULT {r['status']} on {r['host']} ({r['os']}): {r['evidence'].splitlines()[0]}")
    for c in r.get("concerns") or []:
        print(f"  CONCERN: {c.get('text')}  FIX OFFERED: {c.get('fix_offered')}")
    checks = artefacts_check(r)
    for name, ok, why in checks:
        print(f"  {'ARTEFACT OK ' if ok else 'ARTEFACT BAD'} {name}: {why}")
    good = r["status"] == "pass" and all(ok for _, ok, _ in checks)
    print(f"VERDICT  {'PASS' if good else 'FAIL'}: {r['status']}, {sum(ok for _, ok, _ in checks)} of "
          f"{len(checks)} artefact(s) arrived with the sha256 the worker signed")
    return 0 if good else 3
