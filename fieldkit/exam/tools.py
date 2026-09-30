"""The two toolsets a model gets in the exam. Every call is recorded.

raw: what a basic coding agent has - list a folder, read a file, grep-style search.
kit: raw + `find` (pfind; for a code name it answers DEFINED / NOT DEFINED, never a
     near-miss), `triage` (build-log rules), `refcheck` (which listed files are missing).
     Kit tools do the thinking and end with NEXT; the model makes one choice.

All paths are confined to the exam folder: a model cannot read outside it.
"""
import json
import re
import os
import subprocess
import sys
import time
from pathlib import Path

from ..core import settings

PFIND = settings.ROOT / "toolbox" / "pfind" / "pfind.py"
READ_LIMIT = 120          # lines per read_file call
SEARCH_LIMIT = 40         # matching lines per search_text call

RAW_SCHEMA = [
    {"type": "function", "function": {
        "name": "list_dir", "description": "List the files and folders in a folder of the project.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "folder, e.g. '.' or 'src'"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": f"Read up to {READ_LIMIT} lines of a file, starting at a line number.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer", "description": "1-based, default 1"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search_text", "description": f"Find lines containing a text in all files (like grep -rn). "
                                              f"Returns at most {SEARCH_LIMIT} lines as path:line: text.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
]
KIT_SCHEMA = RAW_SCHEMA + [
    {"type": "function", "function": {
        "name": "find", "description": "Best tool to locate anything: ranked search over file names AND contents "
                                       "(also multi-line code snippets). Returns the top files with the matching "
                                       "lines, best first, and what to do next.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "triage", "description": "Read a build log and name the failure: cause and fix. Use this for any "
                                         "failed build or install log.",
        "parameters": {"type": "object", "properties": {"log_path": {"type": "string"}}, "required": ["log_path"]}}},
    {"type": "function", "function": {
        "name": "refcheck", "description": "Check a file that LISTS other files (a manifest, a list of paths): "
                                           "returns exactly which listed files do not exist.",
        "parameters": {"type": "object", "properties": {"list_path": {"type": "string"}}, "required": ["list_path"]}}},
]


class Toolbox:
    def __init__(self, root, kit=False):
        self.root = Path(root).resolve()
        self.kit = kit
        self.calls = []

    @property
    def schema(self):
        return KIT_SCHEMA if self.kit else RAW_SCHEMA

    def _path(self, rel):
        p = (self.root / (rel or ".")).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError("path is outside the project")
        return p

    def _rel(self, p):
        return Path(p).resolve().relative_to(self.root).as_posix()

    def dispatch(self, name, args):
        t0 = time.monotonic()
        try:
            fn = {"list_dir": self.list_dir, "read_file": self.read_file, "search_text": self.search_text,
                  "find": self.find, "triage": self.triage, "refcheck": self.refcheck}.get(name)
            if fn is None or (name in ("find", "triage", "refcheck") and not self.kit):
                out = f"ERROR: no tool named {name!r}. Tools: {', '.join(t['function']['name'] for t in self.schema)}"
            else:
                out = fn(**(args or {}))
        except TypeError as e:
            out = f"ERROR: bad arguments for {name}: {e}"
        except (ValueError, OSError) as e:
            out = f"ERROR: {e}"
        self.calls.append({"name": name, "args": args, "chars": len(out), "seconds": round(time.monotonic() - t0, 2)})
        return out

    # -- raw ---------------------------------------------------------------
    def list_dir(self, path="."):
        p = self._path(path)
        if not p.is_dir():
            return f"ERROR: {path} is not a folder"
        items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        return "\n".join(f"{x.name}/" if x.is_dir() else x.name for x in items) or "(empty)"

    def read_file(self, path, start_line=1):
        p = self._path(path)
        if not p.is_file():
            return f"ERROR: {path} is not a file"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        s = max(1, int(start_line or 1))
        chunk = lines[s - 1:s - 1 + READ_LIMIT]
        body = "\n".join(f"{s + i}: {line}" for i, line in enumerate(chunk))
        more = f"\n... {len(lines) - (s - 1 + len(chunk))} more lines (read_file with start_line={s + len(chunk)})" \
            if s - 1 + len(chunk) < len(lines) else ""
        return (body or "(no lines there)") + more

    def search_text(self, text):
        if not text:
            return "ERROR: empty search"
        hits = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if text in line:
                        hits.append(f"{self._rel(p)}:{i}: {line.strip()[:160]}")
            except OSError:
                continue
        if not hits:
            return "no lines contain that text"
        extra = f"\n... {len(hits) - SEARCH_LIMIT} more lines not shown" if len(hits) > SEARCH_LIMIT else ""
        return "\n".join(hits[:SEARCH_LIMIT]) + extra

    # -- kit -----------------------------------------------------------------
    def find(self, query):
        if not query:
            return "ERROR: empty query"
        q = query.strip()
        paths = [t.strip(",;'\"`") for t in re.split(r"[\s,;]+", q)]
        paths = [t for t in paths if re.fullmatch(r"[\w.\-]+(/[\w.\-]+)*\.[A-Za-z0-9]{1,6}|[\w.\-]+(/[\w.\-]+)+", t)]
        if len(paths) >= 2 and len(paths) >= len(q.split()) * 0.8:
            return self._check_paths(paths)
        m = re.fullmatch(r"(?:def\s+|class\s+|function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\))?", q)
        if m:
            return self._find_name(m.group(1))
        data = self._pfind(query, snippet="\n" in q)
        if isinstance(data, str):
            return data
        if not data["results"]:
            return (f"NOT FOUND: no file name or file content matches that text.\n"
                    "NEXT: answer that it is not in this project.")
        return "\n".join(self._listing(data) + ["NEXT: the top file is the best match; read it around "
                                                 "the listed line only if you need more."])

    def _pfind(self, query, snippet=False, regex=False, content_only=False):
        if not PFIND.is_file():
            return f"ERROR: find needs pfind, which is not gathered here - run: fieldkit gather --only pfind"
        args = [sys.executable, str(PFIND), "-", str(self.root), "--json", "--limit", "5", "--max", "3", "--no-color"]
        args += (["-x"] if snippet else []) + (["-r"] if regex else []) + (["--content"] if content_only else [])
        r = subprocess.run(args, input=query, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, env=dict(os.environ))
        try:
            return json.loads(r.stdout)
        except ValueError:
            return f"ERROR: find failed: {(r.stderr or r.stdout)[:300]}"

    def _listing(self, data):
        out = [f"{data['matched']} file(s) matched; best first:"]
        for res in data["results"]:
            out.append(f"- {self._rel(res['path'])}  ({', '.join(res['why'])})")
            out += [f"    {s['line']}: {s['text'][:140]}" for s in res["samples"]]
        return out

    def _find_name(self, name):
        """A code name: say where it is DEFINED, or plainly that it is not.
        Near-misses (rebalance_caches for rebalance_cache) are never presented as the answer -
        on 2026-09-29 that pushed Gemma into naming a Markdown file as the definition."""
        defined = self._pfind(rf"^\s*(async\s+)?(def|class|function|fn|func)\s+{name}\b", regex=True,
                              content_only=True)
        if isinstance(defined, str):
            return defined
        hits = [(self._rel(r_["path"]), s["line"], s["text"]) for r_ in defined["results"] for s in r_["samples"]]
        if hits:
            path, line, text = hits[0]
            out = [f"DEFINED: `{name}` is defined at {path}:{line}", f"    {line}: {text[:140]}"]
            if len(hits) > 1:
                out.append("also defined at: " + ", ".join(f"{p}:{l}" for p, l, _ in hits[1:4]))
            return "\n".join(out + [f"NEXT: answer {path}:{line}."])
        exact = self._pfind(rf"\b{name}\b", regex=True, content_only=True)
        if isinstance(exact, str):
            return exact
        if exact["results"]:
            where = ", ".join(f"{self._rel(r_['path'])}:{r_['samples'][0]['line']}" for r_ in exact["results"][:4]
                              if r_["samples"])
            return (f"NOT DEFINED: `{name}` is mentioned ({where}) but no file defines it.\n"
                    f"NEXT: answer that `{name}` is not defined in this project.")
        similar = self._pfind(name, content_only=True)
        near = []
        if not isinstance(similar, str):
            for r_ in similar["results"][:4]:
                for s in r_["samples"][:1]:
                    words = sorted(set(re.findall(rf"\w*{re.escape(name)}\w*", s["text"])) - {name})
                    if words:
                        near.append(f"{words[0]} ({self._rel(r_['path'])}:{s['line']})")
        tail = f" Only similar names exist: {', '.join(near)}." if near else ""
        return (f"NOT DEFINED: no file defines or mentions `{name}`.{tail}\n"
                f"NEXT: answer that `{name}` does not exist in this project"
                + (" (the similar names are different things)." if near else "."))

    def _check_paths(self, paths):
        """A list of file paths given to find: say which exist. Answering "NOT FOUND" to the whole
        string (2026-09-29) made Gemma report all 12 files of a manifest as missing."""
        missing = [p for p in paths if not (self.root / p).exists()]
        present = len(paths) - len(missing)
        out = [f"That is a list of {len(paths)} file paths, so each was checked: {present} exist, "
               f"{len(missing)} missing."]
        out += [f"- missing: {p}" for p in missing] or ["- none missing"]
        out.append("NEXT: answer with exactly the missing ones listed above. (For a file that lists paths, "
                   "refcheck does this in one call.)")
        return "\n".join(out)

    def refcheck(self, list_path):
        from ..build import refcheck as rc
        p = self._path(list_path)
        if not p.is_file():
            return f"ERROR: {list_path} is not a file"
        r = rc.check_manifest(p, self.root)
        return "\n".join(rc.lines(r))

    def triage(self, log_path):
        from ..build import triage as tr
        p = self._path(log_path)
        if not p.is_file():
            return f"ERROR: {log_path} is not a file"
        r = tr.triage_file(p, "auto")
        out = [f"verdict: {r['verdict']} ({r['error_count']} error lines)"]
        for m in r["matches"][:3]:
            out += [f"- known failure {m['id']}", f"  cause: {m['cause']}", f"  fix: {m['fix']}"]
        if not r["matches"]:
            out += [f"  {e['line'][:160]}" for e in r["errors"][:5]]
        errs = [e["line"] for e in r["errors"] if "Unmet build dependencies" in e["line"]]
        if errs:
            out.append(f"  evidence: {errs[0][:160]}")
        out.append("NEXT: answer with the cause and the fix above." if r["matches"] else
                   "NEXT: read the error lines above; they are the real failure.")
        return "\n".join(out)
