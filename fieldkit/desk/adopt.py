"""fieldkit adopt - move a tool out of the Documents/Scripts dumping ground INTO the harness, and repoint everything.

    fieldkit adopt NAME [NAME...] [--into GROUP] [--apply] [--json]
    fieldkit adopt --finish [--apply]

Scripts is where a session drops a script so it survives the session; it is not the harness (owner, 2026-10-13: it
should be empty after each weekly sweep). Adopting NAME (a folder, or loose files grouped with --into) moves it to
local/kits/<name> - the owner's own tools, git-ignored like the rest of local/ - and repoints what used the old place:

  - cards in local/tools.yaml           ${DOCUMENTS}/Scripts/NAME  ->  ${FIELDKIT}/local/kits/NAME
  - text that names the old path         skills (~/.claude, ~/.gemini, local/skills), memory files, CLAUDE.md, GEMINI.md,
                                         other tools in Scripts and local/kits (a sibling's hard-coded path)
  - Windows shortcuts                    Desktop, Start menu and Startup .lnk: target, arguments, working folder, icon
  - a running process from the old place cannot be moved under it: the folder is COPIED, everything is repointed, and
                                         the old copy gets an .adopted marker; `--finish` removes it once nothing runs
                                         from it (the sweep counts a marked folder as done)

Without --apply it only prints the plan. With --apply: every file it edits is backed up first (state/adopt/<time>/),
the copy is checked file by file, the moved tools' card tests run, and ANY failure restores the backups and removes the
copy (fail closed). Tests find tools through local/tests/_owner.py (local/kits first), so they need no edit.
Public code that names a Scripts path is listed as manual: it changes by a reviewed commit, never by this command.
"""
import datetime as dt
import filecmp
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..core import settings

MARKER = ".adopted"
TEXT_EXT = {".md", ".txt", ".py", ".ps1", ".cmd", ".bat", ".json", ".yaml", ".yml", ".sh", ".toml", ".ini"}


def scripts_root():
    return Path(settings.expand("${DOCUMENTS}")) / "Scripts"


def kits_root():
    return settings.ROOT / "local" / "kits"


def text_roots():
    home = Path.home()
    roots = [home / ".claude" / "skills", home / ".gemini" / "skills", settings.ROOT / "local" / "skills",
             home / ".claude" / "CLAUDE.md", home / ".gemini" / "GEMINI.md", scripts_root(), kits_root()]
    roots += sorted((home / ".claude" / "projects").glob("*/memory"))
    return roots


def lnk_roots():
    home, appdata = Path.home(), Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    start = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return [home / "Desktop", start, start / "Startup"]


# -- path forms ------------------------------------------------------------------------------------------
def forms(name, dest=None):
    """(regex, replacement) pairs turning every spelling of Scripts/NAME into local/kits/DEST (DEST defaults to NAME;
    a loose file adopted into a group folder has DEST = GROUP/NAME). The card form comes first so cards get
    ${FIELDKIT}; any other spelling keeps its own prefix and separator."""
    dest = dest or name
    n = re.escape(name)
    end = r"""(?=$|[\\/"'\s,;)\]}])"""                   # NAME ends here: a separator, a quote or the end
    back = dest.replace("/", "\\")
    quoted = " / ".join(f'"{x}"' for x in ["Fieldkit", "local", "kits"] + dest.split("/"))
    return [
        (re.compile(r"\$\{DOCUMENTS\}/Scripts/" + n + end), lambda m: "${FIELDKIT}/local/kits/" + dest),
        (re.compile(r"Scripts\\" + n + end), lambda m: "Scripts\\Fieldkit\\local\\kits\\" + back),
        (re.compile(r"Scripts/" + n + end), lambda m: "Scripts/Fieldkit/local/kits/" + dest),
        (re.compile(r'"Scripts" / "' + n + '"'), lambda m: '"Scripts" / ' + quoted),
    ]


