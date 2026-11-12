# Version: 1.0.0 · updated 26-11-12-12-00
"""The OS parity inventory: every tool card limited to one system names its counterpart, its port, or why it is native.

Owner, 2026-11-12: "the same capabilities we have on Windows, I want on Linux ... it must be the same on the Linux
side." 42 cards were Windows-only and 32 Linux-only. This module holds the checked list (desk/os_parity.yaml):

  EQUIVALENT   the other system has a card or command that does the job (`counterpart`)
  PORT         doable: the other system's mechanism is named (`mechanism`), `order` says what to port first
  NATIVE-ONLY  a hardware or OS feature that does not exist on the other side (`reason`, a sentence a reviewer accepts)

platformcheck.check() calls problems(): a NEW single-system card fails CI until it has a row here (or is widened).

    fieldkit parity os [--json] [--write] [--since REF] [--briefs DIR] [--live]
        --write     regenerate docs/PLATFORMS.md          --since REF   only the records a branch touched
        --briefs    one data-only brief per batch of gaps  --live       also which external programs this machine has
    Reads and reports only (no model, no edits); exit 3 while gaps or problems exist. Execution is `fieldkit task start BRIEF`.
"""
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA = HERE / "os_parity.yaml"
DOC = ROOT / "docs" / "PLATFORMS.md"
STATUSES = ("EQUIVALENT", "PORT", "NATIVE-ONLY")
_NEED = {"EQUIVALENT": "counterpart", "PORT": "mechanism", "NATIVE-ONLY": "reason"}
MIN_WORDS = 4


def load(path=None):
    p = Path(path or DATA)
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("rows") or {}


def _single(card):
    from . import platformcheck
    return platformcheck.single(card)


def problems(all_cards, rows=None, strict_stale=True):
    """-> [str]: a single-system card with no valid row; a row for a card that is gone or now runs on both."""
    rows = load() if rows is None else rows
    out, seen = [], set()
    by_id = {c.get("id"): c for c in all_cards}
    for c in all_cards:
        one = _single(c)
        if not one:
            continue
        cid = c.get("id")
        seen.add(cid)
        row = rows.get(cid)
        if not row:
            other = "Linux" if one == "windows" else "Windows"
            out.append(f"{cid}: {one} only and not in desk/os_parity.yaml - name its counterpart (EQUIVALENT), its "
                       f"{other} mechanism (PORT) or a reason it is native (NATIVE-ONLY), or port it")
            continue
        st = row.get("status")
        if st not in STATUSES:
            out.append(f"{cid}: os_parity status {st!r} (allowed: {', '.join(STATUSES)})")
            continue
        text = str(row.get(_NEED[st]) or "").strip()
        if len(text.split()) < (1 if st == "EQUIVALENT" else MIN_WORDS):
            out.append(f"{cid}: os_parity {st} needs a real `{_NEED[st]}` (got {text!r})")
        elif st == "EQUIVALENT" and text in by_id and _single(by_id[text]) == one:
            out.append(f"{cid}: its counterpart {text} runs only on {one} too")
        if st == "PORT" and not isinstance(row.get("order"), int):
            out.append(f"{cid}: os_parity PORT needs an integer `order` (1 = first)")
    if strict_stale:
        for cid in sorted(set(rows) - seen):
            out.append(f"{cid}: in os_parity.yaml but not a single-system card any more - delete the row (ported?)")
    return out


def inventory(all_cards=None, rows=None):
    from . import platformcheck
    all_cards = platformcheck.cards() if all_cards is None else all_cards
    rows = load() if rows is None else rows
    out = []
    for c in all_cards:
        one = _single(c)
        if not one:
            continue
        r = rows.get(c["id"]) or {}
        st = r.get("status", "MISSING")
        out.append({"id": c["id"], "only": one, "purpose": " ".join(str(c.get("title") or "").split()),
                    "status": st, "detail": r.get(_NEED.get(st, ""), ""), "order": r.get("order")})
    out.sort(key=lambda x: (x["only"], {"PORT": 0, "EQUIVALENT": 1, "NATIVE-ONLY": 2}.get(x["status"], 3),
                            x["order"] or 99, x["id"]))
    return out


# ---- the inventory beyond cards (owner spec 2026-11-12): static, from files in the repo, same on both systems ------
FIELDS = ("id", "kind", "name", "present_on", "missing_on", "status", "evidence", "action", "target", "mechanism",
          "effort", "proof", "order")
