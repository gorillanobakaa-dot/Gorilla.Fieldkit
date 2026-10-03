"""Producers: harness state -> decision briefs, deterministically. Every excerpt is copied from the data (the register,
the patch, the claim's own sentence, the allowlist entry); nothing is summarised by a model.

    from_decisions   a VIOLATED or UNCHECKABLE product decision (decisions.check)
    from_patches     a PARTIAL or MISSING patch without a recorded maintainer decision (claims audit)
    from_claims      a cluster of CONTRADICTED or UNPROVEN public claims (claims audit; claims/backlog-<major>.yaml
                     when it exists, else clustered by failing evidence / by source document)
    from_dispositions  an OWNER-DECISION source disposition nobody approved yet (leakgate/dispositions.json)
    from_allowlist   an allowlist proposal nobody approved yet (leakgate/allow.json)
    from_deferred    a step parked for the maintainer (buildh/deferred.py)
    from_owner_edits an uncommitted change to a file the build reads, in the owner repository (buildh/decision.py)

`collect(ctx)` runs them all (or `kinds`) and returns the valid briefs in priority order; a producer that cannot run
is reported as a problem, never silently skipped.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml

from . import schema

PRIVACY_NOTE = "the default for an unproven privacy or security claim is to make it true and prove it, never to delete it"
ORDER = {"decision": 0, "claims-x": 1, "patch": 2, "disposition": 3, "allowlist": 4, "deferred": 5, "owner-edit": 6, "claims-u": 7}
OWNER_EDIT_DIRS = ("config/", "gorilla-patchset/", "harness/", "decisions/")    # files the build or the gate reads


def _h(text, n=8):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:n]


def _slug(text, n=40):
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-")[:n]


def _ident(text, n=60):
    """A readable id part; when it has to be shortened, a hash of the full text keeps it unique."""
    s = _slug(text, 10_000)
    return s if len(s) <= n else s[:n].rstrip("-") + "-" + _h(text, 6)


class Context:
    """What the producers read. Expensive results are computed once and may be passed in (a gate that has just run
    decisions.check or the claims audit hands its result over instead of running it again)."""

    def __init__(self, task_id, owner=None, workdir=None, install_dir=None, steps=None, decisions_res=None,
                 claims_res=None, find_install=True):
        from ..buildh import task
        self.task_id = task_id
        self.t = task.load(task_id) if (owner is None or workdir is None or steps is None) else None
        if owner is None:
            from ..buildh import buildrun
            owner = buildrun._owner_root(self.t)
        self.owner = Path(owner) if owner else None
        self.workdir = workdir or (self.t or {}).get("workdir")
        self.steps = steps if steps is not None else (self.t or {}).get("steps", [])
        if install_dir is None and find_install:
            try:
                from ..buildh import install
                install_dir = install.find_install()
            except Exception:                           # no install is a fact the briefs report, not a crash
                install_dir = None
        self.install = install_dir
        self._dec, self._claims = decisions_res, claims_res
        self._texts = {}

    def decisions(self):
        if self._dec is None:
            from ..buildh import decisions as dec
            self._dec = dec.check(self.owner, self.workdir, self.install)
        return self._dec

    def claims(self):
        if self._claims is None:
            from ..buildh import claims as cl
            from ..buildh import task
            self._claims = cl.audit(self.owner, self.workdir, self.install, self.steps,
                                    journal=task.STATE / self.task_id / "journal.jsonl", write_register=False,
                                    task_id=self.task_id)
        return self._claims

    def text(self, rel):
        if rel not in self._texts:
            p = self.owner / rel
            self._texts[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.is_file() else []
        return self._texts[rel]

    def line_of(self, rel, needle, start=0):
        for i, l in enumerate(self.text(rel)[start:], start + 1):
            if needle in l:
                return i
        return None

    def links(self, needle, files, limit=12):
        """Where a thing is named: ['path:line', ...] over the given owner-relative files."""
        out = []
        for rel in files:
            for i, l in enumerate(self.text(rel), 1):
                if needle in l:
                    out.append(f"{rel}:{i}")
                    if len(out) >= limit:
                        return out
        return out


REGISTER_REL = "decisions/PRODUCT-DECISIONS.yaml"
CLAIMS_REL = "claims/CLAIMS.yaml"


def _public_docs(ctx):
    from ..buildh import claims as cl
    return [d for d in cl.PRIMARY_DOCS if (ctx.owner / d).is_file()]


def _hold(what_blocks):
    return {"id": "hold", "label": "Do nothing for now", "what_changes": "Nothing changes. The need stays open and this brief "
            "is shown again next time.", "user_impact": "None now; " + what_blocks + ".",
            "credibility_impact": "None now; an open gap that stays open for long is still a gap others can find.",
            "cost": "Nothing now; the work waits.", "reversible": True, "records": "journal"}


# -- 1. product decisions --------------------------------------------------------------------------------------------
def from_decisions(ctx):
    from ..buildh import decisions as dec
    res = ctx.decisions()
    reg = dec.load(ctx.owner)
    entries = {e.get("id"): e for e in reg["entries"]}
    out = []
    for row in res["rows"]:
        if row["verdict"] not in ("VIOLATED", "UNCHECKABLE"):
            continue
        e = entries.get(row["id"]) or {}
        at = ctx.line_of(REGISTER_REL, f"id: {row['id']}")
        items = [{"what": f"the decision as the maintainer recorded it ({row['id']}, decided {e.get('decided')})",
                  "excerpt": f"title: {e.get('title')}\nprovenance: {e.get('provenance')}\nwhy: {str(e.get('why') or '').strip()}",
                  "location": f"{REGISTER_REL}:{at}",
                  "linked_from": ctx.links(f"decision: {row['id']}", [CLAIMS_REL]) + ctx.links(row["id"], _public_docs(ctx))}]
        bad = [x for x in row["evidence"] if x.startswith("FAIL") or "cannot check" in x]
        for x in bad:
            kind = x.split(":", 1)[0].replace("FAIL", "").strip()
            items.append({"what": ("a check that FAILED" if x.startswith("FAIL") else "a check that could NOT RUN") + f" ({kind})",
                          "excerpt": x, "location": f"{REGISTER_REL}:{at} (verify list of {row['id']})"})
        n_fail = sum(1 for x in bad if x.startswith("FAIL"))
        n_unrun = len(bad) - n_fail
        verdict = row["verdict"]
        title = e.get("title") or row["title"]
        blocks = ["the release and the regression baseline (they need every decision ENFORCED)",
                  "the post-install 'decisions' row stays FAIL"]
        if verdict == "VIOLATED":
            opts = [
                {"id": "make-true", "label": "Keep the decision and make the build obey it",
                 "what_changes": f"The decision stays exactly as recorded. A new register entry ties this answer to the same "
                                 f"{len(e.get('verify') or [])} check(s), so every build is held to it until the tree and the "
                                 "installed browser pass them.",
                 "user_impact": f"Users get what was promised: {title}.",
                 "credibility_impact": "What the project says and what it ships agree again; nothing is withdrawn.",
                 "cost": f"Work: {n_fail} failing check(s) to fix in the tree, then a new build and install.",
                 "reversible": True, "records": "register", "verify": e.get("verify") or []},
                {"id": "trade-off", "label": "Ship without it this time, as a known trade-off",
                 "what_changes": "A PENDING register entry records that this release ships without the decision. It blocks a "
                                 "release until the maintainer edits the original entry to status trade-off by hand.",
                 "user_impact": f"Users do NOT get: {title}. Whatever the decision protected them from stays possible.",
                 "credibility_impact": "Every public sentence that promises this must be reworded before release; a gap that "
                                       "others find before we say it is far worse.",
                 "cost": "No build work now; documentation work and a visible step back.", "reversible": True,
                 "records": "register"},
                _hold("the release and the baseline stay blocked")]
            why = (f"the maintainer already decided this ({e.get('decided')}: {str(e.get('provenance'))[:120]}); the check shows "
                   "the build lost it, and decisions lost silently in a port are exactly what the register exists to stop. "
                   + PRIVACY_NOTE[0].upper() + PRIVACY_NOTE[1:] + ".")
            rec = "make-true"
        else:
            opts = [
                {"id": "make-checkable", "label": "Supply what the check needs and run it again",
                 "what_changes": "Nothing in the register. The missing input (an installed build, a file, a tool) is provided "
                                 "and `decisions` is run again; the answer is recorded in the task journal.",
                 "user_impact": "None directly; it finds out whether users actually get what was decided.",
                 "credibility_impact": "An unchecked promise becomes a checked one.",
                 "cost": f"Small: {n_unrun} check(s) could not run; see the excerpts for what was missing.",
                 "reversible": True, "records": "journal"},
                {"id": "change-check", "label": "Change how this decision is verified",
                 "what_changes": "A PENDING register entry asks for a different check for this decision; the maintainer "
                                 "writes the new check into the original entry by hand.",
                 "user_impact": "None, if the new check proves the same thing.",
                 "credibility_impact": "Fine if the new check is at least as strict; weaker checks are how claims drift.",
                 "cost": "Writing a new check and its test.", "reversible": True, "records": "register"},
                _hold("the decision stays unchecked, and unchecked is not passed")]
            why = "a check that cannot run proves nothing; usually the input is simply missing (for example no installed build)."
            rec = "make-checkable"
        b = {"id": f"B-DECISION-{row['id']}", "topic": f"Product decision {row['id']} ({title}) is {verdict} in this build",
             "kind": "decision", "task": ctx.task_id,
             "source": {"check": "decisions.check", "verdict": verdict,
                        "command": f"fieldkit build-harness decisions {ctx.task_id}"},
             "affected": {"count": len(bad), "counts": {"checks failing": n_fail, "checks that could not run": n_unrun,
                                                        "checks in total": len(e.get("verify") or [])}, "items": items},
             "options": opts, "recommendation": {"option": rec, "why": why},
             "what_gets_recorded": "For a register option: a NEW entry appended to decisions/PRODUCT-DECISIONS.yaml (never an "
                                   "edit of the old one), amending " + row["id"] + ", with today's date and your own words as "
                                   "provenance; status pending unless checks are attached. Always: the task journal gets "
                                   "the brief's sha256, the option and your words.",
             "blocking": {"blocks": blocks, "deadline": "before the next release or baseline"},
             "evidence": [{"file": REGISTER_REL, "line": at, "text": f"id: {row['id']}"}] +
                         [{"file": REGISTER_REL, "line": at, "text": x} for x in bad],
             "target": {"decision": row["id"]}}
        out.append(b)
    return out


# -- 2. patches ------------------------------------------------------------------------------------------------------
def _patch_hunks(path):
    """-> {(file, n): {"lines": [...], "line": line number of the @@ header in the .patch}}"""
    from ..buildh import firefox
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    lines = text.splitlines()
    out, cur, n = {}, None, 0
    for i, l in enumerate(lines, 1):
        if l.startswith("+++ "):
            p = l[4:].split("\t")[0].strip()
            cur, n = (p[2:] if p.startswith(("a/", "b/")) else p), 0
        elif l.startswith("@@") and cur is not None:
            n += 1
            out.setdefault((cur, n), {"line": i})
    for f in firefox.parse_patch(text):
        for k, h in enumerate(f["hunks"], 1):
            if (f["file"], k) in out and "lines" not in out[(f["file"], k)]:
                out[(f["file"], k)]["lines"] = h["lines"]
    return out, lines


def _changed(lines, limit=8):
    ch = [l for l in lines or [] if l[:1] in ("+", "-") and l[1:].strip()]
    return "\n".join(ch[:limit]) + (f"\n... {len(ch) - limit} more changed line(s)" if len(ch) > limit else "")


def from_patches(ctx):
    from ..buildh import claims as cl
    res = ctx.claims()
    pol = json.loads((ctx.owner / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    pset_rel = pol.get("patchset_root", "gorilla-patchset/patches").strip("/")
    cited = {}
    for c in res["claims"]:
        for r in c.get("results") or []:
            if r[0] == "patch":
                cited.setdefault(r[1], []).append(c)
            elif r[0] == "patch_group":
                cited.setdefault("group:" + str(r[1]), []).append(c)
    out = []
    for p in res["patch_audit"]["patches"]:
        if not p["fail"]:
            continue
        rel = f"{pset_rel}/{p['patch']}"
        hunks, plines = _patch_hunks(ctx.owner / rel)
        bad = [h for h in p["hunks"] if h["status"] not in cl.HUNK_OK and not h["explained"]]
        claims_here = cited.get(p["patch"], []) + cited.get("group:" + p["group"], [])
        privacy = p["patch"].startswith(cl.PRIVACY_GROUPS)
        items = [{"what": "what the patch says it does (its own header)", "excerpt": p["purpose"] or "(the patch has no header)",
                  "location": f"{rel}:1 ({p['purpose_source']})",
                  "linked_from": [f"{c['id']} {c['source']}:{c['line']}" for c in claims_here[:12]]}]
        for h in bad:
            hk = hunks.get((h["file"], h["n"])) or {}
            items.append({"what": f"{h['file']} hunk #{h['n']} is {h['status']}: {h['detail'][:200]}",
                          "excerpt": _changed(hk.get("lines")), "location": f"{rel}:{hk.get('line', '?')}"})
        for c in claims_here[:5]:
            items.append({"what": f"a public claim that rests on this patch ({c['id']}, {c['verdict']})",
                          "excerpt": c["text"], "location": f"{c['source']}:{c['line']}"})
        opts = [
            {"id": "make-true", "label": "Carry the patch in full: port the missing hunks",
             "what_changes": f"A PENDING register entry commits this release to carrying {p['patch']}. The {len(bad)} missing "
                             "hunk(s) are ported (harness port steps, or by hand with `submit --hand`) until the claims audit "
                             "says IMPLEMENTED.",
             "user_impact": f"Users get the whole change the patch describes: {p['purpose'][:160]}",
             "credibility_impact": "What the public patch set says and what the build carries agree; a person who checks "
                                   "the patch against the tree finds it there.",
             "cost": f"Port work: {len(bad)} of {len(p['hunks'])} hunk(s), then a build and the audit again.",
             "reversible": True, "records": "register"},
            {"id": "accept-partial", "label": "Ship it as it is and say so",
             "what_changes": f"A PENDING register entry records that {p['patch']} is {p['verdict']} on purpose. It stays "
                             "failing until the maintainer sets the entry to trade-off and names it in claims/CLAIMS.yaml "
                             "patch_decisions.",
             "user_impact": f"Users do NOT get the {len(bad)} missing part(s) of this change.",
             "credibility_impact": f"{len(claims_here)} public claim(s) rest on this patch and must be reworded; a patch "
                                   "the README describes but the tree does not carry is worse than no claim.",
             "cost": "No port work; documentation work and a visible gap.", "reversible": True, "records": "register",
             "removes_claim": bool(privacy and claims_here)},
            {"id": "drop", "label": "Take the patch out of the patch set",
             "what_changes": "A PENDING register entry asks for the patch to be excluded in config/patch_policy.json "
                             "(with a reason); the maintainer edits the policy by hand.",
             "user_impact": "Users lose whatever part of the change still applies.",
             "credibility_impact": "Every claim that names this patch becomes false and must go; for a privacy patch this "
                                   "reads as a broken promise.",
             "cost": "The least work, the largest loss.", "reversible": True, "records": "register",
             "removes_claim": bool(claims_here) or privacy},
            _hold("the claims audit keeps failing and the baseline stays blocked")]
        why = (f"it is a privacy or security patch ({p['group']}) and {len(claims_here)} public claim(s) rest on it; "
               + PRIVACY_NOTE + "." if privacy else
               f"the public patch set says the browser carries this change ({len(claims_here)} claim(s) cite it); carrying it "
               "is what makes that true.")
        out.append({"id": f"B-PATCH-{_ident(p['patch'], 60)}", "kind": "patch", "task": ctx.task_id,
                    "topic": f"Patch {p['patch']} is {p['verdict']} in the tree ({p['implemented_pct']}% implemented, "
                             f"{len(bad)} hunk(s) missing) and no maintainer decision explains it",
                    "source": {"check": "claims audit (patch implementation)", "verdict": p["verdict"],
                               "command": f"fieldkit build-harness claims {ctx.task_id}"},
                    "affected": {"count": len(bad), "counts": {"hunks missing": len(bad), "hunks in the patch": len(p["hunks"]),
                                                               "public claims citing it": len(claims_here)}, "items": items},
                    "options": opts, "recommendation": {"option": "make-true", "why": why},
                    "what_gets_recorded": "A NEW pending entry appended to decisions/PRODUCT-DECISIONS.yaml naming the patch, "
                                          "with today's date and your own words as provenance (hold: the task journal only). "
                                          "The journal always gets the brief's sha256.",
                    "blocking": {"blocks": ["claims --strict, so the regression baseline and a release"],
                                 "deadline": "before the next release or baseline"},
                    "evidence": [{"file": rel, "line": (hunks.get((h["file"], h["n"])) or {}).get("line"),
                                  "text": f"{h['file']} #{h['n']} {h['status']}: {h['detail'][:160]}"} for h in bad],
                    "target": {"patch": p["patch"], "privacy": privacy, "_sort": (not privacy, p["verdict"] != "MISSING",
                                                                                  -len(bad), p["patch"])}})
    return out


# -- 3. claim clusters -----------------------------------------------------------------------------------------------
def load_backlog(owner, version_major):
    """claims/backlog-<major>.yaml, written by another tool. Tolerated shapes: a list, or a dict holding the list under
    clusters / backlog / items; each cluster has an id and a title (or topic / name) and its claim ids under claims /
    claim_ids / members (ids or dicts with an id). Absent or unreadable -> None (the clusters are derived instead)."""
    p = Path(owner) / "claims" / f"backlog-{version_major}.yaml"
    if not p.is_file():
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if isinstance(data, dict):
        data = next((data[k] for k in ("clusters", "backlog", "items") if isinstance(data.get(k), list)), None)
    if not isinstance(data, list):
        return None
    out = []
    for n, c in enumerate(data, 1):
        if not isinstance(c, dict):
            continue
        ids = next((c[k] for k in ("claims", "claim_ids", "members") if isinstance(c.get(k), list)), [])
        ids = [x.get("id") if isinstance(x, dict) else x for x in ids]
        out.append({"id": str(c.get("id") or f"backlog-{n}"), "title": str(c.get("title") or c.get("topic") or c.get("name") or
                                                                           f"backlog cluster {n}"),
                    "claims": [str(i) for i in ids if i], "note": c.get("why") or c.get("note") or c.get("action")})
    return {"path": str(p), "clusters": out}


def _clusters(res, backlog):
    bad = {c["id"]: c for c in res["claims"] if c["verdict"] in ("CONTRADICTED", "UNPROVEN")}
    clusters, covered = [], set()
    for c in (backlog or {}).get("clusters", []):
        members = [bad[i] for i in c["claims"] if i in bad]
        if members:
            clusters.append({"id": "B-CLAIMS-" + _slug(c["id"]), "title": c["title"], "members": members, "from": "backlog",
                             "note": c.get("note")})
            covered.update(m["id"] for m in members)
    rest = [c for c in bad.values() if c["id"] not in covered]
    sig = {}
    for c in rest:
        if c["verdict"] == "CONTRADICTED":
            fails = sorted(json.dumps([r[0], r[1]], sort_keys=True, default=str) for r in c["results"] if r[2] == "FAIL")
            key = ("X", tuple(fails))
        else:
            unrun = sorted({r[0] for r in c["results"] if r[2] == "cannot run"})
            key = ("U", c["source"], "unrun-" + "-".join(unrun) if unrun else "no-evidence")
        sig.setdefault(key, []).append(c)
    for key, members in sig.items():
        members.sort(key=lambda c: (c["source"], c["line"], c["id"]))
        if key[0] == "X":
            first = [r for r in members[0]["results"] if r[2] == "FAIL"]
            title = "Public claims CONTRADICTED by the same failing check: " + "; ".join(f"{r[0]} {str(r[1])[:60]}" for r in first)[:160]
            cid = "B-CLAIMS-X-" + _h(key)
        else:
            title = (f"Public claims in {key[1]} that nothing proves yet" + (" (no evidence linked)" if key[2] == "no-evidence"
                                                                             else f" (a check could not run: {key[2][6:]})"))
            cid = "B-CLAIMS-U-" + _ident(f"{key[1]}-{key[2]}", 70)
        clusters.append({"id": cid, "title": title, "members": members, "from": "derived"})
    return clusters


def from_claims(ctx):
    from ..buildh import claims as cl
    res = ctx.claims()
    major = str(res.get("version") or ((ctx.t or {}).get("meta", {}).get("upstream") or {}).get("version") or "157").split(".")[0]
    backlog = load_backlog(ctx.owner, major)
    ids_at = {}
    for i, l in enumerate(ctx.text(CLAIMS_REL), 1):
        m = re.match(r"\s*-?\s*id:\s*(C-\d+)\s*$", l)
        if m:
            ids_at.setdefault(m.group(1), i)
    out = []
    for c in _clusters(res, backlog):
        m = c["members"]
        nx = sum(1 for x in m if x["verdict"] == "CONTRADICTED")
        strong = sum(1 for x in m if cl.STRONG.search(x["text"]))
        srcs = sorted({x["source"] for x in m})
        items = []
        prim = {d: i for i, d in enumerate(cl.PRIMARY_DOCS)}
        # the examples a reader sees first: contradicted, front-page documents, strong privacy wording
        m = sorted(m, key=lambda x: (x["verdict"] != "CONTRADICTED", prim.get(x["source"], len(prim)),
                                     not cl.STRONG.search(x["text"]), x["source"], x["line"], x["id"]))
        for x in m:
            fail = [f"{r[0]} {str(r[1])[:80]}: {str(r[3])[:120]}" for r in x["results"] if r[2] != "ok"]
            items.append({"what": f"{x['id']} {x['verdict']}: {x['why']}" + (f" ({'; '.join(fail)[:240]})" if fail else ""),
                          "excerpt": x["text"], "location": f"{x['source']}:{x['line']}",
                          "linked_from": [f"{CLAIMS_REL}:{ids_at.get(x['id'], '?')}"]})
        verdict = "CONTRADICTED" if nx else "UNPROVEN"
        opts = [
            {"id": "make-true", "label": "Make the claims true and prove them",
             "what_changes": f"The {len(m)} sentence(s) stay as published. A PENDING register entry lists them; each gets "
                             "evidence (a check in claims/CLAIMS.yaml) and, where the build falls short, the build is fixed "
                             "until the claims audit says PROVEN.",
             "user_impact": "Users get the protection the sentences promise, and anyone can check it.",
             "credibility_impact": "The strongest position: every promise is backed by a check that runs on every build.",
             "cost": f"Evidence work for {len(m)} claim(s)" + (f", and build fixes for {nx} contradicted one(s)" if nx else "") + ".",
             "reversible": True, "records": "register"},
            {"id": "reword", "label": "Reword the sentences to say exactly what is proven",
             "what_changes": "A PENDING register entry lists the claims to be reworded; the public documents are edited by "
                             "hand so each sentence says only what a check proves.",
             "user_impact": "Users get an accurate description instead of a stronger one; the browser does not change.",
             "credibility_impact": "An honest correction, visible in the history; weaker than making it true, much better "
                                   "than leaving a false sentence.",
             "cost": f"Editing {len(srcs)} document(s).", "reversible": True, "records": "register"},
            {"id": "withdraw", "label": "Delete the sentences",
             "what_changes": "A PENDING register entry lists the claims to be removed from the public documents.",
             "user_impact": "Users lose the information, and possibly the protection it described is never built.",
             "credibility_impact": "For a privacy browser a vanished privacy promise reads as a broken one; readers who "
                                   "saw it will notice.",
             "cost": "The least work.", "reversible": False, "records": "register", "removes_claim": True},
            _hold("the claims audit stays NOT OK in strict mode, so the baseline and a release stay blocked")]
        why = (f"{nx} of these are CONTRADICTED by a check that ran: the browser does not yet do what the sentence says. "
               if nx else "") + PRIVACY_NOTE[0].upper() + PRIVACY_NOTE[1:] + "."
        out.append({"id": c["id"], "kind": "claims", "task": ctx.task_id,
                    "topic": f"{c['title']} ({len(m)} claim(s))",
                    "source": {"check": "claims audit" + (f" + backlog {Path(backlog['path']).name}" if c["from"] == "backlog" else ""),
                               "verdict": verdict, "command": f"fieldkit build-harness claims {ctx.task_id}"},
                    "affected": {"count": len(m), "counts": {"CONTRADICTED": nx, "UNPROVEN": len(m) - nx,
                                                             "strong privacy wording": strong, "documents": len(srcs)},
                                 "items": items},
                    "options": opts, "recommendation": {"option": "make-true", "why": why},
                    "what_gets_recorded": "A NEW pending entry appended to decisions/PRODUCT-DECISIONS.yaml listing the claim ids, "
                                          "with today's date and your own words as provenance (hold: the task journal only). "
                                          "The journal always gets the brief's sha256.",
                    "blocking": {"blocks": ["claims --strict: the regression baseline and a release"],
                                 "deadline": "before the next release"},
                    "evidence": [{"file": x["source"], "line": x["line"], "text": x["text"]} for x in m],
                    "target": {"claims": [x["id"] for x in m], "_sort": ("x" if nx else "u", -len(m), c["id"])}})
    return out


# -- 4./5. leak gate -------------------------------------------------------------------------------------------------
PREF_IN_TEXT = re.compile(r"\b[a-z][a-zA-Z0-9_-]*(?:\.[A-Za-z0-9_-]+){1,}\b")


def from_dispositions(ctx):
    p = ctx.owner / "leakgate" / "dispositions.json"
    if not p.is_file():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    raw = p.read_text(encoding="utf-8").splitlines()
    out = []
    for key, d in sorted(data.items()):
        if not isinstance(d, dict) or d.get("disposition") != "OWNER-DECISION" or d.get("approval"):
            continue
        at = next((i for i, l in enumerate(raw, 1) if l.strip().startswith(json.dumps(key))), None)
        prefs = sorted({m for m in PREF_IN_TEXT.findall(d.get("evidence", "")) if "." in m and not m.endswith((".cpp", ".mjs", ".js"))
                        and m.count(".") >= 1 and m.split(".")[0] in ("security", "network", "browser", "privacy", "media",
                                                                     "dom", "extensions", "toolkit", "services", "intl")})
        links = []
        for pr in prefs:
            links += ctx.links(pr, [REGISTER_REL] + _public_docs(ctx), limit=6)
        items = [{"what": f"source file {key} ({d.get('component')}): network APIs {', '.join(d.get('apis') or [])}",
                  "excerpt": d.get("evidence", ""), "location": f"leakgate/dispositions.json:{at}", "linked_from": links}]
        if ctx.workdir and (Path(ctx.workdir) / key).is_file():
            items.append({"what": "the file in the ported tree", "excerpt": f"{key} ({(Path(ctx.workdir) / key).stat().st_size:,} bytes)",
                          "location": f"<tree>/{key}"})
        opts = [
            {"id": "cut", "label": "Stop it from sending: cut it in source",
             "what_changes": "A PENDING register entry commits the next port to a GORILLA PHYSICAL LOCK in this file (the "
                             "sending code returns first); the disposition then becomes cut-in-source.",
             "user_impact": "Nobody is contacted on the browser's own initiative by this code. Whatever the request was "
                            "for (read the excerpt) no longer happens.",
             "credibility_impact": "Matches the rule 'this browser never calls home' (D-157-00) with nothing to explain.",
             "cost": "A source change, a build and the leak gate again.", "reversible": True, "records": "register"},
            {"id": "accept-trade-off", "label": "Keep it, as a trade-off the maintainer accepts",
             "what_changes": "The disposition is approved by the maintainer at this terminal, with your words, and the "
                             "source gate passes for this file.",
             "user_impact": "The behaviour in the excerpt stays: a third party may be contacted as it describes.",
             "credibility_impact": "Acceptable only if it is listed publicly as a trade-off, like the captcha and video ones; "
                                   "an unlisted exception contradicts 'never calls home'.",
             "cost": "Documentation: the trade-off must be named in the public documents.", "reversible": True,
             "records": "disposition"},
            _hold("SOURCE_POLICY stays FAIL, so the leak gate and a release stay blocked")]
        out.append({"id": f"B-DISPOSITION-{_slug(Path(key).name)}-{_h(key, 6)}", "kind": "disposition", "task": ctx.task_id,
                    "topic": f"{key} can reach the network and only the maintainer can decide what happens to it",
                    "source": {"check": "leakgate static source audit (dispositions)", "verdict": "OWNER-DECISION, not approved",
                               "command": f"fieldkit build-harness leakgate {ctx.task_id}"},
                    "affected": {"count": 1, "counts": {"network APIs": len(d.get("apis") or []), "settings named": len(prefs)},
                                 "items": items},
                    "options": opts,
                    "recommendation": {"option": "cut", "why": "the product rule is that the browser never calls home "
                                                               "(D-157-00); only the trade-offs the register lists are allowed, "
                                                               "and this one is not among them yet."},
                    "what_gets_recorded": "cut: a NEW pending entry in decisions/PRODUCT-DECISIONS.yaml. accept-trade-off: the "
                                          "disposition's approval in leakgate/dispositions.json (by owner, at a terminal, with "
                                          "your words and the brief's sha256). Always: the task journal.",
                    "blocking": {"blocks": ["leak gate SOURCE_POLICY", "a release"], "deadline": "before the next release"},
                    "evidence": [{"file": "leakgate/dispositions.json", "line": at, "text": d.get("evidence", "")}],
                    "target": {"disposition": key}})
    return out


def from_allowlist(ctx):
    from ..leakgate import allow as la
    p = la.path_for(ctx.owner)
    data = la.load(p)
    raw = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    out = []
    for e in data.get("entries", []):
        if e.get("approval"):
            continue
        at = next((i for i, l in enumerate(raw, 1) if f'"id": {json.dumps(e.get("id"))}' in l), None)
        video = str(e.get("id", "")).startswith("video-")
        unnamed = "OBSERVED" in str(e.get("component", ""))
        items = [{"what": f"{len(e.get('values') or [])} {e.get('kind')} value(s) seen in scenario(s) {e.get('scenarios')}",
                  "excerpt": "\n".join(str(v) for v in (e.get("values") or [])[:20]), "location": f"leakgate/allow.json:{at}"},
                 {"what": "what the proposal says it is", "excerpt": f"component: {e.get('component')}\npurpose: {e.get('purpose')}\n"
                                                                     f"privacy impact: {e.get('privacy_impact')}\nsecurity impact: "
                                                                     f"{e.get('security_impact')}",
                  "location": f"leakgate/allow.json:{at}"}]
        opts = [
            {"id": "approve", "label": "Approve the entry: this is expected and allowed",
             "what_changes": "The allowlist entry gets the maintainer's approval (at this terminal, with your words and the "
                             "brief's sha256); the leak gate stops reporting these values.",
             "user_impact": "The behaviour stays: " + str(e.get("privacy_impact") or "see the excerpt"),
             "credibility_impact": "Fine when it is one of the listed trade-offs (D-157-12 for video); otherwise it is an "
                                   "exception the public documents must name.",
             "cost": "None now.", "reversible": True, "records": "allowlist"},
            {"id": "cut", "label": "Do not allow it: make the browser stop doing it",
             "what_changes": "A PENDING register entry commits to removing the behaviour; the entry stays unapproved, so the "
                             "leak gate keeps failing until the behaviour is gone.",
             "user_impact": "The behaviour stops once the fix is built.",
             "credibility_impact": "Consistent with 'never calls home'.", "cost": "A source change, a build, the gate again.",
             "reversible": True, "records": "register"},
            _hold("the leak gate keeps reporting it as pending")]
        rec, why = (("approve", "it is the video compromise the maintainer already accepted (D-157-12), seen only in its own "
                                "scenario") if video else
                    ("hold", "the entry does not yet say what it is (component and source are 'OBSERVED'); approving an unnamed "
                             "thing is approving blind") if unnamed else
                    ("cut", "anything not on the register's trade-off list is cut (D-157-00)"))
        out.append({"id": f"B-ALLOW-{_ident(e.get('id'), 60)}", "kind": "allowlist", "task": ctx.task_id,
                    "topic": f"Allowlist proposal {e.get('id')} is waiting for the maintainer's approval",
                    "source": {"check": "leakgate allowlist (proposals)", "verdict": "pending",
                               "command": f"fieldkit build-harness leakgate-propose {ctx.task_id}"},
                    "affected": {"count": len(e.get("values") or []), "counts": {"values": len(e.get("values") or [])},
                                 "items": items},
                    "options": opts, "recommendation": {"option": rec, "why": why},
                    "what_gets_recorded": "approve: the entry's approval in leakgate/allow.json (owner, terminal, your words, the "
                                          "brief's sha256). cut: a NEW pending register entry. Always: the task journal.",
                    "blocking": {"blocks": ["the leak gate reports the values as pending"], "deadline": "before the next release"},
                    "evidence": [{"file": "leakgate/allow.json", "line": at, "text": json.dumps(e, ensure_ascii=False)[:300]}],
                    "target": {"allow": e.get("id")}})
    return out


# -- 6./7. parked steps and owner edits ------------------------------------------------------------------------------
def _from_old_brief(ctx, old, bid, kind, source, carried):
    """The older brief dicts (buildh/decision.py, deferred.py) -> a Brief. Their facts are kept verbatim."""
    labels = {"hold": "Do nothing for now"}
    opts = []
    for o in old["options"]:
        records = "journal"
        opts.append({"id": o["key"], "label": labels.get(o["key"], o["label"]), "what_changes": o["consequence"],
                     "user_impact": ("None now; the build waits." if o["key"] == "hold" else
                                     "The next build has the change exactly as described." if o["key"] in ("keep", "recreate") else
                                     "The next build does not have this change."),
                     "credibility_impact": ("None." if o["key"] == "hold" else
                                            "Recorded with the explanation, so the reason can be shown later."),
                     "cost": "Nothing now." if o["key"] == "hold" else "A typed sentence at a real terminal, after a pause.",
                     "reversible": bool(o.get("reversible", True)), "records": records,
                     **({"carried_out_by": carried} if o["key"] in ("revert", "drop") else {})})
    return opts


def from_deferred(ctx):
    from ..buildh import deferred
    out = []
    for s in ctx.steps:
        if s.get("status") != "deferred":
            continue
        old = deferred.build(ctx.task_id, s["id"])
        if not old.get("options"):
            continue
        short = deferred.short(s["id"])
        cmd = f"fieldkit build-harness deferred {ctx.task_id} {short.replace(' ', '-')} --do \"{old['confirm']}\""
        excerpt = "\n".join([f"-{l}" for l in old["removed"]] + [f"+{l}" for l in old["added"]])
        items = [{"what": f"the parked change ({short}, patch {s['args'].get('patch')})", "excerpt": excerpt or "(empty hunk)",
                  "location": f"{s['args'].get('patch')} -> {s['args'].get('file')}"},
                 {"what": "what the new source says at that spot", "excerpt": "\n".join(old.get("nearby") or []) or "(no anchor found)",
                  "location": f"<tree>/{s['args'].get('file')}"}]
        out.append({"id": f"B-DEFERRED-{_ident(short, 60)}", "kind": "deferred", "task": ctx.task_id, "topic": old["what"],
                    "source": {"check": "port: step deferred", "verdict": "deferred",
                               "command": f"fieldkit build-harness deferred {ctx.task_id} {short.replace(' ', '-')}"},
                    "affected": {"count": len(old["removed"]) + len(old["added"]),
                                 "counts": {"lines removed": len(old["removed"]), "lines added": len(old["added"])},
                                 "items": items},
                    "options": _from_old_brief(ctx, old, None, "deferred", None, cmd),
                    "recommendation": {"option": old["recommended"], "why": old["why"]},
                    "what_gets_recorded": "hold / recreate: the task journal. drop: the deferred door records it (journal, "
                                          "decisions.jsonl, the step marked dropped by the owner).",
                    "blocking": {"blocks": ["the build gate (every parked step must be decided)"], "deadline": None},
                    "evidence": [{"file": s["args"].get("file"), "line": None, "text": x} for x in old["evidence"]],
                    "target": {"step": s["id"]}})
    return out


def from_owner_edits(ctx):
    from ..buildh import decision
    r = subprocess.run(["git", "-C", str(ctx.owner), "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                       text=True, errors="replace")
    if r.returncode != 0:
        return []
    out = []
    for l in r.stdout.splitlines():
        path = l[3:].strip().strip('"')
        if l[:2].strip() != "M" or not path.startswith(OWNER_EDIT_DIRS):
            continue
        old = decision.owner_file_edit(ctx.owner, path)
        if not old.get("options"):
            continue
        changed = [x for x in old["diff"].splitlines() if x[:1] in ("+", "-") and x[:3] not in ("+++", "---")]
        cmd = f"fieldkit build-harness brief \"{ctx.owner}\" {path} --do \"{old['confirm']}\""
        out.append({"id": f"B-EDIT-{_ident(path, 60)}", "kind": "owner-edit", "task": ctx.task_id, "topic": old["what"],
                    "source": {"check": "owner repository: uncommitted change to a file the build reads",
                               "verdict": old["door"] + " door", "command": f"git -C \"{ctx.owner}\" diff -- {path}"},
                    "affected": {"count": len(changed), "counts": {"changed lines": len(changed)},
                                 "items": [{"what": f"the uncommitted change to {path}", "excerpt": "\n".join(changed[:12]),
                                            "location": path}]},
                    "options": _from_old_brief(ctx, old, None, "owner-edit", None, cmd),
                    "recommendation": {"option": old["recommended"], "why": old["why"]},
                    "what_gets_recorded": "hold / keep: the task journal. revert: the brief door saves the diff first and logs it.",
                    "blocking": {"blocks": ["the audit baseline cannot be re-taken while the file differs"], "deadline": None},
                    "evidence": [{"file": path, "line": None, "text": x} for x in old["evidence"]],
                    "target": {"path": path}})
    return out


PRODUCERS = {"decision": from_decisions, "patch": from_patches, "claims": from_claims, "disposition": from_dispositions,
             "allowlist": from_allowlist, "deferred": from_deferred, "owner-edit": from_owner_edits}
PREFIX = {"B-DECISION-": "decision", "B-PATCH-": "patch", "B-CLAIMS-": "claims", "B-DISPOSITION-": "disposition",
          "B-ALLOW-": "allowlist", "B-DEFERRED-": "deferred", "B-EDIT-": "owner-edit"}


def kind_of(brief_id):
    return next((k for p, k in PREFIX.items() if brief_id.startswith(p)), None)


def _rank(b):
    k = b["kind"]
    if k == "claims":
        k = "claims-x" if b["affected"]["counts"].get("CONTRADICTED") else "claims-u"
    return (ORDER[k], b.get("target", {}).get("_sort") or (), b["id"])


def collect(ctx, kinds=None):
    """-> {"briefs": [valid briefs, priority order], "problems": [producer or validation problems]}."""
    briefs, problems = [], []
    if ctx.owner is None:
        return {"briefs": [], "problems": ["no owner repository known for this task: no register, no claims, no leak gate"]}
    for kind, fn in PRODUCERS.items():
        if kinds and kind not in kinds:
            continue
        try:
            made = fn(ctx)
        except Exception as e:                  # a producer that cannot run is a reported problem, never a silent gap
            problems.append(f"{kind}: could not produce briefs: {type(e).__name__}: {e}")
            continue
        for b in made:
            p = schema.problems(b)
            (problems.append(f"{b.get('id')}: " + "; ".join(p)) if p else briefs.append(b))
    seen = {}
    for b in briefs:
        seen[b["id"]] = seen.get(b["id"], 0) + 1
    dup = sorted(i for i, n in seen.items() if n > 1)
    if dup:                                     # two needs under one id could be answered by mistake: refuse both
        problems.append(f"duplicate brief ids (refused): {', '.join(dup)}")
        briefs = [b for b in briefs if b["id"] not in dup]
    briefs.sort(key=_rank)
    for b in briefs:                            # the sort key is not part of what is shown or fingerprinted
        b.get("target", {}).pop("_sort", None)
    return {"briefs": briefs, "problems": problems}


def find(ctx, brief_id):
    kind = kind_of(brief_id)
    if kind is None:
        raise schema.Invalid(f"{brief_id} is not a brief id (they start with {', '.join(PREFIX)})")
    got = collect(ctx, kinds={kind})
    b = next((x for x in got["briefs"] if x["id"] == brief_id), None)
    if b is None:
        raise schema.Invalid(f"no open brief {brief_id}" + (f" ({'; '.join(got['problems'])})" if got["problems"] else
                                                          ": it may be settled already; list them with `briefs TASK`"))
    return b


def listing_lines(got, task_id, limit=None):
    bs = got["briefs"]
    shown = bs if limit is None else bs[:limit]
    out = [f"{len(bs)} open decision brief(s) for {task_id}; each is a full brief, never a bare question:"]
    out += [f"  {schema.summary_line(b)}" for b in shown]
    if len(bs) > len(shown):
        out.append(f"  ... and {len(bs) - len(shown)} more: fieldkit build-harness briefs {task_id}")
    out += [f"  PROBLEM: {p}" for p in got["problems"]]
    if bs:
        out.append(f"NEXT: show one in full: fieldkit build-harness brief show {bs[0]['id']} --task {task_id}")
    return out


def gate_lines(task_id, kinds=None, limit=10, **ctx_kw):
    """For a gate that stops: the open briefs instead of a bare 'needs the owner'. Never raises."""
    try:
        got = collect(Context(task_id, **ctx_kw), kinds=kinds)
    except Exception as e:
        return [f"decision briefs could not be produced ({type(e).__name__}: {e}); run: fieldkit build-harness briefs {task_id}"]
    if not got["briefs"] and not got["problems"]:
        return [f"no open decision briefs for {task_id}" + (f" among {sorted(kinds)}" if kinds else "")]
    return listing_lines(got, task_id, limit=limit)
