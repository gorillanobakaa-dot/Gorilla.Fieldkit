"""session-cost: what an AI coding session costs in dollars, live and after the fact. Read-only.

usage:
    python session_cost.py                          the newest session (any source below)
    python session_cost.py SESSION [SESSION ...]    a transcript .jsonl, an Antigravity .db, or a session id
    python session_cost.py --list [N]               the N newest sessions per source, with dollars
    python session_cost.py --live [SESSION]         keep running; print the running total after every call
    python session_cost.py --doctor                 what this computer has for the meter
    python session_cost.py --since 2026-10-27 [--until 2026-10-28]
                                                    every session with calls in that window, only those calls
  options: --source all|claude-code|antigravity|codex|gorilla-opencode|generic   --warn-usd 5   --calls   --json
           (--json: JSON lines and nothing else on stdout; warnings and notes go to stderr)
           --assume NAME=PUBLIC (price an internal model name as a public one; shown in the report)

Sources (adapters), each read from files the product already writes; nothing is sent anywhere:
  claude-code   ~/.claude/projects/<project>/<session>.jsonl, plus <session>/subagents/*.jsonl.
                Claude Code CLI and the Claude desktop app's Code tab write the same files.
                Usage is per API call (message id; one reply is written as several lines).
  antigravity   ~/.gemini/antigravity/conversations/<cascade>.db (SQLite). Table gen_metadata holds
                one row per model call: protobuf exa.cortex_pb.CortexStepGeneratorMetadata, field 1
                chat_model -> field 4 usage (exa.codeium_common_pb.ModelUsageStats: 2 input, 3 output
                incl. thinking, 4 cache write, 5 cache read, 6 api_provider), field 19 response_model.
                Field names read from the descriptor inside Antigravity's language_server.exe.
                The call time comes from steps.metadata field 1 (created_at) via field 2 step_indices.
  codex         $CODEX_HOME/sessions/**/rollout-*-<thread>.jsonl[.zst] (OpenAI Codex CLI and desktop app);
                token_usage_record lines, one per API response. See class Codex.
  gorilla-opencode  ~/.local/share/gorilla-opencode/gorilla-opencode.db, table sessions: per-session totals
                (last call's prompt, cost from its own price table, calls). See class GorillaOpenCode.
  generic       ~/.fieldkit/usage/<agent>/<session>.jsonl, a documented contract any agent or wrapper can
                write (one line per call: ts, session, model, input_tokens, cached_tokens, output_tokens).
  ibm-bob       not measured: no verified session format yet (see class IBMBob; rotate doctor says WARN).

Prices: prices.json next to this file, one table per provider, each with its source page and date.
A model without a price is counted as unpriced and named in the report, never guessed.

Why long sessions cost more: every call sends the whole conversation again. Re-sent context is
billed as cache reads; the report shows how much of the bill that is, and the cost per call by
how big the conversation was at that call.
Written 2026-10-21 (owner: make the cost visible before it hurts). Standard library only, Python 3.8+.
"""
import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.3.0"
HERE = Path(__file__).resolve().parent
PRICES_FILE = HERE / "prices.json"
# Claude Code honors CLAUDE_CONFIG_DIR (default ~/.claude) on Windows, Linux and macOS alike
CLAUDE_PROJECTS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
ANTIGRAVITY_CONVERSATIONS = Path.home() / ".gemini" / "antigravity" / "conversations"
# exa.codeium_common_pb.APIProvider values seen in Antigravity's descriptor (2026-10-21)
AG_PROVIDERS = {24: "gemini-api", 26: "google-cloud-claude"}


# ---------------------------------------------------------------- prices

