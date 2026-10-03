"""S8 documents and owner tools against the decision register and the shipped build.

A value that a public document or an owner tool asserts is a promise like any claim. When the build changes and the
document does not, two records disagree and a reader cannot tell which is true (an external review of the 155
releases, 2026-10-03, found exactly that: the docs and windows/audit_privacy_claims.py KEPT_DOORS say Safe Browsing
stays on, the release patch locks it off). So every such value is checked, deterministically:

    <owner>/intents/CONSISTENCY.yaml    the assertions, append-only for ids; seeded here from the external review and
                                        reviewable by the maintainer. Each item:
        id, kind (contradiction | lost-layer | undecided | wording), topic, origin (where the finding came from)
        statements: what each document or tool says, each one either
            {where: owner-relative file, quote: verbatim text (must still be there, else STALE), asserts: [checks]}
            {tool: {file, list, names?}}   the tool's own list, parsed from its source (pref, expected value, why)
        recommend / recommend_why (optional): the reasoned recommendation the brief carries
    tool lists not covered by an item      every module-level list of (pref, value, why) tuples in the owner's privacy
                                           audit tools is compared with the shipped build too; a disagreeing list
                                           becomes an automatic item CONS-TOOL-...

Verdicts: AGREE, CONTRADICTED (a statement's check fails on the shipped build or tree), LOST-LAYER (a defence layer
the documents describe is not in the install), UNDECIDED (the build ships a privacy-relevant value no decision of the
register covers), STALE (a quoted statement is no longer in its file), UNCHECKED (a check could not run).
An item is closed for the S8 gate when it AGREES or the maintainer recorded a decision on its brief.
"""
import ast
import json
import re
import time
from pathlib import Path

import yaml

from ..buildh import claims as cl
from ..buildh import decisions as dec
from . import ledger, status

REL = Path("intents") / "CONSISTENCY.yaml"
TOOLS = ("working scripts/audit_privacy_claims.py", "gorilla-patchset/windows/audit_privacy_claims.py")
REVIEW = ("external review of the last four releases, 3 October 2026 (Codex run i-x20, "
          "gorilla-firefox-last-four-releases-report.md)")
