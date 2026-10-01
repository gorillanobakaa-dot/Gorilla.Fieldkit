"""fieldkit build-harness - the Firefox & kernel build harness, from the command line and over MCP.

    fieldkit build-harness latest firefox|kernel
    fieldkit build-harness vault measure firefox | fetch firefox|kernel | verify P | restore P WORKDIR | list
    fieldkit build-harness start firefox [--task ID] [--pin VERSION --source REPO_OR_PATH]
                                         [--budget TOKENS] [--workdir DIR]
    fieldkit build-harness approve TASK          the owner, only here (never over MCP)
    fieldkit build-harness next|status [TASK]
    fieldkit build-harness submit [TASK] [--note TEXT]
    fieldkit build-harness unblock TASK STEP retry|skip      the owner
    fieldkit build-harness log [TASK]
    fieldkit build-harness watch|report [TASK] [--session ID]   the recorder: live, or the whole run

Without TASK, the current task is used (the last one started).
The model gets three MCP tools: build_harness_status, build_harness_next, build_harness_submit.
"""
import json
import time
from pathlib import Path

from . import firefox, task, upstream, vault

CURRENT = task.STATE / "CURRENT"


def current_id(given=None):
    if given:
        return given
    f = task.STATE / "CURRENT"
    if not f.is_file():
        raise task.Refused("no build job has been started; the owner runs: fieldkit build-harness start firefox")
    return f.read_text(encoding="utf-8").strip()


def start_firefox(task_id=None, pin=None, source=None, budget=100_000, workdir=None, harness_root=None):
    from ..core import settings
    harness_root = harness_root or settings.expand("${LOCAL:firefox.root}")
    if pin:
        info = upstream.latest_firefox(versions={"LATEST_FIREFOX_VERSION": pin},
                                       repo=source or upstream.FIREFOX_REPO)
    else:
        info = upstream.latest_firefox(repo=source or upstream.FIREFOX_REPO)
    tid = task_id or f"firefox-{info['version']}"
    wd = Path(workdir) if workdir else vault.root().parent / "Build.Work" / "firefox" / info["version"]
    t = task.start(tid, "firefox-upgrade", wd, firefox.plan(harness_root), budget_tokens=budget,
                   meta={"pinned": info, "harness_root": str(harness_root)})
    task.STATE.mkdir(parents=True, exist_ok=True)
    (task.STATE / "CURRENT").write_text(tid, encoding="utf-8")
    return t


def lines_for(result):
    if "packet" in result:
        return result["packet"]
    out = [f"{k}: {v}" for k, v in result.items() if k not in ("packet",)]
    return "\n".join(out)


def mcp_call(name, args):
    """-> (text, is_error). Three tools only; approval and unblocking are not among them."""
    try:
        # 2026-09-30, live run 1: Gemma filled an optional "task" field with invented text
        # ("build firefox for a specific release", a fictitious mixer.cpp job) and never saw
        # its real job. The model gets no way to name a task: it is always the current one.
        tid = current_id(None)
        if name == "build_harness_status":
            s = task.status(tid)
            cur = s["current"]
            return ("\n".join([f"task {s['task']} ({s['workflow']}): {s['counts']}, {s['checkpoints']} checkpoints",
                               f"current step: {cur['id']} ({cur['kind']}) - {cur['title']}" if cur else "no step left",
                               "NEXT: call build_harness_next"]), False)
        if name == "build_harness_next":
            r = task.packet(tid, by="model")
            if r["state"] == "MODEL STEP":
                return r["packet"], False
            if r["state"] == "BLOCKED":
                return (f"BLOCKED at {r['step']}: {'; '.join(r.get('why') or [])}\n"
                        "NEXT: stop and tell the owner. Do not work around it."), False
            return f"{r['state']}\nNEXT: tell the owner the job is finished.", False
        if name == "build_harness_submit":
            r = task.submit(tid, note=args.get("note", ""), by="model")
            if r["ok"]:
                return f"PASSED the harness check. Checkpoint {r['checkpoint']}.\nNEXT: {r['next']}", False
            return ("FAILED the harness check (your change was put back):\n- " + "\n- ".join(r["why"]) +
                    f"\nAttempts: {r['attempts']}\nNEXT: {r['next']}"), False
        return f"no tool {name!r}", True
    except task.Refused as e:
        return f"REFUSED: {e}", False


