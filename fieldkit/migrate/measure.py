"""`migrate seed` and `migrate check`: the measurements the gates read. Read-only against the tree and the install.

seed    regenerates the intent ledger from the public patch set, judges every intent for this task (status.py), stamps
        each entry with its status for this release and writes <owner>/intents/INTENTS.yaml (and seeds
        intents/CONSISTENCY.yaml when it is missing). The only writes into the owner repository.
check   runs the measurements of the chosen stages and caches each one, stamped with the tree hash and the installed
        BuildID (state/build-harness/TASK/migrate/cache/<name>.json):
          S0  inventory, previous_audit, preflight (preflight --build), thermal (journal evidence, else a live proof)
          S1  intake (pristine N-1 against pristine N)
          S2 S3 S4 S6  intents (the hunk judge + every intent's checks), decisions (decisions.check)
          S4  verify (several minutes on a full tree), visual_static
          S8  claims (the claims audit, register not written), consistency
          S9  export (export-hand, dry run), privacy (privacy scan of the public patch-set repository)
        and always `briefs`: the open decision briefs by kind, for the SITREP.
"""
import json
import subprocess
import time
from pathlib import Path

from ..buildh import task
from . import ledger, plan

ALL = tuple(plan.IDS)


def _compact(rows, intents):
    """status rows -> {id: [status, origin, why, group, patch]} (what the gates and the SITREP need)."""
    patch = {e["id"]: e.get("patch") for e in intents}
    out = {}
    for i, r in rows.items():
        why = list((r.get("tree") or {}).get("why") or [])
        why += [f"{c[0]} {c[2]}: {c[3]}" for c in r.get("checks") or [] if c[2] != "ok"]
        if r.get("open_steps"):
            why.insert(0, f"open steps: {r['open_steps']}")
        out[i] = [r["status"], r.get("origin"), why[:4], r["group"], patch.get(i)]
    return out


def intents_record(m, intents, res):
    return {"rows": _compact(res["rows"], intents), "counts": res["counts"], "by_group": res["by_group"],
            "coverage": ledger.coverage(intents), "release": m.version()}


def seed(m, say=print, write=True):
    """-> {"coverage", "counts", "by_group", "path"}."""
    from . import consistency, status
    if not m.owner:
        raise task.Refused("no owner repository beside this task: no patch set to read intents from")
    say(f"seed: reading the public patch set of {m.owner} ...")
    gen = ledger.generate(m.owner)
    old = ledger.load(m.owner)
    intents = ledger.merge(old["intents"], gen["intents"])
    say(f"seed: {len(gen['intents'])} intents generated from {gen['stats']['hunks']} hunks; judging them for {m.tid} ...")
    res = status.compute(m.t, m.owner, intents, m.install, journal=task.STATE / m.tid / "journal.jsonl", say=say)
    status.stamp(intents, res["rows"], m.version())
    path = None
    if write:
        path = ledger.save(m.owner, intents, gen["stats"])
        consistency.load(m.owner, write_seed=True)
        say(f"seed: wrote {path}")
    m.put("intents", intents_record(m, intents, res))
    return {"coverage": ledger.coverage(intents), "counts": res["counts"], "by_group": res["by_group"], "path": str(path) if path else None,
            "stats": gen["stats"]}


def _previous_audit(m):
    prev = m.previous()
    out = {"previous_task": prev, "previous_version": None, "previous_pristine": None, "previous_pristine_readable": False}
    if prev:
        try:
            pt = task.load(prev)
        except task.Refused:
            pt = None
        if pt:
            out["previous_version"] = ((pt.get("meta") or {}).get("upstream") or {}).get("version")
            root = plan._git(pt["workdir"], "rev-list", "--max-parents=0", "HEAD")
            root = root.split()[0] if root else None
            out["previous_pristine"] = root
            out["previous_workdir"] = pt["workdir"]
            out["previous_pristine_readable"] = bool(root) and plan._git(pt["workdir"], "cat-file", "-e", root) is not None
    if m.owner:
        bl = m.owner / "leakgate" / "baseline.json"
        try:
            b = json.loads(bl.read_text(encoding="utf-8"))
            out["leakgate_baseline"] = {"release": b.get("release"), "from_run": b.get("from_run"), "bootstrap": b.get("bootstrap")}
        except (OSError, ValueError):
            out["leakgate_baseline"] = None
        major = str(out.get("previous_version") or "").split(".")[0]
        rep = m.owner / "claims" / f"AUDIT-{major}.md"
        out["previous_claims_report"] = str(rep.relative_to(m.owner)) if major and rep.is_file() else None
        try:
            from ..buildh import decisions as dec
            reg = dec.load(m.owner)
            out["decisions_register"] = {"release": reg["release"], "sha256": reg["sha256"][:16], "entries": len(reg["entries"])}
        except (FileNotFoundError, OSError, ValueError):
            out["decisions_register"] = None
    backups = Path.home() / "Documents" / "Gorilla.Firefox.Backups"
    pv = out.get("previous_version")
    if pv and backups.is_dir():
        found = sorted(backups.glob(f"{pv}-*/manifest.json"))
        out["previous_install_backup"] = str(found[-1].parent) if found else None
    return out


