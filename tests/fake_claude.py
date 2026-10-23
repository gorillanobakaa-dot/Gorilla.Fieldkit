"""A fake `claude` for the long horizon chaos tests (tests/test_long_horizon.py). Never a real model.

It plays one scripted action per invocation from the JSON list in $FAKE_CLAUDE_PLAN (a counter file beside it says
which), logs every invocation (args, prompt, cwd, FIELDKIT_JOB_DIR) to $FAKE_CLAUDE_PLAN.log.jsonl, and answers like
Claude Code's `-p --output-format json`. Actions ("do"):
  success          {"write": {"path", "text"}, "result", "usd"}   exit 0
  max_turns        {"write"}                                     subtype error_max_turns, exit 1
  usage_limit      {"text"}                                      "Claude AI usage limit reached|EPOCH", exit 1
  429              {"text"}                                      API Error: 429 ... retry-after, exit 1
  crash_mid_commit {"write"}                                     writes, leaves .git/index.lock, dies with no JSON
  plan             {"plan"}                                      writes the plan to the PLAN_FILE named in the prompt
  verdict          {"verdict", "evidence"}                       prints VERDICT: ... as its result
  sleep            {"seconds"}                                   then success (a stuck run for the wall budget)
  hook             {"calls", "tool", "args"}                     runs the real PreToolUse/PostToolUse hook N times
                                                                 (as Claude Code would) and records their answers
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def main():
    plan_p = Path(os.environ["FAKE_CLAUDE_PLAN"])
    plan = json.loads(plan_p.read_text(encoding="utf-8"))
    n_p = plan_p.with_suffix(".n")
    n = int(n_p.read_text()) if n_p.exists() else 0
    n_p.write_text(str(n + 1))
    args = sys.argv[1:]
    prompt = args[args.index("-p") + 1] if "-p" in args and args.index("-p") + 1 < len(args) and \
        not args[args.index("-p") + 1].startswith("--") else sys.stdin.read()
    act = plan[min(n, len(plan) - 1)]
    with open(str(plan_p) + ".log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"n": n, "args": args, "prompt": prompt, "cwd": os.getcwd(), "do": act["do"],
                            "job": os.environ.get("FIELDKIT_JOB_DIR")}) + "\n")
    sid = f"sess-{n + 1}"
    if act.get("write"):
        p = Path(act["write"]["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(act["write"].get("text", "x"), encoding="utf-8")

    def answer(rc=0, **kw):
        out = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "session_id": sid,
               "total_cost_usd": act.get("usd", 0.01), "result": "ok"}
        out.update(kw)
        print(json.dumps(out))
        sys.exit(rc)

    do = act["do"]
    if do == "success":
        answer(result=act.get("result", "job done"))
    if do == "max_turns":
        answer(1, subtype="error_max_turns", is_error=True, num_turns=10000, result=None)
    if do == "usage_limit":
        answer(1, is_error=True, result=act.get("text") or f"Claude AI usage limit reached|{int(time.time()) + 3600}")
    if do == "429":
        answer(1, is_error=True, result=act.get("text") or
               'API Error: 429 {"type":"error","error":{"type":"rate_limit_error"}} retry-after: 5')
    if do == "crash_mid_commit":
        gd = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], capture_output=True, text=True).stdout.strip()
        Path(gd, "index.lock").write_text("")
        print("Segmentation fault (core dumped)", file=sys.stderr)
        sys.exit(139)
    if do == "plan":
        m = re.search(r"PLAN_FILE:\s*(.+)", prompt)
        Path(m.group(1).strip()).write_text(json.dumps(act["plan"]), encoding="utf-8")
        answer(result="plan written")
    if do == "verdict":
        answer(result=f"I checked.\nVERDICT: {act['verdict']} {act.get('evidence', '')}")
    if do == "sleep":
        if act.get("child_pid_file"):          # a grandchild, as Claude Code's own tool processes are
            c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            Path(act["child_pid_file"]).write_text(str(c.pid))
        time.sleep(act["seconds"])
        answer(result="slept")
    if do == "hook":
        outs = []
        for i in range(act["calls"]):
            payload = json.dumps({"tool_name": act.get("tool", "Bash"), "tool_input": act.get("args", {"command": "echo hi"})})
            pre = subprocess.run([sys.executable, "-m", "fieldkit.autopilot.claude_hook"], input=payload,
                                 capture_output=True, text=True)
            post = subprocess.run([sys.executable, "-m", "fieldkit.autopilot.claude_hook", "--post"], input=payload,
                                  capture_output=True, text=True)
            outs.append({"pre": pre.stdout, "post": post.stdout})
        Path(str(plan_p) + f".hook{n}.json").write_text(json.dumps(outs), encoding="utf-8")
        answer(result="hooked")
    print(f"unknown action {do}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
