"""token-audit: where the tokens of Claude Code sessions went. Read-only.

usage:
    python token_audit.py [TRANSCRIPT.jsonl ...]   one or more session transcripts
    python token_audit.py --all [--min-mb 1]       every transcript under ~/.claude/projects
    python token_audit.py --doctor                 what this computer has for the audit
    add --json for one JSON object per session instead of the table

Per session: wall-clock span, API calls, input / cache-write / cache-read / output tokens
(deduplicated by message id: one reply is written as several lines), the context sent per call
(average, peak, and how many calls fell in each 100k band), and how much text the harness's
own hooks injected (ROOM feed, tool router, owner-command stop hook, session-start notes).

Why the context per call matters: every call sends the whole conversation again. Total tokens
are roughly calls x average context, so a session that runs at 500k context costs about five
times one that runs at 100k for the same work.
Written 2026-10-21 (owner: "does the harness cost me a lot of tokens?").
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

VERSION = "1.2.0"
PROJECTS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
# Anthropic first-party API prices, US dollars per million tokens, from prices.json (shared with
# session_cost.py; every number there was read off the provider's price page, with the date).
# Calls on a model missing from the table are counted as unpriced, never guessed.
PRICES_FILE = Path(__file__).resolve().parent / "prices.json"
PRICES = {}           # filled on first use by _prices(): nothing runs on import


def _prices():
    if not PRICES:
        t = json.loads(PRICES_FILE.read_text(encoding="utf-8"))["providers"]["anthropic"]
        PRICES.update({m: e[0] for m, e in t["models"].items()})
        PRICES["_as_of"] = t["fetched"]
    return PRICES
HOOK_MARKERS = {
    "room": "ROOM: what the other session",
    "toolrouter": "TOOL ROUTER",
    "ownercmd": "Owner commands must be pasteable",
    "sessionstart": "SessionStart hook additional context",
}


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _strings(v):
    """Every string inside a JSON value."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)


