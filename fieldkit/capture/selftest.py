"""`fieldkit capture self-test --first-use|--context|--failed|--before-processing|--crash`

Every test runs in a sandbox store (FIELDKIT_CAPTURE_DIR) through the REAL entry points: `python -m fieldkit capture
hook` with an agent's JSON on stdin, `capture find`, `capture ledger`, `capture verify`. It prints PASS/FAIL lines.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import store as st

FK = Path(__file__).resolve().parents[2]
SECRETS = ["ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4", "hunter2hunter2", "s3cr3tEnvValue99"]


class Box:
    """A sandbox store plus the helpers every test uses."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="fkcap-"))
        self.cap = self.dir / "store"
        self.lines, self.failed = [], False

    def env(self, **extra):
        e = dict(os.environ, FIELDKIT_CAPTURE_DIR=str(self.cap), PYTHONPATH=str(FK) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        e.update(extra)
        return e

    def fk(self, *args, stdin=None, **env):
        r = subprocess.run([sys.executable, "-m", "fieldkit", *args], input=stdin, capture_output=True, text=True,
                           timeout=300, cwd=FK, env=self.env(**env))
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def hook(self, agent, payload, **env):
        return self.fk("capture", "hook", "--agent", agent, stdin=json.dumps(payload), **env)

    def find(self, *words):
        rc, out = self.fk("capture", "find", *words, "--json")
        return json.loads(out) if rc == 0 else []

    def ledger(self):
        return json.loads(self.fk("capture", "ledger", "--json")[1])

    def check(self, ok, what, detail=""):
        self.lines.append(("PASS " if ok else "FAIL ") + what + ("" if ok else f" :: {detail}"))
        self.failed |= not ok
        return ok

    def done(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        for l in self.lines:
            print(l)
        return 3 if self.failed else 0


def bash(sid, cmd, code=0, out="", desc="", transcript=None, cwd=None, event="PostToolUse"):
    p = {"session_id": sid, "hook_event_name": event, "tool_name": "Bash", "cwd": cwd or str(FK),
         "tool_input": {"command": cmd, "description": desc},
         "tool_response": {"stdout": out, "stderr": "boom" if code else "", "exit_code": code}}
    if transcript:
        p["transcript_path"] = str(transcript)
    return p


def write(sid, path, content):
    return {"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(FK),
            "tool_input": {"file_path": path, "content": content}, "tool_response": {"filePath": path}}


def first_use():
    b, m = Box(), "fkfirst" + uuid.uuid4().hex[:10]
    rc, _ = b.hook("claude", write("s-" + m, f"/tmp/{m}.py", f"# one-off {m}\nimport json\nprint('{m}')\n"))
    b.check(rc == 0, "hook exits 0 for a Write of a script used once")
    rc, _ = b.hook("claude", bash("s-" + m, f"python -c \"print('{m}')\"", out=m))
    b.check(rc == 0, "hook exits 0 for a Bash command used once")
    recs = b.find(m)
    kinds = sorted(r["kind"] for r in recs)
    b.check(kinds == ["command", "script"], "both single-use items are in the store at once, found by one word", str(kinds))
    b.check(all(r["count"] == 1 and r["state"] == "raw" for r in recs), "each is kept once, state raw (queued, not dropped)")
    journal = (b.cap / "journal.jsonl").read_text(encoding="utf-8")
    b.check(f"import json" in journal and m in journal, "the exact source is on disk in the journal")
    b.check(b.hook("claude", {"tool_name": "Read", "tool_input": {"file_path": "x"}})[0] == 0, "a non-work tool (Read) is ignored, exit 0")
    rc, _ = b.fk("capture", "hook", "--agent", "claude", stdin="{{ not json")
    b.check(rc == 0 and any(r["kind"] == "unparsed" for r in b.find("not json")), "a garbage payload is kept as an unparsed item, exit 0")
    return b.done()


def context():
    b, m = Box(), "fkctx" + uuid.uuid4().hex[:10]
    tr = b.dir / "t.jsonl"
    tr.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": f"fix the install of {m}"}}) + "\n"
                  + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "ok"}}) + "\n", encoding="utf-8")
    cmd = f"python tool.py --password {SECRETS[1]} --out result.txt && echo {m} {SECRETS[0]}"
    b.hook("claude", bash("sess-" + m, cmd, code=0, out=f"done {m}", desc="run it", transcript=tr), FK_TEST_TOKEN=SECRETS[2], FK_PLAIN_VAR="visible")
    recs = b.find(m)
    r = recs[0] if recs else {}
    need = ("kind", "source", "command", "args", "env", "cwd", "deps", "inputs", "outputs", "exit_code", "errors",
            "timestamp", "agent", "session", "problem")
    miss = [k for k in need if k not in r]
    b.check(bool(recs) and not miss, "the record has every context field", f"missing {miss}")
    b.check(r.get("agent") == "claude" and r.get("session") == "sess-" + m and r.get("exit_code") == 0, "agent, session, exit code")
    b.check(m in (r.get("problem") or ""), "problem = the last user prompt from the transcript", str(r.get("problem")))
    b.check(r.get("deps") == ["python", "echo"], "deps = the programs the command uses", str(r.get("deps")))
    b.check((r.get("env") or {}).get("FK_PLAIN_VAR") == "visible", "env is recorded")
    b.check((r.get("env") or {}).get("FK_TEST_TOKEN") == st.REDACTED, "a secret-named env var is redacted")
    raw = (b.cap / "journal.jsonl").read_text(encoding="utf-8")
    leaks = [s for s in SECRETS if s in raw]
    b.check(not leaks, "no secret value reaches the journal (env, arguments, token shapes)", f"{len(leaks)} leaked")
    b.check(r.get("cwd") == str(FK) and r.get("timestamp"), "cwd and timestamp")
    big = "X" * 40000
    b.hook("claude", bash("s2" + m, "echo big " + m, out=big))
    big_rec = [x for x in b.find("big", m) if x["kind"] == "command"]
    b.check(bool(big_rec) and isinstance(big_rec[0]["outputs"], dict), "a large output is kept (spilled to a blob, not cut)")
    rc, out = b.fk("capture", "verify")
    b.check(rc == 0, "verify passes with blobs", out.strip()[-160:])
    return b.done()


