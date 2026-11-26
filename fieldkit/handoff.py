#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
handoff.py - cross-agent session handoff with enforced provider/model/thinking policy.

Finds a session (by UUID, title text, or ChatGPT share link) from Claude Code,
Codex, Kiro, Antigravity/agy, LM Studio, writes a "handoff card" (JSON + Markdown),
then launches ANOTHER agent (or a local LM Studio model) with a short bootstrap
prompt that points at the card.

Uses the Fieldkit privacy scanner and model guard on Linux, macOS, and Windows.

Fieldkit conventions: --json on every run; exit 0 ok, 1 error, 2 bad usage,
3 privacy findings / not scanned. Launching a remote provider is irreversible
(data leaves the machine), so it needs --approve or a typed "yes" at a keyboard;
otherwise the run is a PREVIEW. Fieldkit's own `privacy scan` gates the card.

ECONOMY MODE (default ON): zero swarms, zero subagents, no orchestrator; the session
runs on the cheap main model (Sonnet 5.5 tier) and exploring/writing is steered to the
light model (Haiku 5.5 tier). Per-provider models live in ECONOMY_DEFAULTS and can be
changed in ~/.agent-handoff/config.json ("economy_models"). Provider and thinking effort
are asked for (or pass --provider / --thinking).

    fieldkit handoff <uuid|"title text"|https://chatgpt.com/s/...> [-p project]
    fieldkit handoff --list [-p project]
    fieldkit handoff --use-card CARD.md --provider codex ...   # relaunch an edited card
    python -m fieldkit.handoff ...
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import difflib
import functools
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HOME = Path.home()
HANDOFF_DIR = HOME / ".agent-handoff"
CARD_DIR = HANDOFF_DIR / "cards"
CONFIG_PATH = HANDOFF_DIR / "config.json"
LEVELS = ["low", "medium", "high"]
LMSTUDIO_URL = "http://localhost:1234/v1"
JSON_MODE = False


def say(*a: Any) -> None:
    """Human text: stdout normally, stderr in --json mode so stdout stays pure JSON."""
    print(*a, file=sys.stderr if JSON_MODE else sys.stdout)

# ---------------------------------------------------------------------------
# 1. PROVIDER REGISTRY  (display name -> real CLI model id)
# ---------------------------------------------------------------------------
# Display names are what you see in menus. CLIs want *ids*. Override any id or
# add models in ~/.agent-handoff/config.json:
#   {"model_ids": {"claude": {"Opus 5.5": "opus"}},
#    "extra_models": {"codex": ["gpt-whatever"]},
#    "binaries": {"antigravity": "agy"}}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-")


def _claude_id(m: str) -> str:
    return "claude-" + _slug(m).replace(".", "-")  # "Sonnet 5.5" -> claude-sonnet-5-5


@dataclasses.dataclass
class Provider:
    binary: str
    models: List[str]
    id_fn: Callable[[str], str]
    economy: Dict[str, str] = dataclasses.field(default_factory=dict)


# main = session model, light = exploring / writing / helper model.
# Claude/opencode tiers are known; antigravity/kiro/codex entries are best guesses:
# fix them in config.json ("economy_models": {"codex": {"main": "...", "light": "..."}}).
ECONOMY_DEFAULTS: Dict[str, Dict[str, str]] = {
    "claude": {"main": "Sonnet 5.5", "light": "Haiku 5.5"},
    "opencode": {"main": "Sonnet 5.5", "light": "Haiku 5.5"},
    "antigravity": {"main": "Claude Sonnet 5.5", "light": "Gemini 3.8 Flash"},
    "kiro": {"main": "Claude Sonnet 5.5", "light": "Auto"},
    "codex": {"main": "GPT-6.1 Terra", "light": "GPT-6 Luna"},
    "lmstudio": {"main": "auto-detect-local-server", "light": "auto-detect-local-server"},
}


def economy_rules(c: "HandoffCard") -> str:
    light = c.light_model or c.target_model
    return (f"ECONOMY MODE. (1) ZERO swarms, ZERO subagents, no orchestrator or parallel agents: do all work "
            f"in this one session. (2) Session model: {c.target_model}. (3) Exploring (searching/reading) and "
            f"writing/drafting belong on the light model ({light}); use it for those wherever you can switch "
            f"models, otherwise keep reads minimal. (4) Be terse, make small diffs, never re-read files you "
            f"do not need.")


def enforcement(c: "HandoffCard") -> Dict[str, List[str]]:
    """What the launcher really enforces vs what is only instructed in the prompt."""
    p, eco = c.target_provider, c.economy_mode
    enf, adv = [f"model = {c.target_model}"], []
    if p == "claude":
        enf.append(f"thinking budget MAX_THINKING_TOKENS={CLAUDE_THINK_TOKENS[c.thinking_effort]}")
        if eco:
            enf += ["subagent tools disallowed (--disallowedTools Task Agent)",
                    f"helper/background model env -> {c.light_model}"]
            adv.append("explore/write on the light model: the session itself runs on the main model")
    elif p == "codex":
        enf.append(f"reasoning effort = {c.thinking_effort}")
    elif p == "lmstudio":
        enf.append("single request, so no subagents are possible")
        if eco:
            enf.append("max_tokens=1500, temperature=0.2")
    else:
        adv.append(f"thinking effort = {c.thinking_effort} (stated in the prompt only)")
    if eco and p not in ("claude", "lmstudio"):
        adv += ["no swarms/subagents (stated in the prompt only)", "explore/write on the light model (prompt only)"]
    return {"enforced": enf, "advisory": adv}


