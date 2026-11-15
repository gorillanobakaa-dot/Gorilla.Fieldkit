# Version: 1.0.0 · P37 one song sheet
"""fieldkit rules inventory | lint --files | sync | hooks: every project's agent instructions on one song sheet
(P37, owner 2026-11-13: "the same rules, the same .md's ... as lean as possible without any duplicated or conflicting
instructions so we don't burn 40K tokens only reading those instructions").

    inventory   every instruction file of every managed project on this machine: bytes, estimated tokens (bytes/4),
                which agent loads it when, git-tracked or not; the global files, SKILL.md frontmatter, the memory index
    lint        duplicates (one sentence, normalized, in two files one agent loads together, or twice in one file),
                conflicts (known pairs in rules/song.yaml; "always X" against "never X"), a token budget per file.
                Exit 3 on a finding; each carries the cut it proposes. Nothing is deleted or rewritten here.
    sync        a manifest (SHA-256 of each file, CRLF-insensitive) of the shared files; with --against FILE the
                other laptop's manifest: a mismatch is a finding. The private store (state/rules-store/, git-ignored,
                never published) keeps a copy of each UNTRACKED project file; --apply refreshes the store from this
                machine, --restore brings a missing or older file back from it. Moving the store between laptops is
                `fieldkit sync` over git for tracked files and the cable (`fieldkit peers link`) for the store.
    hooks       the bytes each hook injected per turn (state/hook-bytes.jsonl, written by the room hook): mean, p95,
                total per event against the cap in rules/song.yaml.

Data first: rules/song.yaml holds the budgets, the project list, the conflict pairs and the hook caps.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = "rules/song.yaml"
GLOBALS = {"claude-code": (".claude", "CLAUDE.md"), "codex": (".codex", "AGENTS.md"), "gemini": (".gemini", "GEMINI.md")}
#: which agents read a project file (Claude Code reads CLAUDE.md; this repo's AGENTS.md is also project context there)
READERS = {"CLAUDE.md": ["claude-code"], "AGENTS.md": ["claude-code", "codex"], "GEMINI.md": ["gemini"],
           "copilot-instructions.md": ["copilot"]}


def policy(root=None):
    import yaml
    return yaml.safe_load((Path(root or ROOT) / POLICY).read_text(encoding="utf-8"))


def norm_bytes(data):
    """The bytes a hash is taken over: CRLF and a trailing newline do not count (Windows against Linux)."""
    return data.replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"


def sha(path):
    return hashlib.sha256(norm_bytes(Path(path).read_bytes())).hexdigest()


def tokens(nbytes, pol):
    return -(-int(nbytes) // int(pol.get("bytes_per_token") or 4))


def tracked(path):
    """-> True/False: is the file in a git repository's index; None when it is in no repository at all."""
    p = Path(path)
    try:
        top = subprocess.run(["git", "-C", str(p.parent), "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=10)
        if top.returncode:
            return None
        r = subprocess.run(["git", "-C", top.stdout.strip(), "ls-files", "--error-unmatch", str(p.resolve())],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


def _frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text.replace("\r\n", "\n"), re.S)
    return m.group(0) if m else ""


def inventory(home=None, root=None, pol=None, git=tracked):
    """-> [row]: path, project, kind (global|project|skill|memory), agents, loads, bytes, tokens, loaded_bytes
    (a SKILL.md loads its frontmatter only), tracked (True | False | None)."""
    pol = pol or policy(root)
    home = Path(home or Path.home())
    rows = []

    def add(path, project, kind, agents, loader, body=True):
        data = path.read_bytes()
        loaded = len(data) if body else len(_frontmatter(data.decode("utf-8", "replace")).encode())
        rows.append({"path": str(path), "project": project, "kind": kind, "agents": agents,
                     "loads": pol["loaders"].get(loader, ""), "bytes": len(data), "loaded_bytes": loaded,
                     "tokens": tokens(loaded, pol), "tracked": git(path) if kind in ("project", "skill") else None})

    for agent, (d, name) in GLOBALS.items():
        p = home / d / name
        if p.is_file():
            add(p, "(global)", "global", [agent], name)
    for proj in pol["projects"]:
        base = home / proj
        if not base.is_dir():
            continue
        for f in pol["files"]:
            p = base / f
            if p.is_file():
                add(p, proj, "project", READERS.get(Path(f).name, []), Path(f).name)
        skills = base / "skills"
        for s in sorted(skills.glob("*/SKILL.md")) if skills.is_dir() else []:
            add(s, proj, "skill", ["claude-code", "codex", "gemini"], "SKILL.md", body=False)
    slug = "-" + str(home / pol["projects"][0]).strip("/").replace("/", "-").replace(".", "-")
    mem = home / ".claude" / "projects" / slug / "memory" / "MEMORY.md"
    if mem.is_file():
        add(mem, pol["projects"][0], "memory", ["claude-code"], "MEMORY.md")
    return rows


# ------------------------------------------------------------------------------------------------ lint

_MARK = re.compile(r"<!--.*?-->", re.S)
_SPLIT = re.compile(r"(?<=[.;:!?])\s+|\n+")


def sentences(text, min_chars=45):
    """-> [(normalized, original)]: the comparable sentences of a rules text (markup, bullets and case dropped)."""
    out = []
    for raw in _SPLIT.split(_MARK.sub("", text.replace("\r\n", "\n"))):
        s = re.sub(r"^[\s>*#\-\d.)]+", "", raw).strip()
        n = re.sub(r"[^a-z0-9 ]+", "", s.lower())
        n = re.sub(r"\s+", " ", n).strip()
        if len(n) >= min_chars:
            out.append((n, s))
    return out


def _group(rows):
    """-> {agent: [row]}: the files one agent loads in the same session (its global file and the project's)."""
    g = {}
    for r in rows:
        for a in r["agents"]:
            g.setdefault((a, r["project"] if r["kind"] != "global" else None), []).append(r)
    by_agent = {}
    for (a, proj), rs in g.items():
        if proj is None:
            continue
        by_agent.setdefault((a, proj), []).extend(rs)
    for (a, proj), rs in list(g.items()):
        if proj is None:
            for (a2, p2) in list(by_agent):
                if a2 == a:
                    by_agent[(a2, p2)].extend(x for x in rs if x not in by_agent[(a2, p2)])
    return by_agent


def _f(kind, path, evidence, proposal=""):
    return {"kind": kind, "file": path, "evidence": evidence, "proposal": proposal, "level": "error"}


def lint(rows=None, home=None, root=None, pol=None):
    """-> [finding] over the inventory: budget, duplicate, conflict. Each has the cut it proposes."""
    pol = pol or policy(root)
    rows = inventory(home, root, pol) if rows is None else rows
    found = []
    for r in rows:
        b = (pol["budget"].get(r["kind"] if r["kind"] != "skill" else "project") or {})
        if r["kind"] == "skill":
            continue
        if b and r["tokens"] > b["limit"]:
            found.append(_f("budget", r["path"], f"{r['tokens']} tokens, limit {b['limit']} (target {b['target']})",
                            f"cut {r['tokens'] - b['target']} tokens: keep only what this file owns, point to a doc"))
    texts = {}
    for r in rows:
        if r["kind"] != "skill":
            texts[r["path"]] = Path(r["path"]).read_text(encoding="utf-8", errors="replace")
    mn = int(pol.get("min_sentence_chars") or 45)
    sents = {p: sentences(t, mn) for p, t in texts.items()}
    seen = set()
    for r in rows:                                                  # twice in one file
        c = {}
        for n, s in sents.get(r["path"], []):
            c.setdefault(n, []).append(s)
        for n, ss in c.items():
            if len(ss) > 1 and (r["path"], n) not in seen:
                seen.add((r["path"], n))
                found.append(_f("duplicate", r["path"], f"said {len(ss)} times: \"{ss[0][:90]}\"",
                                "keep the first, delete the others"))
    for (agent, proj), rs in sorted(_group(rows).items()):          # in two files one agent loads together
        order = {"global": 0, "memory": 2}
        rs = sorted((x for x in rs if x["kind"] != "skill"), key=lambda x: order.get(x["kind"], 1))
        for i, a in enumerate(rs):
            for b in rs[i + 1:]:
                if a["path"] == b["path"]:
                    continue
                key = (a["path"], b["path"], agent)
                na = {n for n, _ in sents.get(a["path"], [])}
                for n, s in sents.get(b["path"], []):
                    if n in na and (key, n) not in seen:
                        seen.add((key, n))
                        found.append(_f("duplicate", b["path"], f"same as {a['path']} ({agent} loads both): \"{s[:90]}\"",
                                        f"delete it from {b['path']}: {a['kind']} files own shared rules"))
        both = "\n".join(texts[x["path"]] for x in rs)
        for c in pol.get("conflicts") or []:
            ma, mb = re.search(c["a"], both, re.I), re.search(c["b"], both, re.I)
            if ma and mb and ("conflict", c["why"], proj, agent) not in seen:
                seen.add(("conflict", c["why"], proj, agent))
                found.append(_f("conflict", proj, f"{c['why']}: \"{ma.group(0)}\" against \"{mb.group(0)}\" ({agent})",
                                "the owner decides which one stands; the other is deleted"))
        if pol.get("polarity"):
            pos, neg = {}, {}
            for x in rs:
                for m in re.finditer(r"\b(always|never)\s+((?:[a-z`'-]+\s+){1,5}[a-z`'-]+)", texts[x["path"]], re.I):
                    k = re.sub(r"[^a-z ]", "", m.group(2).lower()).strip()
                    (pos if m.group(1).lower() == "always" else neg).setdefault(k, x["path"])
            for k in sorted(set(pos) & set(neg)):
                if ("pol", k, proj, agent) not in seen:
                    seen.add(("pol", k, proj, agent))
                    found.append(_f("conflict", proj, f"\"always {k}\" ({pos[k]}) against \"never {k}\" ({neg[k]})",
                                    "the owner decides which one stands"))
    return found


# ------------------------------------------------------------------------------------------------ sync

def store_dir(root=None):
    from .core.settings import state_root
    return Path(state_root()) / "rules-store"


def manifest(rows):
    """-> {key: sha256}: the project and global files, keyed by project + file name (no machine path)."""
    out = {}
    for r in rows:
        if r["kind"] in ("global", "project"):
            out[f"{r['project']}/{Path(r['path']).name}"] = sha(r["path"])
    return out


def compare(mine, other):
    """-> [finding]: a file on one laptop only, or with another hash. CRLF-insensitive (the hash is)."""
    found = []
    for k in sorted(set(mine) | set(other)):
        if k not in other:
            found.append(_f("sync", k, "here only: the other laptop has no such file"))
        elif k not in mine:
            found.append(_f("sync", k, "the other laptop only: this one has no such file"))
        elif mine[k] != other[k]:
            found.append(_f("sync", k, f"differs: here {mine[k][:10]}, there {other[k][:10]}"))
    return found


def store_apply(rows, store=None, restore=False, home=None):
    """Refresh the private store from the UNTRACKED project files (never published); restore=True instead copies a
    file the store holds back where it is missing. -> [line]."""
    store = Path(store or store_dir())
    out = []
    if restore:
        home = Path(home or Path.home())
        for f in sorted(store.glob("*/*")) if store.is_dir() else []:
            proj = f.parent.name.replace("__", "/")
            dst = home / proj / f.name
            if not dst.exists() and dst.parent.is_dir():
                dst.write_bytes(f.read_bytes())
                out.append(f"restored {dst}")
        return out
    for r in rows:
        if r["kind"] == "project" and r["tracked"] is not True:
            d = store / r["project"].replace("/", "__")
            d.mkdir(parents=True, exist_ok=True)
            dst = d / Path(r["path"]).name
            data = norm_bytes(Path(r["path"]).read_bytes())
            if not dst.is_file() or dst.read_bytes() != data:
                dst.write_bytes(data)
                out.append(f"stored {r['project']}/{dst.name}")
    return out


# ------------------------------------------------------------------------------------------------ hooks

def hook_log():
    from .core.settings import state_root
    return Path(state_root()) / "hook-bytes.jsonl"


def record(event, nbytes, session=None, now=None, path=None):
    """One line per hook call that injected text. Never raises (a hook must not fail)."""
    try:
        import time
        p = Path(path or hook_log())
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": int(now or time.time()), "event": event, "bytes": int(nbytes),
                                "session": session}) + "\n")
    except Exception:  # noqa: BLE001
        pass