def _inventory(m):
    from ..buildh import decisions as dec
    led = ledger.load(m.owner)
    pset, groups = __import__("fieldkit.buildh.firefox", fromlist=["_policy"])._policy(m.owner)
    try:
        dreg = dec.load(m.owner)
        nd = len(dreg["entries"])
    except (FileNotFoundError, OSError, ValueError):
        nd = None
    from ..buildh import claims as cl
    cp = m.owner / cl.REGISTER
    nc = None
    if cp.is_file():
        nc = sum(1 for l in cp.read_text(encoding="utf-8").splitlines() if l.startswith("- id: C-"))
    st = led.get("stats") or {}
    return {"groups": len(groups), "groups_enabled": sum(1 for g in groups.values() if g.get("status") == "enabled"),
            "patch_files": st.get("patch_files"), "hunks": st.get("hunks"), "added_lines": st.get("added_lines"),
            "removed_lines": st.get("removed_lines"), "pref_lines_added": st.get("pref_lines_added"),
            "new_files": st.get("new_files"), "deleted_files": st.get("deleted_files"), "intents": len(led["intents"]),
            "decisions": nd, "claims": nc, "coverage": ledger.coverage(led["intents"]),
            "ledger_fingerprint": ledger.fingerprint(led["intents"])}


def check(m, only=None, say=print):
    """Run the measurements of the stages in `only` (default: all). -> {name: summary}."""
    stages = set(only or ALL)
    done = {}
    t0 = time.time()

    def took(name, summary):
        done[name] = summary
        say(f"  measured {name}: {summary}  ({time.time() - t0:.0f} s)")
    if "S0" in stages:
        if not ledger.load(m.owner)["exists"]:
            say("  S0: no intent ledger yet; run `migrate seed` first (the inventory counts it)")
        else:
            took("inventory", json.dumps({k: v for k, v in m.put("inventory", _inventory(m))["data"].items()
                                          if k in ("patch_files", "hunks", "intents", "decisions", "claims")}))
        took("previous_audit", m.put("previous_audit", _previous_audit(m))["data"].get("previous_task"))
        from ..buildh import preflight
        rows = preflight.run(m.tid, build=True, model=False, fix_locks=False, fan_required=True)
        m.put("preflight", rows)
        took("preflight", f"{sum(r['ok'] for r in rows)} of {len(rows)} rows pass")
        if not any(e.get("event") == "thermal" and e.get("source") for e in m.events()):
            from ..thermal import sensors
            name, fn, detail = sensors.best(prove=True)
            m.put("thermal", {"ok": bool(name), "detail": f"{name}: {detail}" if name else detail})
            took("thermal", detail[:120])
    if "S1" in stages:
        took("intake", _intake(m, say))
    need_intents = stages & {"S2", "S3", "S4", "S6"}
    led = ledger.load(m.owner) if m.owner else {"exists": False}
    if need_intents and led["exists"]:
        from . import status
        res = status.compute(m.t, m.owner, led["intents"], m.install, journal=task.STATE / m.tid / "journal.jsonl", say=say)
        m.put("intents", intents_record(m, led["intents"], res))
        took("intents", json.dumps(res["counts"]))
    elif need_intents:
        say("  no intent ledger: run `migrate seed` first")
    if stages & {"S4", "S6", "S8"}:
        from ..buildh import decisions as dec
        d = dec.check(m.owner, m.t["workdir"], m.install)
        m.put("decisions", d)
        took("decisions", ", ".join(f"{v} {sum(r['verdict'] == v for r in d['rows'])}" for v in ("ENFORCED", "VIOLATED", "PENDING", "UNCHECKABLE")))
    if "S4" in stages:
        from ..buildh import verify as vf
        say("  verify: a full read of the tree (several minutes) ...")
        rep = vf.verify(m.tid)
        m.put("verify", [list(x) for x in vf.problems(rep)])
        took("verify", f"{sum(1 for _, ok, _ in vf.problems(rep) if not ok)} problem row(s)")
        from .. import visual
        row = visual.preflight_row(m.t)
        m.put("visual_static", {"ok": row["ok"], "evidence": row["evidence"]})
        took("visual_static", "ok" if row["ok"] else row["evidence"][:100])
    claims_res = None
    if "S8" in stages:
        from ..buildh import claims as cl
        say("  claims audit (register not written) ...")
        claims_res = cl.audit(m.owner, m.t["workdir"], m.install, m.t["steps"], journal=task.STATE / m.tid / "journal.jsonl",
                              strict=True, write_register=False, task_id=m.tid, version=m.version())
        x = claims_res["totals"]
        m.put("claims", {**{k: v for k, v in x.items() if not isinstance(v, dict)}, "strict_ok": claims_res["strict_ok"],
                         "lenient_ok": claims_res["lenient_ok"]})
        took("claims", f"proven {x['PROVEN']} of {x['claims']}, contradicted {x['CONTRADICTED']}, unproven {x['UNPROVEN']}")
        from . import consistency
        k = consistency.evaluate(m.owner, m.t["workdir"], m.install, task_id=m.tid, journal=task.STATE / m.tid / "journal.jsonl")
        m.put("consistency", k)
        took("consistency", json.dumps(k["counts"]))
    if "S9" in stages:
        from ..buildh import export as ex
        pol = json.loads((m.owner / "config" / "patch_policy.json").read_text(encoding="utf-8"))
        root = m.owner / pol["patchset_root"]
        major = (m.version() or "x").split(".")[0]
        w = ex.export(m.tid, root, major, say=lambda _: None, dry=True)
        names = [(n, k) for n, k, _ in w["kinds"]]
        gdir = {"privacy": root / f"22.EGRESS.LOCKDOWN.{major}", "port": root / f"21.PORT.FIXES.{major}"}
        missing = [n for n, k in names if not (gdir[k] / n).is_file()]
        m.put("export", {"exported": len(names) - len(missing), "missing": missing})
        took("export", f"{len(names) - len(missing)} of {len(names)} hand edit(s) in the public patch set")
        from ..core import privacy
        pub = m.owner / "gorilla-patchset"
        try:
            rep = privacy.scan_path(pub, git_only=True)
            m.put("privacy", {"findings": sum(len(v) for v in rep.values()), "files": sorted(rep)[:10]})
            took("privacy", f"{sum(len(v) for v in rep.values())} finding(s)")
        except (OSError, ValueError) as e:
            m.put("privacy", {"findings": 1, "files": [f"not scanned: {e}"]})
    took("briefs", _briefs(m, claims_res))
    p = m.plan()
    if p:
        p["last_gates"] = {s: plan.gate(m, s)["ok"] for s in plan.IDS[:-1]}
        plan.save_plan(m.tid, p)
        task.journal(m.t, "migrate-check", stages=sorted(stages), measured=sorted(done))
    return done