def failed():
    b, m = Box(), "fkfail" + uuid.uuid4().hex[:10]
    s = "sess-" + m
    b.hook("claude", bash(s, f"pytest tests/{m}.py", code=3, out="1 failed", event="PostToolUseFailure"))
    b.hook("claude", bash("other-" + m, f"pytest tests/{m}.py", code=0, out="1 passed"))
    b.hook("claude", bash(s, f"pytest tests/{m}.py -x", code=0, out="1 passed"))
    b.hook("claude", bash(s, f"pytest tests/{m}.py -x", code=0, out="1 passed"))
    recs = b.find(m)
    bad = [r for r in recs if r["exit_code"] == 3]
    good = [r for r in recs if r["exit_code"] == 0 and r["session"] == s]
    b.check(len(bad) == 1, "the failed attempt (exit 3) is kept")
    b.check(len(good) == 1 and good[0]["count"] == 2, "its correction is kept once and the repeat is counted")
    b.check(bool(bad and good) and bad[0]["corrected_by"] == good[0]["id"] and good[0]["id"] and bad[0]["id"] in good[0]["fixes"],
            "the failure is linked to the later success in the same session")
    foreign = [r for r in recs if r["session"] == "other-" + m]
    b.check(len(foreign) == 1 and not foreign[0]["fixes"], "a success in another session does not claim the failure")
    b.check(bool(bad) and "boom" in (bad[0]["errors"] or ""), "the error text of the failure is kept")
    rc, out = b.fk("capture", "ledger", "--strict")
    b.check(rc == 0, "strict ledger: nothing lost or unaccounted", out.strip()[-160:])
    return b.done()


