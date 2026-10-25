# Version: 1.0.0 · updated 26-10-25
"""Why a local model is slow: read LM Studio's own server log, request by request.

    fieldkit lmstudio-log                       the newest log (~/.cache/lm-studio/server-logs/YYYY-MM/*.log)
    fieldkit lmstudio-log --day 2026-10-25      every log of that day
    fieldkit lmstudio-log FILE... [--last 10] [--json]

WHY (2026-10-25)
    Gemma 4 12B in LM Studio felt much slower inside Gorilla OpenCode than in LM Studio's own chat. The answer was in
    LM Studio's server log, read by hand: a 12,990-token prompt prefilled at 49 tok/s (4 min 23 s) before the first
    word, then 3.7 tok/s of generation; and twice "ggml_vulkan: device lost" (the Intel GPU driver dropped the
    device mid-request), fixed by loading the model on the CPU. This prints those numbers for every request.

Per request (llama.cpp's slot lines, keyed by task id; task ids restart when a model is loaded again):
    start time, model, messages, prompt tokens evaluated, prefill seconds and tok/s, generated tokens, generation
    tok/s, n_tokens (the context the slot held at the end), and whether it ended in a Vulkan device lost.
Read-only: the log is only read. Nothing is sent anywhere.
"""
import argparse
import json
import re
from pathlib import Path

HEAD = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]\[(\w+)\](?:\[([^\]]+)\])?\s?(.*)$")
TASK = re.compile(r"\bid\s+(\d+)\s*\|\s*task\s+(-?\d+)\s*\|")
PROMPT_EVAL = re.compile(r"prompt eval time =\s*([\d.]+) ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")
GEN_EVAL = re.compile(r"(?<!prompt )\beval time =\s*([\d.]+) ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")
PROGRESS = re.compile(r"prompt processing, n_tokens =\s*(\d+), progress = [\d.]+, t =\s*([\d.]+) s /\s*([\d.]+) tokens")
STOP = re.compile(r"stop processing: n_tokens =\s*(\d+)")
RUNNING = re.compile(r"Running chat completion on conversation with (\d+) messages")
LOST = re.compile(r"device lost|ErrorDeviceLost", re.I)

FIX_LOST = ("the GPU driver dropped the device mid-request (Vulkan device lost) and the request failed. Load the "
            "model on the CPU: lms unload --all, then lms load <model> --gpu off (LM Studio: GPU offload 0). "
            "Restarting Windows clears the driver's state; until then a large prompt can lose the device again.")