def build_registry(cfg: Dict[str, Any]) -> Dict[str, Provider]:
    reg = {
        "antigravity": Provider("agy", [
            "Gemini 4 Neon", "Gemini 4 Carbon", "Gemini 4 Argon", "Gemini 4 Hydrogen",
            "Orchestrator (Gemini 4 Neon High)", "Gemini 3.8 Flash",
            "Claude Opus 5.5", "Claude Sonnet 5.5", "GPT-OSS 120B"], lambda m: m),
        "claude": Provider("claude", [
            "Opus 5.5", "Sonnet 5.5", "Fable 5.2", "Haiku 5.5", "Fable 5.1", "Haiku 4.5",
            "Sonnet 5", "Opus 5", "Fable 5", "Opus 4.8", "Opus 4.7", "Opus 4.6",
            "Sonnet 4.6"], _claude_id),
        "opencode": Provider("opencode", [
            "Opus 5.5", "Sonnet 5.5", "Fable 5.2", "Haiku 5.5", "Fable 5.1",
            "Haiku 4.5"], lambda m: "anthropic/" + _claude_id(m)),
        "codex": Provider("codex", [
            "GPT-6.2 Sol", "GPT-6.2 Astra", "GPT-6.1 Terra", "GPT-6.1 Sol", "GPT-6 Astra",
            "GPT-6 Sol", "GPT-6 Luna", "GPT-5.6 Sol", "GPT-5.6 Terra"], _slug),
        "kiro": Provider("kiro-cli", ["Auto", "Claude Sonnet 5.5", "Claude Opus 5.5"], _slug),
        "lmstudio": Provider("", ["auto-detect-local-server"], lambda m: m),
    }
    for name, e in ECONOMY_DEFAULTS.items():
        reg[name].economy = dict(e)
    for p, e in cfg.get("economy_models", {}).items():
        if p in reg and isinstance(e, dict):
            reg[p].economy.update(e)
    for p, extra in cfg.get("extra_models", {}).items():
        if p in reg:
            reg[p].models.extend(x for x in extra if x not in reg[p].models)
    for p, b in cfg.get("binaries", {}).items():
        if p in reg:
            reg[p].binary = b
    for p, mapping in cfg.get("model_ids", {}).items():
        if p in reg:
            old = reg[p].id_fn
            reg[p].id_fn = lambda m, old=old, mapping=mapping: mapping.get(m, old(m))
    return reg


def load_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# 2. HANDOFF CARD
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class HandoffCard:
    card_id: str
    created_at: str
    source_agent: str
    source_path: str
    session_id: str
    conversation_title: str
    project_name: str
    cwd: str
    original_goal: str
    last_messages: List[Dict[str, str]]
    recent_tool_calls: List[str]
    artifacts: Dict[str, str]
    target_provider: str = ""
    target_model: str = ""
    thinking_effort: str = "medium"
    economy_mode: bool = True
    card_path: str = ""
    privacy_scan: str = ""
    approved: bool = False
    light_model: str = ""
    last_activity: str = ""

    def to_markdown(self, compact: bool = False) -> str:
        n_msgs, n_chars = (4, 500) if compact else (8, 1500)
        mode = economy_rules(self) if self.economy_mode else "STANDARD"
        out = [
            "# SESSION HANDOFF CARD", "",
            f"- Card: {self.card_id}", f"- Last saved activity (UTC): {self.last_activity or 'unknown'}",
            f"- From: {self.source_agent} (session {self.session_id})",
            f"- Project: {self.project_name} | Working dir: {tilde(self.cwd) or 'unknown'}",
            f"- Policy: {self.target_provider} / {self.target_model} / "
            f"thinking={self.thinking_effort} / {mode}", "",
        ]
        if self.original_goal:
            out += ["## Original goal", self.original_goal[:n_chars], ""]
        out.append("## Recent tool calls")
        out += [f"- {t}" for t in self.recent_tool_calls[-10:]] or ["- none recorded"]
        out += ["", "## Last conversation turns"]
        for m in self.last_messages[-n_msgs:]:
            out.append(f"**[{m['role'].upper()}]** {m['content'][:n_chars]}\n")
        if self.artifacts:
            out.append("## Working artifacts")
            for name, text in self.artifacts.items():
                out.append(f"### {name}\n{text[:2500 if not compact else 800]}\n")
        out.append("## Instruction\nResume from the last state above. Verify the repo state "
                   "(git status / diff) before editing, then continue the unfinished work.")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# 3. HOOK ENGINE
# ---------------------------------------------------------------------------
STAGES = ["post_extract", "policy_enforce", "validate", "post_card_write", "privacy_gate", "pre_dispatch"]


class HookRegistry:
    """Hooks run in registration order per stage; each takes and returns the card."""

    def __init__(self) -> None:
        self._h: Dict[str, List[Callable[[HandoffCard], HandoffCard]]] = {s: [] for s in STAGES}

    def register(self, stage: str, fn: Callable[[HandoffCard], HandoffCard]) -> None:
        self._h[stage].append(fn)

    def run(self, stage: str, card: HandoffCard) -> HandoffCard:
        for fn in self._h[stage]:
            card = fn(card)
        return card


# ---------------------------------------------------------------------------
# 4. DISCOVERY
# ---------------------------------------------------------------------------
SKIP_DIRS = {"node_modules", "Cache", "CachedData", "Code Cache", "GPUCache", "logs",
             "blob_storage", "brain", ".git"}
SESSION_SUFFIXES = {".jsonl", ".json", ".chat", ".db", ".vscdb", ".pb"}


def storage_roots() -> List[Tuple[str, Path]]:
    roots = [
        ("antigravity", HOME / ".gemini" / "antigravity-cli"),
        ("antigravity", HOME / ".gemini" / "antigravity-ide"),
        ("antigravity", HOME / ".gemini" / "antigravity"),
        ("claude", HOME / ".claude" / "projects"),
        ("kiro", HOME / ".kiro" / "sessions"),
        ("codex", HOME / ".codex" / "sessions"),
        ("lmstudio", HOME / ".lmstudio" / "conversations"),
        ("lmstudio", HOME / ".cache" / "lm-studio" / "conversations"),
    ]
    # Only session stores, never the provider's settings or credential databases.
    roots = [(a, r / "conversations" if a == "antigravity" else r) for a, r in roots]
    return [(a, r) for a, r in roots if r.exists()]