SEED = [
    {"id": "CONS-001", "kind": "contradiction",
     "topic": "Safe Browsing and captive-portal detection: older notes and the audit tool say ON, the shipped build locks them OFF",
     "origin": {"source": REVIEW, "section": "2. Gaps and contradictory claims, item 2 (Safe Browsing policy contradicts itself)"},
     "statements": [
         {"where": "gorilla-patchset/patches/05.PREFS/MASTER_PROJECT_LOG_FIREFOX_154_PREFS_PATCHES.md",
          "quote": "What is deliberately KEPT ON: the local malware/phishing blocklist",
          "asserts": [{"pref": {"name": "browser.safebrowsing.malware.enabled", "value": True}},
                      {"pref": {"name": "browser.safebrowsing.phishing.enabled", "value": True}},
                      {"pref": {"name": "network.captive-portal-service.enabled", "value": True}}]},
         {"tool": {"file": "working scripts/audit_privacy_claims.py", "list": "KEPT_DOORS",
                   "names": ["browser.safebrowsing.malware.enabled", "browser.safebrowsing.phishing.enabled",
                             "network.captive-portal-service.enabled"]}},
         {"tool": {"file": "gorilla-patchset/windows/audit_privacy_claims.py", "list": "KEPT_DOORS",
                   "names": ["browser.safebrowsing.malware.enabled", "browser.safebrowsing.phishing.enabled",
                             "network.captive-portal-service.enabled"]}},
         {"where": "docs/PRIVACY-AND-HARDENING.md", "quote": "Google Safe Browsing lookups: off and locked.",
          "asserts": [{"pref": {"name": "browser.safebrowsing.malware.enabled", "value": False, "locked": True}},
                      {"pref": {"name": "browser.safebrowsing.phishing.enabled", "value": False, "locked": True}}]},
         {"where": "docs/PRIVACY-AND-HARDENING.md", "quote": "Captive-portal detection: off.",
          "asserts": [{"pref": {"name": "network.captive-portal-service.enabled", "value": False}}]}],
     "recommend": "build-wins",
     "recommend_why": "the current public page (docs/PRIVACY-AND-HARDENING.md) already says off, and THE RULE D-157-00 "
                      "(this browser never calls home) is what turned them off: list updates go to Google and the portal "
                      "probe to Mozilla. The older notes describe the 155.0.1-win64.2 design; they are marked as history, "
                      "not deleted, and the audit tool's KEPT_DOORS is brought in line with the decision"},
    {"id": "CONS-002", "kind": "lost-layer",
     "topic": "distribution/policies.json (the runtime re-lock of Normandy) is in the tree but not in the installed 157 build",
     "origin": {"source": REVIEW, "section": "1. Normandy / Shield studies: distribution/policies.json locks Normandy values",
                "measured": "2026-10-03: the 157 tree holds distribution/policies.json (12.MOZAMBIQUE.DRILL NEW_FILES); "
                            "the install folder has no distribution/ folder. The local 155.0.1 install backup "
                            "(build 20260914135622) has none either, so the layer was already missing from that build "
                            "on this machine; the review saw it in the published release source"},
     "statements": [
         {"where": "gorilla-patchset/WHAT-WE-CHANGED-AND-WHY.md", "quote": "`policies.json` re-locking all three at runtime.",
          "asserts": [{"installed_present": ["distribution/policies.json"]}]},
         {"where": "gorilla-patchset/README.md", "quote": "plus `distribution/policies.json`",
          "asserts": [{"tree_present": ["distribution/policies.json"]}, {"installed_present": ["distribution/policies.json"]}]}],
     "recommend": "restore",
     "recommend_why": "three layers were promised (source default, locked pref, policy); two hold, the third never reaches the "
                      "install. Restoring it is a packaging fix with a check attached (installed_present), and it keeps "
                      "the published sentence true"},
    {"id": "CONS-003", "kind": "contradiction",
     "topic": "Add-on blocklist and add-on updates listed as kept doors, while D-157-21 locks add-on updates off",
     "origin": {"source": REVIEW, "section": "1. Extension installation: extension blocklist/updates as kept doors"},
     "statements": [
         {"tool": {"file": "working scripts/audit_privacy_claims.py", "list": "KEPT_DOORS",
                   "names": ["extensions.blocklist.enabled", "extensions.update.enabled"]}},
         {"tool": {"file": "gorilla-patchset/windows/audit_privacy_claims.py", "list": "KEPT_DOORS",
                   "names": ["extensions.blocklist.enabled", "extensions.update.enabled"]}},
         {"where": "gorilla-patchset/WHAT-WE-CHANGED-AND-WHY.md",
          "quote": "It still contacts Mozilla for the malicious add-on blocklist and its signature check.",
          "asserts": [{"pref": {"name": "extensions.blocklist.enabled", "value": True}}]},
         {"where": "decisions/PRODUCT-DECISIONS.yaml", "quote": "Dormant reporting and update machinery switched off as well as cut",
          "asserts": [{"pref": {"name": "extensions.update.enabled", "value": False, "locked": True}}]}]},
    {"id": "CONS-004", "kind": "undecided",
     "topic": "Cookies and sessions are kept on shutdown (privacy.clearOnShutdown cookies/sessions false); no decision records it",
     "origin": {"source": REVIEW, "section": "1. Shutdown cleanup: not privacy hardening"},
     "statements": [
         {"where": "gorilla-patchset/patches/05.PREFS/browser_app_profile_firefox.js.patch",
          "quote": "+pref(\"privacy.clearOnShutdown.cookies\",     false);",
          "asserts": [{"pref": {"name": "privacy.clearOnShutdown.cookies", "value": False}},
                      {"pref": {"name": "privacy.clearOnShutdown.sessions", "value": False}},
                      {"pref": {"name": "privacy.clearOnShutdown_v2.cookiesAndStorage", "value": False}}]}],
     "recommend": "record-as-is",
     "recommend_why": "the value has shipped in every release since the 154 patch set and people rely on staying logged in; "
                      "recording it makes it a checked decision instead of an accident, and it is reversible"},
    {"id": "CONS-005", "kind": "wording",
     "topic": "\"Glean instrumentation removed from nsSocketTransport2\": the 157 tree still calls Glean there",
     "origin": {"source": REVIEW, "section": "1. Network-layer Glean / FOG: a gate, not removal of every Glean symbol"},
     "statements": [
         {"where": "gorilla-patchset/WHAT-WE-CHANGED-AND-WHY.md", "quote": "Glean instrumentation removed from `nsSocketTransport2`,",
          "asserts": [{"tree_lacks": {"path": "netwerk/base/nsSocketTransport2.cpp", "text": "glean::"}}]}],
     "recommend": "docs-win",
     "recommend_why": "the default for an unproven privacy claim is to make it true: gate or cut the remaining Glean calls in "
                      "nsSocketTransport2.cpp (a new intent), or reword the sentence to what the code does if that is decided"},
]
HEADER = """# Gorilla Firefox: documents and tools against the decisions and the shipped build (S8 of MIGRATION-PLAN.md).
#
# Every value a public document or an owner tool asserts is checked like a claim. Append-only for ids; the maintainer
# reviews and extends it. Seeded 2026-10-03 from the external review of the last four releases. See
# fieldkit/migrate/consistency.py for the item format and the verdicts.
"""


