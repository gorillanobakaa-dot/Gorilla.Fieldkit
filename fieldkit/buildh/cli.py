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
    fieldkit build-harness briefs TASK [--json] [--technical]   every open decision, as a full brief (fieldkit/briefs)
    fieldkit build-harness brief show ID [--task TASK]          one brief in full (logged as shown)
    fieldkit build-harness decide ID OPTION --words "..."       the maintainer records the answer (real terminal only)
    fieldkit build-harness migrate plan|sitrep|seed|check|gate|init|advance|work|park|unpark|intents|intake|consistency TASK
                                                                migration control: stages S0-S9, gates, SITREP, drift
                                                                guard (fieldkit/migrate); --park "why" on record, repair,
                                                                build-run or decide parks a ticket instead of running
    fieldkit build-harness netbench [TASK] [--bench B1,...] [--profile normal|satellite|slow] [--install-dir D]
                                       [--label NAME] [--links L,...] [--repeat N] [--out DIR]
                                                                network benches B1-B5 against local servers through an
                                                                emulated link (fieldkit/netbench)
    fieldkit build-harness netbench compare A B                 before/after deltas of two netbench results
    fieldkit build-harness probe-compare A B [prefix=TOK] [keys=2] [value=last|all] [limit=N]
                                                                two saved probe outputs: LOST / changed / now-set
                                                                (fieldkit/buildh/probe_compare.py)
    fieldkit build-harness stats [TASK] [--since D] [--until D] the journal counted: builds, attempts, stops, hand
                                                                edits, automation, installs, heat (buildh/stats.py)
    fieldkit build-harness leakgate-status [TASK] [run=STAMP] [thermal=LOG] [hot=70]
                                                                a (running) gate: runs started of expected, alive,
                                                                result, CPU since it started (leakgate/status.py)
    fieldkit build-harness leakgate-context [TASK] [HOST ...] [hosts=FILE.json] [run=STAMP] [width=160]
                                                                every occurrence of each host in the install's omni.ja,
                                                                path:line + context (leakgate/audit.py host_context)
    fieldkit build-harness leakgate-proposal [TASK] RESULT.json ... [override=F] [label=L] [notes=F] [review=F]
                                                                reviewers' rows -> proposed-dispositions-<label>.json,
                                                                approval null; the owner approves at a real terminal

Without TASK, the current task is used (the last one started).
The model gets four MCP tools: build_harness_status, build_harness_next, build_harness_submit, and the read-only
build_harness_briefs. It never gets a tool that records a decision.
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
        if name == "build_harness_briefs":
            # read-only: a model shows the person the brief; it can never record the answer (no decide tool exists)
            from ..briefs import producers, schema
            ctx = producers.Context(tid)
            want = str(args.get("id") or "").strip()
            if want:
                try:
                    b = producers.find(ctx, want)
                except schema.Invalid as e:
                    return f"{e}\nNEXT: call build_harness_briefs without an id to list the open briefs.", False
                return ("\n".join(schema.render(b)) + "\nNEXT: show this whole brief to the person, word for word. Do not "
                        "shorten it into a question. Only the person can answer, at a real terminal."), False
            got = producers.collect(ctx)
            text = producers.listing_lines(got, tid, limit=25)
            if got["briefs"]:
                text += ["", "The first brief in full:", ""] + schema.render(got["briefs"][0])
            return "\n".join(text) + ("\nNEXT: show the person the brief in full; never ask a bare question. To read another, "
                                      "call build_harness_briefs with its id."), False
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
    for sid, why, new in vf.relocate_missing(tid):
        say(f"SYNC: {sid}: {why}; now {new}")
    rep = vf.verify(tid)
    for sid in vf.add_dedupe_steps(tid, rep):
        say(f"SYNC: plan amended, dedupe step added: {sid}")
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
                if res == "DONE":
                    return 0
                # a parked job ("BLOCKED") is a person's; the plan goes on and packet() ends the run with the list
                    
    return 3


def _briefs(act, a, emit):
    """briefs TASK [--json] [--technical] | brief show ID [--task TASK] | brief show TASK ID | decide ID OPTION --words W"""
    from ..briefs import producers, record, schema
    try:
        if act == "briefs":
            tid = current_id(a.task or (a.args[0] if a.args else None))
            got = producers.collect(producers.Context(tid))
            if getattr(a, "technical", False) and not getattr(a, "json", False):
                for b in got["briefs"]:
                    print("\n".join(schema.render(b, technical=True)) + "\n" + "=" * 100)
            emit({"task": tid, "briefs": got["briefs"], "problems": got["problems"]},
                 lambda r: print("\n".join(producers.listing_lines(got, tid))))
            return 3 if got["briefs"] or got["problems"] else 0
        if act == "brief":
            rest = a.args[1:]
            if not rest:
                raise task.Refused("brief show ID [--task TASK]: which brief? list them with: fieldkit build-harness briefs TASK")
            tid = current_id(a.task or (rest[0] if len(rest) > 1 else None))
            b = producers.find(producers.Context(tid), rest[-1])
            digest = record.mark_shown(tid, b)
            emit({**b, "sha256": digest}, lambda _: print("\n".join(schema.render(b, technical=getattr(a, "technical", False)))))
            return 0
        if len(a.args) != 2:
            raise task.Refused("decide BRIEF-ID OPTION --words \"your own words\" [--task TASK]")
        tid = current_id(a.task)
        r = record.decide(tid, a.args[0], a.args[1], getattr(a, "words", None))
        emit(r, lambda r: print(f"RECORDED {r['brief']} -> {r['option']} (brief sha256 {r['sha256'][:12]}): {r['recorded']}"))
        return 0
    except schema.Invalid as e:
        raise task.Refused(str(e))