def agent_transcript(brain: Path) -> Optional[Path]:
    for path in (brain / ".system_generated" / "logs" / "transcript.jsonl", brain / "transcript.jsonl"):
        if path.is_file():
            return path
    return None


def session_id(path: Path) -> str:
    for part in (path.stem, *reversed(path.parts[:-1])):
        match = re.search(UUID_RE.pattern.strip("^$"), part, re.I)
        if match:
            return match.group()
    return path.stem


def iter_session_files(project: Optional[str]) -> List[Tuple[str, Path, float]]:
    found = []
    for agent, root in storage_roots():
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for name in fn:
                p = Path(dp) / name
                if p.suffix not in SESSION_SUFFIXES:
                    continue
                if agent == "antigravity":
                    if not UUID_RE.fullmatch(p.stem):
                        continue
                    transcript = agent_transcript(root.parent / "brain" / p.stem)
                    if transcript:
                        p = transcript
                if agent == "claude" and (p.suffix != ".jsonl" or not UUID_RE.fullmatch(p.stem)):
                    continue
                if project and project.lower() not in str(p).lower() and project.lower() not in read_haystack(p):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                found.append((agent, p, st.st_mtime))
    found.sort(key=lambda t: (-t[2], str(t[1])))
    return found


def read_haystack(p: Path) -> str:
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            head = f.read(1_000_000)
            tail = b""
            if size > 1_500_000:
                f.seek(-500_000, os.SEEK_END)
                tail = f.read()
        return (head + b"\n" + tail).decode("utf-8", errors="ignore").lower()
    except OSError:
        return ""


STOP = {"the", "and", "that", "has", "can", "what", "exactly", "does", "with", "for", "you",
        "are", "this", "how", "its", "was", "have", "from"}


def score_title(query: str, hay: str) -> float:
    toks = [t for t in re.findall(r"[a-z0-9]{3,}", query.lower()) if t not in STOP]
    if not toks:
        return 0.0
    if query.lower() in hay:
        return 1.0
    words: Optional[List[str]] = None
    hits = 0.0
    for t in toks:
        if t in hay:
            hits += 1
            continue
        if words is None:  # lazy: only needed for typos like "fielkit" -> "fieldkit"
            words = sorted(set(re.findall(r"[a-z0-9]{3,}", hay[:400_000])))[:20_000]
        if difflib.get_close_matches(t, words, n=1, cutoff=0.84):
            hits += 0.8
    return hits / len(toks)


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def codex_titles() -> dict[str, str]:
    titles = {}
    index = HOME / ".codex" / "session_index.jsonl"
    if index.is_file():
        with index.open(encoding="utf-8", errors="replace") as lines:
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and isinstance(record.get("thread_name"), str):
                    titles[record.get("id")] = record["thread_name"]
    return titles


def find_sessions(query: str, project: Optional[str], limit: int = 5):
    files = iter_session_files(project)
    q = query.lower()
    by_name = [(a, p, m, 1.0) for a, p, m in files if q == session_id(p).lower() or q in p.name.lower()]
    if by_name:
        return by_name[:limit]
    scored = []
    if UUID_RE.fullmatch(query):
        return []
    titles = codex_titles()
    for agent, p, mtime in files:
        title = titles.get(session_id(p), "") if agent == "codex" else ""
        s = score_title(query, title.lower()) if title else 0
        if s < 1:
            s = max(s, score_title(query, read_haystack(p)))
        if s >= 0.7:
            scored.append((agent, p, mtime, s))
    scored.sort(key=lambda t: (t[3], t[2]), reverse=True)
    return scored[:limit]


def find_brain_dir(session_id: str) -> Optional[Path]:
    for _, root in storage_roots():
        for base in (root, root.parent):
            d = base / "brain" / session_id
            if d.is_dir():
                return d
    return None


# ---------------------------------------------------------------------------
# 5. PARSING
# ---------------------------------------------------------------------------
def _short(v: Any, n: int = 140) -> str:
    return (v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False))[:n]


def _content_text(content: Any, tools: List[str]) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    parts: List[str] = []
    if isinstance(content, list):
        for it in content:
            if isinstance(it, str):
                parts.append(it)
            elif isinstance(it, dict):
                t = it.get("type", "")
                if t in ("tool_use", "function_call", "tool_call"):
                    tools.append(f"{it.get('name')}({_short(it.get('input') or it.get('arguments') or '', 120)})")
                elif t in ("thinking", "reasoning", "tool_result", "function_call_output"):
                    continue
                elif isinstance(it.get("text"), str):
                    parts.append(it["text"])
    return "\n".join(parts)


def extract_record(obj: Dict[str, Any]) -> Tuple[str, str, List[str], str]:
    """Normalise one record from Claude Code / Codex / generic formats."""
    tools: List[str] = []
    inner = obj
    msg = obj.get("message")
    if isinstance(msg, dict):
        inner = msg
    elif isinstance(obj.get("payload"), dict):
        inner = obj["payload"]
    role = str(inner.get("role") or obj.get("role") or obj.get("type") or "event")
    if inner.get("type") in ("function_call", "tool_call"):
        tools.append(f"{inner.get('name')}({_short(inner.get('arguments') or '', 120)})")
    content = inner.get("content", inner.get("text", obj.get("content", msg if isinstance(msg, str) else "")))
    text = _content_text(content, tools).strip()
    if role == "USER_INPUT":
        role = "user"
        text = text.partition("</USER_REQUEST>")[0].removeprefix("<USER_REQUEST>").strip()
    elif role == "PLANNER_RESPONSE":
        role = "assistant"
    for tc in inner.get("tool_calls") or []:
        tools.append(_short(tc, 150))
    cwd = obj.get("cwd") or inner.get("cwd") or ""
    return role, text, tools, cwd if isinstance(cwd, str) else ""