def path_for(owner):
    return Path(owner) / REL


def load(owner, write_seed=False):
    """-> items. With write_seed, the file is created (or the missing seed ids appended) in the owner repository."""
    p = path_for(owner)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else None
    items = (data or {}).get("items") or []
    have = {i.get("id") for i in items}
    missing = [s for s in SEED if s["id"] not in have]
    if missing and write_seed:
        items = items + missing
        p.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump({"version": 1, "items": items}, sort_keys=False, allow_unicode=True, width=4096,
                              default_flow_style=None)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(HEADER + "\n" + body)
    elif missing:
        items = items + missing
    return items


def tool_lists(path):
    """{list name: [(pref, expected, why)]} for every module-level list of 3-tuples of strings whose first is a pref."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return {}
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        try:
            val = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
        if isinstance(val, list) and val and all(isinstance(x, tuple) and len(x) == 3 and all(isinstance(y, str) for y in x)
                                                 and cl.PREFNAME.match(x[0]) for x in val):
            out[node.targets[0].id] = val
    return out


def _typed(v):
    from ..buildh import proof
    v = proof._norm(v)                                  # a tool writes an empty string as '""'
    return {"true": True, "false": False}.get(v, int(v) if re.fullmatch(r"-?\d+", v) else v)


def _decided(task_id):
    """{brief id: (option, words)} from the journal's decide events (hold is not an answer)."""
    from ..buildh import task
    p = task.STATE / task_id / "journal.jsonl"
    out = {}
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") == "decide" and e.get("option") != "hold":
                out[e.get("brief")] = (e.get("option"), e.get("words"))
    return out


def brief_id(item):
    return ("B-LOSTLAYER-" if item.get("kind") == "lost-layer" else "B-CONSISTENCY-") + item["id"]