def audit(path):
    seen, calls, tools, models = set(), [], {}, {}
    usd = usd_read = 0.0
    unpriced_calls = 0
    first = last = None
    hook_hits = dict.fromkeys(HOOK_MARKERS, 0)
    hook_chars = dict.fromkeys(HOOK_MARKERS, 0)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if d.get("timestamp"):
                t = _ts(d["timestamp"])
                first = first or t
                last = t
            if d.get("type") != "assistant":
                # injected text: from the marker to the end of the string that carries it
                for s in _strings(d):
                    for k, m in HOOK_MARKERS.items():
                        i = s.find(m)
                        if i >= 0:
                            hook_hits[k] += s.count(m)
                            hook_chars[k] += len(s) - i
                continue
            msg = d.get("message") or {}
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tools[c.get("name")] = tools.get(c.get("name"), 0) + 1
            u, mid = msg.get("usage"), msg.get("id")
            if not u or mid in seen:
                continue
            seen.add(mid)
            call = (u.get("input_tokens", 0), u.get("cache_creation_input_tokens", 0),
                    u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0))
            calls.append(call)
            model = msg.get("model") or "?"
            models[model] = models.get(model, 0) + 1
            price = _prices().get(model)
            if price is None:
                unpriced_calls += 1
                continue
            split = u.get("cache_creation") or {}
            w1h = split.get("ephemeral_1h_input_tokens", 0)
            w5m = call[1] - w1h          # no split recorded: priced as the cheaper 5-minute write
            usd += (call[0] * price["input"] + w5m * price["cache_write_5m"] + w1h * price["cache_write_1h"]
                    + call[2] * price["cache_read"] + call[3] * price["output"]) / 1e6
            usd_read += call[2] * price["cache_read"] / 1e6
    ctx = [c[0] + c[1] + c[2] for c in calls]
    bands = [0] * 10
    for c in ctx:
        bands[min(9, c // 100_000)] += 1
    return {
        "session": Path(path).stem,
        "hours": round((last - first).total_seconds() / 3600, 1) if first else 0.0,
        "api_calls": len(calls),
        "input": sum(c[0] for c in calls),
        "cache_write": sum(c[1] for c in calls),
        "cache_read": sum(c[2] for c in calls),
        "output": sum(c[3] for c in calls),
        "context_avg": int(sum(ctx) / len(ctx)) if ctx else 0,
        "context_first": ctx[0] if ctx else 0,
        "context_max": max(ctx) if ctx else 0,
        "context_bands_100k": bands,
        "hook_hits": hook_hits,
        "hook_chars": hook_chars,
        "top_tools": sorted(tools.items(), key=lambda kv: -kv[1])[:6],
        "models": models,
        # what these calls would cost on API credits at PRICES (as of PRICES_AS_OF);
        # calls on a model missing from PRICES are counted in unpriced_calls, not guessed
        "api_usd": round(usd, 2),
        "api_usd_context_reread": round(usd_read, 2),
        "unpriced_calls": unpriced_calls,
        "prices_as_of": _prices()["_as_of"],
    }


def table(rows):
    print(f"{'session':10} {'hours':>6} {'calls':>6} {'ctx avg':>8} {'ctx max':>8} "
          f"{'read M':>8} {'write M':>8} {'out K':>7} {'hooks K chars':>13} {'API $':>9} {'unpriced':>8}")
    for r in sorted(rows, key=lambda r: -r["cache_read"]):
        print(f"{r['session'][:8]:10} {r['hours']:6} {r['api_calls']:6} {r['context_avg']:8} "
              f"{r['context_max']:8} {r['cache_read'] / 1e6:8.1f} {r['cache_write'] / 1e6:8.2f} "
              f"{r['output'] / 1e3:7.0f} {sum(r['hook_chars'].values()) // 1000:13} "
              f"{r['api_usd']:9,.0f} {r['unpriced_calls']:8}")
    if rows:
        tot = sum(r["cache_read"] + r["cache_write"] + r["input"] + r["output"] for r in rows)
        usd = sum(r["api_usd"] for r in rows)
        unpriced = sum(r["unpriced_calls"] for r in rows)
        print(f"\n{len(rows)} session(s), {tot / 1e6:,.0f} M tokens in total "
              "(read = context sent again on every call; it dominates)")
        print(f"On API credits at {_prices()['_as_of']} prices: ${usd:,.0f}"
              + (f" for the priced calls; {unpriced} call(s) on models without a price are not included"
                 if unpriced else ""))


def doctor():
    ok = sys.version_info >= (3, 8)
    found = sorted(PROJECTS.glob("*/*.jsonl")) if PROJECTS.is_dir() else []
    print(f"token-audit {VERSION} doctor")
    print(f"  [{'ok' if ok else '--'}] Python {sys.version.split()[0]} (needs 3.8+)")
    print(f"  [{'ok' if found else '--'}] transcripts under {PROJECTS}: {len(found)}")
    print("  needs nothing else: standard library only, reads files, writes nothing, no network")
    return 0 if ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="token_audit", description="Where the tokens of Claude Code sessions went.")
    p.add_argument("transcripts", nargs="*", help="session .jsonl files")
    p.add_argument("--all", action="store_true", help="every transcript under ~/.claude/projects")
    p.add_argument("--min-mb", type=float, default=1.0, help="with --all: skip files smaller than this (default 1)")
    p.add_argument("--json", action="store_true", help="one JSON object per session")
    p.add_argument("--doctor", action="store_true", help="check this computer and stop")
    a = p.parse_args(argv)
    if a.doctor:
        return doctor()
    paths = [Path(t) for t in a.transcripts]
    if a.all:
        paths += [f for f in PROJECTS.glob("*/*.jsonl") if f.stat().st_size >= a.min_mb * 1e6]
    if not paths:
        p.error("name a transcript, or use --all")
    missing = [str(x) for x in paths if not x.is_file()]
    if missing:
        print("token_audit: not found: " + ", ".join(missing), file=sys.stderr)
        return 2
    rows = [audit(x) for x in paths]
    if a.json:
        for r in rows:
            print(json.dumps(r))
    else:
        table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