def before_processing():
    b, m = Box(), "fkbefore" + uuid.uuid4().hex[:10]
    b.hook("claude", write("s" + m, f"/tmp/{m}.sh", f"#!/bin/sh\necho {m}\n"))
    iid = b.find(m)[0]["id"]
    s = st.Store(b.cap)
    seen = {}

    def steps(i):
        seen["on_disk"] = m in (b.cap / "journal.jsonl").read_text(encoding="utf-8")
        seen["state"] = s.state()["state"][i]["state"]
        raise RuntimeError("card, test, integration and PR steps all fail")

    s.run_processing(iid, steps)
    b.check(seen.get("on_disk") is True and seen.get("state") == "processing", "the raw item was on disk before the first processing step ran")
    r = b.find(m)[0]
    b.check(r["state"] == "raw" and "all fail" in (r["error"] or ""), "every step failed: the item is still there, back in the queue with the error")
    led = b.ledger()
    b.check(iid in led["outstanding"], "it is listed as outstanding (visible, not dropped)")
    rc, _ = b.fk("capture", "ledger", "--strict")
    b.check(rc == 0, "an outstanding raw item is not a loss: strict still exits 0")
    s.run_processing(iid, lambda i: ("procedure", "kept as a recorded procedure: nothing to generalise"))
    r = b.find(m)[0]
    b.check(r["state"] == "procedure" and r["evidence"], "a later worker finishes it with evidence")
    s.set_state(iid, "tool")                                  # a done state without evidence is unaccounted
    rc, out = b.fk("capture", "ledger", "--strict")
    b.check(rc == 3 and iid in out, "a done state with no evidence makes strict exit 3", out.strip()[-120:])
    return b.done()


CHILD = r"""
import json, os, sys, time
from fieldkit.capture import store as st
s = st.Store(os.environ["FIELDKIT_CAPTURE_DIR"])
n = int(sys.argv[1])
for i in range(n):
    iid, _ = s.record({"kind": "command", "source": f"echo {i}", "command": f"echo {i}", "cwd": ".", "exit_code": 0,
                       "agent": "crash", "session": "c", "path": None})
    print("ACK", iid, flush=True)
s.begin_event({"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "c",
               "tool_input": {"command": "echo orphan-event"}, "tool_response": {"exit_code": 0}}, "claude")
print("ORPHAN", flush=True)
with open(s.journal, "ab") as f:                       # a write cut off half way through its line
    f.write(b'{"seq":999,"ts":"2026-01-01T00:00:00","prev":"abc","type":"item","data":{"id":"cap-half","kind":"comm')
    f.flush()
    os.fsync(f.fileno())
print("MIDWRITE", flush=True)
time.sleep(120)
"""


def crash():
    b = Box()
    b.cap.mkdir(parents=True)
    script = b.dir / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(script), "5"], stdout=subprocess.PIPE, text=True, env=b.env(), cwd=FK)
    acked = []
    for line in p.stdout:
        line = line.strip()
        if line.startswith("ACK"):
            acked.append(line.split()[1])
        if line == "MIDWRITE":
            break
    p.kill()
    p.wait()
    b.check(len(acked) == 5, "the child acknowledged 5 records, then died mid-write (killed)", str(len(acked)))
    rc, out = b.fk("capture", "ledger", "--json")                # restart: first command after the kill
    led = json.loads(out) if rc == 0 else {}
    have = {i["id"] for i in led.get("items", [])}
    b.check(set(acked) <= have, "after restart every acknowledged record is in the store")
    b.check(any("orphan-event" in (r.get("command") or "") for r in b.find("orphan-event")),
            "an event written before the crash but not yet parsed became its record on restart")
    b.check(b.fk("capture", "hook", "--agent", "claude", stdin=json.dumps(bash("after", "echo after-crash")))[0] == 0
            and bool(b.find("after-crash")), "capture carries on appending after the crash")
    torn = b.cap / "journal.torn"
    b.check(torn.is_file() and b"cap-half" in torn.read_bytes(), "the half-written line was kept in journal.torn, not discarded")
    rc, out = b.fk("capture", "verify")
    b.check(rc == 0, "verify: hash chain intact after the crash", out.strip()[-200:])
    rc, out = b.fk("capture", "ledger", "--strict")
    b.check(rc == 0, "strict ledger: nothing lost or unaccounted", out.strip()[-200:])
    led = b.ledger()
    b.check(led["hook_events_total"] <= len(led["items"]) + led["deduplicated"], "hook events <= items + deduplicated")
    return b.done()


TESTS = {"first_use": first_use, "context": context, "failed": failed, "before_processing": before_processing, "crash": crash}