def apply_cap(text, event, pol=None):
    """-> text with its NOTE lines (rules/song.yaml hooks.capped_prefixes, or a leading count) cut to line_max bytes each
    and, together, to the event's cap_bytes. Every other line (NET, ROOM, EAR, SESSION ROTATION ...) is kept whole."""
    try:
        pol = pol or policy()
        cap = int((pol["hooks"]["cap_bytes"] or {}).get(event) or 0)
        pre = tuple(pol["hooks"].get("capped_prefixes") or ())
        lmax = int(pol["hooks"].get("line_max") or 220)
    except Exception:  # noqa: BLE001
        return text
    if not cap:
        return text
    out, used, cut = [], 0, 0
    for line in text.splitlines():
        if not (line.startswith(pre) or re.match(r"\d+ ", line)):
            out.append(line)
            continue
        raw = line.encode("utf-8")
        short = raw[:lmax].decode("utf-8", "ignore") + ("..." if len(raw) > lmax else "")
        n = len(short.encode("utf-8")) + 1
        if used + n <= cap:
            used += n
            out.append(short)
        else:
            cut += len(raw) + 1
    if cut:
        out.append(f"(+{cut} B of hook notes cut: fieldkit rules hooks)")
    return "\n".join(out)


def hook_stats(path=None, pol=None):
    """-> {event: {calls, total, mean, p95, max, cap, over}} from the recorded hook bytes."""
    pol = pol or policy()
    p = Path(path or hook_log())
    per = {}
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            d = json.loads(line)
            per.setdefault(d["event"], []).append(int(d["bytes"]))
        except (ValueError, KeyError):
            continue
    out = {}
    for ev, xs in per.items():
        xs.sort()
        cap = int((pol["hooks"]["cap_bytes"] or {}).get(ev) or 0)
        out[ev] = {"calls": len(xs), "total": sum(xs), "mean": sum(xs) // len(xs), "p95": xs[min(len(xs) - 1, int(len(xs) * .95))],
                   "max": xs[-1], "cap": cap, "over": sum(1 for x in xs if cap and x > cap)}
    return out


