"""Run the exam against an OpenAI-compatible server (LM Studio by default).

The chat loop follows model-eval's run_scenario: send, execute tool calls,
append results, repeat until the model answers without a tool call or the
round limit is hit. Truncation is recorded, never scored as a pass.
"""
import json
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import fixture, tasks
from .tools import Toolbox

SYSTEM = ("You are a coding assistant working inside a small project folder. You can only see the project "
          "through the tools. " + tasks.FORMAT)
DEFAULT_BASE = "http://localhost:1234/v1"


def chat(base, model, messages, tools, max_tokens=1024, timeout=900):
    body = {"model": model, "messages": messages, "tools": tools, "tool_choice": "auto",
            "max_tokens": max_tokens, "temperature": 0}
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
    calls, malformed = [], 0
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args, malformed = {}, malformed + 1
        calls.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args if isinstance(args, dict) else {}})
    usage = data.get("usage") or {}
    return {"content": msg.get("content") or "", "tool_calls": calls, "malformed": malformed,
            "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0)}


def run_task(task, model, kit, base=DEFAULT_BASE, max_rounds=10, workdir=None):
    work = Path(workdir or tempfile.mkdtemp(prefix="fieldkit-exam-"))
    root = work / "project"
    if root.exists():
        shutil.rmtree(root)
    fixture.build(root)
    box = Toolbox(root, kit=kit)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task.prompt}]
    rec = {"task": task.id, "model": model, "toolset": "kit" if kit else "raw", "rounds": 0, "prompt_tokens": 0,
           "completion_tokens": 0, "malformed_calls": 0, "error": None, "truncated": False}
    reply_text = ""
    t0 = time.monotonic()
    for _ in range(max_rounds):
        rec["rounds"] += 1
        try:
            r = chat(base, model, messages, box.schema)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            break
        rec["prompt_tokens"] += r["prompt_tokens"]
        rec["completion_tokens"] += r["completion_tokens"]
        rec["malformed_calls"] += r["malformed"]
        reply_text = r["content"]
        messages.append({"role": "assistant", "content": r["content"], **({"tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
            for c in r["tool_calls"]]} if r["tool_calls"] else {})})
        if not r["tool_calls"]:
            break
        for c in r["tool_calls"]:
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                             "content": box.dispatch(c["name"], c["arguments"])[:8000]})
    else:
        rec["truncated"] = True
    rec["seconds"] = round(time.monotonic() - t0, 1)
    answer, followed = tasks.final_answer(reply_text)
    passed, why = task.grade(answer) if not rec["error"] and not rec["truncated"] else (False, "no final answer")
    rec.update(passed=passed, why=why, answer=answer[:400], answer_format=followed,
               tool_calls=[{"name": c["name"], "args": c["args"], "chars": c["chars"]} for c in box.calls])
    shutil.rmtree(work, ignore_errors=True) if workdir is None else None
    return rec


def run(model, toolsets=("raw", "kit"), task_ids=None, base=DEFAULT_BASE, out_dir=None, max_rounds=10, log=print):
    """All tasks x toolsets for one model. Writes one JSON per run and returns the records."""
    out_dir = Path(out_dir) if out_dir else None
    records = []
    for t in tasks.TASKS:
        if task_ids and t.id not in task_ids:
            continue
        for ts in toolsets:
            log(f"  {model} | {t.id:<10} | {ts} ...")
            rec = run_task(t, model, kit=(ts == "kit"), base=base, max_rounds=max_rounds)
            rec["run"] = out_dir.name if out_dir else ""
            log(f"      {'PASS' if rec['passed'] else 'FAIL'}  {rec['why']}  "
                f"({rec['rounds']} rounds, {len(rec['tool_calls'])} calls, {rec['prompt_tokens']} prompt tokens, "
                f"{rec['seconds']} s)")
            records.append(rec)
            if out_dir:
                out_dir.mkdir(parents=True, exist_ok=True)
                name = f"{model.replace('/', '_')}__{t.id}__{ts}.json"
                (out_dir / name).write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    return records


# The kit tool made for each task. A kit only helps if the model picks it; the
# summary reports how often it did, so "kit passed" and "kit was used" stay apart.
KIT_TOOL_FOR = {"locate": "find", "trap": "find", "snippet": "find", "build-log": "triage", "manifest": "refcheck"}


def used_kit_tool(rec):
    want = KIT_TOOL_FOR.get(rec["task"])
    return bool(want) and any(c["name"] == want for c in rec["tool_calls"])


def summary(records):
    """Per model x toolset: passes, prompt tokens, seconds, calls, and (kit) how often the task's tool was chosen."""
    rows = {}
    for r in records:
        k = (r.get("run", ""), r["model"], r["toolset"])
        s = rows.setdefault(k, {"run": r.get("run", ""), "model": r["model"], "toolset": r["toolset"], "tasks": 0,
                                "passed": 0,
                                "prompt_tokens": 0, "seconds": 0.0, "tool_calls": 0, "format_misses": 0,
                                "chose_kit_tool": 0})
        s["tasks"] += 1
        s["passed"] += int(r["passed"])
        s["prompt_tokens"] += r["prompt_tokens"]
        s["seconds"] += r["seconds"]
        s["tool_calls"] += len(r["tool_calls"])
        s["format_misses"] += int(not r["answer_format"])
        s["chose_kit_tool"] += int(r["toolset"] == "kit" and used_kit_tool(r))
    return list(rows.values())