def _change_args(args, tid, inst, tmp_prefix, say=print):
    """omni= / file= / add= / sub= / tree-since= / tree-until= words of a command line -> (options, change dict, notes).
    The change goes into a throwaway COPY of the install (probe, visual); `options` are the other key=value words.
    tree-since=build means the commit the installed build was compiled from."""
    from . import probe as pb
    kv = [x for x in args if "=" in x]
    opts = {k: v for k, v in (x.split("=", 1) for x in kv if not x.startswith(("omni=", "file=", "add=", "sub=")))}
    since = opts.pop("tree-since", None)
    until = opts.pop("tree-until", "HEAD")
    change = {"omni": dict(x[5:].rsplit("=", 1) for x in kv if x.startswith("omni=")),
              "files": dict(x[5:].rsplit("=", 1) for x in kv if x.startswith("file=")),
              "added": dict(x[4:].rsplit("=", 1) for x in kv if x.startswith("add=")),
              "subs": [tuple(x[4:].split("=>", 1)) for x in kv if x.startswith("sub=") and "=>" in x]}
    notes = []
    if since:
        # the source changes since a commit ("build": the commit the installed build was compiled from)
        import tempfile as _tf
        from . import compile as _cg
        if since == "build":
            since = json.loads(_cg._record_path(tid).read_text(encoding="utf-8"))["head"]
        got, skipped = pb.tree_since(task.load(tid)["workdir"], since, inst, _tf.mkdtemp(prefix=tmp_prefix), until)
        change["omni"].update({k: str(v) for k, v in got.items()})
        notes.append(f"tree-since {since[:10]}: {len(got)} member(s) replaced; {len(skipped)} change(s) not applied:")
        notes += [f"  {path}: {why}" for path, why in skipped]
    for n in notes:
        say(f"  {n}")
    return opts, {k: v for k, v in change.items() if v}, notes


