# Version: 1.0.0 · updated 26-10-15-13-59
"""fieldkit build-harness - the Firefox & kernel build harness, from the command line and over MCP.

    fieldkit build-harness latest firefox|kernel
    fieldkit build-harness vault measure firefox | fetch firefox|kernel | verify P | restore P WORKDIR | list
    fieldkit build-harness start firefox [--task ID] [--pin VERSION --source REPO_OR_PATH]
                                         [--budget TOKENS] [--workdir DIR]
    fieldkit build-harness approve TASK          the owner, only here (never over MCP)
    fieldkit build-harness next|status [TASK]
    fieldkit build-harness submit [TASK] [--note TEXT] [--adapted WHY]   --adapted: saved for the owner, no attempt spent
    fieldkit build-harness proposals [TASK]               the adapted ports waiting for the owner
    fieldkit build-harness accept [TASK] --all|STEP...    the owner accepts them in one go (applied, judged by meaning, checkpointed)
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
    fieldkit build-harness repro [TASK] [--run|--compare]       the binary reproduced: the compiled tree built again in
                                                                another folder and objdir, packages compared byte for
                                                                byte, every difference named (buildh/reprobuild.py)
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
import re
import os as _os
import time

from ..core import settings as _settings
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
        change["omni"].update({k: str(v) for k, v in got.items() if not k.startswith("@")})
        change["files"].update({k[1:]: str(v) for k, v in got.items() if k.startswith("@")})   # an unpacked build
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
        import sys as _sys
        # everything after "window", exactly as typed: options such as --only and --drive belong to the command in the
        # window, not to this launcher (2026-10-10: they were swallowed and the keyboard check was skipped twice)
        raw = _sys.argv[_sys.argv.index("window") + 1:] if "window" in _sys.argv else list(a.args)
        r = window.launch(raw)
        print(f"started in its own window (PID {r['pid']}); everything it prints is also in {r['log']}")
        return 0
    if act == "symptom":
        # symptom "<what you see>" [--all]: the tool already built and proven for a visible problem (symptoms.py).
        # The FIRST command for a screenshot or a complaint, before any guessing (owner 2026-10-15)
        from . import symptoms
        from ..core import settings as _st
        words = " ".join(a.args)
        hits = symptoms.SYMPTOMS if getattr(a, "all", False) else symptoms.match(words)
        if not hits:
            print(f"no recorded symptom matches {words!r}. Diagnose it with the harness's probes (build-harness probe, "
                  "clip-check, decisions), then add an entry to fieldkit/buildh/symptoms.py so the next session "
                  f"starts from the tool. All recorded symptoms: {_st.self_cmd()} build-harness symptom --all")
            return 3
        emit({"symptoms": hits}, lambda r: print("\n".join(symptoms.lines(r["symptoms"], _st.self_cmd()))))
        return 0
    if act == "follow":
        # follow [<log>|latest] [cmd=build-run] [every=1] [timeout=S]: a window log's milestones as they are written,
        # flushed; exits with the run's own exit code (124 at the timeout) (window.py)
        from . import window
        pos = [x for x in a.args if "=" not in x]
        opts = dict(x.split("=", 1) for x in a.args if "=" in x)
        log = window.latest_log(opts.get("cmd")) if not pos or pos[0] == "latest" else Path(pos[0])
        if not log:
            raise task.Refused("follow: no window log found")
        print(f"following {log}", flush=True)
        code = window.follow(log, say=lambda m: print(m, flush=True), every=opts.get("every") == "1",
                             timeout=float(opts["timeout"]) if opts.get("timeout") else None)
        return 124 if code is None else code
    if act == "awake":
        # awake PID: keep the machine from idle-sleeping until that process exits (a run started without the window)
        from . import window
        if not a.args or not a.args[0].isdigit():
            raise task.Refused("awake PID: the process to stay awake for")
        r = window.hold_awake(int(a.args[0]))
        print(f"staying awake until process {a.args[0]} exits (holder PID {r['pid']}); a closed lid or Sleep still sleeps")
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
        # an unknown or missing sub-command used to exit 0 and print nothing (found 2026-10-18)
        sub, rest = (a.args[0], a.args[1:]) if a.args else ("", [])
        if sub not in ("measure", "fetch", "verify", "restore", "restore-files", "list"):
            raise task.Refused(f"vault: say what to do: measure | fetch firefox|kernel | verify WHAT | restore WHAT DIR | "
                               f"restore-files firefox WORKDIR PATH... | list (got {sub or 'nothing'!r})")
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
        if sub == "restore-files":
            # restore-files firefox WORKDIR PATH [PATH ...]: pristine files back without git (vault.restore_files)
            r = vault.restore_files(rest[0], rest[1], rest[2:], a.version)
            emit(r, lambda r: print("\n".join([f"restored {p}" for p in r["restored"]]
                                               + [f"already pristine {p}" for p in r["already"]]
                                               + [f"NOT IN THE RELEASE (left as it is) {p}" for p in r["not_in_release"]]
                                               + ([f"the replaced versions are in {r['trash']}"] if r["trash"] else []))))
            return 0 if not r["not_in_release"] else 3
        if sub == "list":
            return emit(vault.listing(), lambda rows: [print(f"{r['product']:8} {r['version']:10} {r['fetched']}  {r['path']}") for r in rows]) or 0
    if act == "start":
        if not a.args or a.args[0] != "firefox":            # `start` alone was an IndexError (2026-10-18)
            raise task.Refused("start firefox: the only workflow so far (the kernel workflow comes next)")
        t = start_firefox(a.task, a.pin, a.source, a.budget, a.workdir, harness_root=a.harness)
        # a local name: `settings` is imported further down in run(), which makes it local to the whole function
        # (2026-10-16: start created the task, then crashed printing this with "cannot access free variable")
        from ..core import settings as _set
        fk = _set.self_cmd()
        return emit({"task": t["id"], "steps": [s["id"] for s in t["steps"]], "workdir": t["workdir"]},
                    lambda r: print(f"task {r['task']} planned: {', '.join(r['steps'])}\n"
                                    f"working copy: {r['workdir']}\n"
                                    f"NEXT: the owner reads the plan and runs: {fk} build-harness approve {r['task']}\n"
                                    f"      or let the harness open the window for them: {fk} terminal ask -- "
                                    f"{fk} build-harness approve {r['task']}")) or 0
    if act == "herald":
        # herald --say "words" | herald [name=..] [pid=..] [log=..] [every=..]: the spoken herald (herald.py), from any
        # folder (2026-10-10: `python -m fieldkit.buildh.herald` was given to the owner and depends on where it runs)
        from . import herald as _h
        opts = dict(x.split("=", 1) for x in a.args if "=" in x)
        words = " ".join(x for x in a.args if "=" not in x)
        if words or "say" in opts:
            how = _h.say(opts.get("say", words) or "test")
            print(f"herald: {how}" + (_h.HEARD if how == "spoken" else f" ({_h.LAST_PROBLEM['why']})"))
            if how != "spoken":
                print("\n".join(_h.explain(_h.voice_check())))
            return 0
        _h.watch(opts.get("name", "the run"), int(opts.get("pid", 0)), opts.get("log") or None, int(opts.get("every", 60)))
        return 0
    if act == "leakgate-selftest":
        # leakgate-selftest: can the leak gate run on this Linux machine (root, ip, nft, ...)? Changes nothing; needs no build job.
        # Documented as `fieldkit leakgate-linux selftest` since 2026-10-02 but never connected (found 2026-10-10)
        from ..leakgate import linux as lx
        r = lx.selftest()
        if a.json:
            print(json.dumps(r, indent=1))
        else:
            for t, p in r["tools"].items():
                print(f"  {'ok  ' if p else 'MISS'} {t:<12} {p or ''}")
            print(f"  {'ok  ' if r['root'] else 'MISS'} root")
            print("READY" if r["ok"] else f"NOT READY: missing {r['missing_required'] or []}"
                  + ("" if r["root"] else "; the leak gate needs root (network namespace, nftables)"))
            print("NEXT: " + (f"{_settings.self_cmd()} build-harness leakgate TASK" if r["ok"] else
                              "fieldkit doctor --for leakgate (it gives the install lines); run as root; then "
                              "fieldkit build-harness leakgate-selftest"))
        return 0 if r["ok"] else 3
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
    if act == "submit" and getattr(a, "dry_run", False):
        r = task.trial(tid, a.note or "", adapted=getattr(a, "adapted", "") or "", hand=bool(getattr(a, "hand", False)))
        emit(r, None)
        return 0 if r["ok"] else 3
    if act == "submit":
        r = task.submit(tid, a.note or "", by="hand" if getattr(a, "hand", False) else "cli", adapted=getattr(a, "adapted", "") or "")
        emit(r, None)
        return 0 if r["ok"] else 3
    if act == "withdraw":
        if len(a.args) < 2 or not (a.note or "").strip():
            raise task.Refused("withdraw TASK STEP --note \"why\"")
        r = task.withdraw(tid, a.args[1], a.note.strip(), who=_os.environ.get("USER") or _os.environ.get("USERNAME") or "owner")
        return emit(r, lambda r: print(f"withdrawn {r['withdrawn']}: {r['reason']}")) or 0
    if act == "api-drift":
        # api-drift TASK [--apply]: upstream API changes (build/api-drift/firefox.yaml) in the ported C/C++ files
        from ..build import apidrift
        t = task.load(tid)
        r = apidrift.scan(t["workdir"])
        if getattr(a, "apply", False) and r["found"]:
            r["applied"] = apidrift.apply(t["workdir"], r["found"])
        def show(r):
            print("\n".join(apidrift.lines(r)))
            if r.get("applied"):
                print("APPLIED and checkpointed: " + "; ".join(r["applied"]))
        emit(r, show)
        return 0 if r["ok"] or r.get("applied") else 3
    if act == "profile-check":
        # profile-check TASK [profile=<intended>] [paint=1]: every profile on the machine and which version last ran
        # it, every launcher of the install and the profile it names, the desktop's menu cache, and (paint=1) the new
        # tab's pixels on a COPY of the intended profile (fieldkit/buildh/profilecheck.py; born 2026-10-15)
        from . import profilecheck as pc, install as _inst
        opts = dict(x.split("=", 1) for x in list(getattr(a, "args", None) or [])[1:] if "=" in x)
        inst = a.install_dir or _inst.find_install()
        want = opts.get("profile") or pc.intended()
        paint = pc.paint_probe(inst, say=lambda m: None) if opts.get("paint", "0") not in ("0", "") else None
        found = pc.profiles()
        rows = pc.rows(inst, want, found=found, paint=paint)
        def show(r):
            import time as _t
            for p in r["profiles"]:
                print(f"  {_t.strftime('%Y-%m-%d %H:%M', _t.localtime(p['when']))}  {p['last_version'] or '?':10} "
                      f"{p['path']}  (last run by {p['last_install'] or '?'})")
            for row in r["rows"]:
                print(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence']}")
            print("PROFILE-CHECK " + ("OK" if all(x["ok"] for x in r["rows"]) else "NOT OK"))
        emit({"profiles": found, "rows": rows, "intended": want}, show)
        return 0 if all(x["ok"] for x in rows) else 3
    if act == "profile-fresh":
        # profile-fresh TASK from=<old profile> to=<new profile> [machine=<pref>=<value>:<reason> ...] [--apply]: the
        # person's data into a fresh profile, none of the old version's settings; lists user.js settings the build does
        # not carry (fieldkit/buildh/profilefresh.py; born 2026-10-15 from the 154-era profile under Gorilla 158)
        from . import profilefresh, proof, install as _inst
        kv = dict(x.split("=", 1) for x in (list(getattr(a, "args", None) or [])) if "=" in x and not x.startswith("machine="))
        machine = {}
        for x in (list(getattr(a, "args", None) or [])):
            if x.startswith("machine="):
                name, _, rest = x[len("machine="):].partition("=")
                val, _, why = rest.partition(":")
                machine[name] = (val, why or "this machine only")
        src, dst = kv.get("from"), kv.get("to")
        if not src or not dst:
            print("profile-fresh needs from=<old profile> to=<new profile>")
            return 2
        target = getattr(a, "install_dir", None) or _inst.find_install()
        r = {"plan": profilefresh.plan(src, dst),
             "stranded": profilefresh.stranded(Path(src) / "user.js", proof.shipped_prefs(target) if target else {})}
        if getattr(a, "apply", False):
            r["applied"] = profilefresh.apply(src, dst, machine)
            r["check"] = profilefresh.check(dst)
        def show(r):
            print(f"copy: {', '.join(r['plan']['copy'])}")
            print(f"never: {', '.join(r['plan']['never'])}")
            for n, v, b in r["stranded"]:
                print(f"  STRANDED {n} = {v} (build: {b if b is not None else 'not set'}): move it into the build or "
                      "into machine=, or decide it is not needed, before retiring the old profile")
            if r.get("check"):
                print("PROFILE-FRESH " + ("OK" if r["check"]["ok"] else "NOT OK: " + "; ".join(r["check"]["problems"])))
        emit(r, show)
        return 0 if not r.get("check") or r["check"]["ok"] else 3
    if act in ("binary-patches", "whole-files"):
        # binary-patches TASK [--apply]: every GIT binary patch of the shared set (pictures, icons) in the tree, by git hash;
        # --apply restores the missing ones, each verified (fieldkit/buildh/binpatch.py; born 2026-10-15: 0 of 31 applied).
        # whole-files TASK: every NEW_FILES/REPLACE_FILES file of the set against the tree (report; lossaudit.whole_files)
        import json as _json
        from . import binpatch, lossaudit
        t = task.load(tid)
        root = Path(t["meta"]["harness_root"])
        pol = _json.loads((root / "config" / "patch_policy.json").read_text(encoding="utf-8"))
        sets = root / "patches"
        if act == "whole-files":
            r = lossaudit.whole_files(sets, t["workdir"], pol)
            emit(r, lambda r: print("\n".join(lossaudit.whole_lines(r))))
            return 0 if not (r["missing"] or r["differs"]) else 3
        if getattr(a, "apply", False):
            got = binpatch.restore(t["workdir"], sets, pol)
            print("\n".join([f"  restored {x}" for x in got["restored"]] + [f"  FAILED {x}" for x in got["failed"]]))
        r = binpatch.audit(t["workdir"], sets, pol)
        emit(r, lambda r: print("\n".join(binpatch.lines(r))))
        return 0 if r["ok"] else 3
    if act == "clip-check":
        # clip-check TASK [repeats=3] [stock=0] [swap=<build>=<tree> ...] [try=<build>=<file> ...] [pref=<n>=<v> ...]: is dialog
        # text cut in the installed build? repeated, against stock of the same version, a cause convicted only against
        # a control (fieldkit/buildh/clipcheck.py; born 2026-10-15 from the owner's "Leave page?" screenshot)
        from . import clipcheck, install as _inst
        from ..build import decode_test
        t = task.load(tid)
        inst = a.install_dir or _inst.find_install()
        if not inst:
            raise task.Refused("no installed build found: give --install-dir")
        kv = [x.split("=", 1) for x in a.args[1:] if "=" in x]
        opts = {k: v for k, v in kv if k not in ("swap", "pref", "try")}
        swaps = dict(v.split("=", 1) for k, v in kv if k == "swap")
        prefs = dict(v.split("=", 1) for k, v in kv if k == "pref")
        tries = dict(v.split("=", 1) for k, v in kv if k == "try")
        stock = None
        if opts.get("stock", "1") != "0":
            # the control is stock Firefox of the version the INSTALLED build reports (application.ini), never a guess
            ini = Path(inst) / "application.ini"
            m = re.search(r"^Version=(\S+)", ini.read_text(errors="replace"), re.M) if ini.is_file() else None
            ver = opts.get("version") or (m.group(1) if m else None)
            if not ver:
                raise task.Refused(f"cannot tell the installed version ({ini} has no Version=): give version=<x.y>")
            stock = str(Path(decode_test.stock_firefox(ver)).parent)
        r = clipcheck.run(inst, workdir=t["workdir"], repeats=int(opts.get("repeats", 3)), stock_dir=stock,
                          swaps=swaps, prefs=prefs, tries=tries, surface=opts.get("surface", "leave-page"),
                          out=lambda m: print(m, flush=True))
        emit(r, lambda r: print(r["detail"]))
        return 0 if r["ok"] else 3
    if act == "pref-bisect":
        # pref-bisect TASK: which of our graphics pref changes costs dropped frames, by halving (isolated, measured)
        from ..build import prefbisect
        t = task.load(tid)
        r = prefbisect.run(t["workdir"], _settings.ROOT / "state" / "decode-test")
        emit(r, lambda r: print(r["detail"]))
        return 0 if r["ok"] else 3
    if act == "decode-test":
        # decode-test TASK: does the INSTALLED browser decode video on the GPU's video engine (D-158-06)?
        from ..build import decode_test
        r = decode_test.run(_settings.ROOT / "state" / "decode-test")
        emit(r, lambda r: print(r["detail"]))
        return 0 if r["ok"] else 3
    if act == "loss-audit":
        # loss-audit TASK [FOLDER ...]: every line every Gorilla patch ever added, counted in the ported tree
        from . import lossaudit
        t = task.load(tid)
        root = Path((t.get("meta") or {}).get("harness_root") or ".")
        if not (root / ".git").is_dir():
            raise task.Refused(f"{root} is not the shared patch-set repository (no git history to read)")
        sources = [("git", lossaudit.from_git(root))]
        for d in sorted(root.glob("patchset-*")) + [Path(x) for x in a.args[1:]]:
            if not d.is_dir():
                raise task.Refused(f"no patch folder at {d}")
            sources.append((d.name, lossaudit.from_folder(d, d.name)))
        r = lossaudit.audit(t["workdir"], sources)
        emit(r, lambda r: print("\n".join(lossaudit.lines(r))))
        return 0
    if act == "archive-gap":
        # archive-gap TASK [ARCHIVE]: what the last Linux-built patch set changed and this port never touched
        from . import companions
        t = task.load(tid)
        root = Path((t.get("meta") or {}).get("harness_root") or ".")
        arch = Path(a.args[1]) if len(a.args) > 1 else root / "patchset-155.0b4"
        if not arch.is_dir():
            raise task.Refused(f"no archived patch set at {arch}")
        rows = companions.archive_gap(t["workdir"], arch)
        if rows is None:
            raise task.Refused(f"{t['workdir']} is not a work tree with a pristine root commit")
        def show(rows):
            for x in rows:
                print(f"  {x['group']:32} {x['file']}")
            print(f"ARCHIVE-GAP: {len(rows)} file(s) the archived set {arch.name} changed and this port did not. "
                  "Each is either a deliberate removal (say so in the patch policy) or a missing piece (apply it).")
        emit(rows, show)
        return 0
    if act == "companions":
        # companions TASK [LOG] [--apply]: a compile error naming a missing declaration -> its other half, from the
        # archived patch sets, dry-run with git before anything is applied
        from . import companions
        t = task.load(tid)
        root = (t.get("meta") or {}).get("harness_root")
        log = Path(a.args[1]) if len(a.args) > 1 else Path(t["workdir"]) / "build_logs" / "latest_build.log"
        if not root or not Path(root).is_dir():
            raise task.Refused(f"task {tid} has no harness folder to search (meta.harness_root)")
        if not log.is_file():
            raise task.Refused(f"no build log at {log}")
        r = companions.diagnose(t["workdir"], root, log.read_text(encoding="utf-8", errors="replace"))
        if getattr(a, "apply", False) and r["plan"]:
            r["applied"] = companions.apply(t["workdir"], r["plan"])
        r["plan"] = [{k: v for k, v in c.items() if k not in ("text", "mtime")} for c in r["plan"]]
        def show(r):
            print("\n".join(companions.lines(r, root)))
            if r.get("applied"):
                print("APPLIED: " + "; ".join(r["applied"]["applied"]) if r["applied"]["ok"]
                      else f"APPLY FAILED at {r['applied']['failed']}: {r['applied']['why']}")
        emit(r, show)
        return 0 if r["ok"] and (not r.get("applied") or r["applied"]["ok"]) else 3
    if act == "fluent-check":
        from . import fluentcheck
        t = task.load(tid)
        r = fluentcheck.check(t["workdir"], (t.get("meta") or {}).get("harness_root"))
        emit(r, lambda r: print("\n".join(fluentcheck.lines(r))))
        return 0 if r["ok"] else 3
    if act == "describe":
        # describe TASK [STEP_ID]: the step in full words (the current one by default), for messages and window titles
        t = task.load(tid)
        sid = a.args[1] if len(a.args) > 1 else None
        s = next((x for x in t["steps"] if x["id"] == sid), None) if sid else task.current(t)
        if s is None:
            raise task.Refused(f"no step {sid!r}" if sid else "no current step")
        return emit({"step": s["id"], "words": task.describe_step(t, s)}, lambda r: print(r["words"])) or 0
    if act == "lessons":
        # lessons TASK [--all]: the lessons no harness rule covers yet (lessonrules.py); --all adds the ruled ones
        from . import lessonrules as _lr
        rows = task.lessons(tid)
        waiting, ruled = _lr.split(rows)

        def _show(_):
            out = [f"{len(waiting)} lesson(s) waiting to become harness rules ({len(ruled)} already are):"]
            out += [f"  {r['t']}  {r['step'][:60]}\n      {r['lesson']}" for r in waiting]
            if getattr(a, "all", False):
                out += ["ruled (lesson -> the tool that does it now):"]
                out += [f"  {r['lesson'][:90]}\n      -> {rule['tool']}" for r, rule in ruled]
            print("\n".join(out))
        return emit(waiting, _show) or 0
    if act == "drop":
        # drop TASK --note "reason" PATCH... [keep=PATCH]
        pats = [x for x in a.args[1:] if not x.startswith("keep=")]
        keep = [x[5:] for x in a.args[1:] if x.startswith("keep=")]
        if not pats or not (a.note or "").strip():
            raise task.Refused("drop TASK --note \"why (quote the author)\" PATCH ... [keep=PATCH]")
        r = task.drop(tid, pats, a.note.strip(), who=_os.environ.get("USER") or _os.environ.get("USERNAME") or "owner", keep=keep)
        return emit(r, lambda r: print(f"dropped {len(r['dropped'])} step(s); files back to pristine: {', '.join(r['files'])}; "
                                       f"applied again: {', '.join(r['reapplied']) or 'none'}")) or 0
    if act == "proposals":
        rows = task.proposals(tid)
        return emit(rows, lambda rows: print("\n".join(
            [f"{len(rows)} adapted port(s) wait for the owner:"] +
            [f"  {r['step']}\n      why adapted: {r['reason'][:160]}\n      the letter said: {'; '.join(r.get('literal_check_said') or [])[:160]}" for r in rows] +
            ([f"NEXT, in one go: {_settings.self_cmd()} build-harness accept {tid} --all"] if rows else [])))) or 0
    if act == "accept":
        # `--all` (a flag) or no ids: every waiting proposal. 2026-10-14: the printed `accept TASK --all` failed with
        # 'unrecognized arguments: --all' because the flag was never declared; the tests called task.accept() only
        ids = [] if getattr(a, "all", False) else [x for x in a.args[1:] if x != "--all"]
        r = task.accept(tid, step_ids=ids or None, who=_os.environ.get("USER") or _os.environ.get("USERNAME") or "owner")
        return emit(r, lambda r: print("\n".join(
            [f"accepted {len(r['accepted'])}: {', '.join(r['accepted'])}" if r["accepted"] else "accepted none"] +
            [f"  FAILED {f['step']}: {f['why']}" for f in r["failed"]]))) or (0 if not r["failed"] else 3)
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
    if act == "release-roundtrip":
        # release-roundtrip TASK tag=<tag> [repo=owner/name] [asset=x.deb] [keep=1]: remove the installed browser,
        # download the published .deb, check it against SHA256SUMS, install it, verify (build/release_roundtrip.py)
        from ..build import release_roundtrip as _rr
        import tempfile as _tf
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        if not opts.get("tag"):
            raise task.Refused("release-roundtrip TASK tag=<release tag> [repo=owner/name] [asset=<file.deb>] [keep=1]")
        t = task.load(tid)
        r = _rr.run(opts["tag"], _tf.mkdtemp(prefix="groundtrip_"), t["workdir"], repo=opts.get("repo", _rr.DEFAULT_REPO),
                    asset=opts.get("asset"), keep=opts.get("keep", "0") not in ("0", "no", "false", ""),
                    say=lambda m: print(m, flush=True))
        task.journal(t, "release-roundtrip", ok=r["ok"], tag=opts["tag"], failed=[x["check"] for x in r["rows"] if not x["ok"]])
        print(f"RELEASE-ROUNDTRIP {'OK' if r['ok'] else 'FAILED'}: {sum(x['ok'] for x in r['rows'])}/{len(r['rows'])} rows")
        return 0 if r["ok"] else 3
    if act == "platform-steps":
        # platform-steps TASK: what to do on THIS operating system, computed from the machine (platformsteps.py)
        from . import platformsteps as _ps
        from ..core import settings as _st
        print("\n".join(_ps.lines(task.load(tid), self_cmd=_st.self_cmd())))
        return 0
    if act == "rebase-set":
        # rebase-set TASK [write=1]: pristine upstream + the patch set = the built tree, by superseding the patches
        # that no longer apply and generating 23.REBASE.<major> (rebase.py). Dry unless write=1.
        from . import rebase as _rb
        from .ownercheck import patchset_owner
        t = task.load(tid)
        owner = patchset_owner(t)
        if not owner:
            raise task.Refused("no patch-set repo for this task (meta.harness_root with config/patch_policy.json)")
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        r = _rb.run(owner, t["workdir"], t["meta"]["upstream"]["version"].split(".")[0],
                    write=opts.get("write", "0") not in ("0", "no", "false", ""), say=lambda m: print(m, flush=True))
        task.journal(t, "rebase-set", **{k: v for k, v in r.items() if k != "replay"}, replay=str(r["replay"])[:300])
        return 0 if r["ok"] else 3
    if act == "carry":
        # build-harness carry TASK COMMIT PATCH... --note WHY: the hand step is already in these patches of the set
        from . import export as ex
        from .ownercheck import patchset_owner
        import json as _js
        if len(a.args) < 3 or not a.note:
            raise task.Refused("usage: build-harness carry TASK COMMIT PATCH-FILE... --note \"why the set already carries it\"")
        t = task.load(tid)
        owner = patchset_owner(t)
        pol = _js.loads((owner / "config" / "patch_policy.json").read_text(encoding="utf-8"))
        full = ex._git(t["workdir"], "rev-parse", "--verify", a.args[1] + "^{commit}").strip()
        if not full:
            raise task.Refused(f"no commit {a.args[1]} in the work tree")
        ex.carry(tid, full, a.args[2:], a.note, owner / pol["patchset_root"])
        print(f"recorded: {full[:12]} is carried by {', '.join(a.args[2:])}; export-hand skips it, replay proves it")
        return 0
    if act == "export-hand":
        from . import export as ex, buildrun
        t = task.load(tid)
        from .ownercheck import patchset_owner
        owner = patchset_owner(t)               # the patch-set repo on Linux as well (2026-10-15)
        if not owner:
            raise task.Refused("no patch-set repo for this task (meta.harness_root with config/patch_policy.json)")
        import json as _js
        pol = _js.loads((owner / "config" / "patch_policy.json").read_text(encoding="utf-8"))
        r = ex.export(tid, owner / pol["patchset_root"], t["meta"]["upstream"]["version"].split(".")[0], say=lambda m: print(m, flush=True),
                      dry=bool(getattr(a, "dry_run", False)))
        print("register new groups in config/patch_policy.json if they are not there; then prove the set: build-harness replay " + tid)
        return 0
    if act == "release-check":
        from . import releasecheck as rc_
        rows = rc_.run(tid, say=lambda m: print(m, flush=True), skip_post_install=bool(getattr(a, "fast", False)) or getattr(a, "build", False))
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
        # visible=1: a normal window instead of headless (2026-10-15: the new tab drew in every headless snapshot and
        # was black on the owner's screen; only a real window goes through the GPU path a person sees)
        _visible = opts.get("visible", "0") not in ("0", "no", "false", "")
        r = pb.run(inst, opts["js"], url=opts.get("url", "about:blank"), wait=float(opts.get("wait", 15)),
                   headless=not _visible, profile=opts.get("profile"),
                   omni=change.get("omni"), timeout=float(opts.get("timeout", 90)), say=lambda m: print(m, flush=True),
                   files=change.get("files"), added=change.get("added"), subs=change.get("subs"))
        for l in r["timeline"]:                  # the probe's lines and its local pages' requests, in time order
            print("  " + l)
        print(f"PROBE {'DONE' if r['done'] else 'TIMED OUT'} in {r['seconds']} s" + (f"; replaced {r['patched']}" if r["patched"] else ""))
        return 0 if r["done"] else 3
    if act == "hidden-pages-doc":
        # hidden-pages-doc TASK [write=1]: the hidden about: pages explained page by page, English (public, both
        # tracks) and Romanian (private, plain words), rendered from the owner's source and checked (hiddendocs.py)
        from . import hiddendocs as hd, ownercheck, changecheck
        owner = ownercheck._owner_root(task.load(tid))
        if not owner:
            raise task.Refused("this task has no owner repo")
        _root, repo = changecheck.patchset_repo(owner)
        if not repo:
            raise task.Refused("the patch-set repo is not a git checkout")
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        if opts.get("release"):
            intro, entries = hd.load(owner)
            text = hd.render_release(intro, entries, hd.register(owner))
            from pathlib import Path as _P
            _P(opts["release"]).write_bytes(text.encode("utf-8"))
            print(f"release edition: {opts['release']} ({len(text)} characters of the release page's 125000)")
            return 0
        if opts.get("write", "0") not in ("0", "no", "false", ""):
            r = hd.write(owner, repo, say=lambda m: print(m, flush=True))
            for f in r["written"]:
                print("  " + f)
            rows = r["rows"]
        else:
            rows = hd.check(owner, repo)
        for row in rows:
            print(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence']}")
        print("HIDDEN-PAGES DOC " + ("OK" if all(r["ok"] for r in rows) else "NOT OK"))
        return 0 if all(r["ok"] for r in rows) else 3
    if act == "release-page":
        # release-page TASK dir=<releases-draft/<release>>: RELEASE-BODY.md from <dir>/PAGE.parts, then release-cover on
        # it (releasepage.py; 2026-10-19: the page had been assembled by a script nobody kept)
        from . import releasepage, releasecover, ownercheck
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        if not opts.get("dir"):
            raise task.Refused("usage: build-harness release-page TASK dir=<releases-draft/<release>>")
        page, n = releasepage.write(opts["dir"])
        print(f"written: {page} ({n} characters)")
        rows = releasecover.run(ownercheck._owner_root(task.load(tid)), str(page), opts.get("previous"))
        for row in rows:
            print(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence']}")
        print("RELEASE PAGE " + ("COVERS EVERYTHING" if all(r["ok"] for r in rows) else "INCOMPLETE"))
        return 0 if all(r["ok"] for r in rows) else 3
    if act == "release-cover":
        # release-cover TASK page=<release page .md> [previous=<tag or file>]: the release page names every decision
        # made since the last release and fits GitHub's limit (releasecover.py)
        from . import releasecover, ownercheck
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        owner = ownercheck._owner_root(task.load(tid))
        if opts.get("due"):
            # due=<file>: the decisions this page must name, in the register's own words, whole (2026-10-18)
            import sys as _s
            from .decisions import register_file
            reg = register_file(Path(owner))
            pages = releasecover.platform_pages(suffix="-win64" if _s.platform == "win32" else None)
            ids = [i for i, _ in releasecover.uncovered(reg, pages, min(p["published"] for p in pages))]
            Path(opts["due"]).write_text(releasecover.due_text(reg, ids), encoding="utf-8")
            print(f"due: {len(ids)} decision(s) written in full to {opts['due']}")
            if not opts.get("page"):
                return 0
        rows = releasecover.run(owner, opts.get("page"), opts.get("previous"))
        for row in rows:
            print(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence']}")
        print("RELEASE PAGE " + ("COVERS EVERYTHING" if all(r["ok"] for r in rows) else "INCOMPLETE"))
        return 0 if all(r["ok"] for r in rows) else 3
    if act == "leakgate-scope":
        # leakgate-scope TASK [from=<tree>]: what changed since the last passed gate, and whether any of it can reach
        # the network (leakgate/scope.py); reports only, the gate is unchanged
        from ..leakgate import scope as lsc
        from ..core import settings as _st
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        r = lsc.run(task.load(tid)["workdir"], _st.ROOT / "state" / "leakgate-launcher", tid,
                    task.STATE / tid / "builds.jsonl", from_tree=opts.get("from"))
        lp = r.get("last_pass") or {}
        print(f"  last passed gate: {lp.get('run', '(none)')} on build {lp.get('build_id')} (tree {str(r['from'])[:12]})")
        print(f"  changed since: {len(r['cannot']) + len(r['can'])} file(s): {len(r['cannot'])} cannot reach the network, "
              f"{len(r['can'])} can")
        for n, why in r["can"][:25]:
            print(f"    can: {n} ({why})")
        print("LEAK-GATE SCOPE: " + r["verdict"])
        return 0
    if act == "set-proof":
        # set-proof [TASK] [patchset=DIR] [version=158.0] [platform=windows|linux|all]: pristine + each platform's
        # selection must apply; excluded dependencies and refreshed lists named (setproof.py, 2026-10-16)
        from . import setproof, vault as _vault
        opts = dict(x.split("=", 1) for x in a.args if "=" in x)
        if opts.get("patchset"):
            pset_root = Path(opts["patchset"]).resolve()
        else:
            from .ownercheck import patchset_owner
            pset_root = Path(patchset_owner(task.load(tid)))
        versions = sorted((r["version"] for r in _vault.listing() if r["product"] == "firefox"),
                          key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")])
        version = opts.get("version") or (versions[-1] if versions else None)
        if not version:
            raise task.Refused("set-proof: no Firefox in the vault; fetch it first (build-harness vault fetch firefox)")
        pristine = _vault.root() / "firefox" / version
        plats = setproof.PLATFORMS if opts.get("platform", "all") == "all" else (opts["platform"],)
        ok = True
        out = []
        for p in plats:
            r = setproof.prove(pset_root, pristine, p, say=lambda m: None)
            ok &= r["ok"]
            out.append(r)
        if a.json:
            print(json.dumps(out, indent=1))
        else:
            for r in out:
                print("\n".join(setproof.lines(r)))
        return 0 if ok else 3
    if act == "profile-diff":
        # profile-diff TASK probe=<name> profile=<dir> [install=<dir>]: the probe on a fresh profile and on a copy of
        # the real one; exit 3 when they differ (profilediff.py; born 2026-10-18, Perplexity only on the real profile)
        from . import profilediff, install as _inst
        opts = dict(x.split("=", 1) for x in a.args if "=" in x)
        if not opts.get("probe") or not opts.get("profile"):
            raise task.Refused("usage: build-harness profile-diff TASK probe=<name> profile=<profile dir> [install=<dir>]")
        inst = opts.get("install") or getattr(a, "install_dir", None) or _inst.find_install()
        if not inst:
            raise task.Refused("no installed Gorilla found; name it: install=<dir>")
        r = profilediff.diff(inst, opts["probe"], opts["profile"])
        print(json.dumps(r, indent=1) if a.json else "\n".join(profilediff.lines(r, opts["probe"], opts["profile"])))
        return 0 if r["ok"] else 3
    if act == "search-strip":
        # search-strip TASK [--apply]: D-158-13's leftovers (other engines' icons, wordmarks) out of the source
        from . import searchstrip
        w = task.load(tid)["workdir"]
        r = searchstrip.apply(w) if a.apply else searchstrip.check(w)
        print(json.dumps(r if a.apply else r, indent=1) if a.json else
              ("removed: " + ", ".join(f"{k} {len(v)}" for k, v in r.items()) if a.apply else
               ("SEARCH-STRIP: Google only" if r["ok"] else f"SEARCH-STRIP: {len(r['left'])} left, e.g. {r['left'][:5]}")))
        return 0 if a.apply or r["ok"] else 3
    if act == "runbook":
        # runbook run|status FILE: the harness runs the steps (fieldkit/autopilot/runbook.py); here so that
        # `build-harness window runbook run FILE` gives it the detached window, the log and the keep-awake
        # 2026-10-18 (found by the tool-harness review session): the first word was taken as the task id, so
        # `runbook status FILE` ran "status" as a file. ACTION and FILE are read from the words; no FILE: refused
        from ..cli import runbook_main
        words = [w for w in a.args if w in ("run", "status", "start")] or ["status"]
        files = [w for w in a.args if w.lower().endswith(".md")]
        if not files:
            raise task.Refused("usage: build-harness runbook run|status|start FILE.md (the runbook file is required)")
        return runbook_main(words[0], files[0])
    if act == "pref-conflicts":
        # pref-conflicts TASK: two definitions the patch set adds for one pref, with different values under
        # conditions that can both hold (prefconflicts.py; 2026-10-16: Inter vs Segoe UI, 8 more). Exit 3 on any.
        from . import prefconflicts
        found = prefconflicts.scan(task.load(tid)["workdir"])
        print(json.dumps(found, indent=1) if a.json else "\n".join(prefconflicts.lines(found)))
        return 3 if found else 0
    if act == "profile-keep":
        # profile-keep [TASK] [install=DIR]: which profile holds the saved logins and which install folder opens it;
        # FAIL when DIR (default: the Gorilla install) would open another, empty profile (profilekeep.py, D-157-28)
        from . import profilekeep, install as _inst
        opts = dict(x.split("=", 1) for x in a.args if "=" in x)
        target = Path(opts["install"]) if opts.get("install") else _inst.find_install()
        if not target:
            raise task.Refused("profile-keep: no single Gorilla install found; name it with install=DIR")
        r = profilekeep.check(target, _inst.profiles_dir())
        print(json.dumps(r, indent=1) if a.json else "\n".join(profilekeep.lines(r)))
        return 0 if r["ok"] else 3
    if act == "leakgate-runs":
        # leakgate-runs TASK: every gate run with its verdict, build, size and every file that cites it; only an
        # uncited run is free to remove (fieldkit/leakgate/runs.py; 2026-10-16: runs chosen by date were evidence)
        from ..leakgate import runs as lruns
        from ..core import settings as _st
        from .ownercheck import _owner_root
        tdir = _st.ROOT / "state" / "build-harness" / "leakgate" / tid
        owner = _owner_root(task.load(tid))
        rows = lruns.runs(tdir, owner, extra_roots=[_st.ROOT / "state" / "build-harness" / tid])
        if a.json:
            print(json.dumps(rows, indent=1))
        else:
            human = lambda b: f"{b / (1 << 30):.2f} GiB" if b >= 1 << 30 else f"{b / (1 << 20):.1f} MiB"
            print("\n".join(lruns.lines(rows, human)))
        return 0
    if act == "leakgate-summary":
        # leakgate-summary TASK [log=<launcher log>] [out=<file.md>]: the gate's run scene by scene, in plain words,
        # for the release page (leakgate/summary.py); default: the newest launcher run of this task
        from ..leakgate import summary as lsum
        from ..core import settings as _st
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        logs = sorted((_st.ROOT / "state" / "leakgate-launcher").glob(f"{tid}-*.log"))
        # 2026-10-19: the page describes the RELEASE run behind state/leakgate_result.json, with its (re-judged) verdict;
        # the newest log was a newtab-only re-run, and the page said "1 scenes ... FAIL"
        from . import buildrun as _br
        verdict = None
        if not opts.get("log"):
            try:
                st = json.loads((Path(_br._owner_root(task.load(tid))) / "state" / "leakgate_result.json").read_text(encoding="utf-8"))
                art = Path(st["artifacts"])
                verdict = json.loads((art / "test-results.json").read_text(encoding="utf-8"))
                logs = [l for l in logs if art.name in lsum.read(l)] or logs
            except (OSError, ValueError, KeyError):
                verdict = None
        log = Path(opts["log"]) if opts.get("log") else (logs[-1] if logs else None)
        if not log or not log.is_file():
            raise task.Refused("leakgate-summary: no leak-gate launcher log for this task")
        res = log.with_suffix(".result.json")
        r = lsum.run(log, res if res.is_file() else None, opts.get("build"), verdict=verdict)
        if opts.get("out"):
            Path(opts["out"]).write_text(r["markdown"], encoding="utf-8", newline="\n")
            print(f"  written: {opts['out']}")
        else:
            print(r["markdown"])
        if r["missing"]:
            print(f"LEAK-GATE SUMMARY NOT OK: scene(s) without a plain-words line: {r['missing']} (leakgate/summary.py PLAIN)")
            return 3
        print(f"LEAK-GATE SUMMARY OK: {len(r['scenes'])} scenes from {log.name}")
        return 0
    if act == "night-pack":
        # night-pack TASK prepare [release=TAG] [rejudge=RUN dep=REF rerun=RUN] [gate=0] | arm [hours=12] | run |
        #                 status | disarm      (owner 2026-10-19: pre-approve once, go to sleep)
        from . import nightpack as npk
        sub = a.args[1] if len(a.args) > 1 else "status"
        o = dict(x.split("=", 1) for x in a.args[2:] if "=" in x)
        say = lambda m: print(m, flush=True)
        if sub == "prepare":
            npk.prepare(tid, release=o.get("release"), rejudge=o.get("rejudge"), dep=o.get("dep"), rerun=o.get("rerun"),
                        gate=o.get("gate", "1") != "0", say=say)
            return 0
        if sub == "arm":
            return npk.arm(tid, hours=float(o.get("hours", 12)), say=say)
        if sub == "run":
            return npk.run(tid, say=say)
        if sub == "disarm":
            return npk.disarm(tid, say=say)
        print(json.dumps(npk.status(tid), indent=1))
        return 0
    if act == "release-publish":
        # release-publish TASK prepare repo=DIR gh=OWNER/REPO tag=T title="..." notes=FILE asset=F [asset=F ...]
        #                               [push=REF ...] [ci=WORKFLOW ...] [supersede=OLDTAG supersede_title="..."
        #                               supersede_notes=FILE]
        # release-publish TASK send tag=T     the owner's one action (owner terminal only; asks yes once)
        from . import releasepublish as rp
        sub = a.args[1] if len(a.args) > 1 else ""
        kv = [x.split("=", 1) for x in a.args[2:] if "=" in x]
        one = {k: v for k, v in kv}
        many = lambda k: [v for kk, v in kv if kk == k]
        say = lambda m: print(m, flush=True)
        if sub == "prepare":
            r = rp.prepare(tid, one["repo"], one["tag"], one["title"], one["notes"], many("asset"),
                           supersede=one.get("supersede"), supersede_title=one.get("supersede_title"),
                           supersede_notes=one.get("supersede_notes"), gh_repo=one.get("gh"),
                           pushes=many("push") or None, ci=many("ci") or None, say=say)
            print("RELEASE PREPARED: the owner runs `send`" if r["ok"] else "RELEASE NOT PREPARED (rows above)")
            return 0 if r["ok"] else 3
        if sub == "send":
            return rp.send(one["tag"], say=say)
        raise task.Refused("release-publish TASK prepare|send ...")
    if act == "installer-test":
        # installer-test TASK: the shipped installer installed silently into a temp folder (no shortcuts, no service),
        # checked file by file against the proved zip, uninstalled by its own uninstaller, machine as before (2026-10-19)
        from . import installertest
        r = installertest.run(tid, say=lambda m: print(m, flush=True))
        print("INSTALLER TEST " + ("OK" if r["ok"] else "NOT OK: " + "; ".join(x["check"] for x in r["rows"] if not x["ok"])))
        return 0 if r["ok"] else 3
    if act == "stops":
        # stops TASK [since=YYYY-MM-DD]: every window run that did not pass, classified - the browser, the check or the
        # machine - and each check's false alarms (stops.py)
        import datetime as _dt
        from . import stops as st
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        since = _dt.datetime.fromisoformat(opts["since"]) if opts.get("since") else _dt.datetime.now() - _dt.timedelta(days=7)
        r = st.run(tid, since=since)
        for s in r["stops"]:
            print(f"  {s['at']}  {s['cmd']:13} {s['check']:16} {s['verdict']:12} {s['evidence']}")
        print("  per check: " + "; ".join(f"{k} {v['stops']} stop(s), {v['false alarms']} false alarm(s)"
                                          for k, v in sorted(r["by_check"].items())))
        print(f"STOPS: {len(r['stops'])} of {r['runs']} run(s) since {since:%Y-%m-%d}")
        return 0
    if act == "calibrate":
        # calibrate TASK [only=<fault id>,...]: every check passes the known-good package and fails a copy broken in
        # the way it guards against (calibrate.py)
        from . import calibrate as cal
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        only = set(opts["only"].split(",")) if opts.get("only") else None
        res = cal.run(tid, only=only, say=lambda m: print(m, flush=True))
        for row in res["rows"][:1]:
            print(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence']}")
        print("CALIBRATION " + ("OK" if all(r["ok"] for r in res["rows"]) else "NOT OK"))
        return 0 if all(r["ok"] for r in res["rows"]) else 3
    if act == "timebombs":
        # timebombs TASK [margin=42] [at=YYYY-MM-DD]: every date compiled into the tree that switches a protection off,
        # judged against `at` plus `margin` days (timebombs.py, D-157-42)
        import datetime as _dt
        from . import timebombs as tb
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        at = _dt.date.fromisoformat(opts["at"]) if opts.get("at") else None
        rows = tb.rows(task.load(tid)["workdir"], at=at, margin=int(opts.get("margin", tb.MARGIN_DAYS)))
        for row in rows:
            print(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence']}")
        print("TIME BOMBS " + ("OK" if all(r["ok"] for r in rows) else "NOT OK"))
        return 0 if all(r["ok"] for r in rows) else 3
    if act == "lists-refresh":
        # lists-refresh TASK [ref=main] [write=1]: the HSTS preload, key-pin and CT log lists from the upstream
        # repository, taken only when pure data and fresher; record them afterwards (timebombs.py, D-157-42)
        from . import timebombs as tb
        opts = dict(x.split("=", 1) for x in a.args[1:] if "=" in x)
        res = tb.refresh(task.load(tid)["workdir"], ref=opts.get("ref", "main"), write=opts.get("write") == "1")
        for row in res["rows"]:
            print(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence']}")
        if res["written"]:
            print(f"  written: {', '.join(res['written'])} (from {res['source']}); now: fieldkit build-harness record {tid}")
        print("LISTS " + ("FRESH" if all(r["ok"] for r in res["rows"]) else "NOT TAKEN"))
        return 0 if all(r["ok"] for r in res["rows"]) else 3
    if act == "about-registry":
        # about-registry TASK [--json]: every about: page the source can register (tree and pristine upstream), what
        # the newest full reading of a build registered, and the owner's verdict (aboutregistry.py, D-157-40)
        from . import aboutpages as ap, aboutregistry as ar
        t = task.load(tid)
        w = t["workdir"]
        entries = ar.merged(w, ar.pristine_of(w))
        newest = None
        for f in sorted(ap.STATE.glob("about-pages-*.json"), reverse=True) if ap.STATE.is_dir() else []:
            import json as _json
            d = _json.loads(f.read_text(encoding="utf-8"))
            if d.get("registered"):         # every run lists every registered page, a partial one too
                newest = d
                break
        if newest:
            ar.with_runtime(entries, newest["registered"])
        ar.with_register(entries, ap.register_for(t))
        emit({"entries": entries, "build": (newest or {}).get("build_id", "").replace("-partial", "") or None},
             lambda r: print("\n".join(ar.table(r["entries"]) + [f"build read: {r['build'] or 'none kept'}"])))
        return 0
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
                   only=only, walk=walk, visible=visible, dwell=dwell, on_line=on_line, shots=opts.get("shots"),
                   register=None if only or walk else ap.register_for(task.load(tid)),
                   omni=change.get("omni"), files=change.get("files"), added=change.get("added"), subs=change.get("subs"))
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
    if act == "repro":
        # the binary reproduced (2026-10-11): plan (reads only), --run (the second build, then the comparison) or
        # --compare (only the comparison) - fieldkit/buildh/reprobuild.py
        from . import reprobuild as rb
        say = lambda m: print(m, flush=True)
        if getattr(a, "run", False):
            r = rb.run(tid, say=say)
        elif getattr(a, "compare", False):
            r = rb.compare_only(tid, say=say)
        else:
            r = rb.plan(tid)
            if a.json:
                print(json.dumps(r, indent=1, default=str))
            else:
                for row in r["rows"]:
                    say(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence']}")
                if r["ok"]:
                    say(f"second source folder: {r['work2']}\nsecond objdir:        {r['obj2']}")
                    say(f"NEXT: {_settings.self_cmd()} build-harness window repro {tid} --run   (a full build, like the first)")
                else:
                    say("REPRO CANNOT RUN YET: fix the FAIL rows above")
            return 0 if r["ok"] else 3
        if not r.get("ok") and r.get("why"):
            say("NOT REPRODUCED: " + r["why"])
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
            # `leakgate-rejudge TASK [RUN] [dep=REF]`: the same evidence judged again by the current judge and allowlist;
            # dep=REF compares the crates with that git ref's Cargo.lock in the ported tree (owner 2026-10-19, D)
            if not task.owner_terminal():                   # 2026-10-19: re-judging is the owner's, at a real terminal
                raise task.Refused("re-judging a leak gate is the owner's, at a real terminal: " + task.agent_shell())
            rest = a.args[1:]
            dep = next((x.split("=", 1)[1] for x in rest if x.startswith("dep=")), None)
            rerun = next((x.split("=", 1)[1] for x in rest if x.startswith("rerun=")), None)   # owner 2026-10-19
            rest = [x for x in rest if not x.startswith(("dep=", "rerun="))]
            runs = sorted(p for p in workroot.iterdir() if (p / "events.jsonl").is_file())
            run_dir = workroot / rest[0] if rest else runs[-1]
            new, changes = lg.rejudge(run_dir, owner, why=a.note or "",
                                      dependency_baseline=(t["workdir"], dep) if dep else None,
                                      rerun=workroot / rerun if rerun else None)
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
                                        n_minus_1_tree=vf._truth_root(t["meta"].get("harness_root") or "", t["workdir"]), say=say,   # 2026-10-18: no older-upstream stand-in; the owner's release N-1 or a stated "no baseline"
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
        # 2026-10-19: a run limited to some scenarios (--only) is never the release result. Recorded as release_run it
        # would have let release-check pass the whole gate on one scenario; it goes to its own file instead, and only
        # `leakgate-rejudge ... rerun=RUN` folds it into a full run's verdict.
        partial = bool(only)
        (owner / "state" / ("leakgate_result.partial.json" if partial else "leakgate_result.json")).write_text(_js.dumps({
            "build_id": bid, "BUILD": result["BUILD"], "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "release_run": bool(getattr(a, "release", False)) and not partial, "only": sorted(only) if only else None,
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
        # the truth tree's task was fixed in the code as firefox-155.0.1; --truth-task names another (default unchanged)
        repo155 = task.load(getattr(a, "truth_task", None) or "firefox-155.0.1")["workdir"]
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
        owner = dec.register_root(t)
        if owner is None:
            print(f"no decision register for {tid}: expected decisions/PRODUCT-DECISIONS.yaml in the owner repo or in "
                  f"{t.get('meta', {}).get('harness_root')}. Nothing was checked.")
            return 3
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
