"""Harvest index: what does every script ever written here do?

Reads every script under toolbox/ (or any folder) WITHOUT running it and
records, per script:

  title, summary     from the script's own docs: Python docstring,
                     PowerShell comment help (.SYNOPSIS/.DESCRIPTION), Rust //!,
                     or the leading comment block of shell/C/C#/JS
  functions          top-level functions (Python, PowerShell, Rust, shell)
  options            command-line options (argparse flags, PowerShell param())
  needs              third-party Python imports (the dependencies to install)
  touches            registry / network / admin / processes / services /
                     file deletion / git / hardware - a quick risk picture
  lines, language, portable (no hard-coded home), probe class, tests beside it

Then:
    fieldkit harvest [--root DIR] [--out DIR]   -> harvest.json + HARVEST.md
    fieldkit harvest --find "words ..."         -> best-matching scripts, ranked

The index is small and flat on purpose: a 4B model can read HARVEST.md or ask
--find and get a short answer, instead of exploring hundreds of files.
"""
import ast
import json
import re
import sys
from pathlib import Path

from .desk.discover import HOME_PATH
from .desk.registry import probe_safety

LANG = {".py": "python", ".ps1": "powershell", ".psm1": "powershell", ".sh": "shell", ".rs": "rust",
        ".cs": "csharp", ".cpp": "cpp", ".c": "c", ".h": "c", ".js": "javascript", ".cmd": "batch", ".bat": "batch"}
STDLIB = set(getattr(sys, "stdlib_module_names", ()))
TOUCHES = [
    ("registry", r"\bwinreg\b|HKLM:|HKCU:|HKEY_|Set-ItemProperty|reg(\.exe)? (add|delete)|New-ItemProperty"),
    ("network", r"\burllib\b|\brequests\b|\bhttpx\b|Invoke-WebRequest|Invoke-RestMethod|\bcurl\b|\bwget\b|socket\.|netsh"),
    ("admin", r"RunAs|#Requires -RunAsAdministrator|IsUserAnAdmin|\bsudo\b|Administrator"),
    ("processes", r"Stop-Process|taskkill|os\.kill|\bkill\b -|Start-Process|subprocess\."),
    ("services", r"Set-Service|Stop-Service|sc(\.exe)? config|systemctl|New-Service"),
    ("deletes-files", r"Remove-Item|shutil\.rmtree|os\.remove|\.unlink\(|\brm -rf?\b|del /"),
    ("git", r"\bgit (push|commit|reset|clone|apply)|git\", \"(push|commit|apply)"),
    ("hardware", r"/sys/class|hwmon|WMI|Get-CimInstance|MSAcpi|ec_(read|write)|PawnIO|inpout"),
    ("power", r"powercfg|PROCTHROTTLE|cpufreq"),
    ("office", r"\bdocx\b|openpyxl|\bpptx\b|reportlab|pypdf|\.docx|\.xlsx"),
    ("gui-automation", r"UIAutomation|SendKeys|pyautogui|SetForegroundWindow|mouse_event"),
]
_TOUCH_RX = [(k, re.compile(p, re.I)) for k, p in TOUCHES]
WORD = re.compile(r"[a-z0-9]+")


def _first_para(text, limit=400):
    para = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    para = re.sub(r"\s+", " ", para).strip()
    return para[:limit] + ("…" if len(para) > limit else "")


def _leading_comments(text, prefixes=("#", "//", "::", "REM", "rem")):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s and not out:
            continue
        if s.startswith("#!") or s.startswith("@echo") or s.lower().startswith("# -*-"):
            continue
        hit = next((p for p in prefixes if s.startswith(p)), None)
        if hit is None:
            break
        out.append(s[len(hit):].strip(" =-*!/#"))
    return "\n".join(out).strip()


def _python(text):
    info = {"functions": [], "options": [], "needs": [], "doc": ""}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return info
    info["doc"] = ast.get_docstring(tree) or ""
    info["functions"] = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and not n.name.startswith("_")]
    needs = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            needs |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            needs.add(n.module.split(".")[0])
        elif isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_argument":
            for a in n.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    info["options"].append(a.value)
    info["needs"] = sorted(m for m in needs if m not in STDLIB and m not in ("__future__",))
    return info