def load_prices(path=PRICES_FILE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_PRICES = {}


def prices():
    """prices.json, read once on first use (nothing runs on import)."""
    if not _PRICES:
        _PRICES.update(load_prices())
    return _PRICES


def canonical_model(name, prices):
    """claude-opus-4-6-thinking -> claude-opus-4-6; claude-sonnet-4-5-20250929 -> claude-sonnet-4-5."""
    m = (name or "").strip().lower()
    m = re.sub(r"\[[^\]]*\]$", "", m)          # claude-opus-5-5[1m]
    m = re.sub(r"-\d{8}$", "", m)               # dated snapshot ids
    changed = True
    while changed:
        changed = False
        for s in prices.get("suffixes_stripped", []):
            if m.endswith(s):
                m = m[: -len(s)]
                changed = True
    return m


def rate(prices, provider, model, day, prompt_tokens, speed="standard"):
    """Prices per million tokens for one call, or (None, reason)."""
    table = prices["providers"].get(provider)
    if table is None:
        return None, "no price table for provider " + provider
    entries = table["models"].get(model)
    if not entries:
        return None, "no published price for " + model
    if day is None and any(e.get("from") or e.get("to") for e in entries):
        return None, "call has no date and the price of %s depends on the date" % model
    chosen = None
    for e in entries:
        if day is not None and ((e.get("from") and day < e["from"]) or (e.get("to") and day > e["to"])):
            continue
        chosen = e
        break
    if chosen is None:
        return None, "no price for %s on %s" % (model, day)
    r = {k: chosen[k] for k in ("input", "output", "cache_read") if k in chosen}
    r["cache_write_5m"] = chosen.get("cache_write_5m", r["input"])
    r["cache_write_1h"] = chosen.get("cache_write_1h", r["input"])
    over = chosen.get("over")
    if over and prompt_tokens > over["prompt_tokens"]:
        r = {k: over.get(k, over["input"] if k.startswith("cache_write") else r[k]) for k in r}
    if speed == "fast":
        fast = chosen.get("fast")
        if not fast:
            return None, "no fast-mode price for " + model
        factor = fast["input"] / chosen["input"]
        r = {k: (fast[k] if k in fast else v * factor) for k, v in r.items()}
    return r, ""


def price_call(c, prices, assume=None):
    """Adds usd, usd_reread, priced, reason, priced_as to one call dict (in place) and returns it."""
    if c.get("aggregate"):        # a session's totals, already priced by the product itself (no per-call tokens)
        none = c["n"] and c["n_unpriced"] >= c["n"]
        c.update(priced_as=c["model"], context=c["input"], priced=not none,
                 reason="%s recorded no priced call" % c["source"] if none else
                 "priced by %s's own price table" % c["source"])
        return c
    raw = c["model"]
    model = canonical_model(raw, prices)
    model = (assume or {}).get(raw) or (assume or {}).get(model) or model
    c["priced_as"] = model
    c["context"] = c["input"] + c["cache_write_5m"] + c["cache_write_1h"] + c["cache_read"]
    day = c["ts"][:10] if c.get("ts") else None
    r, why = rate(prices, c["provider"], model, day, c["context"], c.get("speed") or "standard")
    if r is None:
        c.update(usd=0.0, usd_reread=0.0, priced=False, reason=why)
        return c
    mult = 1.0
    if c.get("geo") == "us":
        mult = prices["providers"][c["provider"]].get("us_geo_multiplier", 1.0)
    reread = c["cache_read"] * r["cache_read"]
    usd = (c["input"] * r["input"] + c["cache_write_5m"] * r["cache_write_5m"]
           + c["cache_write_1h"] * r["cache_write_1h"] + reread + c["output"] * r["output"])
    c.update(usd=usd * mult / 1e6, usd_reread=reread * mult / 1e6, priced=True, reason="")
    return c


# ---------------------------------------------------------------- adapters

def _call(source, session, ts, model, provider, inp=0, w5m=0, w1h=0, read=0, out=0, geo=None, speed=None):
    return {"source": source, "session": session, "ts": ts, "model": model or "?", "provider": provider,
            "input": inp, "cache_write_5m": w5m, "cache_write_1h": w1h, "cache_read": read, "output": out,
            "geo": geo, "speed": speed}


class ClaudeCode:
    name = "claude-code"

    def __init__(self, root=CLAUDE_PROJECTS):
        self.root = Path(root)

    def sessions(self):
        return sorted(self.root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) \
            if self.root.is_dir() else []

    def owns(self, path):
        return path.suffix == ".jsonl"

    def title(self, path):
        t = path.with_suffix("") / "custom-title.json"
        try:
            return json.loads(t.read_text(encoding="utf-8")).get("customTitle", "")
        except (OSError, ValueError, AttributeError):
            return ""

    def files(self, path):
        return [path] + sorted((path.with_suffix("") / "subagents").glob("*.jsonl"))

    def read(self, path, state):
        """New calls since the last read. state keeps a byte offset per file and the ids seen."""
        offsets = state.setdefault("offsets", {})
        seen = state.setdefault("seen", set())
        calls = []
        for f in self.files(path):
            key = str(f)
            try:
                with open(f, "rb") as fh:
                    fh.seek(offsets.get(key, 0))
                    chunk = fh.read()
            except OSError:
                continue
            end = chunk.rfind(b"\n") + 1     # a line still being written is read next time
            offsets[key] = offsets.get(key, 0) + end
            for line in chunk[:end].splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict) or d.get("type") != "assistant":
                    continue
                msg = d.get("message") or {}
                u, mid = msg.get("usage"), msg.get("id")
                if not u or mid in seen:
                    continue
                seen.add(mid)
                if msg.get("model") == "<synthetic>":
                    continue                      # written by Claude Code itself, not an API call
                w = u.get("cache_creation_input_tokens", 0) or 0
                w1h = (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0) or 0
                calls.append(_call(self.name, path.stem, d.get("timestamp"), msg.get("model"), "anthropic",
                                   u.get("input_tokens", 0) or 0, w - w1h, w1h,
                                   u.get("cache_read_input_tokens", 0) or 0, u.get("output_tokens", 0) or 0,
                                   u.get("inference_geo"), u.get("speed")))
                calls[-1]["sub"] = f != path          # a subagent's call: its bill, not the session's context
        return calls


