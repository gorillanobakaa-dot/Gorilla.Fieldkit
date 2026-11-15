"""accept - the everyday-jobs acceptance suite for Gorilla OpenCode (HARNESS-PLAN P5a).

    fieldkit accept list [--json]                      the jobs (fieldkit/method/accept_suite.yaml) and what each needs
    fieldkit accept run --model M [--job ID ...] [--timeout S] [--json]
                                                       run each job through `gorilla-opencode -p` in a fresh folder with
                                                       its own config pinned to M, judge the DISK (or the reply), append
                                                       one row per job to state/accept/results.jsonl
    fieldkit accept report [--json]                    pass/miss counts per model and OS, Windows and Linux side by side

A job whose program is not installed is SKIP (counted, never a pass). The judge reads the disk or the final reply; a
model's claim of success is never believed (rule 1: verify the artefact). A miss row keeps the prompt, the reply tail and
the judge's reason. Exit 0 no miss, 3 a miss or error, 2 bad usage.
"""
import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import zlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

SUITE = Path(__file__).parent / "method" / "accept_suite.yaml"
RESULTS = Path(__file__).resolve().parent.parent / "state" / "accept" / "results.jsonl"


def _png():
    """A valid 1x1 PNG (every chunk with its CRC), built here so no hand-typed byte string can be corrupt."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def jobs():
    return yaml.safe_load(SUITE.read_text(encoding="utf-8"))


def os_name():
    return "windows" if sys.platform.startswith("win") else ("darwin" if sys.platform == "darwin" else "linux")


def missing_needs(job, which=shutil.which):
    """`needs` is an any-of list: the job is skipped only when none of the programs is installed."""
    need = job.get("needs") or []
    return [] if not need or any(which(n) for n in need) else list(need)


def _make(path, spec):
    path.parent.mkdir(parents=True, exist_ok=True)
    kind, _, body = str(spec).partition(":")
    if kind == "text":
        path.write_text(body, encoding="utf-8", newline="\n")
    elif kind == "json":
        path.write_text(json.dumps(json.loads(body)), encoding="utf-8")
    elif kind == "zip":
        with zipfile.ZipFile(path, "w") as z:
            for n, t in yaml.safe_load(body).items():
                z.writestr(n, str(t))
    elif kind == "png":
        path.write_bytes(_png())
    elif kind == "png-text":
        subprocess.run([shutil.which("magick") or shutil.which("convert"), "-size", "500x160", "xc:white", "-pointsize",
                        "96", "-fill", "black", "-annotate", "+20+110", body, str(path)], check=True, capture_output=True)
    elif kind == "mp4-seconds":
        subprocess.run([shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=" + body, str(path)],
                       check=True, capture_output=True)
    else:
        raise ValueError(f"unknown setup kind {kind!r}")


def setup(job, folder):
    for rel, spec in (job.get("setup") or {}).items():
        _make(folder / rel, spec)
    for rel, text in (job.get("serve") or {}).items():
        target = folder.parent / (folder.name + "-serve") / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


def judge(spec, folder, reply):
    """-> (ok, reason). Reads the disk or the reply, never the model's claim of success."""
    kind = spec["kind"]
    p = folder / spec["path"] if "path" in spec else None
    if kind == "answer_has":
        ok = str(spec["value"]).lower() in (reply or "").lower()
        return ok, "" if ok else f"reply lacks {spec['value']!r}"
    if kind == "file_absent":
        if p.exists():
            return False, f"{spec['path']} still exists"
    elif kind == "file_exists":
        if not p.is_file():
            return False, f"{spec['path']} is missing"
    elif kind == "file_text":
        if not p.is_file():
            return False, f"{spec['path']} is missing"
        if str(spec["value"]) not in p.read_text(encoding="utf-8", errors="replace"):
            return False, f"{spec['path']} lacks {spec['value']!r}"
    elif kind == "json_value":
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return False, f"{spec['path']} unreadable: {e}"
        if doc.get(spec["key"]) != spec["value"]:
            return False, f"{spec['key']} is {doc.get(spec['key'])!r}, want {spec['value']!r}"
        if spec.get("and_key") and spec["and_key"] not in doc:
            return False, f"key {spec['and_key']} was dropped"
    else:
        raise ValueError(f"unknown judge kind {kind!r}")
    if spec.get("and_exists") and not (folder / spec["and_exists"]).exists():
        return False, f"{spec['and_exists']} was lost"
    if spec.get("and_absent") and (folder / spec["and_absent"]).exists():
        return False, f"{spec['and_absent']} is still there (copied, not moved)"
    return True, ""


