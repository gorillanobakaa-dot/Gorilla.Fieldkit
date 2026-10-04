"""The migration plan as code: stages S0-S9, their exit gates, the state file, and `advance`.

The plan is the owner's document (Gorilla.firefox/MIGRATION-PLAN.md, section 3). Here every exit gate is a list of
ITEMS, each green or red with its evidence and the one command that moves it. A red item that needs a person names
the decision brief that asks (fieldkit/briefs). Gates read what `migrate check` measured (cached under
state/build-harness/TASK/migrate/cache/, stamped with the tree hash and the installed BuildID it was measured on):
a measurement taken on another tree or another build is STALE, and stale is never green.

    plan.json   {task, previous, stage, guard, created, history: [{stage, entered, by, adopted?, carried?}], last_gates}

A stage is entered only through `advance`, and only while the current stage's gate is green; S9 (publish) needs every
gate S0-S8 green, including the ones an adopted migration carried in from before it was put under control.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from ..buildh import task

STAGES = [
    {"id": "S0", "name": "Preflight",
     "what": "Inventory: groups, patches, intents, decisions, claims. Audit of the previous release. Toolchain, disk, "
             "thermal sensor proven.",
     "gate": "Previous release audit stored; inventory counts recorded; machine ready.",
     "commands": ["migrate seed TASK", "migrate check TASK --only S0", "preflight TASK --build"]},
    {"id": "S1", "name": "Upstream intake",
     "what": "Pristine N-1 against pristine N: new prefs, hosts, modules, actors, about: pages, background tasks, "
             "AI/ML. Each is an INTAKE item.",
     "gate": "Every intake item has a disposition (cut at source, accepted with a decision, or not network-capable).",
     "commands": ["migrate check TASK --only S1", "migrate intake TASK", "briefs TASK", "brief show ID", "decide ID OPTION"]},
    {"id": "S2", "name": "Mechanical apply",
     "what": "Patches applied in policy order; hunks already upstream detected.",
     "gate": "Every hunk is APPLIED, ALREADY-UPSTREAM, or queued for S3.",
     "commands": ["start firefox", "approve TASK", "next TASK"]},
    {"id": "S3", "name": "Intent port",
     "what": "Each intent whose hunks did not apply is located by its anchors in the new tree and re-implemented; "
             "every edit is recorded.",
     "gate": "Every intent ported, or obsolete with a recorded reason.",
     "commands": ["next TASK", "submit TASK --hand", "record TASK FILE...", "deferred TASK"]},
    {"id": "S4", "name": "Tree verification",
     "what": "verify, repair, truthbound, intent tree checks, decision tree checks, visual static checks.",
     "gate": "All green.",
     "commands": ["verify TASK", "repair TASK", "truthbound TASK", "migrate check TASK --only S4"]},
    {"id": "S5", "name": "Build",
     "what": "build-run: idle wait, thermal proof, governor, known stops fixed, stale files swept.",
     "gate": "BUILD OK; packaging checks green.",
     "commands": ["build-run TASK"]},
    {"id": "S6", "name": "Install and proof",
     "what": "install (backup first), post-install: prefs, excised, startup, egress, ad blocking, leaks, decisions, "
             "intents, visual runtime, the maintainer's scripts.",
     "gate": "All rows green; the keyboard tests run with --drive after a warning.",
     "commands": ["install TASK", "post-install TASK --drive", "migrate check TASK --only S6"]},
    {"id": "S7", "name": "Leak gate",
     "what": "leakgate --release in an administrator shell: every scenario, including user actions.",
     "gate": "Release PASS; the first release seeds the baseline.",
     "commands": ["leakgate TASK --release"]},
    {"id": "S8", "name": "Claims and consistency",
     "what": "Claims audit --strict; every public document and owner tool agrees with the decisions and the build.",
     "gate": "No UNPROVEN or CONTRADICTED claim without a recorded decision.",
     "commands": ["claims TASK --strict", "migrate consistency TASK", "briefs TASK"]},
    {"id": "S9", "name": "Publish",
     "what": "export-hand; the public patch set replayed onto pristine upstream equals the built tree; privacy scans; "
             "push only on the maintainer's recorded yes.",
     "gate": "Identical tree hash; scans clean; pushed.",
     "commands": ["export-hand TASK", "privacy scan --git", "decide B-PUBLISH-TASK publish"]},
]
STAGE = {s["id"]: s for s in STAGES}
IDS = [s["id"] for s in STAGES]
FH = "fieldkit build-harness"


def state_dir(task_id):
    return task.STATE / task_id / "migrate"


def load_plan(task_id):
    p = state_dir(task_id) / "plan.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_plan(task_id, p):
    d = state_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps(p, indent=1) + "\n", encoding="utf-8", newline="\n")


def journal_events(task_id):
    p = task.STATE / task_id / "journal.jsonl"
    out = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _git(wd, *args):
    r = subprocess.run(["git", "-C", str(wd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    return r.stdout.strip() if r.returncode == 0 else None


class Migration:
    """What the gates read, loaded lazily and once. Tests pass `t`, `owner`, `install` and `tree` directly."""

    def __init__(self, task_id, t=None, owner=None, install=None, tree=None, find_install=True):
        self.tid = task_id
        self.t = t or task.load(task_id)
        if owner is None:
            from ..buildh import buildrun
            owner = buildrun._owner_root(self.t)
        self.owner = Path(owner) if owner else None
        self._install, self._find, self._tree = install, find_install, tree
        self._events = None

    @property
    def install(self):
        if self._install is None and self._find:
            from ..buildh import install
            try:
                self._install = install.find_install()
            except Exception:
                self._install = None
            self._find = False
        return self._install

    def tree(self):
        if self._tree is None:
            self._tree = _git(self.t["workdir"], "rev-parse", "HEAD^{tree}") or "?"
        return self._tree

    def build_id(self):
        from ..buildh import claims as cl
        return cl.installed_build_id(self.install)

    def events(self):
        if self._events is None:
            self._events = journal_events(self.tid)
        return self._events

    def plan(self):
        return load_plan(self.tid)

    def cache_path(self, name):
        return state_dir(self.tid) / "cache" / f"{name}.json"

    def cache(self, name):
        try:
            return json.loads(self.cache_path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, name, data):
        p = self.cache_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tree": self.tree(), "build_id": self.build_id(), "data": data}
        p.write_text(json.dumps(rec, indent=1, default=str) + "\n", encoding="utf-8", newline="\n")
        return rec

    def build_result(self):
        try:
            return json.loads((task.STATE / self.tid / "build-result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def marker(self):
        try:
            return json.loads((Path(self.install) / "gorilla-install.json").read_text(encoding="utf-8")) if self.install else None
        except (OSError, ValueError):
            return None

    def version(self):
        return ((self.t.get("meta") or {}).get("upstream") or {}).get("version")

    def previous(self):
        p = self.plan()
        if p and p.get("previous"):
            return p["previous"]
        return guess_previous(self.tid, self.version())


def guess_previous(task_id, version):
    """The task of the newest release older than this one (a `firefox-<version>` id is preferred)."""
    if not version or not task.STATE.is_dir():
        return None
    def key(v):
        return tuple(int(x) for x in re.findall(r"\d+", v or ""))
    best = None
    for d in task.STATE.iterdir():
        f = d / "task.json"
        if d.name == task_id or not f.is_file():
            continue
        try:
            v = ((json.loads(f.read_text(encoding="utf-8")).get("meta") or {}).get("upstream") or {}).get("version")
        except (OSError, ValueError):
            continue
        if v and key(v) < key(version):
            cand = (key(v), d.name == f"firefox-{v}", d.name)
            if best is None or cand > best:
                best = cand
    return best[2] if best else None


# -- gate items -----------------------------------------------------------------------------------------------------
def item(iid, ok, what, evidence, command=None, brief=None, needs="tool"):
    return {"id": iid, "ok": bool(ok), "what": what, "evidence": str(evidence)[:400], "command": command,
            "brief": brief, "needs": "human" if brief else needs}


def _fresh(m, rec, by="tree"):
    """-> (ok, why) whether a cached measurement speaks for the tree / build as they are now."""
    if not rec:
        return False, "not measured"
    if by == "tree" and rec.get("tree") != m.tree():
        return False, f"STALE: measured on tree {str(rec.get('tree'))[:10]}, the tree is now {m.tree()[:10]}"
    if by == "build" and str(rec.get("build_id")) != str(m.build_id()):
        return False, f"STALE: measured on build {rec.get('build_id')}, the installed build is {m.build_id()}"
    return True, f"measured {rec.get('at')}"


def _measure(m, stage):
    return f"{FH} migrate check {m.tid} --only {stage}"


def gate_S0(m):
    from . import ledger
    out = []
    led = ledger.load(m.owner) if m.owner else {"exists": False, "intents": []}
    out.append(item("S0.ledger", led["exists"], "the intent ledger exists (intents/INTENTS.yaml)",
                    f"{len(led['intents'])} intents" if led["exists"] else "no ledger", f"{FH} migrate seed {m.tid}"))
    inv = m.cache("inventory")
    ok = bool(inv) and led["exists"] and inv["data"].get("ledger_fingerprint") == ledger.fingerprint(led["intents"])
    out.append(item("S0.inventory", ok, "inventory counts recorded for this ledger",
                    json.dumps({k: v for k, v in (inv or {}).get("data", {}).items() if k != "ledger_fingerprint"})[:300]
                    if inv else "not measured", _measure(m, "S0")))
    pa = m.cache("previous_audit")
    ok = bool(pa) and bool(pa["data"].get("previous_pristine_readable"))
    out.append(item("S0.previous-audit", ok, "the previous release's audit is stored (its pristine source, decisions, "
                    "claims, leak-gate baseline)", json.dumps(pa["data"])[:300] if pa else "not measured", _measure(m, "S0")))
    pf = m.cache("preflight")
    if not pf:
        out.append(item("S0.machine", False, "the machine is ready (preflight --build)", "not measured", _measure(m, "S0")))
    else:
        for r in pf["data"]:
            out.append(item(f"S0.machine:{_slug(r['check'])}", r["ok"], f"machine: {r['check']}", r["evidence"],
                            f"{FH} preflight {m.tid} --build"))
    th = [e for e in m.events() if e.get("event") == "thermal" and e.get("source")]
    tc = m.cache("thermal")
    ok = bool(th) or bool(tc and tc["data"].get("ok"))
    ev = (f"sensor {th[-1]['source']} tracked a real compile (peak {th[-1].get('peak')} C, {th[-1]['t']})" if th else
          (tc["data"].get("detail") if tc else "no proof"))
    out.append(item("S0.thermal", ok, "the CPU temperature sensor is proven", ev, "fieldkit thermal prove"))
    return out


def gate_S1(m):
    from . import intake
    rec = m.cache("intake")
    if not rec:
        return [item("S1.intake", False, "upstream intake measured (pristine N-1 against pristine N)", "not measured",
                     _measure(m, "S1"))]
    d = rec["data"]
    out = [item("S1.intake", True, "upstream intake measured", f"{d.get('old_version')} -> {d.get('new_version')}: "
                + ", ".join(f"{k} {v['new']} new ({v['auto']} auto)" for k, v in d["counts"].items()), _measure(m, "S1"))]
    decided = intake.decided_items(m.tid)
    open_ = {c["id"]: c for c in intake.open_clusters(d, decided)}
    for c in d.get("clusters") or []:
        oc = open_.get(c["id"])
        out.append(item(f"S1.{c['id']}", oc is None, f"intake: {c['category']} under {c['component']} has a disposition",
                        f"{len(oc['open'])} of {len(c['items'])} item(s) without a disposition" if oc else
                        f"{len(c['items'])} item(s) decided", f"{FH} brief show {c['brief']} --task {m.tid}", brief=c["brief"] if oc else None))
    return out


def _intents(m):
    return m.cache("intents")


def gate_S2(m):
    t = m.t
    out = [item("S2.approved", t.get("approved"), "the port plan is approved by the owner", "approved" if t.get("approved") else "not approved",
                f"{FH} approve {m.tid}", needs="human")]
    applies = [s for s in t["steps"] if s["id"].startswith("apply-")]
    bad = [s["id"] for s in applies if s.get("status") != "done"]
    out.append(item("S2.apply", applies and not bad, "every enabled group applied mechanically",
                    f"{len(applies) - len(bad)} of {len(applies)} groups applied" + (f"; open: {bad[:5]}" if bad else ""),
                    f"{FH} next {m.tid}"))
    rec = _intents(m)
    ok, why = _fresh(m, rec, "tree")
    if not ok:
        out.append(item("S2.hunks", False, "every hunk APPLIED, ALREADY-UPSTREAM or queued for S3", why, _measure(m, "S2")))
        return out
    unq = [i for i, r in rec["data"]["rows"].items() if r[0] == "NOT-IN-TREE" and r[1] == "applied"]
    out.append(item("S2.hunks", not unq, "every hunk APPLIED, ALREADY-UPSTREAM or queued for S3",
                    f"{len(unq)} intent(s) not in the tree and with no port step: {unq[:6]}" if unq else "all landed or queued",
                    f"{FH} migrate work {m.tid} {unq[0]}" if unq else None))
    return out


def gate_S3(m):
    from ..briefs import producers as bp
    out = []
    opn = [s for s in m.t["steps"] if s.get("status") in ("pending", "failed", "blocked", "deferred") and not s["id"].startswith("final")]
    for s in opn[:25]:
        out.append(item(f"S3.step:{s['id']}", False, f"step {s['id']} closed", f"{s.get('status')}: {'; '.join(s.get('last_why') or [])[:200]}",
                        f"{FH} next {m.tid}" if s.get("kind") != "owner" else f"{FH} deferred {m.tid} {s['id']}",
                        needs="human" if s.get("status") in ("blocked", "deferred") else "tool"))
    if len(opn) > 25:
        out.append(item("S3.steps-more", False, "more open steps", f"{len(opn) - 25} more", f"{FH} status {m.tid}"))
    if not opn:
        out.append(item("S3.steps", True, "every port, hand and relocation step closed", f"{len(m.t['steps'])} steps, none open"))
    rec = _intents(m)
    ok, why = _fresh(m, rec, "tree")
    if not ok:
        out.append(item("S3.intents", False, "every intent ported, or obsolete with a recorded reason", why, _measure(m, "S3")))
        return out
    bad = [(i, r) for i, r in rec["data"]["rows"].items() if r[0] in ("NOT-IN-TREE", "OPEN")]
    for i, r in bad:
        patch = r[4] if len(r) > 4 else ""
        out.append(item(f"S3.{i}", False, f"intent {i} ({patch}) ported or explained", "; ".join(r[2])[:300] or r[0],
                        f"{FH} migrate work {m.tid} {i}", brief=f"B-PATCH-{bp._ident(patch, 60)}" if patch else None))
    if not bad:
        out.append(item("S3.intents", True, "every intent ported, or obsolete with a recorded reason",
                        f"{rec['data']['counts']}"))
    return out


def gate_S4(m):
    out = []
    v = m.cache("verify")
    ok, why = _fresh(m, v, "tree")
    if not ok:
        out.append(item("S4.verify", False, "verify: the tree parses, imports resolve, every change explained", why, _measure(m, "S4")))
    else:
        for name, good, ev in v["data"]:
            out.append(item(f"S4.verify:{_slug(name)}", good, name, ev, f"{FH} verify {m.tid}"))
    tb = [e for e in m.events() if e.get("event") == "truthbound"]
    head_t = _git(m.t["workdir"], "log", "-1", "--format=%ci")
    last = tb[-1] if tb else None
    ok = bool(last) and not last.get("unexplained") and head_t is not None and last["t"] >= head_t[:19]
    out.append(item("S4.truthbound", ok, "truthbound: every changed file explained, after the last tree change",
                    (f"last run {last['t']}: {last.get('changed')} changed, {len(last.get('unexplained') or [])} unexplained; "
                     f"last tree change {head_t[:19] if head_t else '?'}") if last else "never run", f"{FH} truthbound {m.tid}"))
    rec = _intents(m)
    ok, why = _fresh(m, rec, "tree")
    if not ok:
        out.append(item("S4.intents-tree", False, "every intent's tree check passes", why, _measure(m, "S4")))
    else:
        bad = [(i, r) for i, r in rec["data"]["rows"].items() if r[0] == "TREE-CHECK-FAILS"]
        for i, r in bad:
            out.append(item(f"S4.{i}", False, f"intent {i}: its own tree check passes where the hunk judge finds it",
                            "; ".join(r[2])[:300], f"{FH} migrate work {m.tid} {i}"))
        if not bad:
            out.append(item("S4.intents-tree", True, "every intent's tree check passes", "none fails"))
    d = m.cache("decisions")
    ok, why = _fresh(m, d, "tree")
    if not ok:
        out.append(item("S4.decisions-tree", False, "decision tree checks pass", why, _measure(m, "S4")))
    else:
        tree_kinds = ("tree_contains", "tree_lacks", "tree_absent", "tree_present", "mozconfig_has", "image_sharp", "fieldkit_test")
        bad = [r for r in d["data"]["rows"] if r["verdict"] in ("VIOLATED", "UNCHECKABLE")
               and any(x.split(":", 1)[0].replace("FAIL", "").strip() in tree_kinds for x in r["evidence"] if x.startswith("FAIL"))]
        for r in bad:
            out.append(item(f"S4.decision:{r['id']}", False, f"decision {r['id']} holds in the tree", "; ".join(r["evidence"])[:300],
                            f"{FH} decisions {m.tid}", brief=f"B-DECISION-{r['id']}"))
        if not bad:
            out.append(item("S4.decisions-tree", True, "decision tree checks pass", "no tree check fails"))
    vs = m.cache("visual_static")
    ok, why = _fresh(m, vs, "tree")
    out.append(item("S4.visual-static", ok and vs["data"].get("ok"), "visual static checks (crisp icons, 2x headroom)",
                    vs["data"].get("evidence") if ok else why, _measure(m, "S4")))
    return out


def gate_S5(m):
    br = m.build_result()
    ok = bool(br) and br.get("tree") == m.tree()
    out = [item("S5.build", ok, "BUILD OK for the tree as it is now",
                (f"built tree {str(br.get('tree'))[:10]} verified {br.get('verified_at')}" + ("" if ok else f"; the tree is now {m.tree()[:10]}"))
                if br else "no build result", f"{FH} build-run {m.tid}")]
    ev = m.events()
    last_start = max((i for i, e in enumerate(ev) if e.get("event") == "build-start"), default=-1)
    ver = [e for e in ev[last_start + 1:] if e.get("event") == "build-verified"]
    out.append(item("S5.packaging", bool(ver) and ver[-1].get("ok"), "packaging checks green (build-verify) after the last build",
                    f"build-verified {ver[-1]['t']}: ok={ver[-1].get('ok')}" if ver else "no build-verified after the last build start",
                    f"{FH} build-verify {m.tid}"))
    return out


def gate_S6(m):
    out = []
    br, mk = m.build_result(), m.marker()
    zip_sha = (((br or {}).get("artifacts") or {}).get("zip") or {}).get("sha256")
    ok = bool(mk) and bool(zip_sha) and mk.get("zip_sha256") == zip_sha
    out.append(item("S6.installed", ok, "the installed browser is this build", (f"install marker {str(mk.get('zip_sha256'))[:12]}, "
                    f"build zip {str(zip_sha)[:12]}" if mk else f"no install marker in {m.install}"), f"{FH} install {m.tid}"))
    ev = m.events()
    key = os.path.normcase(os.path.abspath(str(m.install))) if m.install else None
    mine = [e for e in ev if key and os.path.normcase(os.path.abspath(str(e.get("target", "")))) == key]
    li = max((i for i, e in enumerate(mine) if e.get("event") == "install" and e.get("ok")), default=None)
    pis = [e for e in mine[li + 1:] if e.get("event") == "post_install"] if li is not None else []
    if not pis:
        out.append(item("S6.post-install", False, "post-install ran since the latest install", "not run", f"{FH} post-install {m.tid} --drive"))
    else:
        rows = {}
        for name, rc, st, *_ in pis[-1].get("results") or []:
            rows.setdefault(name, []).append((rc, st))
        for name, rs in rows.items():
            good = all(rc == 0 for rc, _ in rs)
            sts = sorted({st for _, st in rs})
            cmd = f"{FH} post-install {m.tid} --drive" if "SKIPPED" in sts else f"{FH} post-install {m.tid} --only {name}"
            brief = None
            out.append(item(f"S6.post-install:{name}", good, f"post-install row {name}", f"{pis[-1]['t']}: {', '.join(sts)}",
                            cmd, brief=brief))
    rec = _intents(m)
    ok, why = _fresh(m, rec, "build")
    if not ok:
        out.append(item("S6.intents-build", False, "every intent's build check passes on the installed build", why, _measure(m, "S6")))
    else:
        bad = [(i, r) for i, r in rec["data"]["rows"].items() if r[0] == "BUILD-CONTRADICTED"]
        for i, r in bad:
            out.append(item(f"S6.{i}", False, f"intent {i} holds in the installed build", "; ".join(r[2])[:300],
                            f"{FH} migrate work {m.tid} {i}"))
        if not bad:
            out.append(item("S6.intents-build", True, "every intent's build check passes", f"{rec['data']['counts'].get('PROVEN-IN-BUILD', 0)} proven"))
    d = m.cache("decisions")
    ok, why = _fresh(m, d, "build")
    if not ok:
        out.append(item("S6.decisions", False, "every product decision holds in the installed build", why, _measure(m, "S6")))
    else:
        bad = [r for r in d["data"]["rows"] if r["verdict"] in ("VIOLATED", "UNCHECKABLE")]
        for r in bad:
            out.append(item(f"S6.decision:{r['id']}", False, f"decision {r['id']} holds", "; ".join(r["evidence"])[:300],
                            f"{FH} decisions {m.tid}", brief=f"B-DECISION-{r['id']}"))
        if not bad:
            out.append(item("S6.decisions", True, "every product decision holds", "none violated"))
    return out


def gate_S7(m):
    p = m.owner / "state" / "leakgate_result.json" if m.owner else None
    try:
        st = json.loads(p.read_text(encoding="utf-8")) if p else None
    except (OSError, ValueError):
        st = None
    bid = m.build_id()
    ok = bool(st) and st.get("release_run") and st.get("FINAL_RESULT") == "PASS" and str(st.get("BUILD")) == str(bid)
    if st:
        fails = [k for k, v in (st.get("policies") or {}).items() if v != "PASS"]
        ev = (f"latest run {st.get('when')}: build {st.get('BUILD')} ({'release' if st.get('release_run') else 'quick'}), "
              f"{st.get('FINAL_RESULT')}" + (f", failing {', '.join(fails[:8])}" if fails else "")
              + ("" if str(st.get("BUILD")) == str(bid) else f"; the installed build is {bid}: this build has no run"))
    else:
        ev = "no leak-gate result"
    return [item("S7.leakgate", ok, "leak gate: release PASS for the installed build", ev,
                 f"{FH} leakgate {m.tid} --release   (administrator shell; never in parallel with a build)")]


def gate_S8(m):
    out = []
    c = m.cache("claims")
    ok, why = _fresh(m, c, "build")
    if not ok:
        out.append(item("S8.claims", False, "claims audit --strict: no UNPROVEN or CONTRADICTED claim without a decision", why, _measure(m, "S8")))
    else:
        x = c["data"]
        out.append(item("S8.claims", x.get("strict_ok"), "claims audit --strict: no UNPROVEN or CONTRADICTED claim without a decision",
                        f"claims {x.get('claims')}: proven {x.get('PROVEN')}, unproven {x.get('UNPROVEN')}, contradicted "
                        f"{x.get('CONTRADICTED')}, stale {x.get('STALE')}; patches failing {x.get('patches_failing')}",
                        f"{FH} briefs {m.tid}", needs="human"))
    k = m.cache("consistency")
    ok, why = _fresh(m, k, "build")
    if not ok:
        out.append(item("S8.consistency", False, "documents and tools agree with the decisions and the build", why, _measure(m, "S8")))
    else:
        for x in k["data"]["items"]:
            out.append(item(f"S8.{x['id']}", x["closed"], f"consistency: {x['topic'][:120]}", f"{x['verdict']}" +
                            (f"; decided: {x['decided']['option']}" if x.get("decided") else ""),
                            f"{FH} brief show {x['brief']} --task {m.tid}", brief=None if x["closed"] else x["brief"]))
    d = m.cache("decisions")
    ok, why = _fresh(m, d, "build")
    if ok:
        pend = [r for r in d["data"]["rows"] if r["verdict"] == "PENDING"]
        out.append(item("S8.decisions-strict", not pend, "no decision left pending (strict)", f"pending: {[r['id'] for r in pend]}" if pend else "none pending",
                        f"{FH} decisions {m.tid} --strict", needs="human"))
    return out


def gate_S9(m, deep=True):
    out = []
    if deep:
        red = [s for s in IDS[:-1] if not all(i["ok"] for i in GATES[s](m))]
        out.append(item("S9.all-gates", not red, "every gate S0-S8 green (including those carried in at adoption)",
                        f"red: {', '.join(red)}" if red else "all green", f"{FH} migrate sitrep {m.tid}"))
    ex = m.cache("export")
    ok, why = _fresh(m, ex, "tree")
    out.append(item("S9.export", ok and not ex["data"].get("missing"), "every hand edit is in the public patch set (export-hand)",
                    (f"{ex['data'].get('exported')} exported, missing: {ex['data'].get('missing')[:5]}" if ok else why), f"{FH} export-hand {m.tid}"))
    rp = [e for e in m.events() if e.get("event") == "replay-proof"]
    ok = bool(rp) and rp[-1].get("ok") and rp[-1].get("tree") == m.tree()
    out.append(item("S9.replay", ok, "the public patch set replayed onto pristine upstream gives this tree",
                    f"{rp[-1]['t']}: tree {str(rp[-1].get('tree'))[:10]}" if rp else
                    "no replay proof recorded",
                    f"{FH} replay {m.tid}"))
    pv = m.cache("privacy")
    ok, why = _fresh(m, pv, "tree")
    out.append(item("S9.privacy", ok and not pv["data"].get("findings"), "privacy scan of what git would publish is clean",
                    f"{pv['data'].get('findings')} finding(s) in {pv['data'].get('files')}" if ok else why, _measure(m, "S9")))
    yes = [e for e in m.events() if e.get("event") == "decide" and e.get("brief") == f"B-PUBLISH-{m.tid}" and e.get("option") == "publish"]
    out.append(item("S9.publish-yes", bool(yes), "the maintainer's recorded yes to publish", f"recorded {yes[-1]['t']}: {yes[-1].get('words')}" if yes
                    else "not recorded", f"{FH} brief show B-PUBLISH-{m.tid} --task {m.tid}", brief=None if yes else f"B-PUBLISH-{m.tid}"))
    return out


GATES = {"S0": gate_S0, "S1": gate_S1, "S2": gate_S2, "S3": gate_S3, "S4": gate_S4, "S5": gate_S5, "S6": gate_S6,
         "S7": gate_S7, "S8": gate_S8, "S9": gate_S9}


def _slug(s, n=40):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:n]


def gate(m, stage):
    items = GATES[stage](m)
    return {"stage": stage, "name": STAGE[stage]["name"], "ok": all(i["ok"] for i in items), "items": items,
            "red": [i for i in items if not i["ok"]]}


def all_gates(m, deep=False):
    out = {}
    for s in IDS:
        out[s] = gate(m, s) if s != "S9" else {"stage": "S9", "name": STAGE["S9"]["name"],
                                                "items": (its := gate_S9(m, deep=deep)), "ok": all(i["ok"] for i in its),
                                                "red": [i for i in its if not i["ok"]]}
    return out


def position(m, gates=None):
    """Where an in-flight migration stands by its evidence: the stage after the last one whose work is visibly done.
    Used to adopt a task that was started before migration control existed (it never invents progress: a stage
    counts as passed only by what the record, the tree and the install show)."""
    g = gates or all_gates(m)
    ids = {i["id"]: i for s in g.values() for i in s["items"]}
    if g["S7"]["ok"]:
        return "S8"
    if ids.get("S6.installed", {}).get("ok"):
        return "S7" if g["S6"]["ok"] else "S6"
    if ids.get("S5.build", {}).get("ok"):
        return "S6"
    applies = [s for s in m.t["steps"] if s["id"].startswith("apply-")]
    open_ = [s for s in m.t["steps"] if s.get("status") in ("pending", "failed", "blocked", "deferred")]
    if applies and all(s.get("status") == "done" for s in applies):
        return "S3" if open_ else ("S5" if g["S4"]["ok"] else "S4")
    if m.t.get("approved"):
        return "S2"
    return "S0"


def init(m, previous=None, at=None, by="cli"):
    """Put a migration under control. A task already past S0 is ADOPTED at the stage its evidence shows (or `at`);
    the red items of the gates before that stage are CARRIED: they must be green before S9."""
    if m.plan():
        raise task.Refused(f"{m.tid} is already under migration control (stage {m.plan()['stage']}); see: {FH} migrate sitrep {m.tid}")
    prev = previous or m.previous()
    g = all_gates(m)
    stage = at or position(m, g)
    if stage not in IDS:
        raise task.Refused(f"no stage {stage!r}; stages are {', '.join(IDS)}")
    carried = [{"stage": s, "item": i["id"], "evidence": i["evidence"][:200]} for s in IDS[:IDS.index(stage)] for i in g[s]["red"]]
    p = {"task": m.tid, "previous": prev, "stage": stage, "guard": "on", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
         "history": [{"stage": stage, "entered": time.strftime("%Y-%m-%d %H:%M:%S"), "by": by,
                      "adopted": stage != "S0", "carried": len(carried)}],
         "carried": carried, "last_gates": {s: g[s]["ok"] for s in IDS}}
    save_plan(m.tid, p)
    from . import sitrep
    rep = sitrep.build(m, gates=g)
    task.journal(m.t, "migrate-init", stage=stage, previous=prev, adopted=stage != "S0", carried=len(carried),
                 sitrep=sitrep.journal_block(rep))
    return p


def advance(m, by="cli"):
    """Enter the next stage, only while the current stage's gate is green (S9 needs every gate S0-S8 green)."""
    p = m.plan()
    if not p:
        raise task.Refused(f"{m.tid} is not under migration control; start it: {FH} migrate init {m.tid}")
    cur = p["stage"]
    if cur == IDS[-1]:
        raise task.Refused("S9 is the last stage; the migration ends when its gate is green")
    g = gate(m, cur)
    if not g["ok"]:
        first = g["red"][0]
        raise task.Refused(f"the {cur} {STAGE[cur]['name']} gate is RED ({len(g['red'])} item(s)); a stage cannot be entered "
                           f"while the previous gate is red. First: {first['id']}: {first['evidence'][:160]}. "
                           f"NEXT: {first['command'] or f'{FH} migrate sitrep {m.tid}'}")
    nxt = IDS[IDS.index(cur) + 1]
    if nxt == "S9":
        red = [s for s in IDS[:-1] if not gate(m, s)["ok"]]
        if red:
            raise task.Refused(f"S9 (publish) needs every gate S0-S8 green; red: {', '.join(red)} "
                               f"(including what was carried in when the migration was adopted). NEXT: {FH} migrate sitrep {m.tid}")
    p["stage"] = nxt
    p["history"].append({"stage": nxt, "entered": time.strftime("%Y-%m-%d %H:%M:%S"), "by": by})
    p.setdefault("last_gates", {})[cur] = True
    save_plan(m.tid, p)
    from . import guard
    guard.clear_work(m.tid)                       # an item of the stage just left is never active in the next one
    from . import sitrep
    rep = sitrep.build(m)
    task.journal(m.t, "migrate-stage", stage=nxt, left=cur, sitrep=sitrep.journal_block(rep))
    return p