REC_STATUS = ("MISSING", "PARTIAL", "INCOMPATIBLE", "NATIVE-ONLY")
ACTIONS = ("CREATE", "PORT", "ADAPT", "WRAP", "DOCUMENT")
_OSNAME = {"windows": "linux", "linux": "windows"}
_SKILL_OS = {"windows": re.compile(r"\bwindows\b|powershell|\.exe\b|\bregistry\b", re.I),
             "linux": re.compile(r"\blinux\b|\bdebian\b|systemd|\.deb\b|\bapt\b", re.I)}
_GUARD = re.compile(r"^\s+if (not )?(sys\.platform (!=|==) .win32.|os\.name (!=|==) .nt.|not _?is_windows\(\)|"
                    r"_?is_windows\(\))[^\n]*:\s*$", re.M)
_LINUX_BRANCH = re.compile(r"sys\.platform\.startswith\(.linux|sys\.platform == .linux|platform\.system\(\) == .Linux|def \w*linux\w*\(")
_ONE_SIDED = re.compile(r"(return( \[\]| None| \{\}| False| \"\")?|raise .*|pass)$")


def _rel(p):
    return Path(p).resolve().relative_to(ROOT).as_posix()


def _norm(plats):
    s = {("linux" if p == "debian" else p) for p in plats or []} & {"windows", "linux"}
    return s


def _item(kind, name, present, evidence):
    present = sorted(present)
    return {"id": f"{kind}:{name}", "kind": kind, "name": name, "present_on": present,
            "missing_on": sorted(set(_OSNAME.values()) & {_OSNAME[p] for p in present}), "evidence": evidence}


def _line(text, needle):
    i = text.find(needle)
    return text.count("\n", 0, i) + 1 if i >= 0 else 1


def scan_stages(root=None):
    """Pipelines and emerge recipes whose stages carry `platforms:` of one system -> one item per file."""
    root = Path(root or ROOT)
    out = []
    for pat in ("fieldkit/build/pipelines/*.yaml", "fieldkit/emerge/recipes/*.yaml"):
        for f in sorted(root.glob(pat)):
            text = f.read_text(encoding="utf-8")
            try:
                d = yaml.safe_load(text) or {}
            except yaml.YAMLError:
                continue
            stages = d.get("stages") if isinstance(d, dict) else None
            seen = set()
            for s in stages if isinstance(stages, list) else []:
                if isinstance(s, dict) and s.get("platforms"):
                    n = _norm(s["platforms"])
                    seen |= n
            if len(seen) == 1:
                kind = "stage"
                out.append(_item(kind, f.stem, seen, f"{f.relative_to(root).as_posix()}:{_line(text, 'platforms:')}"))
    return out


def scan_skills(root=None):
    root = Path(root or ROOT)
    out = []
    for f in sorted(root.glob("skills/*/SKILL.md")):
        text = f.read_text(encoding="utf-8")
        m = re.search(r"^description:\s*(.*?)\n(?=[A-Za-z][\w-]*:)", text, re.S | re.M)
        d = m.group(1) if m else ""
        have = {p for p, rx in _SKILL_OS.items() if rx.search(d)}
        if len(have) == 1:
            out.append(_item("skill", f.parent.name, have, f"{f.relative_to(root).as_posix()}:{_line(text, 'description:')}"))
    return out