def rewrite(text, names, dests=None):
    """Rewrite every old spelling of each name; dests maps a name to its place under local/kits."""
    count = 0
    for name in names:
        for rx, rep in forms(name, (dests or {}).get(name)):
            text, k = rx.subn(rep, text)
            count += k
    return text, count


# -- discovery -------------------------------------------------------------------------------------------
def _text_files(root, skip):
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in {".git", "__pycache__", "node_modules", ".venv", "venv", "results"}
                  and not any(Path(dp, d) == s or s in Path(dp, d).parents for s in skip)]
        for f in fns:
            p = Path(dp, f)
            if p.suffix.lower() in TEXT_EXT and p.stat().st_size < 2 * 2**20:
                yield p


def text_hits(names, skip=()):
    """Files outside the moved tools that name them. Fieldkit itself is never walked from the Scripts root (its
    public code is listed by public_hits for a reviewed commit, local/tools.yaml is handled as the cards, state/ is
    derived); local/kits and local/skills are roots of their own."""
    hits = {}
    tools_yaml = (settings.ROOT / "local" / "tools.yaml").resolve()
    for root in text_roots():
        root = Path(root)
        exclude = [Path(s) for s in skip] + [settings.ROOT] + [scripts_root() / n for n in _sweep_ignored()]
        exclude = [s for s in exclude if not (root == s or s in root.parents)]
        for p in _text_files(root, exclude):
            if p.resolve() == tools_yaml:
                continue
            try:
                raw = p.read_bytes()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            _, k = rewrite(text, names)
            if k:
                hits[str(p)] = k
    return hits


def _sweep_ignored():
    """Scripts entries the owner set aside on purpose (sweep.ignore, e.g. a frozen review copy): never edited."""
    try:
        from . import sweep
        return [n for n in sweep.ignored() if n != "Fieldkit"]
    except Exception:
        return []


def _ps(script):
    if sys.platform != "win32":
        return ""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def shortcut_hits(old_paths):
    """[{lnk, field, value}] for every .lnk whose target/arguments/working folder/icon names an old path."""
    if sys.platform != "win32":
        return []
    roots = ";".join(str(r) for r in lnk_roots() if r.is_dir())
    # a foreach STATEMENT cannot feed a pipeline: @( ... ) collects it first (2026-10-13: the bare form found nothing)
    out = _ps("$sh = New-Object -ComObject WScript.Shell; @(foreach ($r in '" + roots.replace("'", "''") + "'.Split(';')) {"
              " Get-ChildItem -LiteralPath $r -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object {"
              " $l = $sh.CreateShortcut($_.FullName); [pscustomobject]@{lnk=$_.FullName; TargetPath=$l.TargetPath;"
              " Arguments=$l.Arguments; WorkingDirectory=$l.WorkingDirectory; IconLocation=$l.IconLocation} } })"
              " | ConvertTo-Json -Compress")
    try:
        rows = json.loads(out or "[]")
    except json.JSONDecodeError:
        return []
    rows = rows if isinstance(rows, list) else [rows]
    hits = []
    for row in rows:
        for field in ("TargetPath", "Arguments", "WorkingDirectory", "IconLocation"):
            v = row.get(field) or ""
            if any(o.lower() in v.lower() for o in old_paths):
                hits.append({"lnk": row["lnk"], "field": field, "value": v})
    return hits


def running_from(paths):
    """[(pid, command line)] of processes started from inside any of PATHS."""
    if sys.platform != "win32":
        return []
    out = _ps("Get-CimInstance Win32_Process | Where-Object CommandLine | Select-Object ProcessId, CommandLine"
              " | ConvertTo-Json -Compress")
    try:
        rows = json.loads(out or "[]")
    except json.JSONDecodeError:
        return []
    rows = rows if isinstance(rows, list) else [rows]
    low = [str(p).lower() for p in paths]
    return [(r["ProcessId"], r["CommandLine"]) for r in rows
            if any(l in (r.get("CommandLine") or "").lower() for l in low) and r["ProcessId"] != os.getpid()]