def isolated_env(root, model):
    """Own config/data/state pinned to MODEL, so the owner's live config changing mid-run cannot skew a row.
    Copies the local endpoints and MCP servers, plus the saved sign-in of the ONE provider a cloud model names
    (antigravity.gemini-..., never the others)."""
    real = Path.home() / ".config" / "gorilla-opencode" / "config.json"
    try:
        src = json.loads(real.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        src = {}
    cfg = {k: src[k] for k in ("localEndpoints", "mcpServers", "shell", "extras", "extrasChoiceMade") if k in src}
    prefix = model.split(".", 1)[0]
    if prefix != "local" and isinstance(src.get("providers", {}).get(prefix), dict):
        # a cloud model: carry that one provider's saved sign-in (never read or printed here); the env folder is
        # removed when the job ends
        cfg["providers"] = {prefix: src["providers"][prefix]}
        oauth = real.parent / f"{prefix}-oauth.json"
        if oauth.is_file():
            (root / "config" / "gorilla-opencode").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(oauth, root / "config" / "gorilla-opencode" / oauth.name)
    cfg["agents"] = {a: {"model": model} for a in ("coder", "summarizer", "task", "title")}
    cfg["permissions"] = {"mode": "auto"}
    d = root / "config" / "gorilla-opencode"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return dict(os.environ, XDG_CONFIG_HOME=str(root / "config"), XDG_DATA_HOME=str(root / "data"),
                XDG_STATE_HOME=str(root / "state"), NO_COLOR="1")


def default_agent(prompt, folder, model, timeout):
    env = isolated_env(folder.parent / (folder.name + "-env"), model)
    r = subprocess.run(["gorilla-opencode", "-p", prompt, "-q", "-c", str(folder)], cwd=folder, env=env,
                       capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


RAN_MARK = "--- what actually ran"


def ran_record(reply, limit=1500):
    """What the model actually did, as Gorilla OpenCode records it at the end of the reply ('' when absent)."""
    i = (reply or "").find(RAN_MARK)
    return reply[i:i + limit] if i >= 0 else ""


def run_job(job, model, agent=default_agent, timeout=300, which=shutil.which):
    """-> one result row. The agent is injected so a test needs no model."""
    row = {"job": job["id"], "model": model, "os": os_name(), "ts": int(time.time())}
    gap = missing_needs(job, which)
    if gap:
        return {**row, "result": "SKIP", "reason": "needs " + " or ".join(gap)}
    folder = Path(tempfile.mkdtemp(prefix="fk-accept-"))
    server = None
    try:
        setup(job, folder)
        prompt = job["prompt"]
        if job.get("serve"):
            root = folder.parent / (folder.name + "-serve")
            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            prompt = prompt.replace("{url}", f"http://127.0.0.1:{server.server_port}")
        t0 = time.time()
        try:
            reply = agent(prompt, folder, model, timeout)
        except subprocess.TimeoutExpired:
            return {**row, "result": "MISS", "reason": f"timeout after {timeout}s", "prompt": prompt}
        except OSError as e:
            return {**row, "result": "ERROR", "reason": str(e)}
        ok, why = judge(job["judge"], folder, reply)
        out = {**row, "result": "PASS" if ok else "MISS", "seconds": round(time.time() - t0, 1)}
        if not ok:
            out.update(reason=why, prompt=prompt, reply_tail=(reply or "")[-600:], ran=ran_record(reply))
        return out
    finally:
        if server:
            server.shutdown()
        for suffix in ("", "-serve", "-env"):  # folders this run made in the temp root: scratch, not owner data
            shutil.rmtree(folder.parent / (folder.name + suffix), ignore_errors=True)


def report(rows):
    """-> {(model, os): {"pass": n, "miss": n, "skip": n, "error": n}}"""
    table = {}
    for r in rows:
        t = table.setdefault((r["model"], r["os"]), {"pass": 0, "miss": 0, "skip": 0, "error": 0})
        t[r["result"].lower()] += 1
    return table


def load_rows(path=None):
    path = Path(path or RESULTS)
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv):
    ap = argparse.ArgumentParser(prog="fieldkit accept", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--json", action="store_true")
    rn = sub.add_parser("run")
    rn.add_argument("--model", required=True)
    rn.add_argument("--job", action="append")
    rn.add_argument("--timeout", type=int, default=300)
    rn.add_argument("--json", action="store_true")
    rp = sub.add_parser("report")
    rp.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        js = jobs()
        if a.json:
            print(json.dumps(js))
        else:
            for j in js:
                need = "needs " + "/".join(j["needs"]) if j.get("needs") else ""
                print(f"{j['id']:14} {need:22} {j['prompt'][:70]}")
        print("NEXT: fieldkit accept run --model MODEL")
        return 0
    if a.cmd == "run":
        sel = [j for j in jobs() if not a.job or j["id"] in a.job]
        if not sel:
            print("no such job. NEXT: fieldkit accept list")
            return 2
        rows = [run_job(j, a.model, timeout=a.timeout) for j in sel]
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        with RESULTS.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        if a.json:
            print(json.dumps(rows))
        else:
            for r in rows:
                print(f"{r['result']:5} {r['job']:14} {r.get('reason', '')}")
        t = report(rows)[(a.model, os_name())]
        print(f"{t['pass']} pass, {t['miss']} miss, {t['skip']} skip, {t['error']} error ({a.model} on {os_name()})")
        print("NEXT: fieldkit accept report")
        return 3 if t["miss"] or t["error"] else 0
    t = report(load_rows())
    if a.json:
        print(json.dumps({f"{m}|{o}": v for (m, o), v in t.items()}))
    else:
        for (m, o), v in sorted(t.items()):
            print(f"{m:30} {o:8} pass {v['pass']} miss {v['miss']} skip {v['skip']} error {v['error']}")
    print("NEXT: fieldkit accept run --model MODEL   (a model or OS with no row has not been tried)")
    return 0
