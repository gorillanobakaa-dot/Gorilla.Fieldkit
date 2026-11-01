"""The integration check: read a repository's real registries, hold every capability to every surface in
surfaces.yaml, print one line per missing surface with the file to fix. Deterministic, offline, no OS-specific
calls (git is the only program it may start, and only for --changed)."""
import json
import re
import subprocess
from pathlib import Path

import yaml

RULES_FILE = Path(__file__).with_name("surfaces.yaml")


def load_rules():
    return yaml.safe_load(RULES_FILE.read_text(encoding="utf-8"))


class Repo:
    """A folder read through small cached helpers; paths are always POSIX-style relative strings."""

    def __init__(self, root):
        self.root = Path(root)
        self._text = {}

    def text(self, rel):
        if rel not in self._text:
            p = self.root / rel
            try:
                self._text[rel] = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
            except OSError:
                self._text[rel] = None
        return self._text[rel]

    def exists(self, rel):
        return (self.root / rel).exists()

    def glob(self, pattern):
        if not any(c in pattern for c in "*?["):
            return [pattern] if (self.root / pattern).is_file() else []
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.glob(pattern) if p.is_file())

    def git(self, *args):
        r = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return r.returncode, r.stdout


def detect_kind(repo, rules):
    for kind, cfg in rules.items():
        if repo.exists(cfg["repo_marker"]):
            return kind
    return None


def _norm(s):
    return re.sub(r"[-_.\s]", "", s.lower())


def _helper_name(rel):
    n = rel.rsplit("/", 1)[-1]
    for ext in (".gz", ".exe"):
        if n.endswith(ext):
            n = n[: -len(ext)]
    return n