class Collector:
    def __init__(self) -> None:
        self.msgs: collections.deque = collections.deque(maxlen=40)
        self.tools: collections.deque = collections.deque(maxlen=30)
        self.first_user = ""
        self.cwd = ""
        self.any_role: collections.deque = collections.deque(maxlen=40)

    def add(self, obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        role, text, tools, cwd = extract_record(obj)
        self.tools.extend(tools)
        self.cwd = cwd or self.cwd
        if not text or text.startswith(("<system-reminder", "<environment_context")):
            return
        m = {"role": role, "content": text}
        self.any_role.append(m)
        if role.lower() in ("user", "assistant", "human", "ai"):
            self.msgs.append(m)
            if not self.first_user and role.lower() in ("user", "human"):
                self.first_user = text

    def result(self) -> Dict[str, Any]:
        msgs = list(self.msgs) or list(self.any_role)
        return {"messages": msgs, "tools": list(self.tools), "goal": self.first_user, "cwd": self.cwd}


def _find_msg_list(obj: Any, depth: int = 0) -> Optional[List[Any]]:
    if depth > 4:
        return None
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj[:5]):
        return obj
    if isinstance(obj, dict):
        for k in ("messages", "history", "chat", "conversation", "turns", "items"):
            if k in obj:
                r = _find_msg_list(obj[k], depth + 1)
                if r:
                    return r
        for v in obj.values():
            if isinstance(v, (dict, list)):
                r = _find_msg_list(v, depth + 1)
                if r:
                    return r
    return None


def parse_file(p: Path) -> Dict[str, Any]:
    c = Collector()
    try:
        if p.suffix == ".jsonl":
            with p.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            c.add(json.loads(line))
                        except ValueError:
                            continue
        elif p.suffix in (".json", ".chat"):
            data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
            for rec in _find_msg_list(data) or []:
                c.add(rec)
            if not c.any_role:
                c.add({"role": "json_dump", "content": json.dumps(data, ensure_ascii=False)[:2000]})
        elif p.suffix in (".db", ".vscdb"):
            _parse_sqlite(p, c)
        elif p.suffix == ".pb":  # protobuf: best-effort printable strings only
            raw = p.read_bytes()[-400_000:].decode("latin-1")
            strings = re.findall(r"[\x20-\x7e]{60,}", raw)[-12:]
            for s in strings:
                c.add({"role": "pb_text", "content": s})
    except (OSError, ValueError) as exc:
        c.add({"role": "system", "content": f"parse warning: {exc}"})
    return c.result()


def _parse_sqlite(p: Path, c: Collector) -> None:
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        c.add({"role": "system", "content": f"sqlite open failed: {exc}"})
        return
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            qt = '"' + t.replace('"', '""') + '"'
            try:
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({qt})")]
                tcols = [x for x in cols if any(k in x.lower() for k in
                         ("content", "text", "message", "body", "prompt", "payload", "value"))]
                if not tcols:
                    continue
                where = ""
                if t == "ItemTable" and "key" in cols:
                    where = " WHERE key LIKE '%chat%' OR key LIKE '%agent%' OR key LIKE '%conversation%' OR key LIKE '%session%'"
                sel = ", ".join('"' + x + '"' for x in tcols)
                rows = conn.execute(f"SELECT {sel} FROM {qt}{where} LIMIT 200").fetchall()[-15:]
            except sqlite3.Error:
                continue  # one bad table must not kill the rest
            for row in rows:
                for v in row:
                    if isinstance(v, bytes):
                        v = v.decode("utf-8", errors="ignore")
                    if not isinstance(v, str) or not v.strip():
                        continue
                    try:
                        recs = _find_msg_list(json.loads(v))
                    except ValueError:
                        recs = None
                    if recs:
                        for r in recs[-20:]:
                            c.add(r)
                    else:
                        c.add({"role": t, "content": v[-800:]})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. CARD BUILDERS
# ---------------------------------------------------------------------------
def _new_card_id() -> str:
    return f"card-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def build_card_from_file(agent: str, path: Path, query: str, project: Optional[str]) -> HandoffCard:
    parsed = parse_file(path)
    sid = session_id(path)
    artifacts: Dict[str, str] = {}
    brain = find_brain_dir(sid) or find_brain_dir(path.parent.name)
    if brain:
        for name in ("task.md", "implementation_plan.md", "walkthrough.md"):
            f = brain / name
            if f.exists():
                artifacts[name] = f.read_text(encoding="utf-8", errors="ignore")
    return HandoffCard(
        card_id=_new_card_id(), created_at=_now(), source_agent=agent, source_path=str(path),
        session_id=sid, conversation_title=query,
        project_name=project or (Path(parsed["cwd"]).name if parsed["cwd"] else path.parent.name),
        cwd=parsed["cwd"], original_goal=parsed["goal"],
        last_messages=parsed["messages"][-12:], recent_tool_calls=parsed["tools"][-15:],
        artifacts=artifacts, last_activity=dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat())


def build_card_from_brain_only(session_id: str, brain: Path, project: Optional[str]) -> HandoffCard:
    arts = {n: (brain / n).read_text(encoding="utf-8", errors="ignore")
            for n in ("task.md", "implementation_plan.md", "walkthrough.md") if (brain / n).exists()}
    return HandoffCard(_new_card_id(), _now(), "antigravity", str(brain), session_id, session_id,
                       project or "unknown", "", "", [], [], arts)