def _powershell(text):
    help_block = re.search(r"<#(.*?)#>", text, re.S)
    doc = ""
    if help_block:
        h = help_block.group(1)
        syn = re.search(r"\.SYNOPSIS\s*(.*?)(?=\n\s*\.[A-Z]|\Z)", h, re.S)
        desc = re.search(r"\.DESCRIPTION\s*(.*?)(?=\n\s*\.[A-Z]|\Z)", h, re.S)
        doc = "\n\n".join(x.group(1).strip() for x in (syn, desc) if x) or h.strip()
    if not doc:
        doc = _leading_comments(text, ("#",))
    params = re.search(r"param\s*\((.*?)\)\s*\n", text, re.S | re.I)
    options = re.findall(r"\$([A-Za-z]\w*)", params.group(1)) if params else []
    functions = re.findall(r"^\s*function\s+([\w-]+)", text, re.M | re.I)
    return {"doc": doc, "options": sorted(set(options)), "functions": functions, "needs": []}


def _other(text, lang):
    if lang == "rust":
        doc = "\n".join(l.strip()[3:].strip() for l in text.splitlines() if l.strip().startswith("//!"))
        fns = re.findall(r"^\s*pub\s+fn\s+(\w+)", text, re.M)
    elif lang == "shell":
        doc = _leading_comments(text, ("#",))
        fns = re.findall(r"^\s*(?:function\s+)?(\w+)\s*\(\)\s*\{", text, re.M)
    elif lang == "batch":
        doc = _leading_comments(text, ("::", "REM", "rem"))
        fns = []
    else:
        m = re.match(r"\s*/\*(.*?)\*/", text, re.S)
        doc = m.group(1) if m else _leading_comments(text, ("//",))
        fns = []
    return {"doc": doc, "functions": fns[:30], "options": [], "needs": []}


def index_file(path, root):
    path = Path(path)
    lang = LANG.get(path.suffix.lower())
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if lang == "python":
        info = _python(text)
    elif lang == "powershell":
        info = _powershell(text)
    else:
        info = _other(text, lang)
    doc = info.pop("doc").strip()
    rel = path.relative_to(root).as_posix()
    entry = {"id": rel, "source": rel.split("/")[0], "language": lang, "lines": text.count("\n") + 1,
             "title": re.sub(r"\s+", " ", doc.splitlines()[0]).strip()[:140] if doc else "",
             "summary": _first_para(doc) if doc else "",
             "touches": [k for k, rx in _TOUCH_RX if rx.search(text)],
             "portable": not HOME_PATH.search(text),
             "is_test": path.name.startswith("test_") or "/tests/" in f"/{rel}",
             **info}
    if lang == "python":
        safe, why = probe_safety(path)
        entry["probe"] = "safe" if safe else ("library" if why.startswith("library") else
                                              "runs-on-load" if why.startswith("RUNS") else "no-cli")
    return entry


def build(root):
    root = Path(root)
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in LANG
                   and not ({"__pycache__", ".git", "target", "node_modules"} & set(p.parts)))
    return [index_file(p, root) for p in files]


def _tokens(s):
    return WORD.findall(s.lower())


def find(index, query, limit=10):
    """Rank scripts for a plain-words query. Title words count most, then summary, names, options."""
    q = [w for w in _tokens(query) if len(w) > 2]
    scored = []
    for e in index:
        if e.get("is_test"):
            continue
        fields = [(e["title"], 4), (e["id"], 3), (" ".join(e["functions"]), 2), (e["summary"], 2),
                  (" ".join(e["options"]), 1), (" ".join(e["touches"]), 1)]
        score = 0
        for text, w in fields:
            toks = set(_tokens(text))
            score += w * sum(1 for t in q if t in toks or any(x.startswith(t) for x in toks))
        if score:
            scored.append((score, e))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [dict(e, score=s) for s, e in scored[:limit]]


def to_markdown(index):
    out = ["# Harvest index", "",
           f"{sum(1 for e in index if not e['is_test'])} scripts, {sum(1 for e in index if e['is_test'])} tests. "
           "One line each: path - what it does [language, lines, touches].", ""]
    by_src = {}
    for e in index:
        by_src.setdefault(e["source"], []).append(e)
    for src, items in by_src.items():
        tools = [e for e in items if not e["is_test"]]
        out.append(f"## {src} ({len(tools)} scripts, {len(items) - len(tools)} tests)")
        for e in tools:
            flags = ", ".join([e["language"] or "?", f"{e['lines']} lines"] + e["touches"][:4]
                              + ([] if e["portable"] else ["hard-coded home"]))
            out.append(f"- `{e['id']}` - {e['title'] or '(no description)'} [{flags}]")
        out.append("")
    return "\n".join(out)