def parse_sums(text):
    """-> [(sha, 'goos-goarch/file')] from a SHA256SUMS text; comments and blanks skipped."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"([0-9a-fA-F]{64})\s+\*?(\S+)", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


# --- discovery: which capabilities does the repository really have ---------------------------------------------

def _expand(path):
    return path.replace("${FIELDKIT}/", "").replace("${FIELDKIT}\\", "")


def load_cards(repo, rel):
    text = repo.text(rel)
    if text is None:
        return {}
    data = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}
    return {str(c["id"]): c for c in data.get("tools", []) if isinstance(c, dict) and c.get("id")}


def card_command(card):
    entry = card.get("entry") or []
    if isinstance(entry, list) and "fieldkit" in entry:
        i = entry.index("fieldkit")
        if i + 1 < len(entry):
            return str(entry[i + 1])
    return str(card["id"])


def discover(repo, cfg, cap):
    """-> {name: {"card": dict | None}} in file order."""
    found = {}
    for src in cap["discover"]:
        t = src["type"]
        if t == "sums":
            for _, rel in parse_sums(repo.text(src["file"])):
                found.setdefault(_helper_name(rel), {})
        elif t == "bin_dirs":
            base = repo.root / src["dir"]
            if base.is_dir():
                for d in sorted(p for p in base.iterdir() if p.is_dir()):
                    for f in sorted(p for p in d.iterdir() if p.is_file() and not p.name.lower().startswith("readme")):
                        found.setdefault(_helper_name(f.name), {})
        elif t == "regex_names":
            for n in re.findall(src["regex"], repo.text(src["file"]) or ""):
                found.setdefault(n, {})
        elif t == "cards":
            for cid, card in load_cards(repo, src["file"]).items():
                found.setdefault(cid, {"card": card})
        elif t == "packages":
            base = repo.root / src["dir"]
            plumbing = set(cfg.get("plumbing", []))
            if base.is_dir():
                for d in sorted(p for p in base.iterdir() if p.is_dir()):
                    if (d / "__init__.py").is_file() and not d.name.startswith("_") and d.name not in plumbing:
                        found.setdefault(d.name, {})
    return found


# --- --changed: only what the diff adds or changes -------------------------------------------------------------

def changed_names(repo, base, cap):
    """The names of this capability type that `git diff BASE` adds or changes."""
    names = set()
    for ch in cap.get("changed", []):
        k, f = ch["kind"], ch["file"]
        if k in ("sums_added", "regex_added"):
            rc, out = repo.git("diff", base, "-U0", "--", f)
            added = [l[1:] for l in out.splitlines() if l.startswith("+") and not l.startswith("+++")]
            if k == "sums_added":
                names |= {_helper_name(r) for _, r in parse_sums("\n".join(added))}
            else:
                for l in added:
                    m = re.search(ch["regex"].replace("^\\+", "^"), l)
                    if m:
                        names.add(m.group(1))
        elif k == "cards_changed":
            rc, old = repo.git("show", f"{base}:{f}")
            before = {}
            if rc == 0:
                before = {str(c["id"]): c for c in (yaml.load(old, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
                                                   or {}).get("tools", []) if isinstance(c, dict) and c.get("id")}
            for cid, card in load_cards(repo, f).items():
                if before.get(cid) != card:
                    names.add(cid)
        elif k == "packages_added":
            rc, out = repo.git("diff", "--name-status", base, "--", f)
            for l in out.splitlines():
                m = re.match(r"A\s+" + re.escape(f) + r"/([^/]+)/__init__\.py$", l.strip())
                if m:
                    names.add(m.group(1))
        elif k == "bin_changed":
            rc, out = repo.git("diff", "--name-only", base, "--", f)
            names |= {_helper_name(l) for l in out.splitlines() if l.strip()}
    return names


# --- rules -----------------------------------------------------------------------------------------------------

def _sub(s, name, command):
    return (s or "").replace("{name}", name).replace("{command}", command)


def _esc_sub(s, name, command):
    return (s or "").replace("{name}", re.escape(name)).replace("{command}", re.escape(command))


def fix_file(rule, name, command):
    if rule.get("create"):
        return rule["create"]
    if rule.get("files"):
        return rule["files"][0]
    if rule.get("path"):
        return _sub(rule["path"], name, command)
    if rule.get("file"):
        return rule["file"]
    if rule.get("glob"):
        return rule["glob"]
    return "fieldkit/desk/tools.yaml"


def _newest(files):
    def key(f):
        m = re.search(r"v(\d+)\.(\d+)\.(\d+)", f)
        return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
    return max(files, key=key) if files else None


def manifest_entries(repo, rel):
    try:
        data = json.loads(repo.text(rel) or "")
    except ValueError:
        return []
    out = []

    def walk(o):
        if isinstance(o, dict):
            if "id" in o and "detect" in o:
                out.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out


def manifest_entry(repo, rule, name, aliases):
    want = {_norm(name)} | {_norm(a) for a in aliases.get(name, [])}
    for e in manifest_entries(repo, rule["file"]):
        bins = {_norm(_helper_name(b)) for b in (e.get("detect") or {}).get("binaries", [])}
        if _norm(str(e["id"])) in want or bins & want:
            return e
    return None


def check_rule(repo, cfg, rule, name, card=None):
    """-> None when the surface holds, else the sentence saying what is missing."""
    t = rule["type"]
    command = card_command(card) if card else name
    what = rule.get("what", "missing")
    if t == "word_in":
        pat = _esc_sub(rule.get("pattern"), name, command) if rule.get("pattern") else None
        tok = command if rule.get("token") == "command" else name
        rx = re.compile(pat or r"(?<![\w-])" + re.escape(tok) + r"(?![\w-])", re.I)
        for f in rule["files"]:
            for rel in repo.glob(f):
                if rx.search(repo.text(rel) or ""):
                    return None
        return what
    if t == "exists":
        return None if repo.exists(_sub(rule["path"], name, command)) else what
    if t == "manifest":
        return None if manifest_entry(repo, rule, name, cfg.get("aliases", {})) else what
    if t == "systems":
        sums = parse_sums(repo.text(rule.get("sums", "internal/toolbox/SHA256SUMS")))
        e = manifest_entry(repo, {"file": rule.get("manifest", "internal/arsenal/manifest.json")}, name,
                           cfg.get("aliases", {}))
        said = json.dumps((e or {}).get("absent_on", "")).lower()
        lacking = []
        for system, prefix in cfg["systems"].items():
            has = any(r.startswith(prefix) and _helper_name(r) == name for _, r in sums)
            if not has and system not in said:
                lacking.append(system)
        return f"{what}: {', '.join(lacking)}" if lacking else None
    if t == "block":
        newest = _newest(repo.glob(rule["glob"]))
        if not newest:
            return f"{what} (no release page found)"
        txt = repo.text(newest) or ""
        i, j = txt.find(rule["begin"]), txt.find(rule["end"])
        block = txt[i:j] if 0 <= i < j else ""
        return None if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", block, re.I) else f"{what} ({newest})"
    if t == "kind_has":
        rx = re.compile(rule["regex"])
        return None if any(rx.search(repo.text(r) or "") for f in rule["files"] for r in repo.glob(f)) else what
    if card is None:
        return None
    if t == "card_field":
        v = card.get(rule["field"])
        return None if (v and str(v).strip()) else what
    if t == "card_path":
        p = card.get("path")
        if not p:
            return what
        if "${" in _expand(str(p)):
            return None                      # lives outside this repository (${DOCUMENTS}...): not ours to check
        return None if repo.exists(_expand(str(p))) else what
    if t == "card_test":
        test = card.get("test") or []
        files = [_expand(str(x)) for x in test if str(x).endswith(".py")]
        return None if any(repo.exists(f) for f in files) else what
    if t == "card_platforms":
        pl = {str(x).lower() for x in (card.get("platforms") or [])}
        need = set(cfg.get("required_systems", []))
        return None if need <= pl or card.get("platforms_why") else what
    return f"unknown rule type {t!r} in surfaces.yaml"


def check_package_card(repo, cfg, rule, name):
    for card in load_cards(repo, "fieldkit/desk/tools.yaml").values():
        p = _expand(str(card.get("path", "")))
        if p == f"fieldkit/{name}" or p.startswith(f"fieldkit/{name}/") or p == f"fieldkit/{name}.py":
            return None
    return rule.get("what")


def run_check(root, base=None, kind=None):
    """-> {"ok", "kind", "checked", "gaps": [{kind, capability, surface, what, fix}]}"""
    repo = Repo(root)
    rules = load_rules()
    kind = kind or detect_kind(repo, rules)
    if kind is None:
        return {"ok": False, "kind": None, "checked": [], "gaps": [], "error": "not a Gorilla.Opencode or Fieldkit repo"}
    cfg = rules[kind]
    gaps, checked, kind_done = [], [], set()
    anything = False
    for ctype, cap in cfg["capabilities"].items():
        caps = discover(repo, cfg, cap)
        if base:
            wanted = changed_names(repo, base, cap)
            caps = {n: v for n, v in caps.items() if n in wanted}
        for name, info in caps.items():
            anything = True
            checked.append(f"{ctype}:{name}")
            card = info.get("card")
            command = card_command(card) if card else name
            for rule in cap["surfaces"]:
                if rule.get("scope") == "kind":
                    continue
                if rule["type"] == "package_card":
                    msg = check_package_card(repo, cfg, rule, name)
                else:
                    msg = check_rule(repo, cfg, rule, name, card)
                if msg:
                    gaps.append({"kind": ctype, "capability": name, "surface": rule["id"], "what": msg,
                                 "fix": fix_file(rule, name, command)})
    if anything or not base:
        for ctype, cap in cfg["capabilities"].items():
            for rule in cap["surfaces"]:
                if rule.get("scope") == "kind" and rule["id"] not in kind_done:
                    kind_done.add(rule["id"])
                    msg = check_rule(repo, cfg, rule, "*")
                    if msg:
                        gaps.append({"kind": ctype, "capability": "*", "surface": rule["id"], "what": msg,
                                     "fix": fix_file(rule, "*", "*")})
    return {"ok": not gaps, "kind": kind, "checked": checked, "gaps": gaps}