def build_card_from_text(text: str, source: str, title: str, project: Optional[str]) -> HandoffCard:
    return HandoffCard(_new_card_id(), _now(), source, title, title[-24:], title,
                       project or "external", "", "", [{"role": "transcript", "content": text[-6000:]}], [], {})


def fetch_share_link(url: str, project: Optional[str]) -> HandoffCard:
    address = urllib.parse.urlsplit(url)
    if address.scheme != "https" or address.netloc != "chatgpt.com" or not address.path.startswith(("/share/", "/s/")):
        raise Blocked("Expected an HTTPS ChatGPT share link.", 2, "BAD_URL")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    text = ""
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")
        html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] could not fetch share link: {exc}", file=sys.stderr)
    if len(text) < 300:
        print("[warn] Share pages are rendered by JavaScript, so the transcript usually can't be "
              "scraped.\n       Copy the conversation into a text file and use: handoff --file chat.txt",
              file=sys.stderr)
        text = f"(Transcript not retrievable automatically.) Shared conversation URL: {url}"
    return build_card_from_text(text[:6000], "chatgpt-web", url, project)


# ---------------------------------------------------------------------------
# 7. HOOKS
# ---------------------------------------------------------------------------
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9_\-]{20,}", r"ghp_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}",
    r"AKIA[0-9A-Z]{16}", r"AIza[0-9A-Za-z_\-]{30,}", r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}",
    r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^\s'\"]{8,}",
]


def _home_variants() -> List[str]:
    h = str(HOME)
    return sorted({x for x in (h, HOME.as_posix(), h.replace("\\", "\\\\")) if x}, key=len, reverse=True)


def tilde(s: str) -> str:
    """Home-folder paths become ~ (Fieldkit's privacy scan flags home paths)."""
    for h in _home_variants():
        s = s.replace(h, "~")
    return s


def find_local_json() -> Optional[Path]:
    cands = [Path.cwd(), *Path.cwd().parents]
    spec = importlib.util.find_spec("fieldkit")
    if spec and spec.origin:
        cands.append(Path(spec.origin).resolve().parent.parent)
    for d in cands:
        f = d / "fieldkit.local.json"
        if f.is_file():
            return f
    return None


_UNLOADED = object()
_TERMS_RE: Any = _UNLOADED


def _private_terms_re() -> Optional["re.Pattern[str]"]:
    """Your private words: privacy.terms from fieldkit.local.json (never written in code)."""
    global _TERMS_RE
    if _TERMS_RE is _UNLOADED:
        _TERMS_RE = None
        f = find_local_json()
        if f:
            try:
                terms = json.loads(f.read_text(encoding="utf-8")).get("privacy", {}).get("terms", [])
                terms = sorted({t for t in terms if isinstance(t, str) and len(t) >= 3}, key=len, reverse=True)
                if terms:
                    _TERMS_RE = re.compile("|".join(re.escape(t) for t in terms), re.I)
            except (OSError, ValueError, AttributeError):
                pass
    return _TERMS_RE


@functools.lru_cache(maxsize=1)
def configured_private_terms():
    from .core.privacy import private_terms
    return tuple(private_terms())


def redact(s: str) -> str:
    from .core import privacy
    s = tilde(s)
    for kind, pattern in privacy.PERSONAL + privacy.SECRETS:
        s = re.sub(pattern, "~" if "path" in kind else "[REDACTED]", s, flags=re.I)
    for pat in SECRET_PATTERNS:
        s = re.sub(pat, "[REDACTED]", s)
    terms = _private_terms_re()
    if terms:
        s = terms.sub("[PRIVATE]", s)
    for term in configured_private_terms():
        s = re.sub(privacy.term_pattern(term).pattern, "[PRIVATE]", s, flags=re.I)
    return s


def redact_hook(card: HandoffCard) -> HandoffCard:
    card.last_messages = [{"role": m["role"], "content": redact(m["content"])} for m in card.last_messages]
    card.recent_tool_calls = [redact(t) for t in card.recent_tool_calls]
    card.artifacts = {k: redact(v) for k, v in card.artifacts.items()}
    card.project_name = redact(card.project_name)
    card.session_id = redact(card.session_id)
    card.original_goal = redact(card.original_goal)
    return card


def prompt_choice(title: str, options: List[str], default_idx: int = 0) -> str:
    if JSON_MODE or not sys.stdin.isatty():
        raise SystemExit(f"error: '{title}' needs a value but stdin is not interactive. "
                         f"Pass it as a flag (--provider/--model/--thinking/--economy).")
    print(f"\n[POLICY] {title}")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}{' (default)' if i - 1 == default_idx else ''}")
    while True:
        raw = input(f"Select [1-{len(options)}] (Enter={default_idx + 1}): ").strip()
        if not raw:
            return options[default_idx]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("Invalid selection.")


def lmstudio_models() -> List[str]:
    try:
        with urllib.request.urlopen(f"{LMSTUDIO_URL}/models", timeout=2) as r:
            ids = [m["id"] for m in json.loads(r.read().decode())["data"] if "id" in m]
            if ids:
                return ids
    except Exception:  # noqa: BLE001
        pass
    return ["auto-detect-local-server"]