def scan_workflows(root=None):
    root = Path(root or ROOT)
    out = []
    for f in sorted(root.glob(".github/workflows/*.yml")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s+if: runner\.os == '(Linux|Windows)'", text, re.M):
            ln = text.count("\n", 0, m.start()) + 1
            name = next((l.split("name:", 1)[1].strip() for l in reversed(text[:m.start()].splitlines()[-4:])
                         if "name:" in l), f"line{ln}").strip()
            out.append(_item("workflow", f"{f.name}:{name[:40].rstrip()}", {m.group(1).lower()},
                             f"{f.relative_to(root).as_posix()}:{ln}"))
    return out


def scan_hooks(root=None):
    root = Path(root or ROOT)
    out = []
    names = {p.name for p in (root / "hooks").glob("*")} if (root / "hooks").is_dir() else set()
    for n in sorted(names):
        stem, ext = Path(n).stem, Path(n).suffix
        if ext == ".sh" and f"{stem}.ps1" not in names:
            out.append(_item("hook", n, {"linux"}, f"hooks/{n}:1"))
        if ext == ".ps1" and f"{stem}.sh" not in names:
            out.append(_item("hook", n, {"windows"}, f"hooks/{n}:1"))
    return out


def scan_modules(root=None):
    """Modules with a sys.platform / os.name guard that returns nothing (or raises) on ONE side: the other side is a stub.
    First such guard per file. Only the plain guard shapes; a heuristic, reported as PARTIAL."""
    root = Path(root or ROOT)
    out = []
    for f in sorted((root / "fieldkit").rglob("*.py")):
        text = f.read_text(encoding="utf-8", errors="replace")
        if _LINUX_BRANCH.search(text):                  # the file has its own Linux side: not a stub
            continue
        for m in _GUARD.finditer(text):
            nxt = next((l.strip() for l in text[m.end():].split("\n") if l.strip()), "")
            if not _ONE_SIDED.match(nxt):
                continue
            g = m.group(0)
            win_true = bool(re.search(r"==|\bis_windows\(\)", g)) != bool(m.group(1))        # condition true on Windows
            present = "linux" if win_true else "windows"                                      # the side that returns early is the stub
            out.append(_item("module", f.relative_to(root).as_posix(), {present}, f"{f.relative_to(root).as_posix()}:"
                             f"{text.count(chr(10), 0, m.start()) + 1}"))
            break
    return out


def scan_all(root=None):
    return scan_stages(root) + scan_skills(root) + scan_workflows(root) + scan_hooks(root) + scan_modules(root)


def load_items(path=None):
    p = Path(path or DATA)
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("items") or {}


def _card_line(cid):
    f = HERE / "tools.yaml"
    for i, l in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if l.strip() in (f"- id: {cid}", f"id: {cid}"):
            return i
    return 1


def _first_fields(r, only, mech_default=""):
    return {"effort": r.get("effort", "M"), "proof": r.get("proof"), "order": r.get("order"),
            "target": r.get("target"), "mechanism": r.get("mechanism") or r.get("reason") or r.get("counterpart") or mech_default}


def records(all_cards=None, rows=None, items=None, found=None):
    """The fixed record per gap, every kind, sorted: -> [dict with FIELDS]. A detected item with no record is MISSING/CREATE."""
    from . import platformcheck
    all_cards = platformcheck.cards() if all_cards is None else all_cards
    rows = load() if rows is None else rows
    items = load_items() if items is None else items
    found = scan_all() if found is None else found
    recs = []
    for c in all_cards:
        one = _single(c)
        if not one:
            continue
        cid, r = c["id"], rows.get(c["id"]) or {}
        st = r.get("status")
        other = _OSNAME[one]
        status = {"PORT": "MISSING", "EQUIVALENT": "INCOMPATIBLE", "NATIVE-ONLY": "NATIVE-ONLY"}.get(st, "MISSING")
        action = {"PORT": "PORT", "EQUIVALENT": "DOCUMENT", "NATIVE-ONLY": "DOCUMENT"}.get(st, "CREATE")
        entry = " ".join(str(a) for a in (c.get("entry") or [])[:4] if "${" not in str(a)) or cid
        f = _first_fields(r, one)
        tgt = r.get("target") or (f"{entry} on {other}: {r.get('mechanism')}" if st == "PORT" else
                                  r.get("counterpart") or r.get("reason") or "")
        recs.append({"id": f"card:{cid}", "kind": "card", "name": cid, "present_on": [one], "missing_on": [other],
                     "status": status, "evidence": f"fieldkit/desk/tools.yaml:{_card_line(cid)}", "action": action,
                     "target": tgt, "mechanism": f["mechanism"], "effort": f["effort"],
                     "proof": f["proof"] or (f"a test of the {other} path with fixtures next to the card's test"
                                             if st == "PORT" else "none needed" if st else ""),
                     "order": r.get("order")})
    for it in found:
        r = items.get(it["id"]) or {}
        rec = dict(it)
        st = r.get("status", "MISSING")
        rec.update(status=st, action=r.get("action", "CREATE"), target=r.get("target") or "",
                   mechanism=r.get("mechanism") or r.get("reason") or "", effort=r.get("effort", "M"),
                   proof=r.get("proof") or "", order=r.get("order"))
        if r.get("present_on"):
            rec["present_on"] = sorted(r["present_on"])
            rec["missing_on"] = sorted(set(_OSNAME) - set(r["present_on"]))
        recs.append(rec)
    recs.sort(key=lambda x: (x["status"] == "NATIVE-ONLY", x["order"] or 99, x["kind"], x["id"]))
    return recs


def item_problems(items=None, found=None, strict_stale=True):
    items = load_items() if items is None else items
    found = scan_all() if found is None else found
    out, seen = [], set()
    for it in found:
        iid = it["id"]
        seen.add(iid)
        r = items.get(iid)
        if not r:
            out.append(f"{iid}: {'/'.join(it['present_on'])} only ({it['evidence']}) and not in desk/os_parity.yaml items: - "
                       f"give it status, action, target, mechanism, effort, proof, order or a NATIVE-ONLY reason")
            continue
        if r.get("status") not in REC_STATUS:
            out.append(f"{iid}: status {r.get('status')!r} (allowed: {', '.join(REC_STATUS)})")
        elif r["status"] == "NATIVE-ONLY":
            if len(str(r.get("reason") or "").split()) < MIN_WORDS:
                out.append(f"{iid}: NATIVE-ONLY needs a real `reason`")
        else:
            if r.get("action") not in ACTIONS:
                out.append(f"{iid}: action {r.get('action')!r} (allowed: {', '.join(ACTIONS)})")
            doc = r.get("action") == "DOCUMENT"            # a counterpart exists: only `target` (the counterpart) is needed
            for k in ("target",) if doc else ("target", "mechanism", "proof"):
                if not str(r.get(k) or "").strip():
                    out.append(f"{iid}: a gap record needs `{k}`")
            if not doc and r.get("effort") not in ("S", "M", "L"):
                out.append(f"{iid}: effort must be S, M or L")
            if not doc and not isinstance(r.get("order"), int):
                out.append(f"{iid}: needs an integer `order`")
    if strict_stale:
        for iid in sorted(set(items) - seen):
            out.append(f"{iid}: record in os_parity.yaml items: but the file no longer shows it single-system - delete it")
    return out


def gaps(recs):
    return [r for r in recs if r["status"] != "NATIVE-ONLY" and r["action"] != "DOCUMENT"]


def lists(recs):
    """The four lists the owner asked for."""
    g = {"windows_only": [], "linux_only": [], "different": [], "native_only": []}
    for r in recs:
        if r["status"] == "NATIVE-ONLY":
            g["native_only"].append(r["id"])
        elif r["status"] in ("PARTIAL", "INCOMPATIBLE"):
            g["different"].append(r["id"])
        elif r["present_on"] == ["windows"]:
            g["windows_only"].append(r["id"])
        else:
            g["linux_only"].append(r["id"])
    return g


def since(ref, recs):
    """Records whose evidence file changed between REF and HEAD (what a branch added or touched)."""
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", f"{ref}...HEAD"], capture_output=True, text=True)
    files = set(r.stdout.split())
    return [x for x in recs if x["evidence"].rsplit(":", 1)[0] in files]


def briefs(recs, outdir, batch=4):
    """One data-only brief per batch of gap records (same kind and order). Writes files; changes no code."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    groups = {}
    for r in gaps(recs):
        groups.setdefault((r["order"] or 99, r["kind"]), []).append(r)
    n = 0
    for (order, kind), rs in sorted(groups.items()):
        for i in range(0, len(rs), batch):
            n += 1
            chunk = rs[i:i + batch]
            lines = [f"# Parity batch {n:02d}: {kind} gaps, order {order}", "",
                     "Data only, written by `fieldkit parity os --briefs`. Execute with `fieldkit task start`.", ""]
            for r in chunk:
                lines += [f"## {r['id']}", ""] + [f"- {k}: {json.dumps(r[k], ensure_ascii=False)}" for k in FIELDS[2:]] + [""]
            (outdir / f"parity-{n:02d}-{kind}-o{order}.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return n


_LIVE = ("dpkg", "apt-get", "systemctl", "systemd-run", "journalctl", "loginctl", "cpupower", "readelf", "nft", "ss", "pkexec",
         "sudo", "powershell", "pwsh", "schtasks", "netsh", "powercfg", "reg", "wpr", "xrandr", "cec-ctl", "iw", "nmcli",
         "tcpdump", "debsums", "auditctl", "edid-decode")


def live():
    """Which external programs this machine has: a separate section, never part of the deterministic result."""
    import shutil
    return {p: bool(shutil.which(p)) for p in _LIVE}


def _cell(s, n=110):
    s = str(s).replace("|", "/")
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def markdown(inv, recs=None):
    n = {s: sum(1 for r in inv if r["status"] == s) for s in STATUSES}
    lines = ["# Platforms: what runs on Windows, what runs on Linux", "",
             "Generated by `fieldkit parity os --write` from `fieldkit/desk/os_parity.yaml`, the tool cards "
             "(`fieldkit/desk/tools.yaml`), the pipelines and recipes, the skills, the workflows, the hooks and the modules. "
             "Do not edit by hand. Same output on Windows and Linux: it reads files in the repo only.", "",
             f"{len(inv)} tool cards run on one system only: {n['PORT']} to port, {n['EQUIVALENT']} with an "
             f"equivalent on the other system, {n['NATIVE-ONLY']} native-only.", "",
             "- **PORT**: doable; the other system's mechanism is named, the lowest `#` goes first.",
             "- **EQUIVALENT**: the other system has a card or command that does the job.",
             "- **NATIVE-ONLY**: a hardware or OS feature that does not exist on the other side.", "",
             "A new single-system card, stage, skill, workflow step, hook or module guard fails CI until it has a record "
             "(`fieldkit/desk/platformcheck.py`, `fieldkit parity os`).", ""]
    for only, head in (("windows", "Windows-only cards (what Linux gets)"),
                       ("linux", "Linux-only cards (what Windows gets)")):
        lines += [f"## {head}", "", "| Card | Purpose | Status | # | Counterpart, mechanism or reason |",
                  "|---|---|---|---|---|"]
        for r in inv:
            if r["only"] == only:
                lines.append(f"| `{r['id']}` | {_cell(r['purpose'], 80)} | {r['status']} | {r['order'] or ''} | "
                             f"{_cell(r['detail'])} |")
        lines.append("")
    recs = [r for r in (records() if recs is None else recs) if r["kind"] != "card"]
    ls = lists(recs)
    lines += ["## Beyond cards: pipelines, skills, workflows, hooks, modules", "",
              f"{len(recs)} records: {len(ls['windows_only'])} Windows-only, {len(ls['linux_only'])} Linux-only, "
              f"{len(ls['different'])} on both but different, {len(ls['native_only'])} native-only.", "",
              "| Id | Present on | Missing on | Status | Action | Target / reason | Effort | # | Evidence |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in recs:
        lines.append(f"| `{_cell(r['id'], 60)}` | {','.join(r['present_on'])} | {','.join(r['missing_on'])} | {r['status']} | "
                     f"{r['action']} | {_cell(r['target'] or r['mechanism'], 90)} | {r['effort']} | {r['order'] or ''} | "
                     f"{r['evidence']} |")
    lines.append("")
    # one doc, two generators joined (batch 2026-11-12): crossos (PR 468) owns how each single-system command is
    # routed; its test looks for its table inside this doc, so the parity doc carries it as its last section
    from ..core import crossos
    lines += ["## How each single-system command is routed (core/crossos.py)", "", crossos.markdown(crossos.table())]
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    from . import platformcheck
    cards = platformcheck.cards()
    inv = inventory(cards)
    probs = problems(cards) + item_problems()
    recs = records(cards)
    opt = lambda k: argv[argv.index(k) + 1] if k in argv and argv.index(k) + 1 < len(argv) else None
    ref = opt("--since")
    if ref:
        recs = since(ref, recs)
    if "--write" in argv:
        DOC.write_text(markdown(inv) + "\n", encoding="utf-8", newline="\n")
    nb = briefs(recs, opt("--briefs")) if opt("--briefs") else 0
    g = gaps(recs)
    if "--json" in argv:
        out = {"summary": {"records": len(recs), "gaps": len(g), "problems": len(probs)}, "lists": lists(recs),
               "records": [{k: r[k] for k in FIELDS} for r in recs], "inventory": inv, "problems": probs}
        if "--live" in argv:
            out["live"] = live()                           # a separate section: this machine, not the repo
        print(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=False))
    else:
        n = {s: sum(1 for r in inv if r["status"] == s) for s in STATUSES}
        ls = lists(recs)
        print(f"OS PARITY: {len(inv)} single-system cards: PORT {n['PORT']}, EQUIVALENT {n['EQUIVALENT']}, "
              f"NATIVE-ONLY {n['NATIVE-ONLY']}; {len(recs)} records in all ({len(ls['windows_only'])} windows-only, "
              f"{len(ls['linux_only'])} linux-only, {len(ls['different'])} different, {len(ls['native_only'])} native-only), "
              f"{len(g)} gap(s), {len(probs)} problem(s)")
        for p in probs:
            print("  PROBLEM " + p)
        if nb:
            print(f"  wrote {nb} brief(s) to {opt('--briefs')}")
        if "--live" in argv:
            have = live()
            print("LIVE (this machine only): " + ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in have.items()))
        print("NEXT: " + ("fix the rows above in fieldkit/desk/os_parity.yaml" if probs else
                          "port the lowest-order gap (docs/PLATFORMS.md lists them; --briefs DIR writes one brief per batch)"
                          if g else "no gaps"))
    return 3 if (probs or g) else 0


if __name__ == "__main__":
    sys.exit(main())