# 2026-09-30, live run 1: asked to fetch its own job with a tool, Gemma never got it (it
# invented arguments for the tool instead). The driver now fetches the job and puts it in
# the prompt; the model only edits and submits.
# Live run 3: given edit and submit tools, Gemma made no tool call and wrote detailed reports
# of edits it never made. In answer mode (the default) the model only writes the change as
# text in a fixed form; the harness applies it to the file and checks the result.
DRIVE_PROMPT = (
    "You are doing ONE small job in a Firefox build, and nothing else. Your job is below.\n"
    "Read it, then answer in the exact form it asks for. Do not use any tool.\n\n"
    "=== YOUR JOB ===\n")
TOOL_PROMPT = (
    "You are doing ONE small job in a Firefox build, and nothing else. Your job is below.\n"
    "Use your edit tool to make exactly the change it asks for, only in the file it names.\n"
    "Then stop. The build harness checks the file itself afterwards; your words are not checked, "
    "only the file is. If you do not edit the file, the job fails.\n\n"
    "=== YOUR JOB ===\n")


PACKET_LIMIT = 24_000     # chars, ~6k tokens: a 21-line hunk with its window and questions is ~12k; Gemma has 100k


def drive(tid, a):
    """One fresh Gorilla OpenCode run per job, until the task is done or blocked."""
    import os
    import subprocess as sp
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    import threading

    exe = a.agent or "gorilla-opencode"
    t = task.load(tid)
    env = {**os.environ, "GORILLA_OPENCODE_HEADLESS_TIMEOUT": a.job_timeout or "45m"}
    from . import answer as ans, worker
    use_tools = bool(getattr(a, "tools", False))
    if use_tools and os.environ.get("FIELDKIT_ALLOW_MODEL_TOOLS") != "1":
        # Answer mode (the default) gives the model no tools at all: it can only reply with text and the
        # harness applies it. With tools it can write anywhere the user account can, outside the working copy.
        raise task.Refused("--tools lets the model write anywhere on this computer; it is for experiments only. "
                           "Set FIELDKIT_ALLOW_MODEL_TOOLS=1 yourself if you really mean it")
    env.update(worker.environment(worker.write_profile(tools=use_tools)))
    log_path = task.STATE / tid / "drive.log"

    def say(msg):
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def step_submits(step_id):
        f = task.STATE / tid / "journal.jsonl"
        return sum(1 for l in f.read_text(encoding="utf-8").splitlines()
                   if '"event": "submit"' in l and f'"step": "{step_id}"' in l) if f.is_file() else 0

    lock = threading.Lock()
    in_flight_steps = set()

    def process_job_inner(n, state):
        step = state["step"]
        try:
            t_curr = task.load(tid)
            attempts = next((x["attempts"] for x in t_curr["steps"] if x["id"] == step), 0)
            say(f"job {n}: {step} (attempt {attempts + 1}) - fresh {exe} run, job text {state['chars']:,} chars")

            if state["chars"] > PACKET_LIMIT:
                # not the model's fault: do not burn its attempts (live run 10: three 'too large' in 50 s blocked
                # a step that was already done). Park it for the owner at once, with the size.
                with lock:
                    t_now = task.load(tid)
                    for s2 in t_now["steps"]:
                        if s2["id"] == step:
                            s2["status"], s2["last_why"] = "blocked", [f"the job text is {state['chars']:,} chars, over the "
                                                                       f"{PACKET_LIMIT:,} limit for a small model; shrink the hunk or raise the limit"]
                    task.save(t_now)
                    task.journal(t_now, "too-large", step=step, chars=state["chars"], limit=PACKET_LIMIT)
                    say(f"  job text too large ({state['chars']:,} chars > {PACKET_LIMIT:,}): parked for the owner, no attempt used")
                    in_flight_steps.remove(step)
                return "BLOCKED"

            if not use_tools and "Answer with line operations" in state["packet"]:
                # No question form could be made for this hunk. Line operations are the only thing left and the
                # model has failed every one of them (runs 6-12): this is a person's job, not three wasted attempts.
                with lock:
                    t_now = task.load(tid)
                    for s2 in t_now["steps"]:
                        if s2["id"] == step:
                            s2["status"], s2["last_why"] = "blocked", ["the harness could neither merge this hunk nor turn it into "
                                                                       "REMOVE/KEEP questions; a person ports it (edit the file, then "
                                                                       "`build-harness submit`)"]
                    task.save(t_now)
                    task.journal(t_now, "needs-person", step=step)
                    say("  no question form possible: parked for a person, no model attempt used")
                    in_flight_steps.remove(step)
                return "BLOCKED"

            before = step_submits(step)
            t0 = time.time()
            r = sp.run([exe, "-p", (TOOL_PROMPT if use_tools else DRIVE_PROMPT) + state["packet"], "-c", t["workdir"],
                        "-q"], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            answer = (r.stdout or r.stderr).strip()

            with lock:
                task.journal(task.load(tid), "agent-run", step=step, exit=r.returncode, seconds=round(time.time() - t0),
                             answer=answer[-2000:])
                say(f"job {n} finished in {time.time() - t0:.0f} s, exit {r.returncode}. The model said: {answer[-300:]}")

                if not use_tools:
                    t_curr = task.load(tid)
                    cur = next(x for x in t_curr["steps"] if x["id"] == step)
                    target = Path(t_curr["workdir"]) / cur["allowed"][0]
                    use_line_ops = bool(getattr(a, "line_ops", False))
                    if not use_line_ops and cur.get("args", {}).get("hunk"):
                        from . import firefox as ff
                        hunk = cur["args"]["hunk"]
                        _, added, _ = ff.hunk_sides(hunk)
                        file_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
                        at = ff._anchor(file_lines, [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")])
                        if at is not None:
                            auto_rm, uncertain = ff.identify_questions(file_lines, hunk, at)
                            placeable = not added or ff.placeable(file_lines, hunk, at)
                            if uncertain and placeable:
                                try:
                                    decisions = ans.parse_questions(answer, set(uncertain))
                                    count, summary, removed_texts = ff.apply_question_answers(target, decisions, hunk, auto_rm)
                                    t2 = task.load(tid)
                                    for s2 in t2["steps"]:
                                        if s2["id"] == step:
                                            s2["question_removals"] = removed_texts
                                    task.save(t2)
                                    say(f"  applied question answers: {summary}")
                                except (ans.BadAnswer, OSError) as e:
                                    res = task.fail_attempt(tid, f"your answer was not used: {e}", step_id=step)
                                    say(f"  harness check: FAILED - {res['why'][0]}")
                                    in_flight_steps.remove(step)
                                    return "CONTINUE"
                            else:
                                use_line_ops = True
                        else:
                            use_line_ops = True
                    else:
                        use_line_ops = True

                    if use_line_ops:
                        try:
                            count, summary = ans.apply(target, answer)
                            say(f"  applied the answer: {count} operation(s): {summary}")
                        except (ans.BadAnswer, OSError) as e:
                            res = task.fail_attempt(tid, f"your answer was not used: {e}", step_id=step)
                            say(f"  harness check: FAILED - {res['why'][0]}")
                            in_flight_steps.remove(step)
                            return "CONTINUE"

                if step_submits(step) == before:
                    try:
                        res = task.submit(tid, note=f"checked by the driver after the run; the model said: {answer[-300:]}",
                                          by="driver", step_id=step)
                    except task.Refused as e:
                        res = {"ok": False, "why": [str(e)]}
                    say(f"  harness check: {'PASSED' if res['ok'] else 'FAILED - ' + '; '.join(res['why'])[:300]}")
                else:
                    last = [json.loads(l) for l in (task.STATE / tid / "journal.jsonl").read_text(encoding="utf-8").splitlines()
                            if '"event": "submit"' in l and f'"step": "{step}"' in l][-1]
                    say(f"  harness check: {'PASSED' if last['ok'] else 'FAILED - ' + '; '.join(last['why'])[:300]}")

                in_flight_steps.remove(step)
                return "CONTINUE"
        except Exception as e:
            with lock:
                try:
                    res = task.fail_attempt(tid, f"Internal error during model execution: {e}", step_id=step)
                except Exception:
                    pass
                say(f"job {n} crashed: {e}")
                if step in in_flight_steps:
                    in_flight_steps.remove(step)
            return "CRASH"

    jobs_to_run = a.max_jobs or 200
    # ONE job at a time, always: the working copy is one git repository, a failed attempt resets all of it, and
    # one local model serves one request at a time. (The overnight run of 2026-10-01 ran 8 jobs in parallel:
    # git locks collided and eight jobs crashed.)
    from . import preflight
    pre = preflight.run(tid, model=True, fix_locks=False)
    if not all(r["ok"] for r in pre):
        say("PREFLIGHT failed - no job started")
        for l in preflight.lines(pre):
            say(l)
        return 5
    # sync: the tree decides what is done, not the record. A step the record calls done but the tree does not
    # back goes back to pending before any job is handed out (audit of 2026-10-01: 208 such steps overnight).
    from . import verify as vf
    amended = vf.add_missing_new_file_steps(tid)
    if amended:
        say(f"SYNC: plan amended, new-files steps added: {amended}")
    rep = vf.verify(tid)
    reopened = vf.reopen(tid, rep)
    if reopened:
        say(f"SYNC: {len(reopened)} step(s) the record called done are not in the tree; reopened: {reopened[:5]}")
    for name, ok, ev in vf.problems(rep):
        if not ok and not name.startswith("tree: every step"):
            say(f"SYNC WARNING: {name}: {ev}")
    crashes = 0
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = set()
        n = 1

        while n <= jobs_to_run or futures:
            with lock:
                while n <= jobs_to_run and not futures:
                    state = task.packet(tid, by="driver", answer_mode=not use_tools, in_flight_steps=in_flight_steps)
                    if state["state"] == "WAITING":
                        break
                    if state["state"] != "MODEL STEP":
                        say(f"{state['state']}: {state.get('step', '')} {'; '.join(state.get('why') or [])}")
                        if not futures:
                            return 0 if state["state"] == "DONE" else 3
                        break
                    step = state["step"]
                    in_flight_steps.add(step)
                    futures.add(executor.submit(process_job_inner, n, state))
                    n += 1

            if not futures:
                break

            done, futures = wait(futures, return_when=FIRST_COMPLETED)
            for f in done:
                res = f.result()
                crashes = crashes + 1 if res == "CRASH" else 0
                if crashes >= 2:                       # something is wrong with the machine, not the model
                    say("STOPPED: two jobs in a row crashed (see above). Fix the cause; nothing further was started.")
                    return 4
                if res in ("DONE", "BLOCKED"):
                    return 0 if res == "DONE" else 3
                    
    return 3


def run(a, emit):
    """The `fieldkit build-harness` command. `emit(obj, lines_fn)` prints JSON or text."""
    act = a.action
    if act == "latest":
        return emit(upstream.latest(a.args[0]), None) or 0
    if act == "vault":
        sub, rest = a.args[0], a.args[1:]
        if sub == "measure":
            m = vault.measure_firefox()
            return emit(m, lambda m: print(f"Firefox {m['version']}: {m['bytes'] / 1e6:,.0f} MB to download ({m['archive']})")) or 0
        if sub == "fetch":
            return emit(vault.fetch_firefox() if rest[0] == "firefox" else vault.fetch_kernel(), None) or 0
        if sub == "verify":
            r = vault.verify(rest[0], a.version)
            emit(r, lambda r: print(("INTACT " if r["intact"] else "DAMAGED ") + r["vault"] + "".join(f"\n  - {p}" for p in r["problems"])))
            return 0 if r["intact"] else 3
        if sub == "restore":
            return emit(vault.restore(rest[0], rest[1], a.version), None) or 0
        if sub == "list":
            return emit(vault.listing(), lambda rows: [print(f"{r['product']:8} {r['version']:10} {r['fetched']}  {r['path']}") for r in rows]) or 0
    if act == "start":
        if a.args[0] != "firefox":
            raise SystemExit("start: firefox (the kernel workflow comes next)")
        t = start_firefox(a.task, a.pin, a.source, a.budget, a.workdir, harness_root=a.harness)
        return emit({"task": t["id"], "steps": [s["id"] for s in t["steps"]], "workdir": t["workdir"]},
                    lambda r: print(f"task {r['task']} planned: {', '.join(r['steps'])}\n"
                                    f"working copy: {r['workdir']}\n"
                                    f"NEXT: the owner reads the plan and runs: fieldkit build-harness approve {r['task']}")) or 0
    words = [x for x in a.args if x != "baseline"]            # `audit baseline` is not a task name
    tid = current_id(a.task or (words[0] if words else None))
    if act == "approve":
        t = task.approve(tid, "owner (command line)")
        return emit({"task": tid, "approved": t["approved"]}, lambda r: print(f"approved {tid}")) or 0
    if act == "status":
        return emit(task.status(tid), None) or 0
    if act == "next":
        r = task.packet(tid)
        return emit(r, lambda r: print(lines_for(r))) or 0
    if act == "submit":
        r = task.submit(tid, a.note or "")
        emit(r, None)
        return 0 if r["ok"] else 3
    if act == "preflight":
        from . import preflight
        rows = preflight.run(tid, build=bool(a.build), model=bool(a.model), fix_locks=bool(a.fix_locks), fan_required=bool(a.build))
        emit(rows, lambda rows: print("\n".join(preflight.lines(rows))))
        return 0 if all(r["ok"] for r in rows) else 3
    if act == "snapshot":
        from ..core import settings
        from . import snapshot as snap
        hr = a.harness or settings.expand("${LOCAL:firefox.root}")
        version = a.version or (a.args[0] if a.args else None)
        if not version:
            raise task.Refused("snapshot: say which version the live tree is, e.g. --version 155.0.1")
        out = Path(a.out) if a.out else vault.root().parent / "Build.Work" / f"snapshot-{version}"
        m = snap.capture(hr, version, out)
        print("\n".join(snap.lines_capture(m)))
        counts, dead = snap.compare_curated(hr)
        print(f"curated patch set judged by the live tree: {sum(c['APPLIED'] for c in counts.values())} hunks alive, {len(dead)} dead "
              f"(not in the build); list in {out / 'CURATED-DEAD.txt'}")
        (out / "CURATED-DEAD.txt").write_text("\n".join(dead) + "\n", encoding="utf-8")
        if a.prove:
            r = snap.prove(m, out / "proof-tree")
            print("\n".join(snap.lines_proof(r)))
            return 0 if r["ok"] else 3
        return 0
    if act == "verify":
        from . import verify as vf
        rep = vf.verify(tid)
        if getattr(a, "reopen", False):
            rep["reopened"] = vf.reopen(tid, rep)
        emit(rep, lambda rep: print("\n".join(vf.lines(rep) + ([f"reopened: {rep['reopened']}"] if rep.get("reopened") else []))))
        return 0 if all(ok for _, ok, _ in vf.problems(rep)) else 3
    if act == "deferred":
        from . import deferred
        rest = a.args[1:]
        if not rest:
            rows = deferred.listing(tid)
            return emit(rows, lambda rows: print("\n".join([f"{len(rows)} step(s) are parked for the owner:"] +
                                                           [f"  {i}  ({w})" for i, w in rows] +
                                                           ["", "to read the explanation for one: fieldkit build-harness deferred TASK STEP"]))) or 0
        if getattr(a, "do", None):
            print(deferred.apply_drop(tid, rest[0], a.do))
            return 0
        b = deferred.build(tid, rest[0])
        emit(b, lambda b: print("\n".join(deferred.show(b, plain=not a.technical))))
        return 0
    if act == "brief":
        from . import decision
        b = decision.owner_file_edit(a.args[0], a.args[1])
        if getattr(a, "do", None):
            from ..core import settings
            print(decision.apply(b, "revert", a.do, settings.ROOT / "_private"))
            return 0
        emit(b, lambda b: print("\n".join(decision.show(b, plain=not a.technical))))
        return 0
    if act in ("build-gate", "build-verify"):
        from . import compile as cg
        rows = cg.gate(tid, harness_root=a.harness) if act == "build-gate" else cg.verify(tid)
        emit(rows, lambda rows: print("\n".join(cg.lines(rows, act.upper()))))
        if act == "build-gate" and all(r["ok"] for r in rows):
            print("\nThe tree is recorded. The OWNER now compiles it, from a normal terminal (this changes the power scheme):\n"
                  "  1. in Gorilla.firefox: python harness\\gorilla_build.py build\n"
                  "  2. python harness\\gorilla_build.py package\n"
                  "  3. fieldkit build-harness build-verify " + tid)
        return 0 if all(r["ok"] for r in rows) else 3
    if act == "audit":
        from . import audit
        if a.args and a.args[0] == "baseline":
            snap = audit.baseline(tid if tid else "firefox-155.0.1")
            return emit({"baseline": str(audit.BASE), "tests": snap["tests"]["line"]}, None) or 0
        r = audit.run(tid)
        emit(r, lambda r: print("\n".join(audit.lines(r))))
        return 0 if all(c["ok"] for c in r["checks"]) else 3
    if act == "rewind":
        return emit(task.rewind(tid, a.args[1]), None) or 0
    if act == "unblock":
        task.unblock(tid, a.args[1], a.args[2])
        return emit(task.status(tid), None) or 0
    if act in ("report", "watch"):
        from . import recorder
        from ..core import settings
        t = task.load(tid)
        since = int(time.mktime(time.strptime(t["created"], "%Y-%m-%d %H:%M:%S")))
        mcp_logs = sorted((settings.ROOT / "state" / "recorder").glob("mcp-*.jsonl"))

        def findings():
            sid, sess = recorder.session_events(a.session, since=since)
            journal = recorder.jsonl(task.STATE / tid / "journal.jsonl")
            mcp = [m for f in mcp_logs for m in recorder.jsonl(f)]
            return sid, recorder.detect(sess, journal, mcp, budget_tokens=t["budget_tokens"], workdir=t["workdir"])
        if act == "report":
            sid, f = findings()
            out = task.STATE / tid / "recorder-report.json"
            out.write_text(json.dumps({"session": sid, "findings": f, "summary": recorder.summary(f)}, indent=1),
                           encoding="utf-8")
            emit({"session": sid, "findings": f},
                 lambda r: print(f"session {sid}\n" + "\n".join(recorder.lines(f)) + f"\n\nsaved: {out}"))
            return 3 if any(x["level"] == "incident" for x in f) else 0
        seen = set()
        print(f"watching task {tid} (Ctrl+C to stop) - every incident is printed as it appears")
        while True:
            sid, f = findings()
            for x in f:
                key = (x["category"], x["evidence"])
                if key not in seen:
                    seen.add(key)
                    at = time.strftime("%H:%M:%S")
                    print(f"{at}  {x['level'].upper():8} {x['category']}: {x['evidence']}", flush=True)
            s = task.status(tid)
            print(f"\r{time.strftime('%H:%M:%S')}  session {sid or '-'}  steps {s['counts']}  "
                  f"checkpoints {s['checkpoints']}   ", end="", flush=True)
            time.sleep(5)
    if act == "drive":
        return drive(tid, a)
    if act == "compare":
        from . import compare as cmp
        if not a.reference:
            raise SystemExit("compare needs --reference DIR (the person-made result to hold the job against)")
        t = task.load(tid)
        files = cmp.changed_by_job(t["workdir"], t["meta"]["upstream"]["commit"])
        rows = cmp.compare(t["workdir"], a.reference, files, out_dir=task.STATE / tid)
        emit(rows, lambda r: print("\n".join(cmp.lines(r)) + f"\n\nsaved: {task.STATE / tid / 'compare.diff'}"))
        return 0 if all(r["result"] == "same" for r in rows) else 3
    if act == "log":
        f = task.STATE / tid / "journal.jsonl"
        for line in (f.read_text(encoding="utf-8").splitlines() if f.is_file() else [])[-40:]:
            e = json.loads(line)
            print(e["t"], e["event"], e.get("step", ""), json.dumps({k: v for k, v in e.items()
                                                                    if k not in ("t", "task", "event", "step")})[:160])
        return 0
    raise SystemExit(f"unknown build-harness action {act}")
