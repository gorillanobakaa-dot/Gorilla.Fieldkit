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


def drive(tid, a):
    """One fresh Gorilla OpenCode run per job, until the task is done or blocked.

    A fresh run starts with an empty context holding only its own packet, so the job's
    length never fills a small model's context window."""
    import os
    import subprocess as sp
    exe = a.agent or "gorilla-opencode"
    t = task.load(tid)
    env = {**os.environ, "GORILLA_OPENCODE_HEADLESS_TIMEOUT": a.job_timeout or "45m"}
    # live run 2: the owner's profile fed ~9k tokens of prompt per job and its network time
    # limits cut a slow local model off 6 times; the worker profile and local limits fix both
    from . import answer as ans, worker
    use_tools = bool(getattr(a, "tools", False))
    env.update(worker.environment(worker.write_profile(tools=use_tools)))
    log_path = task.STATE / tid / "drive.log"            # UTF-8, written here (PowerShell's Tee-Object wrote UTF-16)

    def say(msg):
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def model_submits():
        f = task.STATE / tid / "journal.jsonl"
        return sum(1 for l in f.read_text(encoding="utf-8").splitlines()
                   if '"event": "submit"' in l and '"by": "model"' in l) if f.is_file() else 0

    for n in range(1, (a.max_jobs or 200) + 1):
        state = task.packet(tid, by="driver", answer_mode=not use_tools)   # runs any script steps first
        if state["state"] != "MODEL STEP":
            say(f"{state['state']}: {state.get('step', '')} {'; '.join(state.get('why') or [])}")
            return 0 if state["state"] == "DONE" else 3
        step = state["step"]
        say(f"job {n}: {step} (attempt {task.status(tid)['current']['attempts'] + 1}) - fresh {exe} run, "
            f"job text {state['chars']:,} chars")
        before = model_submits()
        t0 = time.time()
        r = sp.run([exe, "-p", (TOOL_PROMPT if use_tools else DRIVE_PROMPT) + state["packet"], "-c", t["workdir"],
                    "-q"], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        answer = (r.stdout or r.stderr).strip()
        task.journal(task.load(tid), "agent-run", step=step, exit=r.returncode, seconds=round(time.time() - t0),
                     answer=answer[-2000:])
        say(f"job {n} finished in {time.time() - t0:.0f} s, exit {r.returncode}. The model said: {answer[-300:]}")
        if not use_tools:
            cur = task.current(task.load(tid))
            target = Path(t["workdir"]) / cur["allowed"][0]
            use_line_ops = bool(getattr(a, "line_ops", False))
            # Run 6 countermeasure: question mode (REMOVE/KEEP) is the default;
            # line operations are a --line-ops fallback for when the hunk needs
            # additions, not just removals.
            if not use_line_ops and cur.get("args", {}).get("hunk"):
                from . import firefox as ff
                hunk = cur["args"]["hunk"]
                _, added, _ = ff.hunk_sides(hunk)
                file_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
                at = ff._anchor(file_lines, [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")])
                if not added and at is not None:
                    auto_rm, uncertain = ff.identify_questions(file_lines, hunk, at)
                    if uncertain:
                        try:
                            decisions = ans.parse_questions(answer, set(uncertain))
                            count, summary, removed_texts = ff.apply_question_answers(
                                target, decisions, hunk, auto_rm)
                            # Record removed texts so check_port's collateral() allows them
                            t2 = task.load(tid)
                            for s2 in t2["steps"]:
                                if s2["id"] == step:
                                    s2["question_removals"] = removed_texts
                            task.save(t2)
                            say(f"  applied question answers: {summary}")
                        except (ans.BadAnswer, OSError) as e:
                            res = task.fail_attempt(tid, f"your answer was not used: {e}")
                            say(f"  harness check: FAILED - {res['why'][0]}")
                            continue
                    else:
                        # No uncertain lines — the harness can do it itself
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
                    res = task.fail_attempt(tid, f"your answer was not used: {e}")
                    say(f"  harness check: FAILED - {res['why'][0]}")
                    continue
        # The harness checks the FILE after every run, whatever the model said or called
        # (live run 3: Gemma made no tool call and claimed "submitted successfully").
        if model_submits() == before:
            try:
                res = task.submit(tid, note=f"checked by the driver after the run; the model said: {answer[-300:]}",
                                  by="driver")
            except task.Refused as e:
                res = {"ok": False, "why": [str(e)]}
            say(f"  harness check: {'PASSED' if res['ok'] else 'FAILED - ' + '; '.join(res['why'])[:300]}")
        else:
            last = [json.loads(l) for l in (task.STATE / tid / "journal.jsonl").read_text(encoding="utf-8").splitlines()
                    if '"event": "submit"' in l][-1]
            say(f"  harness check: {'PASSED' if last['ok'] else 'FAILED - ' + '; '.join(last['why'])[:300]}")
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