def default_logs(day=None, root=None):
    root = Path(root) if root else Path.home() / ".cache" / "lm-studio" / "server-logs"
    if day:
        return sorted((root / day[:7]).glob(f"{day}*.log"))
    logs = sorted(root.glob("*/*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1:]


def parse(lines):
    """-> {requests: [...], lost: [...]} from the log's lines, in order."""
    reqs, by_key, epoch, ts, model, pending = [], {}, 0, None, None, None
    lost = []

    def req(task):
        key = (epoch, task)
        if key not in by_key:
            r = {"task": task, "start": ts, "model": model, "messages": None, "prompt_tokens": None,
                 "prefill_s": None, "prefill_tps": None, "gen_tokens": None, "gen_tps": None, "gen_s": None,
                 "n_tokens": None, "end": None, "device_lost": False, "prefill_progress": None}
            by_key[key] = r
            reqs.append(r)
        return by_key[key]

    for raw in lines:
        line = raw.rstrip("\r\n")
        m = HEAD.match(line)
        if m:
            ts = m.group(1)
            if m.group(3):
                model = m.group(3)
            body = m.group(4)
        else:
            body = line
        if "llama_server: model loaded" in body:
            epoch += 1
            continue
        rm = RUNNING.search(body)
        if rm:
            pending = {"messages": int(rm.group(1)), "start": ts, "model": model}
            continue
        tm = TASK.search(body)
        task = int(tm.group(2)) if tm else None
        if LOST.search(body):
            if task is not None and task >= 0:
                req(task)["device_lost"] = True
            if lost and lost[-1]["time"] == ts:            # one loss is several lines in the same second
                if lost[-1]["task"] is None and task is not None and task >= 0:
                    lost[-1]["task"] = task
            else:
                lost.append({"time": ts, "model": model, "task": task if task is not None and task >= 0 else None,
                             "line": body.strip()[:160]})
            continue
        if task is None or task < 0:
            continue
        if "processing task" in body:
            r = req(task)
            if pending:
                r.update(pending)
                pending = None
            continue
        pm = PROMPT_EVAL.search(body)
        if pm:
            r = req(task)
            r["prefill_s"], r["prompt_tokens"], r["prefill_tps"] = float(pm.group(1)) / 1000, int(pm.group(2)), float(pm.group(3))
            continue
        gm = GEN_EVAL.search(body)
        if gm:
            r = req(task)
            r["gen_s"], r["gen_tokens"], r["gen_tps"] = float(gm.group(1)) / 1000, int(gm.group(2)), float(gm.group(3))
            continue
        pr = PROGRESS.search(body)
        if pr:
            req(task)["prefill_progress"] = {"n_tokens": int(pr.group(1)), "s": float(pr.group(2)),
                                             "tps": float(pr.group(3))}
            continue
        sm = STOP.search(body)
        if sm:
            r = req(task)
            r["n_tokens"], r["end"] = int(sm.group(1)), ts
    for x in lost:          # a lost device names the request it killed when the log says which task
        x["fix"] = FIX_LOST
    return {"requests": [r for r in reqs if r["prompt_tokens"] is not None or r["device_lost"] or r["n_tokens"]],
            "lost": lost}


def read(paths):
    lines = []
    for p in paths:
        lines += Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
    out = parse(lines)
    out["files"] = [str(p) for p in paths]
    return out


def _num(x, fmt="{:,}"):
    return "?" if x is None else fmt.format(x)


def lines(d, last=None):
    reqs = d["requests"][-last:] if last else d["requests"]
    out = [f"{len(d['requests'])} request(s) in {', '.join(Path(f).name for f in d.get('files', [])) or 'the log'}"]
    for r in reqs:
        if r["prompt_tokens"] is None and r["prefill_progress"]:
            pp = r["prefill_progress"]
            pre = f"prefill reached {pp['n_tokens']:,} tok at {pp['tps']:.1f} tok/s ({pp['s']:.0f} s), never finished"
        else:
            pre = (f"prompt {_num(r['prompt_tokens'])} tok  prefill {_num(r['prefill_tps'], '{:.1f}')} tok/s "
                   f"({_num(r['prefill_s'], '{:.0f}')} s)")
        gen = ("" if r["gen_tokens"] is None else
               f"  gen {r['gen_tokens']:,} tok {r['gen_tps']:.2f} tok/s ({r['gen_s']:.0f} s)")
        out.append(f"{(r['start'] or '?')[11:]}  {r['model'] or '?'}  task {r['task']}"
                   + (f"  {r['messages']} msg" if r["messages"] else "") + f"  {pre}{gen}"
                   + f"  n_tokens {_num(r['n_tokens'])}" + ("  DEVICE LOST" if r["device_lost"] else ""))
    slow = [r for r in reqs if r["prefill_s"] and r["gen_s"] and r["prefill_s"] > r["gen_s"]]
    if slow:
        out.append(f"{len(slow)} request(s) spent longer reading the prompt than writing the answer: a shorter "
                   "prompt (fewer tools, less history) is what makes them faster, not a faster generation")
    for x in d["lost"]:
        out.append(f"DEVICE LOST {x['time']}  {x['model'] or '?'}  task {x['task']}: {x['line']}")
    if d["lost"]:
        out.append("FIX: " + FIX_LOST)
    return out


def main(argv=None, prog="fieldkit lmstudio-log"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="log files (default: the newest LM Studio server log)")
    ap.add_argument("--day", help="every log of this day, YYYY-MM-DD")
    ap.add_argument("--last", type=int, help="only the last N requests")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    paths = [Path(f) for f in a.files] or default_logs(a.day)
    missing = [str(p) for p in paths if not p.is_file()]
    if not paths or missing:
        print(f"REFUSED: no LM Studio server log {'at ' + ', '.join(missing) if missing else 'found'} "
              "(LM Studio writes them to ~/.cache/lm-studio/server-logs/YYYY-MM/ while its server runs)")
        return 2
    d = read(paths)
    if a.json:
        print(json.dumps(d, indent=1))
    else:
        print("\n".join(lines(d, a.last)))
    return 0 if not d["lost"] else 3
