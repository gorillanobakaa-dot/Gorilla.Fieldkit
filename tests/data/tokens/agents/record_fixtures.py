"""Record scrubbed test fixtures from the real local session stores (Codex rollout, Gorilla OpenCode db).

python record_fixtures.py CODEX_ROLLOUT.zst OPENCODE_DB OUT_DIR
Keeps only the lines/columns the adapters read; ids, paths, titles and account fields are replaced.
"""
import json
import sqlite3
import sys
from pathlib import Path

import zstandard

rollout, db, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
CWD = "C:\\work\\repo"
lines = zstandard.ZstdDecompressor().stream_reader(rollout.open("rb")).read().decode("utf-8").splitlines()
keep = []
real_id = None
for l in lines:
    d = json.loads(l)
    t, p = d.get("type"), d.get("payload") or {}
    if t == "session_meta":
        real_id = p["id"]
        d["payload"] = {"id": "SESSIONID", "session_id": "SESSIONID", "timestamp": p["timestamp"], "cwd": CWD,
                        "originator": p.get("originator"), "cli_version": p.get("cli_version"),
                        "source": p.get("source"), "model_provider": p.get("model_provider")}
    elif t == "turn_context":
        d["payload"] = {"turn_id": "TURN", "cwd": CWD, "model": p["model"], "effort": p.get("effort")}
    elif t == "token_usage_record":
        p.update(thread_id="SESSIONID", session_id="SESSIONID", turn_id="TURN", root_turn_id="TURN",
                 response_id="resp_fixture_%d" % len(keep))
    elif t == "event_msg" and p.get("type") == "token_count":
        p.pop("rate_limits", None)
    elif t == "event_msg" and p.get("type") in ("task_started", "task_complete"):
        d["payload"] = {"type": p["type"]}
    else:
        continue
    keep.append(json.dumps(d, separators=(",", ":")))
text = "\n".join(keep) + "\n"
assert real_id and real_id not in text
sid = "01a0dd9e-0000-7000-8000-000000000001"
text = text.replace("SESSIONID", sid)
name = f"rollout-2026-09-26T13-08-41-{sid}.jsonl"
(out / "codex").mkdir(exist_ok=True)
(out / "codex" / name).write_text(text, encoding="utf-8", newline="\n")
(out / "codex" / (name + ".zst")).write_bytes(zstandard.ZstdCompressor().compress(text.encode("utf-8")))

con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
schema = [r[0] for r in con.execute("select sql from sqlite_master where type='table' and name in "
                                    "('sessions','messages') order by name")]
cols = [r[1] for r in con.execute("pragma table_info(sessions)")]
pick = con.execute("select * from sessions where calls_priced + calls_unpriced > 0 and cost > 0 "
                   "order by updated_at desc limit 1").fetchone()
zero = con.execute("select * from sessions where calls_priced + calls_unpriced > 0 and cost = 0 "
                   "order by updated_at desc limit 1").fetchone()
rows = []
for i, r in enumerate((pick, zero)):
    d = dict(zip(cols, r))
    d.update(id=f"sess-{i + 1}", title=f"fixture session {i + 1}", started_in=CWD, summary_message_id=None)
    rows.append(d)
msgs = []
for i, r in enumerate((pick, zero)):
    m = con.execute("select role, model, created_at from messages where session_id=? and role='assistant' "
                    "order by created_at desc limit 1", (r[0],)).fetchone()
    msgs.append({"id": f"msg-{i + 1}", "session_id": f"sess-{i + 1}", "role": m[0], "parts": "[]", "model": m[1],
                 "created_at": m[2], "updated_at": m[2], "finished_at": None})
(out / "gorilla-opencode.json").write_text(json.dumps({"schema": schema, "sessions": rows, "messages": msgs},
                                                      indent=1) + "\n", encoding="utf-8", newline="\n")
print("ok", len(keep), "codex lines;", [(r["id"], r["prompt_tokens"], r["cost"], r["calls_priced"], r["calls_unpriced"],
                                         r["last_call_cost"]) for r in rows], [m["model"] for m in msgs])