def run(a, emit):
    """The `fieldkit build-harness` command. `emit(obj, lines_fn)` prints JSON or text."""
    act = a.action
    if act == "window":
        # window COMMAND [ARGS ...]: that build-harness command in its own visible window that outlives this session
        from . import window
        r = window.launch(list(a.args))
        print(f"started in its own window (PID {r['pid']}); everything it prints is also in {r['log']}")
        return 0
    if act == "latest":
        return emit(upstream.latest(a.args[0]), None) or 0
    if act == "probe-compare":
        # probe-compare A B [prefix=TOK] [keys=2] [value=last|all] [limit=200]: two saved probe outputs, read only
        from . import probe_compare as pc
        files = [x for x in a.args if "=" not in x]
        opts = dict(x.split("=", 1) for x in a.args if "=" in x)
        if len(files) != 2:
            raise task.Refused("probe-compare A B: two files holding a probe's output (before, after)")
        r = pc.run(files[0], files[1], prefix=opts.get("prefix", "TOK"), keys=int(opts.get("keys", 2)),
                   value=opts.get("value", "last"))
        emit(r, lambda r: print("\n".join(pc.lines(r, limit=int(opts.get("limit", 200))))))
        return 3 if r["LOST"] else 0
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
    if act == "migrate":                                      # migration control: plan, gates, SITREP (fieldkit/migrate)
        from ..migrate import cli as mc
        return mc.run(a, emit, current_id)
    if act == "netbench":                                     # network benches B1-B5, local servers only (fieldkit/netbench)
        from ..netbench import cli as nbc
        return nbc.run(a, emit, current_id)
    # the drift guard: under migration control, record/repair/build-run/decide need a work item of the current stage
    # (or --park "why"), and writers (record, repair, build-run, install, leakgate, submit) run one at a time
    from ..migrate import guard as _guard
    if _guard.enforce(act, a, current_id) == "parked":
        return 0
    if act in ("briefs", "decide") or (act == "brief" and a.args and a.args[0] == "show"):
        return _briefs(act, a, emit)
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
        r = task.submit(tid, a.note or "", by="hand" if getattr(a, "hand", False) else "cli")
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
    if act == "install":
        from . import install as inst
        if getattr(a, "restore", None):
            target = a.install_dir or inst.find_install()
            if not target:
                r = inst.no_target()
                emit(r, lambda r: print(f"RESTORE REFUSED: {r['why']}\n{r['next']}"))
                return 3
            info = inst.restore(a.restore, target, say=lambda m: print(m, flush=True))
            emit({"ok": True, "restored": info}, lambda r: print(f"restored: {r['restored']}"))
            return 0
        r = inst.run(tid, do_backup=not getattr(a, "no_backup", False), say=lambda m: print(m, flush=True), install_dir=a.install_dir)
        emit(r, lambda r: print("INSTALL " + ("OK" if r.get("ok") else "NOT OK: " + str(r.get("why") or r.get("rc")))
                                + (f"\n{r['next']}" if r.get("next") else "")))
        return 0 if r.get("ok") else 3
    if act == "export-hand":
        from . import export as ex, buildrun
        t = task.load(tid)
        owner = Path(buildrun._owner_root(t))
        import json as _js
        pol = _js.loads((owner / "config" / "patch_policy.json").read_text(encoding="utf-8"))
        r = ex.export(tid, owner / pol["patchset_root"], t["meta"]["upstream"]["version"].split(".")[0], say=lambda m: print(m, flush=True))
        print("register new groups in config/patch_policy.json if they are not there; then prove the set: build-harness replay " + tid)
        return 0
    if act == "release-check":
        from . import releasecheck as rc_
        rows = rc_.run(tid, say=lambda m: print(m, flush=True), skip_post_install="--fast" in a.args or getattr(a, "build", False))
        return 0 if all(r["ok"] for r in rows) else 3
    if act == "probe":
        # probe TASK js=<file|name> [url=...] [wait=S] [omni=<jar>:<member>=<file> ...] [file=..] [add=..]: a question to (or a JS fix
        # tried in) a throwaway copy of the installed build, without a build
        from . import probe as pb, buildrun
        from . import install as _inst
        inst = a.install_dir or _inst.find_install()
        if "js" not in dict(x.split("=", 1) for x in a.args[1:] if "=" in x):
            raise task.Refused("probe needs js=<file or one of " + ", ".join(p.stem for p in pb.PROBES.glob("*.js")) + ">")
        if not inst:
            raise task.Refused("no installed build found: give --install-dir")
        opts, change, _notes = _change_args(a.args[1:], tid, inst, "gprobe_tree_", say=lambda m: print(m, flush=True))
        r = pb.run(inst, opts["js"], url=opts.get("url", "about:blank"), wait=float(opts.get("wait", 15)),
                   omni=change.get("omni"), timeout=float(opts.get("timeout", 90)), say=lambda m: print(m, flush=True),
                   files=change.get("files"), added=change.get("added"), subs=change.get("subs"))
        for l in r["timeline"]:                  # the probe's lines and its local pages' requests, in time order
            print("  " + l)
        print(f"PROBE {'DONE' if r['done'] else 'TIMED OUT'} in {r['seconds']} s" + (f"; replaced {r['patched']}" if r["patched"] else ""))
        return 0 if r["done"] else 3
    if act == "satellite":
        # satellite TASK [--install-dir D] [omni=..] [sub=..] [tree-since=..]: Satellite mode judged on a copy - identity
        # and scripts per level and per kind of site, call sites included (satellite.py)
        from . import satellite, install as _inst
        inst = a.install_dir or _inst.find_install()
        if not inst:
            raise task.Refused("no installed build found: give --install-dir")
        opts, change, notes = _change_args(a.args[1:], tid, inst, "gprobe_tree_", say=lambda m: print(m, flush=True))
        rows = satellite.rows(inst, say=lambda m: print(m, flush=True), need_call_list=opts.get("need-list", "1") != "0",
                              omni=change.get("omni"), files=change.get("files"), added=change.get("added"), subs=change.get("subs"))
        for n in notes:
            print("  note: " + n)
        for row in rows:
            print(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence']}")
        return 0 if all(r["ok"] for r in rows) else 3
    if act == "about-pages":
        # about-pages TASK [--install-dir D] [only=a,b] [walk=1 dwell=S countdown=S] [tree-since=..] [sub=..] [omni=..] [timeout=S]: every about: page of a copy
        # of the installed build opened, read and judged, kept and compared with the last other build (aboutpages.py)
        from . import aboutpages as ap, install as _inst
        inst = a.install_dir or _inst.find_install()
        if not inst:
            raise task.Refused("no installed build found: give --install-dir")
        opts, change, notes = _change_args(a.args[1:], tid, inst, "gprobe_tree_", say=lambda m: print(m, flush=True))
        info = _inst.installed(inst) or {}
        only = tuple(x.strip().replace("about:", "") for x in opts.get("only", "").split(",") if x.strip())
        walk = opts.get("walk", "0") not in ("0", "no", "false", "")
        dwell = int(float(opts["dwell"]) * 1000) if "dwell" in opts else (6000 if walk else None)
        on_line = None
        visible = walk and opts.get("visible", "1") not in ("0", "no", "false")
        if walk and not visible:              # visible=0: the same walk headless (to test it without a window)
            def on_line(l):
                t = ap.live(l)
                if t:
                    print(t, flush=True)
        if visible:
            import time as _t
            # the window takes the foreground: say so and count down first (announce before foreground tests)
            print("A Gorilla window will open on this screen and walk every link of about:about, "
                  f"{dwell / 1000:g} s per page. It is a throwaway copy with its own profile, behind a dead proxy;", flush=True)
            print("your own browser and profile are not touched. Do not type into it while it runs.", flush=True)
            for n in range(int(opts.get("countdown", 15)), 0, -1):
                print(f"  starting in {n} s ...", flush=True)
                _t.sleep(1)
            def on_line(l):
                t = ap.live(l)
                if t:
                    print(t, flush=True)
        r = ap.run(inst, info.get("build_id"), say=lambda m: print(m, flush=True), timeout=float(opts.get("timeout", 900 if not walk else 1800)),
                   only=only, walk=walk, visible=visible, dwell=dwell, on_line=on_line, omni=change.get("omni"), files=change.get("files"), added=change.get("added"), subs=change.get("subs"))
        for n in notes:
            print("  note: " + n)
        for row in r["rows"]:
            print(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence']}")
        if r["changes"] is None:
            print("  compared: no earlier run of another build kept")
        else:
            print(f"  compared with the run of {r['previous']}: " + (f"{len(r['changes'])} change(s)" if r["changes"] else "no change"))
            for l in r["changes"]:
                print("    " + l)
        print(f"  kept: {r['path']}")
        return 0 if all(row["ok"] for row in r["rows"]) else 3
    if act == "weigh":
        # weigh TASK [pages=a,b] [reps=N] [clean=0 settle=S] [before=DIR] [tree-since=..] [tree-until=..] [sub=A=>B ...] [omni=...]:
        # RAM and CPU per page, the installed build against the same build with the change (fieldkit/buildh/weigh.py)
        from . import probe as pb, weigh as wg, install as _inst
        import json as _js
        import tempfile as _tf
        from . import compile as _cg
        kv = [x for x in a.args[1:] if "=" in x]
        opts = {k: v for k, v in (x.split("=", 1) for x in kv if not x.startswith(("omni=", "sub=")))}
        omni = dict(x[5:].rsplit("=", 1) for x in kv if x.startswith("omni="))
        subs = [tuple(x[4:].split("=>", 1)) for x in kv if x.startswith("sub=") and "=>" in x]
        inst = a.install_dir or _inst.find_install()
        if not inst:
            raise task.Refused("no installed build found: give --install-dir")
        notes = []
        since = opts.get("tree-since")
        if since:
            if since == "build":
                since = _js.loads(_cg._record_path(tid).read_text(encoding="utf-8"))["head"]
            got, skipped = pb.tree_since(task.load(tid)["workdir"], since, inst, _tf.mkdtemp(prefix="gweigh_tree_"),
                                         opts.get("tree-until", "HEAD"))
            omni.update({k: str(v) for k, v in got.items()})
            notes.append(f"tree {since[:10]}..{opts.get('tree-until', 'HEAD')[:10]}: {len(got)} packaged member(s) changed")
            notes += [f"not applied to the copy: {p} ({why})" for p, why in skipped]
        if not omni and not subs and not opts.get("before"):
            raise task.Refused("weigh needs a change: tree-since=, sub=, omni= or before=<another installed build>")
        pages = tuple(p for p in opts.get("pages", ",".join(wg.DEFAULT_PAGES)).split(",") if p)
        clean = opts.get("clean", "1") not in ("0", "no", "false")
        settle = int(opts.get("settle", 6 if clean else 75))
        if not clean:
            notes.append(f"left running: nothing cleaned before a page, measured {settle} s after opening it")
        before = opts.get("before")
        if before:
            notes.append(f"before = {before} (another installed build), after = {inst}")
        r = wg.run(inst, pages=pages, reps=int(opts.get("reps", 3)), omni=omni or None, subs=subs or None,
                   say=lambda m: print(m, flush=True), clean=clean, settle=settle, before_install=before)
        path = wg.save(r, notes)
        notes.append(f"kept: {path}")
        notes.append("CPU while a page opens is noisy: compare the medians and look at the spread of the runs")
        emit(r["summary"], lambda s: print("\n".join(wg.lines(s, notes))))
        return 0
    if act == "ui-check":
        # ui-check TASK [--static] [--install-dir D]: the UI rules on the tree, then (unless --static) the readable-
        # and-working probe on a copy of the installed build (fieldkit/buildh/uicheck.py)
        from . import uicheck, install as _inst
        t = task.load(tid)
        rows = [{"check": f"UI rule {r['rule']}", "ok": r["ok"], "evidence": r["evidence"]} for r in uicheck.lint(t["workdir"])]
        if not getattr(a, "static", False):
            inst = a.install_dir or _inst.find_install()
            if not inst:
                raise task.Refused("no installed build found: give --install-dir, or --static for the tree rules only")
            rows += uicheck.probe_rows(inst)
        for r in rows:
            print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['check']}: {r['evidence'][:300]}")
        print("UI OK" if all(r["ok"] for r in rows) else "UI NOT OK")
        return 0 if all(r["ok"] for r in rows) else 3
    if act == "techniques":
        from . import techniques as tq
        t = task.load(tid)
        res = tq.scan(t["workdir"])
        if a.json:
            print(json.dumps(res, indent=1))
        else:
            print("\n".join(tq.lines(res, verbose=True)))
        return 0 if all(r["ok"] for r in res) else 3
    if act == "replay":
        from . import replay as rp
        r = rp.run(tid, say=lambda m: print(m, flush=True))
        return 0 if r["ok"] else 3
    if act == "leakgate-status":
        # leakgate-status TASK [run=STAMP] [thermal=LOG] [hot=70]: progress of a (running) gate and the CPU since it
        # started, from the run's own files; read only, safe while the gate runs (fieldkit/leakgate/status.py)
        from ..leakgate import status as lst
        from . import buildrun
        kv = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        tlog = kv.get("thermal")
        if not tlog:
            try:
                tlog = Path(buildrun._owner_root(task.load(tid))) / "state" / "thermal_watch.log"
            except (task.Refused, OSError, KeyError, TypeError):
                tlog = None
        r = lst.status(tid, kv.get("run"), thermal_log=tlog, hot=float(kv.get("hot", 70)))
        emit(r, lambda r: print("\n".join(lst.lines(r))))
        return 0
    if act == "leakgate-context":
        # leakgate-context TASK [HOST ...] [hosts=FILE.json] [run=STAMP] [width=160] [per=8] [--install-dir D]: every
        # occurrence of each host in the install's omni.ja archives, path:line and context, for disposition evidence.
        # No HOST and no hosts=: the hosts the newest run (or run=) still needs a decision for. Read only.
        from ..leakgate import audit as lau, status as lst
        from . import install as _inst
        kv = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        names = [x for x in a.args[1:] if "=" not in x]
        if kv.get("hosts"):
            hosts, where = lau.hosts_to_review(json.loads(Path(kv["hosts"]).read_text(encoding="utf-8")))
        elif names:
            hosts, where = names, {}
        else:
            src = lst.run_dir(tid, kv.get("run")) / "binary-hosts.json"
            if not src.is_file():
                raise task.Refused(f"no {src}: name the hosts, or hosts=FILE.json")
            hosts, where = lau.hosts_to_review(json.loads(src.read_text(encoding="utf-8")))
            print(f"{len(hosts)} host(s) still need a decision in {src.parent.name}", flush=True)
        inst = a.install_dir or _inst.find_install()
        if not inst:
            raise task.Refused("no installed build found: give --install-dir (a run's build-direct copy works too)")
        ctx = lau.host_context(inst, hosts, where=where, width=int(kv.get("width", 160)), per_member=int(kv.get("per", 8)))
        emit({"install": str(inst), "hosts": ctx}, lambda r: print("\n".join(lau.context_lines(ctx))))
        return 0 if all(ctx.values()) else 3
    if act == "leakgate-proposal":
        # leakgate-proposal TASK RESULT.json ... [override=FILE.json ...] [label=NAME] [notes=FILE.md] [run=STAMP]
        # [review=FILE.md]: reviewers' rows -> proposed-dispositions-<label>.json (approval null on every entry) in the
        # task's leak-gate folder, plus the owner's review document. A proposal only: approving stays the owner's,
        # at a real terminal (leakgate-dispositions); nothing here can approve (fieldkit/leakgate/dispositions.py)
        from ..leakgate import dispositions as _ld, status as lst
        from . import buildrun, install as _inst
        rest = a.args[1:]
        files = [x for x in rest if "=" not in x]
        overrides = [x.split("=", 1)[1] for x in rest if x.startswith("override=")]
        kv = dict(x.split("=", 1) for x in rest if "=" in x and not x.startswith("override="))
        if not files:
            raise task.Refused("leakgate-proposal TASK RESULT.json ...: the reviewers' result files (rows of key or host, "
                               "what, reachable, disposition, reason, evidence)")
        t = task.load(tid)
        legend = {"<tree>": t.get("workdir"), "<install>": _inst.find_install(), "<owner>": buildrun._owner_root(t)}
        try:
            r = _ld.propose(tid, lst.run_dir(tid, kv.get("run")), files, overrides, label=kv.get("label"),
                            notes_file=kv.get("notes"), legend={k: v for k, v in legend.items() if v},
                            review_path=kv.get("review"))
        except _ld.Invalid as e:
            raise task.Refused(f"nothing written: {e}")
        emit(r, lambda r: print(f"proposed {r['entries']} disposition(s): {r['json']}\nreview for the owner: {r['review']}\n"
                                + "".join(f"  {c}: {n}\n" for c, n in r["counts"].items() if n)
                                + (f"NEEDS FIX (never approvable): {r['open']}\n" if r["open"] else "")
                                + f"NOTHING IS APPROVED. The maintainer reads the review and decides, at a real terminal: "
                                  f"fieldkit build-harness leakgate-dispositions {tid}"))
        return 0
    if act in ("leakgate", "leakgate-approve", "leakgate-propose", "leakgate-baseline", "leakgate-dispositions", "leakgate-rejudge"):
        from ..leakgate import gate as lg, allow as la
        from . import buildrun, verify as vf
        import json as _js
        t = task.load(tid)
        owner = Path(buildrun._owner_root(t))
        say = lambda m: print(m, flush=True)
        if act == "leakgate-approve":
            ids = set(x for x in a.args[1:]) or {"*proposed*"}
            done = la.approve(la.path_for(owner), ids, say=say)
            print(f"approved {len(done)}: {done}")
            return 0
        workroot = task.STATE / "leakgate" / tid
        if act == "leakgate-rejudge":
            # `leakgate-rejudge TASK [RUN]`: the same evidence judged again by the current judge and allowlist
            runs = sorted(p for p in workroot.iterdir() if (p / "events.jsonl").is_file())
            run_dir = workroot / a.args[1] if len(a.args) > 1 else runs[-1]
            new, changes = lg.rejudge(run_dir, owner, why=a.note or "")
            for pol, c in changes.items():
                print(f"  {pol}: {c['before']} -> {c['after']}  (removed {len(c['removed'])}, added {len(c['added'])})")
                for x in c["added"][:10]:
                    print(f"      + {x}")
            st_path = owner / "state" / "leakgate_result.json"
            st = _js.loads(st_path.read_text(encoding="utf-8")) if st_path.is_file() else {}
            if Path(st.get("artifacts") or "") == run_dir:
                st.update(FINAL_RESULT=new["FINAL_RESULT"], policies={p: new[p] for p in lg.POLICIES},
                          rejudged=new["REJUDGED"]["at"])
                st_path.write_text(_js.dumps(st, indent=1), encoding="utf-8")
            print(f"REJUDGED {run_dir.name}: FINAL_RESULT {new['FINAL_RESULT']}; failing: "
                  f"{[p for p in lg.POLICIES if new[p] != 'PASS']}")
            return 0
        if act == "leakgate-dispositions":
            # `leakgate-dispositions TASK [KEY...]`: the newest proposed-dispositions-*.json of this task; a KEY names
            # an OWNER-DECISION entry to approve too (never part of the blanket)
            from ..leakgate import dispositions as _ld
            files = sorted(workroot.glob("proposed-dispositions-*.json"), key=lambda p: p.stat().st_mtime)
            if not files:
                raise task.Refused(f"no proposed-dispositions-*.json in {workroot}")
            r = _ld.approve_from(owner / "leakgate" / "dispositions.json", files[-1], named=set(a.args[1:]), say=say)
            print(f"approved {len(r['approved'])} from {files[-1].name}; left {len(r['left'])}")
            for k, why in r["left"]:
                print(f"  LEFT {k}: {why}")
            return 0
        if act == "leakgate-baseline":
            from . import task as _t
            if not _t.owner_terminal():
                raise _t.Refused("the baseline is the owner's to set, at a real terminal")
            st = _js.loads((owner / "state" / "leakgate_result.json").read_text(encoding="utf-8"))
            boot = False
            if st.get("FINAL_RESULT") != "PASS" or not st.get("release_run"):
                full = _js.loads((Path(st["artifacts"]) / "test-results.json").read_text(encoding="utf-8"))
                full["release_run"] = bool(st.get("release_run"))
                ok, why = lg.bootstrap_eligible(full, owner)
                if not ok:
                    raise _t.Refused(f"a baseline is taken only from a release-mode PASS, or as the first baseline: {why}")
                boot = True
                print(f"first baseline: {why}. Run leakgate --release again: it must PASS before anything is published.")
            from . import decisions as _dec, install as _inst
            dres = _dec.check(owner, t["workdir"], _inst.find_install(), strict=True)
            if not dres["ok"]:
                print("\n".join(_dec.lines(dres)))
                from ..briefs import producers as _bp
                print("\n".join(_bp.gate_lines(tid, kinds={"decision"}, decisions_res=dres)))
                raise _t.Refused("the baseline is this build WITH its decisions: every decision must be ENFORCED first "
                                 "(fieldkit build-harness decisions TASK --strict)")
            from . import claims as _cl
            # 2026-10-05 (build 27): not --strict. Strict also demands proof for the ~4,800 sentences of the 154-era
            # project logs that no check covers (unproven, not false), so no baseline could ever be taken. What the
            # baseline needs is what post-install proves: nothing published is contradicted, no patch is failing.
            cres = _cl.run(tid, install_dir=_inst.find_install(), strict=False, write=False)
            if not cres["ok"]:
                print("\n".join(_cl.lines(cres)))
                from ..briefs import producers as _bp
                print("\n".join(_bp.gate_lines(tid, kinds={"patch", "claims"}, claims_res=cres)))
                raise _t.Refused("the baseline is this build WITH what we publish about it: no public claim may be CONTRADICTED "
                                 "and every enabled patch must be IMPLEMENTED or explained first (fieldkit build-harness claims TASK)")
            p = lg.save_baseline(st["artifacts"], owner, t["meta"]["upstream"]["version"], bootstrap=boot,
                                 decisions={"sha256": dres["sha256"], "ids": [x["id"] for x in dres["rows"]]})
            print(f"baseline saved: {p}")
            return 0
        if act == "leakgate-propose":
            last = sorted(p for p in workroot.iterdir() if (p / "events.jsonl").is_file())[-1]
            events = [_js.loads(l) for l in (last / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
            added = la.propose(la.path_for(owner), events, last.name, say=say)
            print(f"proposed {len(added)} entr(ies) from {last.name}; nothing is approved.")
            from ..briefs import producers as _bp                # each proposal as a full brief, not a bare "approve?"
            print("\n".join(_bp.gate_lines(tid, kinds={"allowlist"})))
            return 0
        res = _js.loads((task.STATE / tid / "build-result.json").read_text(encoding="utf-8"))
        backups = Path.home() / "Documents" / "Gorilla.Firefox.Backups"
        prev = None
        for b in sorted(backups.glob("*/manifest.json"), reverse=True):
            m = _js.loads(b.read_text(encoding="utf-8"))
            if m.get("installed", {}).get("version") and m["installed"]["version"] != t["meta"]["upstream"]["version"]:
                prev = b.parent / m["files"]["install"]["zip"]
                break
        only = set(a.only.split(",")) if getattr(a, "only", None) else None
        result, events, st, bi = lg.run(res["artifacts"]["zip"]["file"], owner, t["workdir"], t["meta"]["upstream"],
                                        workroot, repeat=int(getattr(a, "repeat", None) or 1), quick=not getattr(a, "release", False),
                                        only=only, release=getattr(a, "release", False), previous_zip=prev,
                                        n_minus_1_tree=vf._truth_root(t["meta"].get("harness_root") or "", t["workdir"]), say=say,
                                        soak=getattr(a, "soak", None), firewall=getattr(a, "firewall", False))
        for p in lg.POLICIES:
            print(f"  {p:24s} {result[p]}" + (f"  - {result['WHY'][p][0][:220]}" if p in result.get("WHY", {}) else ""))
        print(f"FINAL_RESULT {result['FINAL_RESULT']}  ({result['ARTIFACTS']})")
        try:                                                     # the owner's publish gate reads this, keyed by their build id
            import sys as _sys
            _sys.path.insert(0, str(owner / "working scripts"))
            import verify_address_bar as _v
            bid = _v.build_id(Path(result["ARTIFACTS"]) / "build-direct")
        except Exception:
            bid = None
        (owner / "state").mkdir(exist_ok=True)
        (owner / "state" / "leakgate_result.json").write_text(_js.dumps({
            "build_id": bid, "BUILD": result["BUILD"], "when": time.strftime("%Y-%m-%dT%H:%M:%S"), "release_run": bool(getattr(a, "release", False)),
            "FINAL_RESULT": result["FINAL_RESULT"], "policies": {p: result[p] for p in lg.POLICIES}, "artifacts": result["ARTIFACTS"]}, indent=1), encoding="utf-8")
        task.journal(t, "leakgate", final=result["FINAL_RESULT"], release=bool(getattr(a, "release", False)),
                     failed=[p for p in lg.POLICIES if result[p] != "PASS"], artifacts=result["ARTIFACTS"])
        return 0 if result["FINAL_RESULT"] == "PASS" else 3
    if act == "capture":
        from . import capture
        import shutil as _sh, json as _js
        t = task.load(tid)
        res = _js.loads((task.STATE / tid / "build-result.json").read_text(encoding="utf-8"))
        work = task.STATE / tid / f"capture-{time.strftime('%Y%m%d-%H%M%S')}"
        work.mkdir(parents=True)
        zp = work / "build.zip"                                   # a copy: a later package step rewrites dist/
        _sh.copyfile(res["artifacts"]["zip"]["file"], zp)
        plain = capture.test_copy(zp, work / "browser-plain")
        say = lambda m: print(m, flush=True)
        if getattr(a, "packets_only", False):
            pk = capture.packet_run(plain / "firefox.exe", work, say=say)
            if pk is None:
                print("not elevated: run this in an administrator PowerShell")
                return 3
            (work / "packets.json").write_text(_js.dumps(pk, indent=1), encoding="utf-8")
            for name, r in pk.items():
                v, tr, un = capture.judge(set(r["sni"]) | set(r["dns"]))
                print(f"  [{'ok' if not v and not tr else 'FAIL'}] packets {name}: vendor {sorted(v)} trackers {sorted(tr)} other {sorted(un)[:10]} udp {r['udp'][:6]}")
            task.journal(t, "capture-packets", dir=str(work), scenarios=list(pk))
            return 0
        rows = capture.rows(tid, zp, plain / "firefox.exe", work, say=say, packets=True)
        (work / "capture.json").write_text(_js.dumps(rows, indent=1, default=str), encoding="utf-8")
        for r in rows:
            print(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['check']}: {r['evidence'][:400]}")
        task.journal(t, "capture", dir=str(work), ok=all(r["ok"] for r in rows), failed=[r["check"] for r in rows if not r["ok"]][:12])
        print(f"CAPTURE {'OK' if all(r['ok'] for r in rows) else 'NOT OK'}: {work}")
        return 0 if all(r["ok"] for r in rows) else 3
    if act == "repair":
        from . import repair
        r = repair.run(tid, say=lambda m: print(m, flush=True))
        emit(r, lambda r: print(f"REPAIR {'OK' if r['ok'] else 'NOT OK'}: {len(r['repaired'])} repaired, {len(r['refused'])} refused"))
        return 0 if r["ok"] else 3
    if act == "truthbound":
        from . import truthbound, verify as vf
        t = task.load(tid)
        repo155 = task.load("firefox-155.0.1")["workdir"]
        truth155 = vf._truth_root(t["meta"].get("harness_root") or "", t["workdir"])
        r = truthbound.run(tid, repo155, truth155, say=lambda m: print(m, flush=True))
        emit(r, lambda r: print(f"TRUTHBOUND {'OK' if r['ok'] else 'NOT OK'}: {r['changed']} changed files, {len(r['unexplained'])} unexplained"))
        return 0 if r["ok"] else 3
    if act == "visual":
        # crisp icons and aligned pages: the ported tree, then a throwaway COPY of the install (fieldkit/visual)
        import sys as _sys
        from .. import visual
        from . import install as inst
        t = task.load(tid)
        static_only = bool(getattr(a, "static", False))
        target = None if static_only else (a.install_dir or inst.find_install())
        pages = tuple(p.strip() for p in (getattr(a, "only", None) or "").split(",") if p.strip())

        def say(m):
            print(m, file=_sys.stderr, flush=True)
        change = None
        if any(x.startswith(("omni=", "file=", "add=", "sub=", "tree-since=")) for x in a.args[1:]):
            # a candidate fix put into the runtime COPY (2026-10-04: four CSS/JS fixes proven on their pages this way)
            if static_only:
                raise task.Refused("omni=/file=/add=/sub=/tree-since= change the runtime copy; --static starts no browser")
            _opts, change, _notes = _change_args(a.args[1:], tid, target, "gvisual_tree_", say=say)
        if not static_only:
            say(f"visual: the runtime layer starts a HEADLESS throwaway copy of {target} (no window, no keyboard); "
                "the install and your profiles are only read" + ("; the COPY carries the change given" if change else ""))
        r = visual.check(t, static_only=static_only, install_dir=target, only=pages, say=say, change=change or None)
        emit(r, lambda r: print("\n".join(visual.lines(r))))
        return 0 if r["ok"] else 3
    if act == "decisions":
        from . import decisions as dec, buildrun, install as inst
        t = task.load(tid)
        owner = Path(buildrun._owner_root(t))
        target = a.install_dir or inst.find_install()
        r = dec.check(owner, t["workdir"], target, strict=getattr(a, "strict", False))
        emit(r, lambda r: print("\n".join(dec.lines(r))))
        return 0 if r["ok"] else 3
    if act == "claims":
        # what the public repository claims, against the tree and the installed build (fieldkit/buildh/claims.py)
        from . import claims as cl, install as inst
        if "retire-stale" in a.args:          # claims TASK retire-stale: STALE entries move to `retired:`
            ids = cl.retire_stale(tid, install_dir=a.install_dir or inst.find_install())
            print(f"retired {len(ids)} stale claim(s): {', '.join(ids[:12])}{' ...' if len(ids) > 12 else ''}")
        r = cl.run(tid, install_dir=a.install_dir or inst.find_install(), report_path=getattr(a, "report", None),
                   strict=getattr(a, "strict", False))
        emit({k: v for k, v in r.items() if k not in ("claims", "patch_audit")} | {"worst_gaps": cl.worst_gaps(r)},
             lambda _: print("\n".join(cl.lines(r))))
        return 0 if r["ok"] else 3
    if act == "post-install":
        from . import install as inst
        r = inst.post_install(tid, install_dir=a.install_dir, only=set(a.only.split(",")) if getattr(a, "only", None) else None,
                              say=lambda m: print(m, flush=True), drive=getattr(a, "drive", False))
        emit(r, lambda r: print("POST-INSTALL " + ("OK" if r.get("ok") else "NOT OK: " + str(r.get("why") or "")
                                + "".join(f"\n  {x.get('status') or 'FAIL'}: {x['name']}" + (f" ({x['why']})" if x.get("why") else "")
                                          for x in r.get("results", []) if x.get("status") != "ok"))
                                + (f"\n{r['next']}" if r.get("next") else "")))
        if not r.get("ok") and not getattr(a, "json", False):
            from ..briefs import producers as _bp            # what needs the maintainer, as full briefs, not a bare line
            print("\n".join(_bp.gate_lines(tid, install_dir=r.get("target"))))
        return 0 if r.get("ok") else 3
    if act == "creep":
        from . import verify as vf
        syms, rows = vf.creep(tid)
        emit({"symbols": syms, "creep": rows},
             lambda r: print(f"{len(r['symbols'])} excised symbol(s); {len(r['creep'])} file(s) with new upstream references:\n"
                             + "\n".join(f"  {f}: {n} new | {ex}" for f, n, ex in r["creep"])))
        return 0
    if act == "record":
        from . import handedit
        # `record TASK kind=privacy|port FILE...`: the kind decides the patch-set group on export (no flag of its own:
        # fieldkit/cli.py owns the parser)
        rest = [x for x in a.args[1:]] if len(a.args) > 1 else []
        kinds = [x.split("=", 1)[1] for x in rest if x.startswith("kind=")]
        files = [x for x in rest if not x.startswith("kind=")]
        if len(kinds) > 1:
            raise task.Refused("one kind= per record")
        ids = handedit.record(tid, files, a.note or "", kind=kinds[0] if kinds else None)
        emit({"ok": True, "recorded": ids}, lambda r: print("recorded: " + ", ".join(r["recorded"])))
        # every recorded change is checked and exported at once, not when someone remembers (owner, 2026-10-07)
        from . import changecheck
        r = changecheck.run(tid, say=lambda m: print(m, flush=True), note=a.note)
        return 0 if r["ok"] else 3
    if act == "images":
        from . import images as _img, install as _inst
        inst = a.install_dir or _inst.find_install()
        if not inst:
            raise task.Refused("no installed build found: give --install-dir")
        r = _img.scan(inst)
        emit(r, lambda r: print("\n".join(_img.lines(r))))
        return 0
    if act == "check-change":
        from . import changecheck
        r = changecheck.run(tid, say=lambda m: print(m, flush=True), note=a.note)
        return 0 if r["ok"] else 3
    if act == "build-run":
        from . import buildrun
        r = buildrun.run(tid, force=bool(getattr(a, "force", False)), say=lambda m: print(m, flush=True))
        emit(r, lambda r: print("BUILD " + ("OK" if r.get("ok") else "NOT OK: " + str(r.get("why") or [s["signature"] for s in r.get("stops", [])]))))
        if not r.get("ok") and not getattr(a, "json", False):
            # the briefs that can stop a build; the claims and patch briefs need the 2-minute claims audit (`briefs TASK`)
            from ..briefs import producers as _bp
            print("\n".join(_bp.gate_lines(tid, kinds={"decision", "deferred", "owner-edit", "disposition", "allowlist"})))
            print(f"claims and patch briefs: fieldkit build-harness briefs {tid}")
        return 0 if r.get("ok") else 3
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
    if act == "stats":
        # stats TASK [--since D] [--until D]: the journal counted (builds, attempts, stops, hand edits, installs, heat)
        from . import stats
        r = stats.run(tid, since=getattr(a, "since", None), until=getattr(a, "until", None))
        emit(r, lambda r: print("\n".join(stats.lines(r))))
        return 0
    if act == "log":
        f = task.STATE / tid / "journal.jsonl"
        for line in (f.read_text(encoding="utf-8").splitlines() if f.is_file() else [])[-40:]:
            e = json.loads(line)
            print(e["t"], e["event"], e.get("step", ""), json.dumps({k: v for k, v in e.items()
                                                                    if k not in ("t", "task", "event", "step")})[:160])
        return 0
    raise SystemExit(f"unknown build-harness action {act}")