# ------------------------------------------------------------------------------------------------ cli

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit rules", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["inventory", "lint", "sync", "hooks"])
    ap.add_argument("--files", action="store_true", help="lint: the instruction files (the default here)")
    ap.add_argument("--against", help="sync: the other laptop's manifest (JSON from `rules sync --json`)")
    ap.add_argument("--apply", action="store_true", help="sync: refresh the private store from this machine")
    ap.add_argument("--restore", action="store_true", help="sync: bring files the store holds back where missing")
    ap.add_argument("--home")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    pol = policy()
    rows = inventory(a.home, None, pol)
    if a.action == "inventory":
        tot = sum(r["tokens"] for r in rows)
        if a.json:
            print(json.dumps(rows, indent=1))
        else:
            skills = [r for r in rows if r["kind"] == "skill"]
            if skills:
                print(f"{sum(r['tokens'] for r in skills):>5} tok {sum(r['loaded_bytes'] for r in skills):>6} B  skill   git       "
                      f"{len(skills)} SKILL.md frontmatters (name + description, loaded at start)")
            for r in (x for x in rows if x["kind"] != "skill"):
                t = {True: "git", False: "UNTRACKED", None: "-"}[r["tracked"]]
                print(f"{r['tokens']:>5} tok {r['loaded_bytes']:>6} B  {r['kind']:<7} {t:<9} {','.join(r['agents']):<22} {r['path']}")
            print(f"{tot} tokens in {len(rows)} files (bytes/{pol['bytes_per_token']})")
        print("NEXT: fieldkit rules lint")
        return 0
    if a.action == "lint":
        found = lint(rows, pol=pol)
        if a.json:
            print(json.dumps(found, indent=1))
        else:
            for f in found:
                print(f"{f['kind']:<10} {f['file']}: {f['evidence']}\n           -> {f['proposal']}")
            print(f"{len(found)} finding(s)")
        print("NEXT: " + ("nothing" if not found else "apply each proposed cut (the owner's hard lines stay word for word)"))
        return 3 if found else 0
    if a.action == "sync":
        mine = manifest(rows)
        found = compare(mine, json.loads(Path(a.against).read_text(encoding="utf-8"))) if a.against else []
        if a.apply or a.restore:
            for line in store_apply(rows, restore=a.restore, home=a.home):
                print(line)
        if a.json and not a.against:
            print(json.dumps(mine, indent=1))
        else:
            for f in found:
                print(f"sync       {f['file']}: {f['evidence']}")
            print(f"{len(mine)} file(s) hashed here, {len(found)} mismatch(es)")
        print("NEXT: " + ("nothing" if not found else "copy the newer file over the other, then rules sync again"))
        return 3 if found else 0
    st = hook_stats(pol=pol)
    if a.json:
        print(json.dumps(st, indent=1))
    else:
        for ev, s in sorted(st.items()):
            print(f"{ev:<17} {s['calls']:>5} calls  mean {s['mean']:>5} B  p95 {s['p95']:>5} B  max {s['max']:>5} B  "
                  f"cap {s['cap']}  over {s['over']}")
        print("(no hook calls recorded yet)" if not st else "")
    bad = [e for e, s in st.items() if s["over"]]
    print("NEXT: " + ("nothing" if not bad else "a hook rider over its cap: shorten it at the source"))
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