def make_policy_hook(args: argparse.Namespace, reg: Dict[str, Provider]):
    def hook(card: HandoffCard) -> HandoffCard:
        tty = sys.stdin.isatty() and not JSON_MODE
        card.target_provider = args.provider or prompt_choice("Target provider / agent:", list(reg))
        if card.target_provider not in reg:
            raise SystemExit(f"error: unknown provider '{card.target_provider}'. Options: {', '.join(reg)}")
        eco = reg[card.target_provider].economy
        # Economy is the default. Interactive: asked (Enter = yes). Non-interactive: ON unless --no-economy.
        if args.economy is None:
            if tty:
                label = (f"Yes - ECONOMY: no swarms, no subagents; {eco.get('main')} for the session, "
                         f"{eco.get('light')} for explore/write")
                card.economy_mode = prompt_choice("Economy mode?", [label, "No - standard"], 0).startswith("Yes")
            else:
                card.economy_mode = True
        else:
            card.economy_mode = args.economy
        card.thinking_effort = args.thinking or (prompt_choice("Thinking effort:", LEVELS, 1) if tty else "medium")
        card.light_model = args.light_model or eco.get("light", "")
        if args.model:
            card.target_model = args.model
        elif card.economy_mode:
            card.target_model = eco.get("main", "")
            say(f"[economy] session model={card.target_model} | explore/write model={card.light_model} "
                f"(override: --model / --light-model)")
        elif tty:
            models = lmstudio_models() if card.target_provider == "lmstudio" else reg[card.target_provider].models
            card.target_model = prompt_choice(f"Model for {card.target_provider}:", models)
        else:
            raise SystemExit("error: --no-economy needs --model when not interactive")
        if not card.target_model:
            raise SystemExit(f"error: no model for {card.target_provider}; pass --model")
        return card
    return hook


def make_validate_hook(reg: Dict[str, Provider]):
    def hook(card: HandoffCard) -> HandoffCard:
        if card.target_provider not in reg:
            raise SystemExit(f"validate: bad provider {card.target_provider!r}")
        if card.thinking_effort not in LEVELS:
            raise SystemExit(f"validate: thinking must be one of {LEVELS}")
        if card.economy_mode:
            if "orchestrator" in (card.target_model + " " + card.light_model).lower():
                raise SystemExit("validate: economy mode forbids orchestrator/swarm models. Pick another model "
                                 "or pass --no-economy.")
            main = reg[card.target_provider].economy.get("main")
            if main and card.target_provider != "lmstudio" and card.target_model != main:
                say(f"[warn] economy default for {card.target_provider} is {main}; you chose {card.target_model}")
        known = reg[card.target_provider].models
        if card.target_provider != "lmstudio" and card.target_model not in known:
            say(f"[warn] model {card.target_model!r} not in registry; passing through as-is.")
        return card
    return hook


def write_card_hook(card: HandoffCard) -> HandoffCard:
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    jp, mp = CARD_DIR / f"{card.card_id}.json", CARD_DIR / f"{card.card_id}.md"
    card.card_path = str(mp)
    jp.write_text(json.dumps({key: redact(value) if isinstance(value, str) else value for key, value in dataclasses.asdict(card).items()}, indent=2, ensure_ascii=False), encoding="utf-8")
    mp.write_text(card.to_markdown(), encoding="utf-8")
    say(f"\n[CARD] {mp}\n       {jp}")
    return card


# ---------------------------------------------------------------------------
# 8. PRIVACY GATE + APPROVAL  (fail closed; Fieldkit's own scanner decides)
# ---------------------------------------------------------------------------
class Blocked(Exception):
    def __init__(self, msg: str, code: int = 3, status: str = "BLOCKED") -> None:
        super().__init__(msg)
        self.code, self.status = code, status


def fieldkit_scan(path: Path) -> Tuple[str, str]:
    """Run `fieldkit privacy scan PATH --json`. Returns ('clean'|'findings'|'not-scanned', detail)."""
    from .core.privacy import scan_path
    examined = []
    try:
        findings = scan_path(path, examined=examined)
    except (OSError, ValueError) as exc:
        return "not-scanned", type(exc).__name__
    if findings:
        return "findings", "Review the local card."
    return ("clean", "") if sum(examined) == 1 else ("not-scanned", "No card examined.")


def privacy_gate_hook(args: argparse.Namespace):
    def hook(card: HandoffCard) -> HandoffCard:
        if card.target_provider == "lmstudio":
            card.privacy_scan = "skipped (local model; nothing leaves this computer)"
            return card
        status, detail = fieldkit_scan(Path(card.card_path))
        card.privacy_scan = status
        if status == "findings":
            raise Blocked(f"PRIVACY SCAN FOUND PROBLEMS in {card.card_path}\n{detail}\n"
                          "Edit that file, then relaunch:  handoff --use-card <file> --provider ...",
                          3, "BLOCKED_PRIVACY")
        if status == "not-scanned":
            raise Blocked("Privacy scan did not examine the card.", 3, "BLOCKED_UNSCANNED")
        return card
    return hook


def approval_hook(args: argparse.Namespace):
    def hook(card: HandoffCard) -> HandoffCard:
        if card.target_provider == "lmstudio":  # local: nothing leaves the machine
            card.approved = True
            return card
        say(f"\nLaunch {card.target_provider} / {card.target_model} | thinking={card.thinking_effort} | "
            f"economy={'on' if card.economy_mode else 'off'} | from {card.source_agent}:{card.session_id}")
        say(f"This sends the redacted card to that provider: {card.card_path}")
        if args.approve:
            card.approved = True
        elif sys.stdin.isatty() and not args.dry_run and not args.json:
            card.approved = input("Type 'yes' to approve: ").strip().lower() == "yes"
        return card
    return hook


# ---------------------------------------------------------------------------
# 9. DISPATCH  (policy enforced via real flags where the CLI supports them)
# ---------------------------------------------------------------------------
CLAUDE_THINK_TOKENS = {"low": "4000", "medium": "10000", "high": "31999"}