def public_hits(names):
    """Tracked files of the public repository that name an old Scripts path: listed, never edited here."""
    r = subprocess.run(["git", "-C", str(settings.ROOT), "ls-files"], capture_output=True, text=True)
    hits = []
    for rel in r.stdout.splitlines():
        p = settings.ROOT / rel
        if p.suffix.lower() not in TEXT_EXT or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if rewrite(text, names)[1]:
            hits.append(rel)
    return hits


# -- plan / apply ----------------------------------------------------------------------------------------
def plan(names, into=None):
    src = scripts_root()
    sources = [src / n for n in names]
    missing = [str(s) for s in sources if not s.exists()]
    if missing:
        return {"ok": False, "error": f"not in {src}: {', '.join(missing)}"}
    if into is None and any(s.is_file() for s in sources):
        return {"ok": False, "error": "loose files need --into GROUP (the folder they share in local/kits)"}
    dest = kits_root() / into if into else None
    moves = [(s, (dest / s.name) if dest else kits_root() / s.name) for s in sources]
    taken = [str(d) for _, d in moves if d.exists()]
    if taken:
        return {"ok": False, "error": f"already in local/kits: {', '.join(taken)}"}
    # what other places call these files: loose files keep their name inside the group folder
    keys = [s.name for s in sources]
    if into:
        rename = {s.name: f"{into}/{s.name}" for s in sources}   # loose files keep their name in the group
    else:
        rename = {}
    olds = [str(s) for s in sources]
    tools_yaml = settings.ROOT / "local" / "tools.yaml"
    cards = rewrite(tools_yaml.read_text(encoding="utf-8"), keys)[1] if tools_yaml.is_file() else 0
    return {"ok": True, "moves": [(str(a), str(b)) for a, b in moves], "keys": keys, "group": into, "rename": rename,
            "cards": cards, "text": text_hits(keys, skip=sources), "shortcuts": shortcut_hits(olds),
            "running": running_from(olds), "public": public_hits(keys)}


def _same_tree(a, b):
    if a.is_file():
        return filecmp.cmp(a, b, shallow=False)
    for dp, dns, fns in os.walk(a):
        dns[:] = [d for d in dns if d != "__pycache__"]
        for f in fns:
            pa = Path(dp, f)
            pb = b / pa.relative_to(a)
            if not pb.is_file() or not filecmp.cmp(pa, pb, shallow=False):
                return False
    return True


def _set_shortcut(lnk, field, old_paths, new_paths):
    pairs = ";".join(f"{o}|{n}" for o, n in zip(old_paths, new_paths)).replace("'", "''")
    _ps("$sh = New-Object -ComObject WScript.Shell; $l = $sh.CreateShortcut('" + lnk.replace("'", "''") + "');"
        " $v = $l." + field + "; foreach ($p in '" + pairs + "'.Split(';')) { $a,$b = $p.Split('|');"
        " $v = $v -ireplace [regex]::Escape($a), $b }; $l." + field + " = $v; $l.Save()")