def _intake(m, say):
    from . import intake
    from ..leakgate import allow as la
    prev = m.cache("previous_audit") or {"data": _previous_audit(m)}
    pd = prev["data"]
    if not pd.get("previous_pristine_readable"):
        m.put("intake", None)
        return f"no previous pristine source readable (previous task {pd.get('previous_task')}): S1 cannot be measured"
    new_root = plan._git(m.t["workdir"], "rev-list", "--max-parents=0", "HEAD")
    new_root = new_root.split()[0] if new_root else None
    try:
        disp = json.loads((m.owner / "leakgate" / "dispositions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        disp = {}
    old, new = intake.Rev(pd["previous_workdir"], pd["previous_pristine"]), intake.Rev(m.t["workdir"], new_root)
    try:
        data = intake.compute(old, new, disp, la.load(la.path_for(m.owner)), say=say)
    finally:
        old.close()
        new.close()
    data["old_version"], data["new_version"] = pd.get("previous_version"), m.version()
    m.put("intake", data)
    return ", ".join(f"{k} {v['new']} ({v['needs_disposition']} need a disposition)" for k, v in data["counts"].items())


def _briefs(m, claims_res=None):
    """Open briefs by kind. The claims and patch producers need the claims audit: counted only when it ran here."""
    from ..briefs import producers as bp
    kinds = set(bp.PRODUCERS) - ({"claims", "patch"} if claims_res is None else set())
    dres = (m.cache("decisions") or {}).get("data")
    try:
        ctx = bp.Context(m.tid, owner=m.owner, workdir=m.t["workdir"], install_dir=m.install, steps=m.t["steps"],
                         decisions_res=dres, claims_res=claims_res, find_install=False)
        got = bp.collect(ctx, kinds=kinds)
    except Exception as e:                                   # the SITREP says it could not count, never a silent zero
        m.put("briefs", {"by_kind": {}, "problems": [f"{type(e).__name__}: {e}"], "kinds": sorted(kinds)})
        return f"could not collect: {e}"
    by = {}
    for b in got["briefs"]:
        by[b["kind"]] = by.get(b["kind"], 0) + 1
    prev = (m.cache("briefs") or {}).get("data") or {}
    if claims_res is None:                                   # keep the last measured claims/patch counts, marked as such
        for k in ("claims", "patch"):
            if k in (prev.get("by_kind") or {}):
                by[k] = prev["by_kind"][k]
    m.put("briefs", {"by_kind": by, "problems": got["problems"], "kinds": sorted(kinds),
                     "claims_counted": claims_res is not None or bool(prev.get("claims_counted"))})
    return ", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "none"