def build_command(card: HandoffCard, reg: Dict[str, Provider]) -> Tuple[List[str], Dict[str, str]]:
    prov, p = card.target_provider, reg[card.target_provider]
    mid = p.id_fn(card.target_model)
    light_id = p.id_fn(card.light_model) if card.light_model else mid
    boot = (f"Read the handoff card at {card.card_path} and resume the task exactly where it stopped. "
            f"Thinking effort: {card.thinking_effort}."
            + (" " + economy_rules(card) if card.economy_mode else ""))
    env = os.environ.copy()
    env.update(AGENT_THINKING_EFFORT=card.thinking_effort,
               AGENT_ECONOMY_MODE="1" if card.economy_mode else "0",
               AGENT_LIGHT_MODEL=card.light_model,
               AGENT_HANDOFF_CARD=card.card_path)
    if prov == "claude":
        env["MAX_THINKING_TOKENS"] = CLAUDE_THINK_TOKENS[card.thinking_effort]
        # Prompt goes first: --disallowedTools takes a list and would swallow a trailing prompt.
        cmd = [p.binary, boot, "--model", mid]
        if card.economy_mode:
            env["CLAUDE_CODE_SUBAGENT_MODEL"] = light_id   # any helper that slips through is cheap
            env["ANTHROPIC_SMALL_FAST_MODEL"] = light_id   # background/explore model
            cmd += ["--disallowedTools", "Task", "Agent"]  # zero subagents (tool names: verify with --help)
    elif prov == "codex":
        cmd = [p.binary, "-m", mid, "-c", f'model_reasoning_effort="{card.thinking_effort}"', boot]
    elif prov == "opencode":
        # Binary name for Gorilla OpenCode is a guess: verify, or set it in config.json "binaries".
        names = [p.binary] if p.binary != "opencode" else ["gorilla-opencode", "opencode"]
        exe = next((b for b in names if shutil.which(b)), names[0])
        cmd = [exe, "--model", mid, "--prompt", boot]
    elif prov == "kiro":
        cmd = [p.binary, "chat"] + ([] if card.target_model == "Auto" else ["--model", mid]) + [boot]
    elif prov == "antigravity":
        cmd = [p.binary, "--model", mid, boot]
    else:
        raise ValueError(prov)
    return cmd, env


def _base_result(card: HandoffCard) -> Dict[str, Any]:
    return {"status": "", "card": card.card_path, "privacy_scan": card.privacy_scan,
            "approved": card.approved,
            "source": {"agent": card.source_agent, "session": card.session_id, "path": card.source_path, "last_activity": card.last_activity},
            "target": {"provider": card.target_provider, "model": card.target_model,
                       "light_model": card.light_model, "thinking": card.thinking_effort,
                       "economy": card.economy_mode},
            "policy": enforcement(card)}


def _say_policy(card: HandoffCard) -> None:
    e = enforcement(card)
    say(f"\n[POLICY] {'ECONOMY' if card.economy_mode else 'STANDARD'} | {card.target_provider} | "
        f"{card.target_model} | thinking={card.thinking_effort}")
    for line in e["enforced"]:
        say(f"  ENFORCED : {line}")
    for line in e["advisory"]:
        say(f"  ADVISORY : {line}")


def dispatch(card: HandoffCard, reg: Dict[str, Provider], args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    res = _base_result(card)
    _say_policy(card)
    if card.target_provider == "lmstudio":
        return dispatch_lmstudio(card, args, res)
    cmd, env = build_command(card, reg)
    exe = shutil.which(cmd[0])
    res["command"], res["binary_found"] = cmd, bool(exe)
    if args.dry_run or not card.approved:
        res["status"] = "PREVIEW"
        shown = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        say(f"\n[PREVIEW - nothing launched] {shown}")
        say(f"env: AGENT_THINKING_EFFORT={card.thinking_effort} AGENT_ECONOMY_MODE={env['AGENT_ECONOMY_MODE']}"
            + (f" MAX_THINKING_TOKENS={env['MAX_THINKING_TOKENS']}" if "MAX_THINKING_TOKENS" in env else ""))
        if not exe:
            say(f"[warn] '{cmd[0]}' is not on PATH.")
        if not card.approved and not args.dry_run:
            say("Add --approve (typed by you) to launch.")
        return res, 0
    if not exe:
        res.update(status="ERROR", message=f"'{cmd[0]}' is not on PATH. Open it yourself and tell it to read {card.card_path}")
        return res, 1
    from .lifecycle.models import check_model
    verdict = check_model(reg[card.target_provider].id_fn(card.target_model), origin="handoff")
    if not verdict["ok"]:
        raise Blocked(verdict["why"], 3, "BLOCKED_MODEL")
    cmd[0] = exe  # full path so Windows .cmd shims resolve
    cwd = card.cwd if card.cwd and Path(card.cwd).is_dir() else None
    rc = subprocess.run(cmd, env=env, cwd=cwd, check=False).returncode
    res.update(status="LAUNCHED", exit_code=rc)
    return res, (0 if rc == 0 else 1)


def dispatch_lmstudio(card: HandoffCard, args: argparse.Namespace, res: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    model = card.target_model
    if model == "auto-detect-local-server":
        model = lmstudio_models()[0]
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": f"Thinking effort: {card.thinking_effort}. "
                                          + (economy_rules(card) if card.economy_mode else "")},
            {"role": "user", "content": card.to_markdown(compact=True)}],
        "temperature": 0.2 if card.economy_mode else 0.5,
    }
    if card.economy_mode:
        payload["max_tokens"] = 1500
    res["command"] = ["POST", f"{LMSTUDIO_URL}/chat/completions", model]
    if args.dry_run:
        res["status"] = "PREVIEW"
        say("\n[PREVIEW - nothing sent] POST", f"{LMSTUDIO_URL}/chat/completions\n", json.dumps(payload, indent=2)[:1500])
        return res, 0
    req = urllib.request.Request(f"{LMSTUDIO_URL}/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            reply = json.loads(r.read().decode())["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        res.update(status="ERROR", message=f"LM Studio request failed (server on :1234 running?): {exc}")
        return res, 1
    say("\n[LM STUDIO]\n" + reply)
    res.update(status="LAUNCHED", reply=reply)
    return res, 0


# ---------------------------------------------------------------------------
# 10. CLI
# ---------------------------------------------------------------------------
def pick_session(matches, interactive: bool):
    if not interactive and len(matches) > 1 and matches[0][3] == matches[1][3]:
        ids = ", ".join(session_id(path) for _, path, _, _ in matches)
        raise Blocked(f"Several sessions matched. Select a session ID from {ids}.", 2, "AMBIGUOUS")
    if len(matches) == 1 or not interactive:
        return matches[0]
    labels = [f"{a} | {dt.datetime.fromtimestamp(m):%m-%d %H:%M} | {p.name[:36]} (match {s:.0%})"
              for a, p, m, s in matches]
    return matches[labels.index(prompt_choice("Multiple sessions matched:", labels))]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fieldkit handoff",
                                 description="Hand a session to another agent, with enforced model/thinking policy.")
    ap.add_argument("query", nargs="?", help="session UUID, title text, or ChatGPT share URL")
    ap.add_argument("-p", "--project", help="project name filter")
    ap.add_argument("--provider", choices=["antigravity", "claude", "opencode", "codex", "kiro", "lmstudio"])
    ap.add_argument("--model", help="session model (economy default: the provider's economy main model)")
    ap.add_argument("--light-model", help="explore/write/helper model (economy default per provider)")
    ap.add_argument("--thinking", choices=["low", "medium", "high"])
    ap.add_argument("--economy", dest="economy", action="store_true", default=None,
                    help="economy mode (this is the default when not asked)")
    ap.add_argument("--no-economy", dest="economy", action="store_false")
    ap.add_argument("--file", help="transcript text file ('-' = stdin), for web chats")
    ap.add_argument("--use-card", metavar="CARD.md", help="relaunch an existing (edited) card; skips extraction")
    ap.add_argument("--approve", action="store_true", help="launch for real (you, at the keyboard)")
    ap.add_argument("--dry-run", action="store_true", help="preview only, never launch")
    ap.add_argument("--json", action="store_true", help="machine-readable answer on stdout")
    ap.add_argument("--list", action="store_true", help="list recent sessions")
    return ap