def apply(p, run_tests=True, log=print):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = settings.ROOT / "state" / "adopt" / stamp
    backup.mkdir(parents=True, exist_ok=True)
    edited, made = [], []
    moves = [(Path(a), Path(b)) for a, b in p["moves"]]
    olds, news = [str(a) for a, _ in moves], [str(b) for _, b in moves]

    def keep(path):
        rel = hashlib.sha1(str(path).encode()).hexdigest()[:12] + "-" + Path(path).name
        shutil.copy2(path, backup / rel)
        edited.append((str(path), str(backup / rel)))

    def undo(why):
        for orig, saved in reversed(edited):
            if orig.endswith(".lnk.json"):
                continue
            shutil.copy2(saved, orig)
        for d in made:
            shutil.rmtree(d, ignore_errors=True) if Path(d).is_dir() else Path(d).unlink(missing_ok=True)
        (backup / "ROLLED-BACK.txt").write_text(why + "\n", encoding="utf-8")
        return {"ok": False, "error": why, "backup": str(backup)}

    try:
        for a, b in moves:                                   # 1. copy, then prove the copy
            b.parent.mkdir(parents=True, exist_ok=True)
            if a.is_dir():
                shutil.copytree(a, b, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(a, b)
            made.append(str(b))
            if not _same_tree(a, b):
                return undo(f"the copy of {a} differs from the original")
        keys = p["keys"]
        # the moved files themselves may name their own old place (a .cmd with an absolute path): fixed in the COPY
        own = [f for _, b in moves for f in ([b] if b.is_file() else _text_files(b, []))]
        targets = [settings.ROOT / "local" / "tools.yaml"] + [Path(f) for f in p["text"]]
        for f in targets:                                     # 2. repoint cards and text (backed up first)
            if not f.is_file():
                continue
            raw = f.read_bytes()
            text = raw.decode("utf-8")
            new, n = rewrite(text, keys, p['rename'])
            if n:
                keep(f)
                crlf = b"\r\n" in raw
                f.write_bytes((new.replace("\r\n", "\n").replace("\n", "\r\n") if crlf else new).encode("utf-8"))
        for f in own:                                         # (inside the copy: no backup needed, it is new)
            try:
                raw = Path(f).read_bytes()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            new, n = rewrite(text, keys, p["rename"])
            if n:
                crlf = b"\r\n" in raw
                Path(f).write_bytes((new.replace("\r\n", "\n").replace("\n", "\r\n") if crlf else new).encode("utf-8"))
        for s in p["shortcuts"]:                              # 3. repoint shortcuts
            keep(s["lnk"])
            _set_shortcut(s["lnk"], s["field"], olds, news)
        if run_tests:                                         # 4. the moved tools' own tests
            from . import registry
            ids = [c["id"] for c in registry.load() if any(str(Path(c.get("path") or "")).lower().startswith(n.lower())
                                                            for n in news)]
            if ids:
                res = registry.check(run_tests=True, only=ids)
                bad = [r.get("id") for r in res if r.get("test_ok") is False]
                if bad:
                    return undo(f"tests failed after the move: {', '.join(bad)}")
        running = p["running"]                                # 5. the old copy: removed, or marked
        for a, b in moves:
            if running:
                marker = (a if a.is_dir() else a.parent) / MARKER
                if a.is_dir():
                    marker.write_text(f"adopted {stamp} into {b}\nremove with: fieldkit adopt --finish --apply\n",
                                      encoding="utf-8")
            else:
                shutil.rmtree(a) if a.is_dir() else a.unlink()
        (backup / "PLAN.json").write_text(json.dumps(p, indent=1, default=str), encoding="utf-8")
        return {"ok": True, "moved": p["moves"], "edited": [e for e, _ in edited], "backup": str(backup),
                "old_copy_kept_while_running": [pid for pid, _ in running]}
    except Exception as e:                                    # anything unexpected: put everything back
        return undo(f"{type(e).__name__}: {e}")


def finish(do_apply=False):
    """Old copies marked .adopted whose processes have ended: listed, and removed with --apply."""
    out = []
    for m in sorted(scripts_root().glob(f"*/{MARKER}")):
        d = m.parent
        busy = running_from([str(d)])
        out.append({"folder": str(d), "running": [pid for pid, _ in busy], "removed": False})
        if do_apply and not busy:
            shutil.rmtree(d)
            out[-1]["removed"] = True
    return out


def lines(p):
    if not p.get("ok"):
        return [f"REFUSED: {p.get('error')}"]
    out = [f"move  {a}\n   -> {b}" for a, b in p["moves"]]
    out.append(f"cards repointed in local/tools.yaml: {p['cards']}")
    out += [f"text  {f} ({n})" for f, n in sorted(p["text"].items())]
    out += [f"lnk   {s['lnk']} [{s['field']}]" for s in p["shortcuts"]]
    out += [f"RUNNING from the old place: PID {pid}: copy + repoint now, old copy removed by --finish after restart"
            for pid, _ in p["running"]]
    out += [f"MANUAL (public code, a reviewed commit): {h}" for h in p["public"]]
    return out