def evaluate(owner, workdir, install_dir, task_id=None, journal=None, items=None):
    """-> {"items": [evaluated], "counts", "at"}. Read-only."""
    owner = Path(owner)
    items = items if items is not None else load(owner)
    ctx = {"owner": str(owner), "workdir": str(workdir) if workdir else None, "install": str(install_dir) if install_dir else None,
           "build_id": cl.installed_build_id(install_dir), "journal": journal, "sources": {}, "patches": {},
           "excluded": {}, "disabled": {}}
    try:
        reg = dec.load(owner)["entries"]
    except (FileNotFoundError, OSError, ValueError):
        reg = []
    decided = _decided(task_id) if task_id else {}
    lists = {rel: tool_lists(owner / rel) for rel in TOOLS if (owner / rel).is_file()}
    covered = set()
    for it in items:
        for s in it.get("statements") or []:
            if "tool" in s:
                for n in s["tool"].get("names") or [x[0] for x in lists.get(s["tool"]["file"], {}).get(s["tool"]["list"], [])]:
                    covered.add((s["tool"]["file"], s["tool"]["list"], n))
    auto = []
    for rel, ls in lists.items():
        for name, rows in ls.items():
            left = [r for r in rows if (rel, name, r[0]) not in covered]
            if not left:
                continue
            res = [status.run_check({"pref": {"name": r[0], "value": _typed(r[1])}}, ctx) for r in left]
            bad = [r[0] for r, x in zip(left, res) if x[1] == "FAIL"]
            if bad:
                auto.append({"id": f"CONS-TOOL-{re.sub(r'[^A-Za-z0-9]+', '-', rel.rsplit('.', 1)[0]).strip('-')}-{name}", "kind": "contradiction",
                             "topic": f"{name} in {rel} expects values the shipped build does not have ({len(bad)} pref(s))",
                             "origin": {"source": "the owner tool's own list, parsed from its source"},
                             "statements": [{"tool": {"file": rel, "list": name, "names": bad}}]})
    out = []
    for it in list(items) + auto:
        rows, names = [], set()
        for s in it.get("statements") or []:
            if "tool" in s:
                tl = lists.get(s["tool"]["file"], {}).get(s["tool"]["list"])
                where = f"{s['tool']['file']} {s['tool']['list']}"
                if tl is None:
                    rows.append({"where": where, "quote": None, "state": "STALE", "checks": [],
                                 "why": "the tool or its list is not there any more"})
                    continue
                want = s["tool"].get("names")
                sel = [r for r in tl if not want or r[0] in want]
                checks = [{"pref": {"name": r[0], "value": _typed(r[1])}} for r in sel]
                quote = "; ".join(f"(\"{r[0]}\", \"{r[1]}\", \"{r[2][:60]}\")" for r in sel)
                rows.append(_run(where, quote, checks, ctx))
            else:
                p = owner / s["where"]
                text = cl.norm(p.read_text(encoding="utf-8", errors="replace")) if p.is_file() else None
                if text is None or cl.norm(s["quote"]) not in text:
                    rows.append({"where": s["where"], "quote": s["quote"], "state": "STALE", "checks": [],
                                 "why": "the file no longer says this" if text is not None else "the file is gone"})
                    continue
                rows.append(_run(s["where"], s["quote"], s.get("asserts") or [], ctx))
            for c in rows[-1]["checks"]:
                if c[0] == "pref":
                    names.add(c[3].split(" ", 1)[0].split(":", 1)[0])
        reg_ids = sorted({e.get("id") for e in reg for c in e.get("verify") or [] if "pref" in c
                          and c["pref"].get("name") in names and e.get("status") in ("enforced", "trade-off")})
        fails = [r for r in rows if r["state"] == "CONTRADICTED"]
        lost = [r for r in rows for c in r["checks"] if c[0] == "installed_present" and c[2] == "FAIL"]
        unrun = [r for r in rows if r["state"] == "UNCHECKED"]
        stale = [r for r in rows if r["state"] == "STALE"]
        if it.get("kind") == "lost-layer" and lost:
            verdict = "LOST-LAYER"
        elif fails:
            verdict = "CONTRADICTED"
        elif stale:
            verdict = "STALE"
        elif unrun:
            verdict = "UNCHECKED"
        elif it.get("kind") == "undecided" and not reg_ids:
            verdict = "UNDECIDED"
        else:
            verdict = "AGREE"
        bid = brief_id(it)
        d = decided.get(bid)
        out.append({**{k: v for k, v in it.items() if k != "statements"}, "statements": rows, "verdict": verdict,
                    "register": reg_ids, "brief": bid, "decided": {"option": d[0], "words": d[1]} if d else None,
                    "closed": verdict == "AGREE" or bool(d), "auto": it in auto})
    cnt = {}
    for x in out:
        cnt[x["verdict"]] = cnt.get(x["verdict"], 0) + 1
    return {"items": out, "counts": cnt, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "build_id": ctx["build_id"]}


def _run(where, quote, checks, ctx):
    res = []
    for c in checks:
        kind = next(iter(c))
        lay, v, ev = status.run_check(c, ctx)
        res.append([kind, lay, v, ev])
    state = ("CONTRADICTED" if any(r[2] == "FAIL" for r in res) else "UNCHECKED" if any(r[2] == "cannot run" for r in res)
             else "AGREE")
    return {"where": where, "quote": quote, "state": state, "checks": res, "asserts": list(checks)}


def lines(res):
    out = [f"CONSISTENCY (documents and tools against the register and the shipped build, build {res.get('build_id')})"]
    for x in res["items"]:
        mark = "closed" if x["closed"] else "OPEN"
        out.append(f"  {x['verdict']:<12} {x['id']:<34} [{mark}] {x['topic'][:120]}")
        for s in x["statements"]:
            if s["state"] != "AGREE":
                bad = [c for c in s["checks"] if c[2] != "ok"]
                out.append(f"      {s['state']:<12} {s['where']}: " + ("; ".join(c[3] for c in bad[:3]) or s.get("why", "")))
        if x.get("register"):
            out.append(f"      register: {', '.join(x['register'])}")
    out.append("  " + ", ".join(f"{k.lower()} {v}" for k, v in sorted(res["counts"].items())))
    return out