def finish(res: Dict[str, Any], code: int) -> int:
    if JSON_MODE:
        print(json.dumps(res, indent=2, default=str))
    elif code and res.get("message"):
        print(res["message"], file=sys.stderr)
    return code


def _run(args: argparse.Namespace, ap: argparse.ArgumentParser) -> int:
    if args.list:
        rows = [{"provider": a, "session": session_id(p), "path": tilde(str(p)),
                 "last_activity": dt.datetime.fromtimestamp(m, dt.timezone.utc).isoformat()}
                for a, p, m in iter_session_files(args.project)]
        if args.json:
            return finish({"sessions": rows}, 0)
        for row in rows:
            print(row["last_activity"], row["provider"], row["session"], row["path"])
        return 0
    if not (args.query or args.file or args.use_card):
        ap.error("give a session id / title / URL, or --file, or --use-card")

    reg = build_registry(load_config())
    interactive = sys.stdin.isatty() and not args.json
    skip: set = set()

    if args.use_card:
        md = Path(args.use_card)
        if not md.is_file():
            return finish({"status": "ERROR", "message": f"card not found: {md}"}, 1)
        card = HandoffCard(md.stem, _now(), "existing-card", str(md), md.stem, md.name,
                           args.project or "", os.getcwd(), "", [], [], {}, card_path=str(md))
        skip = {"post_extract", "post_card_write"}
    elif args.file:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8", errors="ignore")
        card = build_card_from_text(text, "pasted", args.query or Path(args.file).name, args.project)
    elif args.query.startswith(("http://", "https://")):
        card = fetch_share_link(args.query, args.project)
    else:
        matches = find_sessions(args.query, args.project)
        if matches:
            agent, path, _, _ = pick_session(matches, interactive)
            card = build_card_from_file(agent, path, args.query, args.project)
        elif not args.project and UUID_RE.fullmatch(args.query) and find_brain_dir(args.query):
            card = build_card_from_brain_only(args.query, find_brain_dir(args.query), args.project)
        else:
            msg = f"No session matching {args.query!r}" + (f" in project {args.project!r}" if args.project else "") \
                  + ". Try: handoff --list"
            return finish({"status": "NOT_FOUND", "message": msg}, 1)
    say(f"[FOUND] {card.source_agent} | {card.session_id} | {tilde(card.source_path)}")

    hooks = HookRegistry()
    hooks.register("post_extract", redact_hook)
    hooks.register("policy_enforce", make_policy_hook(args, reg))
    hooks.register("validate", make_validate_hook(reg))
    hooks.register("post_card_write", write_card_hook)
    hooks.register("privacy_gate", privacy_gate_hook(args))
    hooks.register("pre_dispatch", approval_hook(args))
    for stage in STAGES:
        if stage not in skip:
            card = hooks.run(stage, card)
    res, code = dispatch(card, reg, args)
    return finish(res, code)


def main(argv: Optional[List[str]] = None) -> int:
    global JSON_MODE
    ap = build_parser()
    args = ap.parse_args(argv)
    JSON_MODE = args.json
    try:
        return _run(args, ap)
    except OSError as exc:
        return finish({"status": "ERROR", "message": str(exc)}, 1)
    except Blocked as b:
        return finish({"status": b.status, "message": str(b)}, b.code)
    except SystemExit as e:
        if isinstance(e.code, str):
            return finish({"status": "ERROR", "message": e.code}, 1)
        raise


if __name__ == "__main__":
    sys.exit(main())