def _varint(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return r, i


def _pb(b):
    """Protobuf wire format -> list of (field, value); value is int or bytes."""
    i, out = 0, []
    while i < len(b):
        k, i = _varint(b, i)
        f, w = k >> 3, k & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 2:
            n, i = _varint(b, i)
            v = b[i:i + n]
            i += n
        elif w == 1:
            v = b[i:i + 8]
            i += 8
        elif w == 5:
            v = b[i:i + 4]
            i += 4
        else:
            raise ValueError("wire type %d" % w)
        out.append((f, v))
    return out


def _get(msg, field):
    return [v for f, v in msg if f == field]


def _ints(values):
    out = []
    for v in values:
        if isinstance(v, int):
            out.append(v)
        else:                                      # packed repeated
            i = 0
            while i < len(v):
                x, i = _varint(v, i)
                out.append(x)
    return out


class Antigravity:
    name = "antigravity"

    def __init__(self, root=ANTIGRAVITY_CONVERSATIONS):
        self.root = Path(root)

    def sessions(self):
        return sorted(self.root.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True) \
            if self.root.is_dir() else []

    def owns(self, path):
        return path.suffix == ".db"

    def title(self, path):
        return ""

    def read(self, path, state):
        import sqlite3                                  # lazy: the PreToolUse hook imports this module (hook speed)
        last = state.get("last_idx", -1)
        con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True, timeout=5)
        try:
            rows = con.execute("select idx, data from gen_metadata where idx > ? order by idx", (last,)).fetchall()
            parsed, want = [], set()
            for idx, data in rows:
                state["last_idx"] = idx
                try:
                    root = _pb(data or b"")
                    cm = _get(root, 1)
                    if not cm:
                        continue
                    cm = _pb(cm[0])
                    u = _get(cm, 4)
                    if not u:
                        continue
                    u = dict(_pb(u[0]))
                    steps = _ints(_get(root, 2))
                except (ValueError, IndexError):
                    continue
                model = (_get(cm, 19) or [b""])[0].decode("utf-8", "replace") or "?"
                parsed.append((u, model, steps))
                want.update(steps)
            times = {}
            if want:
                q = "select idx, metadata from steps where idx in (%s)" % ",".join("?" * len(want))
                for sidx, md in con.execute(q, sorted(want)):
                    try:
                        t = _get(_pb(md or b""), 1)
                        if t:
                            t = dict(_pb(t[0]))
                            times[sidx] = t.get(1, 0) + t.get(2, 0) / 1e9
                    except (ValueError, IndexError):
                        pass
        finally:
            con.close()
        calls = []
        for u, model, steps in parsed:
            if not any(u.get(k) for k in (2, 3, 4, 5)):
                continue                          # a row with no tokens at all (seen: no model name either)
            ts =[times[s] for s in steps if s in times]
            iso = datetime.fromtimestamp(min(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None
            provider = AG_PROVIDERS.get(u.get(6)) or ("google-cloud-claude" if model.startswith("claude")
                                                     else "gemini-api")
            # Antigravity records one cache-write count and no 5-minute/1-hour split: priced as 5-minute
            calls.append(_call(self.name, path.stem, iso, model, provider, u.get(2, 0), u.get(4, 0), 0,
                               u.get(5, 0), u.get(3, 0)))
        return calls


def _zstd_reader():
    """A zstd decompress function (stdlib compression.zstd on 3.14+, else the zstandard package), or None."""
    try:
        from compression import zstd                       # Python 3.14+
        return zstd.decompress
    except ImportError:
        pass
    try:
        import zstandard                                    # stream_reader: frames without a content size too
        return lambda b: zstandard.ZstdDecompressor().stream_reader(io.BytesIO(b)).read()
    except ImportError:
        return None


def _new_lines(f, state, key):
    """New complete lines of a growing .jsonl (byte offset kept in state), or all lines of a finished .zst once."""
    offsets = state.setdefault("offsets", {})
    try:
        if f.name.endswith(".zst"):
            size = f.stat().st_size
            if offsets.get(key) == size:
                return []
            unz = _zstd_reader()
            if unz is None:
                raise OSError("a .zst session needs Python 3.14 or the zstandard package: " + f.name)
            data = unz(f.read_bytes())
            offsets[key] = size
            return data.splitlines()
        with open(f, "rb") as fh:
            fh.seek(offsets.get(key, 0))
            chunk = fh.read()
    except FileNotFoundError:
        return []
    end = chunk.rfind(b"\n") + 1                          # a line still being written is read next time
    offsets[key] = offsets.get(key, 0) + end
    return chunk[:end].splitlines()


CODEX_HOME = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
_ROLLOUT = re.compile(r"^rollout-[0-9T:-]+-([0-9a-f]{8}-[0-9a-f-]{27})\.jsonl(?:\.zst)?$")


class _CodexPath(type(Path())):
    """A rollout file whose stem is the thread id (rollout-2026-10-16T22-02-39-<uuid>.jsonl[.zst] -> <uuid>)."""

    @property
    def stem(self):
        m = _ROLLOUT.match(self.name)
        return m.group(1) if m else super().stem


class Codex:
    """OpenAI Codex (CLI and desktop app): $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl, one JSON
    line per item ({timestamp, type, payload}); the desktop app keeps finished ones zstd-compressed (.jsonl.zst).
    Format read from real files (cli 0.158 to 0.162, 2026-09-26 to 2026-10-16) and codex-rs's RolloutLine:
      session_meta.payload.model_provider   the price table ("openai"; a custom provider stays unpriced)
      turn_context.payload.model            the model of the calls that follow
      token_usage_record.payload.usage      one per API response (response_id): input_tokens INCLUDING
                                            cached_input_tokens and cache_write_input_tokens, output_tokens
                                            including reasoning_output_tokens. thread_id != session_id = a subagent.
      event_msg token_count .info           older writers: last_token_usage per response, used only when the file
                                            has no token_usage_record (deduplicated on total_token_usage).
    Titles: $CODEX_HOME/session_index.jsonl ({id, thread_name})."""
    name = "codex"

    def __init__(self, root=None):
        self.root = Path(root) if root else CODEX_HOME / "sessions"

    def sessions(self):
        if not self.root.is_dir():
            return []
        found = [_CodexPath(p) for p in self.root.rglob("rollout-*.jsonl*") if _ROLLOUT.match(p.name)]
        return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)

    def owns(self, path):
        return bool(_ROLLOUT.match(Path(path).name))

    def title(self, path):
        idx = self.root.parent / "session_index.jsonl"
        sid, name = _CodexPath(path).stem, ""
        try:
            with open(idx, "rb") as fh:
                for line in fh:
                    if sid.encode() in line:
                        name = json.loads(line).get("thread_name") or name
        except (OSError, ValueError):
            pass
        return name

    def read(self, path, state):
        seen = state.setdefault("seen", set())
        calls = []
        for line in _new_lines(Path(path), state, "rollout"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            kind, p = d.get("type"), d.get("payload") or {}
            if kind == "session_meta":
                state["provider"] = p.get("model_provider") or state.get("provider")
                state["cwd"] = p.get("cwd") or state.get("cwd")
            elif kind == "turn_context":
                state["model"] = p.get("model") or state.get("model")
                state["cwd"] = p.get("cwd") or state.get("cwd")
            elif kind == "token_usage_record":
                state["records"] = True
                rid = p.get("response_id") or "%s#%s" % (d.get("timestamp"), d.get("ordinal"))
                if rid in seen or not p.get("usage"):
                    continue
                seen.add(rid)
                calls.append(self._call(path, state, d.get("timestamp"), p["usage"]))
                calls[-1]["sub"] = bool(p.get("thread_id") and p.get("session_id")
                                        and p["thread_id"] != p["session_id"])
            elif kind == "event_msg" and p.get("type") == "token_count" and not state.get("records"):
                info = p.get("info") or {}
                total = json.dumps(info.get("total_token_usage"), sort_keys=True)
                if not info.get("last_token_usage") or total == state.get("last_total"):
                    continue
                state["last_total"] = total
                calls.append(self._call(path, state, d.get("timestamp"), info["last_token_usage"]))
                calls[-1]["sub"] = False
        return calls

    def _call(self, path, state, ts, u):
        total = u.get("input_tokens", 0) or 0
        read = u.get("cached_input_tokens", 0) or 0
        write = u.get("cache_write_input_tokens", 0) or 0
        return _call(self.name, _CodexPath(path).stem, ts, state.get("model"), state.get("provider") or "?",
                     max(0, total - read - write), write, 0, read, u.get("output_tokens", 0) or 0)

    def facts(self, path, state):
        return {"cwd": state.get("cwd")}


def _opencode_data():
    """Gorilla OpenCode's data folder (internal/config/store.go DataBase): $XDG_DATA_HOME/gorilla-opencode, else
    ~/.local/share/gorilla-opencode, on Windows too. A data.directory set in its settings is not followed here."""
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "gorilla-opencode"


class _Row:
    """One session that is a row in a database: what rotation and the report need from a session path."""
    suffix = ""

    def __init__(self, store, sid, mtime, title=""):
        self.store, self.stem, self.name, self.mtime, self.row_title = Path(store), sid, sid, mtime, title

    def stat(self):
        return os.stat_result((0, 0, 0, 0, 0, 0, 0, int(self.mtime), int(self.mtime), int(self.mtime)))

    def is_file(self):
        return self.store.is_file()

    def __str__(self):
        return "%s#%s" % (self.store, self.stem)

    def __eq__(self, other):
        return isinstance(other, _Row) and str(other) == str(self)

    def __hash__(self):
        return hash(str(self))


class GorillaOpenCode:
    """Gorilla OpenCode: <data>/gorilla-opencode.db (SQLite), table sessions. It keeps no per-call tokens; it keeps
    per session (internal/llm/agent/agent.go TrackUsage, migrations 20260819/20261021):
      prompt_tokens        the LAST call's prompt (input + cache writes + cache reads): the context per call
      cost                 dollars, summed per call from Gorilla OpenCode's own price table
      calls_priced / calls_unpriced   the calls (counted since the cost-ledger migration, 2026-10-19)
      parent_session_id    set on a sub-agent's session: its calls and cost count in the parent's bill
    So the meter takes the session's totals as they are, priced by Gorilla OpenCode (said in the report)."""
    name = "gorilla-opencode"

    def __init__(self, root=None):
        self.root = Path(root) if root else _opencode_data()
        self.db = self.root / "gorilla-opencode.db"

    def _con(self):
        import sqlite3
        return sqlite3.connect("file:%s?mode=ro" % self.db.as_posix(), uri=True, timeout=5)

    def sessions(self):
        import sqlite3
        if not self.db.is_file():
            return []
        try:
            con = self._con()
            try:
                rows = con.execute("select id, updated_at, title from sessions where parent_session_id is null "
                                   "order by updated_at desc").fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return []
        return [_Row(self.db, sid, upd or 0, title or "") for sid, upd, title in rows]

    def owns(self, path):
        return isinstance(path, _Row) or Path(path).name == "gorilla-opencode.db"

    def title(self, path):
        return getattr(path, "row_title", "")

    def read(self, path, state):
        con = self._con()
        try:
            r = con.execute("select prompt_tokens, cost, calls_priced, calls_unpriced, updated_at, started_in, "
                            "cost_reread, last_call_cost from sessions where id = ?", (path.stem,)).fetchone()
            kids = con.execute("select coalesce(sum(cost), 0), coalesce(sum(calls_priced + calls_unpriced), 0), "
                               "coalesce(sum(calls_unpriced), 0), coalesce(sum(cost_reread), 0) from sessions "
                               "where parent_session_id = ?", (path.stem,)).fetchone()
            model = con.execute("select model from messages where session_id = ? and role = 'assistant' and model "
                                "is not null order by created_at desc limit 1", (path.stem,)).fetchone()
        finally:
            con.close()
        if r is None:
            return []
        ctx, cost, priced, unpriced, upd, cwd, reread, last = r
        state["cwd"] = cwd
        key = [upd, kids[0], kids[1]]
        if state.get("seen_update") == key:
            return []
        state["seen_update"] = key
        ts = datetime.fromtimestamp(upd, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if upd else None
        c = _call(self.name, path.stem, ts, model[0] if model else None, "gorilla-opencode", ctx or 0)
        c.update(aggregate=True, n=(priced or 0) + (unpriced or 0) + kids[1], n_unpriced=(unpriced or 0) + kids[2],
                 usd=(cost or 0.0) + kids[0], usd_reread=(reread or 0.0) + kids[3], usd_last=last or 0.0, sub=False)
        return [c]

    def facts(self, path, state):
        return {"cwd": state.get("cwd")}


USAGE_DIR = Path(os.environ.get("FIELDKIT_USAGE_DIR") or Path.home() / ".fieldkit" / "usage")
CONTRACT_KEYS = ("ts", "session", "model", "input_tokens", "cached_tokens", "output_tokens")


class Generic:
    """Any agent, through a documented contract: <usage dir>/<agent>/<session>.jsonl, one line per API call
        {"ts": "2026-10-26T10:00:00Z", "session": "<id>", "model": "<model id>",
         "input_tokens": N, "cached_tokens": N, "output_tokens": N}
    input_tokens = EVERY prompt token sent with the call, cached ones included (the context of the call);
    cached_tokens = the part of it served from the provider's cache. Optional keys: "provider" (a prices.json
    table, e.g. "openai", "anthropic", "gemini-api"; without it the call is counted, not priced),
    "cache_write_tokens" (part of input_tokens written to the cache), "cwd", "sub" (true: a subagent's call,
    counted in the bill, not as the session's context). <usage dir> = $FIELDKIT_USAGE_DIR, else ~/.fieldkit/usage.
    A line missing a required key, with a negative or non-integer count, or naming another session is skipped and
    counted (state["bad"]); `rotate doctor` shows the count."""
    name = "generic"

    def __init__(self, root=None):
        self.root = Path(root) if root else USAGE_DIR

    def sessions(self):
        return sorted(self.root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) \
            if self.root.is_dir() else []

    def owns(self, path):
        try:
            Path(path).resolve().relative_to(self.root.resolve())
            return Path(path).suffix == ".jsonl"
        except ValueError:
            return False

    def title(self, path):
        return ""

    def read(self, path, state):
        path = Path(path)
        calls = []
        for line in _new_lines(path, state, "usage"):
            try:
                d = json.loads(line)
                ok = isinstance(d, dict) and all(k in d for k in CONTRACT_KEYS) and d["session"] == path.stem
                n = [d[k] for k in ("input_tokens", "cached_tokens", "output_tokens")]
                n.append(d.get("cache_write_tokens", 0))
                ok = ok and all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in n) \
                    and n[1] + n[3] <= n[0]
            except (ValueError, TypeError, KeyError):
                ok = False
            if not ok:
                state["bad"] = state.get("bad", 0) + 1
                continue
            inp, read, out, write = n
            if d.get("cwd"):
                state["cwd"] = d["cwd"]
            c = _call(self.name, path.stem, d["ts"], d["model"], d.get("provider") or "?", inp - read - write,
                      write, 0, read, out)
            c["sub"] = bool(d.get("sub"))
            calls.append(c)
        return calls

    def facts(self, path, state):
        return {"cwd": state.get("cwd")}


BOB_DB = Path.home() / ".bob" / "db" / "bob.db"


class IBMBob:
    """IBM Bob (IDE and `bob` CLI): NOT MEASURED. No install on this machine to read a real session store from, and
    IBM's own docs do not describe one. Third-party readers (memex PR #196, granite-cli issue #151, both 2026) say it
    is ~/.bob/db/bob.db (SQLite, WAL) with a task table holding a costs JSON (input, output, cacheRead, cacheWrite,
    cost) and no model name; that is a lead, not a verified format, so nothing here parses it. To measure Bob: record
    a scrubbed copy of a real bob.db (schema + one task's rows) as a test fixture and write read() from it. Until
    then: sessions() is empty, `rotate doctor` says WARN, and a wrapper can write the generic contract instead."""
    name = "ibm-bob"
    verified = False

    def __init__(self, root=None):
        self.root = Path(root) if root else BOB_DB

    def sessions(self):
        return []

    def owns(self, path):
        return False

    def title(self, path):
        return ""

    def read(self, path, state):
        return []


ADAPTERS = {"claude-code": ClaudeCode, "antigravity": Antigravity, "codex": Codex,
            "gorilla-opencode": GorillaOpenCode, "generic": Generic, "ibm-bob": IBMBob}


def adapters_for(source):
    return [ADAPTERS[s]() for s in (ADAPTERS if source == "all" else [source])]


def resolve(arg, adapters):
    """A path, or a session id (or its first characters) under any adapter's folder."""
    p = Path(arg)
    if p.is_file():
        for a in adapters_for("all"):
            if a.owns(p):
                return a, p
    for a in adapters:
        for s in a.sessions():
            if s.stem.startswith(arg) or str(s) == arg:     # an id, or a database row's "<db>#<id>"
                return a, s
    return None, p


def newest(adapters):
    best = None
    for a in adapters:
        s = a.sessions()
        if s and (best is None or s[0].stat().st_mtime > best[1].stat().st_mtime):
            best = (a, s[0])
    return best


# ---------------------------------------------------------------- report

BANDS = (100_000, 200_000, 300_000, 400_000, 500_000)


def summarize(calls, adapter=None, path=None):
    priced = [c for c in calls if c["priced"]]
    total = sum(c["usd"] for c in priced)
    unpriced = {}
    for c in calls:
        if not c["priced"] or c.get("n_unpriced"):
            why = c["reason"] if not c["priced"] else "no price in %s's own table" % c["source"]
            unpriced.setdefault(c["model"], {"calls": 0, "reason": why})["calls"] += c.get("n_unpriced", 1)
    n_calls = sum(c.get("n", 1) for c in calls)
    n_priced = sum(c.get("n", 1) - c.get("n_unpriced", 0) for c in priced)
    bands = []
    lo = 0
    for hi in BANDS + (None,):
        inb = [c for c in priced if c["context"] >= lo and (hi is None or c["context"] < hi)]
        if inb:
            bands.append({"from": lo, "to": hi, "calls": len(inb),
                          "usd_per_call": sum(c["usd"] for c in inb) / len(inb)})
        lo = hi
    ts = [c["ts"] for c in calls if c.get("ts")]
    span = 0.0
    if len(ts) > 1:
        span = (_parse_ts(max(ts)) - _parse_ts(min(ts))).total_seconds() / 3600
    providers = sorted({c["provider"] for c in calls})
    models = {}
    for c in calls:
        models[c["model"]] = models.get(c["model"], 0) + 1
    return {
        "source": adapter.name if adapter else (calls[0]["source"] if calls else "?"),
        "session": path.stem if path else (calls[0]["session"] if calls else "?"),
        "title": adapter.title(path) if adapter and path else "",
        "first": min(ts) if ts else None, "last": max(ts) if ts else None, "hours": round(span, 2),
        "calls": n_calls, "priced_calls": n_priced,
        "usd": round(total, 4),
        "usd_reread": round(sum(c["usd_reread"] for c in priced), 4),
        "reread_share": round(sum(c["usd_reread"] for c in priced) / total, 4) if total else 0.0,
        "usd_per_call": round(total / n_priced, 4) if n_priced else 0.0,
        "last_call_usd": round(priced[-1].get("usd_last", priced[-1]["usd"]), 4) if priced else 0.0,
        "context_last": (priced or calls)[-1]["context"] if calls else 0,     # the call "last call $" is
        "context_avg": int(sum(c["context"] for c in calls) / len(calls)) if calls else 0,
        "tokens": {k: sum(c[k] for c in calls) for k in
                   ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")},
        "by_context": bands,
        "models": models,
        "unpriced": unpriced,
        "prices": {p: {"source": prices()["providers"][p]["source"], "fetched": prices()["providers"][p]["fetched"]}
                   for p in providers if p in prices()["providers"]},
    }


def _parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _k(n):
    return "%dk" % round(n / 1000) if n >= 1000 else str(n)


def report(s, calls=None, show_calls=False, warn_usd=None, out=sys.stdout):
    w = out.write
    head = "%s %s" % (s["source"], s["session"][:8])
    if s["title"]:
        head += "  \"%s\"" % s["title"]
    when = ""
    if s["first"]:
        end = s["last"][11:16] if s["last"][:10] == s["first"][:10] else s["last"][:16].replace("T", " ")
        when = "  %s -> %s UTC (%.1f h)" % (s["first"][:16].replace("T", " "), end, s["hours"])
    w("%s%s\n" % (head, when))
    n = "%d calls" % s["calls"] if s["priced_calls"] == s["calls"] else \
        "%d calls (%d priced)" % (s["calls"], s["priced_calls"])
    w("  %s, cost $%.2f, on average $%.3f per priced call; the last call cost $%.3f and sent %s tokens\n"
      % (n, s["usd"], s["usd_per_call"], s["last_call_usd"], _k(s["context_last"])))
    if s["usd"]:
        w("  re-reading old context (cache reads): $%.2f = %d%% of the cost\n"
          % (s["usd_reread"], round(100 * s["reread_share"])))
    if len(s["by_context"]) > 1 or (s["by_context"] and show_calls):
        w("  cost per call by how much conversation was sent with it:\n")
        for b in s["by_context"]:
            rng = "%s-%s" % (_k(b["from"]), _k(b["to"])) if b["to"] else "%s+" % _k(b["from"])
            w("    %-10s %5d calls  $%.3f per call\n" % (rng, b["calls"], b["usd_per_call"]))
    for m, u in sorted(s["unpriced"].items()):
        w("  NOT PRICED: %d call(s) on %s (%s); not in the total. Price it as a public model with "
          "--assume %s=<public-name> if you know which one it is.\n" % (u["calls"], m, u["reason"], m))
    if s["source"] == "antigravity" and any(c.startswith("claude") for c in s["models"]):
        w("  note: Antigravity does not record cache writes; for Claude the total is a lower bound.\n")
    for p, v in s["prices"].items():
        w("  prices: %s as of %s, %s\n" % (p, v["fetched"], v["source"]))
    if show_calls and calls:
        w("  %5s %-19s %-26s %8s %7s %9s %10s %6s\n"
          % ("#", "time (UTC)", "model", "context", "output", "call $", "running $", "reread"))
        run = 0.0
        for i, c in enumerate(calls, 1):
            run += c["usd"]
            share = "%d%%" % round(100 * c["usd_reread"] / c["usd"]) if c["usd"] else "-"
            w("  %5d %-19s %-26s %8s %7s %9s %10.2f %6s\n"
              % (i, (c["ts"] or "")[:19].replace("T", " "), c["model"][:26], _k(c["context"]), _k(c["output"]),
                 ("%.4f" % c["usd"]) if c["priced"] else "unpriced", run, share))
    if warn_usd is not None and s["usd"] >= warn_usd:
        warning(s, warn_usd, out)


def warning(s, threshold, out=sys.stdout):
    bar = "!" * 72
    out.write("\a%s\n!!! COST WARNING: this session has passed $%.2f; it has cost $%.2f so far.\n"
              "!!! Every call now sends %s tokens again and costs about $%.3f.\n"
              "!!! Finish this job, write a short handover, and start a fresh session:\n"
              "!!! a fresh session sends far less with every call.\n%s\n"
              % (bar, threshold, s["usd"], _k(s["context_last"]), s["last_call_usd"], bar))


def when(s):
    """--since / --until: a date (local midnight) or an ISO time (local when it names no zone) -> aware datetime."""
    t = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.astimezone()


def in_window(c, since=None, until=None):
    """A call inside [since, until). A call with no time is outside any window: it is never guessed in.
    (Gorilla OpenCode keeps one row per session, stamped with the session's last update.)"""
    if since is None and until is None:
        return True
    if not c.get("ts"):
        return False
    t = _parse_ts(c["ts"])
    return (since is None or t >= since) and (until is None or t < until)


def measure(adapter, path, assume=None, since=None, until=None):
    calls = [price_call(c, prices(), assume) for c in adapter.read(path, {}) if in_window(c, since, until)]
    return calls, summarize(calls, adapter, path)


def touched_since(adapters, since):
    """-> [(adapter, session)] for every session written at or after since (a file older than that holds no call
    in the window)."""
    out = []
    for ad in adapters:
        for s in ad.sessions():
            try:
                if s.stat().st_mtime >= since.timestamp():
                    out.append((ad, s))
            except OSError:
                continue
    return out


def live(adapters, pinned, interval, warn_usd, assume, polls, out=sys.stdout):
    """Follow a session and print the running total whenever a call lands."""
    adapter = path = None
    state, calls, warned, n = {}, [], 0, 0
    while True:
        target = pinned or newest(adapters)
        if target and target[1] != path:
            adapter, path = target
            state, calls, warned = {}, [], 0
            out.write("following %s session %s (%s)\n" % (adapter.name, path.stem, path))
        if adapter is not None:
            new = [price_call(c, prices(), assume) for c in adapter.read(path, state)]
            if new:
                calls += new
                s = summarize(calls, adapter, path)
                last = new[-1]
                out.write("%s  %s %s  %d calls  $%.2f  (last call $%.3f, sent %s, %d%% of the cost is re-reading)\n"
                          % (datetime.now().strftime("%H:%M:%S"), adapter.name, path.stem[:8], s["calls"],
                             s["usd"], last["usd"], _k(last["context"]), round(100 * s["reread_share"])))
                if any(not c["priced"] for c in new):
                    out.write("  (%d new call(s) not priced: %s)\n" % (
                        sum(1 for c in new if not c["priced"]),
                        ", ".join(sorted({c["model"] for c in new if not c["priced"]}))))
                if warn_usd and s["usd"] >= warn_usd * (warned + 1):
                    warned = int(s["usd"] // warn_usd)
                    warning(s, warn_usd * warned, out)
                out.flush()
        n += 1
        if polls and n >= polls:
            return 0
        time.sleep(interval)


def doctor():
    ok = sys.version_info >= (3, 8)
    print("session-cost %s doctor" % VERSION)
    print("  [%s] Python %s (needs 3.8+)" % ("ok" if ok else "--", sys.version.split()[0]))
    try:
        p = load_prices()
        for k, v in p["providers"].items():
            print("  [ok] prices %-20s %2d models, fetched %s, %s" % (k, len(v["models"]), v["fetched"], v["source"]))
    except (OSError, ValueError, KeyError) as e:
        ok = False
        print("  [--] prices.json: %s" % e)
    for a in adapters_for("all"):
        s = a.sessions()
        print("  [%s] %-12s %d session(s) under %s" % ("ok" if s else "..", a.name, len(s), a.root))
    print("  standard library only; reads files; writes nothing; no network")
    return 0 if ok else 1





def main(argv=None):
    p = argparse.ArgumentParser(prog="session_cost", description="What an AI coding session costs, in dollars.")
    p.add_argument("sessions", nargs="*", help="transcript .jsonl, Antigravity .db, or session id")
    p.add_argument("--source", choices=["all"] + list(ADAPTERS), default="all")
    p.add_argument("--list", type=int, nargs="?", const=5, metavar="N", help="the N newest sessions per source")
    p.add_argument("--live", action="store_true", help="keep running and print the total after every call")
    p.add_argument("--interval", type=float, default=5.0, help="with --live: seconds between looks (default 5)")
    p.add_argument("--polls", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--warn-usd", type=float, help="print a loud warning when a session passes this many dollars")
    p.add_argument("--calls", action="store_true", help="one line per call: context, cost, running total")
    p.add_argument("--assume", action="append", default=[], metavar="NAME=PUBLIC",
                   help="price an internal model name as a public one (stated in the report)")
    p.add_argument("--since", help="every session with calls at or after this date or ISO time; only those calls")
    p.add_argument("--until", help="only calls before this date or ISO time")
    p.add_argument("--json", action="store_true", help="one JSON object per session, nothing else on stdout")
    p.add_argument("--doctor", action="store_true", help="check this computer and stop")
    a = p.parse_args(argv)
    if a.doctor:
        return doctor()
    if a.live and a.json:
        p.error("--live prints text as calls land; --json is for a finished measurement")
    try:
        since = when(a.since) if a.since else None
        until = when(a.until) if a.until else None
    except ValueError as e:
        p.error("--since/--until: %s (use YYYY-MM-DD or an ISO time)" % e)
    assume = {}
    for x in a.assume:
        if "=" not in x:
            p.error("--assume takes NAME=PUBLIC")
        k, v = x.split("=", 1)
        assume[k.strip()] = v.strip()
    adapters = adapters_for(a.source)
    if a.live:
        pinned = None
        if a.sessions:
            pinned = resolve(a.sessions[0], adapters)
            if pinned[0] is None:
                print("session_cost: not found: " + a.sessions[0], file=sys.stderr)
                return 2
        try:
            return live(adapters, pinned, a.interval, a.warn_usd, assume, a.polls)
        except KeyboardInterrupt:
            return 0
    targets = []
    if a.list:
        for ad in adapters:
            targets += [(ad, s) for s in ad.sessions()[: a.list]]
    for x in a.sessions:
        ad, path = resolve(x, adapters)
        if ad is None:
            print("session_cost: not found: " + x, file=sys.stderr)
            return 2
        targets.append((ad, path))
    if since and not targets:
        targets = touched_since(adapters, since)
        if not targets:
            return 0                  # nothing written in the window: an empty answer, not an error
    if not targets:
        n = newest(adapters)
        if n is None:
            print("session_cost: no sessions found (run --doctor)", file=sys.stderr)
            return 2
        targets = [n]
    over = False
    for ad, path in targets:
        calls, s = measure(ad, path, assume, since, until)
        if (since or until) and not calls:
            continue
        if since or until:
            s["since"], s["until"] = (since.isoformat() if since else None), (until.isoformat() if until else None)
        if assume:
            s["assumed"] = assume
        over = over or (a.warn_usd is not None and s["usd"] >= a.warn_usd)
        if a.json:
            if a.calls:
                s["call_list"] = calls
            print(json.dumps(s))
        else:
            report(s, calls, a.calls, a.warn_usd)
            for k, v in assume.items():
                print("  assumed: %s priced as %s (your --assume, not a published price for that name)" % (k, v))
            print()
    return 3 if over else 0


if __name__ == "__main__":
    sys.exit(main())
