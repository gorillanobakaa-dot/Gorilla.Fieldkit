"""The runner (emerge): plain code from the first stage to the report; a model is called only when a stage fails.

Per stage, in order:
  1. its key (recipe.py: content-addressed, portable);
  2. a pass recorded for that key whose outputs still have their sha256 -> SKIP (the binpkg idea, for every stage:
     compiles, packages, tests and gates alike). Every pass and failure also goes to testcost's runs.jsonl under
     `emerge:<recipe>:<stage>` with the key, which gives the ETA (the median of the measured passes);
  3. a tier the mode does not include -> not run (leak gate once per release, package checks + post-install on the
     installed build); a docs-only change -> only the `privacy: true` stages;
  4. `offload:` and the stage cannot run here (or `prefer: true`) -> a run job to the other machine over the cable
     (orchestrate_work: the tree by digest, artefacts back with the sha256 the worker signed); stages whose needs are
     already satisfied are sent before the first local stage, so both machines work at once;
  5. else run here through the pipeline engine (fieldkit/core/pipeline.py), its declared outputs recorded by sha256.

On a failure: ONE fix-on-failure model run (autopilot) with the log tail and the recipe, per attempt, max_fix
attempts (default 2). The fix counts only as a commit on a branch that is not main (the recipe or the patch set);
an uncommitted edit, a commit on main or no change at all stops the run. Then the keys are computed again and the
run resumes: stages whose keys did not change are skipped. Past the limit, or for what a model cannot fix (a pin
that does not match, a platform with no offload, the cable), one item goes to `fieldkit inbox` and the run stops
with its report. A green run calls no model.

Preflight runs ONCE before the first stage: the recipe's own checks (pins, disk, RAM, long paths, thermal cap,
mandate) and then the one run guard (fieldkit/core/preflight.py: hog, testcost, scratch, and parity once merged).
Nothing asks the owner anything. Output: one line per stage with the ETA, the report (last-report.json), the
overhead (overhead.json, in fieldkit overhead's shape) and a journal (journal.jsonl) the overhead meter reads.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ..core.host import host, platform_ok
from .recipe import MODES, files_of, sha256_file

OK, WARN, STOP = "ok", "warn", "stop"
SKIPS = ("skip", "tier-skip", "docs-skip")
MAX_FIXES_PER_RUN = 6         # every stage together: a run never loops on model calls, whatever a recipe says


def check_name(recipe, sid):
    return f"emerge:{recipe}:{sid}"


def hostname():
    import platform
    return platform.node() or "here"


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _mins(s):
    return f"{s / 60:.1f} min" if s >= 60 else f"{s:.0f} s"


# -- the store: one record per (stage, key), the outputs of every host that built it ------------------------------
class Store:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, sid, key):
        return self.root / sid / f"{key}.json"

    def get(self, sid, key):
        try:
            return json.loads(self.path(sid, key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, sid, key, who, outputs, seconds):
        """Record a pass for `who`. -> [differences against the other hosts' outputs of the same key]."""
        rec = self.get(sid, key) or {"stage": sid, "key": key, "hosts": {}}
        diffs = []
        mine = {o["name"]: o["sha256"] for o in outputs}
        for other, ent in rec["hosts"].items():
            if other == who:
                continue
            for o in ent.get("outputs", []):
                if o["name"] in mine and mine[o["name"]] != o["sha256"]:
                    diffs.append({"stage": sid, "key": key, "name": o["name"], who: mine[o["name"]],
                                  other: o["sha256"]})
        rec["hosts"][who] = {"outputs": outputs, "seconds": round(seconds, 1), "at": _now()}
        rec["status"] = "pass"
        p = self.path(sid, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        return diffs

    def records(self):
        for p in sorted(self.root.glob("*/*.json")):
            try:
                yield json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

    def forget(self, sid=None):
        n = 0
        for p in self.root.glob(f"{sid or '*'}/*.json"):
            p.unlink()
            n += 1
        return n


def outputs_of(stage):
    """-> [{name, sha256, bytes, path}] for the declared outputs, or raise ValueError naming a pattern that matched
    nothing (fail closed: a stage that says it makes a file and did not, did not pass)."""
    out = []
    for pat in stage.get("outputs") or []:
        hits = files_of([pat])
        if not hits:
            raise ValueError(f"declared output missing: {pat}")
        for base, f in hits:
            out.append({"name": Path(f).relative_to(base).as_posix(), "sha256": sha256_file(f),
                        "bytes": os.path.getsize(f), "path": str(Path(f).resolve())})
    return out


def still_there(outputs):
    """Every recorded output is where it was, with the same bytes."""
    for o in outputs:
        p = Path(o.get("path") or "")
        if not p.is_file() or os.path.getsize(p) != o.get("bytes", -1) or sha256_file(p) != o["sha256"]:
            return False
    return True


# -- the fix path: autopilot, judged by git -------------------------------------------------------------------------
def default_fixer(prompt, cwd=None):
    from ..autopilot import run as ap
    return ap.claude(prompt, cwd=cwd, timeout=3600)


def _git(path, *a):
    try:
        r = subprocess.run(["git", "-C", str(path), *a], capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def fix_repos(recipe):
    """The git repositories a fix may change: the recipe's own and every git source."""
    out = []
    for d in [Path(recipe.source).parent] + [Path(s["git"]) for s in recipe.sources if s.get("git")]:
        top = _git(d, "rev-parse", "--show-toplevel") if d.exists() else None
        if top and top not in out:
            out.append(top)
    return out


def snapshot(repos):
    return {r: {"branch": _git(r, "rev-parse", "--abbrev-ref", "HEAD"), "head": _git(r, "rev-parse", "HEAD"),
                "dirty": _git(r, "status", "--porcelain", "--untracked-files=no")} for r in repos}


def watched_repos(recipe):
    """The checkouts a run must find as it left them: the recipe's own and every git source no stage fetches (a
    fetched source moves by design). Owner order 2026-10-29: a session committed on a new branch in the checkout a
    release emerge was running in."""
    fetched = {Path(s["git"]) for s in recipe.sources if s.get("git") and s.get("fetched_by")}
    out = []
    for d in [Path(recipe.source).parent] + [Path(s["git"]) for s in recipe.sources if s.get("git")]:
        top = _git(d, "rev-parse", "--show-toplevel") if d.exists() and d not in fetched else None
        if top and top not in out:
            out.append(top)
    return out


def moved(before, after):
    """-> a sentence naming each checkout whose branch or HEAD changed between two snapshots, else None."""
    parts = [f"{r}: {b['branch']}@{(b['head'] or '?')[:9]} -> {after[r]['branch']}@{(after[r]['head'] or '?')[:9]}"
             for r, b in before.items() if r in after and (b["branch"], b["head"]) != (after[r]["branch"], after[r]["head"])]
    return "; ".join(parts) or None


def judge(before, after):
    """-> (ok, why). A fix is a commit on a branch; anything else stops the run."""
    moved = []
    for r, a in after.items():
        b = before.get(r, {})
        if a["dirty"] != b.get("dirty") and a["dirty"]:
            return False, f"{r}: uncommitted edits after the fix run: a fix must be a commit, never a hand edit"
        if a["head"] != b.get("head"):
            if a["branch"] in ("main", "master"):
                return False, f"{r}: the fix was committed on {a['branch']}; fixes go on a fix/ branch"
            moved.append(f"{Path(r).name}@{a['branch']} {str(a['head'])[:10]}")
    if not moved:
        return False, "the fix run committed nothing"
    return True, "committed: " + ", ".join(moved)


def fix_prompt(recipe, sid, key, attempt, max_fix, tail, repos):
    stage = recipe.by_id[sid]
    return (f"fieldkit emerge: recipe {recipe.name} ({recipe.source}), stage {sid} failed (fix attempt {attempt} of "
            f"{max_fix}; key {key[:16]}).\nThe stage: {json.dumps(stage.get('run'))}\nIts log ends:\n{tail}\n\n"
            f"Find the cause and fix it AS A CHANGE TO THE RECIPE OR ITS PATCH SET, committed on the branch "
            f"fix/emerge-{recipe.name}-{sid} (never main) in the repository it belongs to: {', '.join(repos)}. "
            "Never leave an uncommitted edit in a build tree, never weaken a check, remove a stage or change a pin, "
            "never run the stage yourself: the runner runs it again. If only the owner can fix it (a decision, a "
            "credential, hardware), add it with `fieldkit inbox add` and change nothing. Say what you changed in "
            "one line.")


# -- offload: a run job over the cable -------------------------------------------------------------------------------
def _house():
    """This machine's household.json ({} when not paired or unreadable: the target is then said unknown)."""
    from ..peers import link
    try:
        return link.household()
    except Exception:  # noqa: BLE001 - an unreadable household is "not paired"
        return {}


def offload_target(device, h=None):
    """-> (row of household.yaml, the link's name for it or None, why it cannot be called or ""). The recipe's
    device (an id, alias or tag) resolves through household.yaml; the link name comes from household.json by tag."""
    from ..peers import household
    h = _house() if h is None else h
    row = household.resolve(device, h=h)
    if row["path"] != "cable":
        return row, None, f"{row['id']} {row['tag']} is reached by {row['path']} only, never the cable"
    name = household.cable_name(row, h)
    return row, name, "" if name else (f"no device of this machine's household.json is {row['tag']} "
                                       f"(fieldkit machine reach; fieldkit peers link devices)")


def target_text(device, h=None):
    """`l15 [L15-WIN] = NAME` for the plan line; the reason instead of the name when the link cannot call it."""
    try:
        row, name, why = offload_target(device, h)
    except ValueError as e:
        return f"{device} ({e})"
    return f"{row['id']} {row['tag']}" + (f" = {name}" if name else f" ({why})")


class LinkOffloader:
    """distcc for stages: orchestrate_work.send (the tree by digest), the signed result, the artefacts by sha256."""

    def __init__(self, household=None):
        self.household = household

    def dispatch(self, recipe, stage, key):
        from .. import orchestrate_work as ow
        o = stage["offload"]
        try:
            _row, name, why = offload_target(o["device"], self.household)
            if not name:
                return {"ok": False, "why": why}
            jid, said, t = ow.send("run", name, o["tree"], o.get("project", "Gorilla.Fieldkit"),
                                   argv=list(o["argv"]), artefacts=list(o.get("artefacts") or []),
                                   wall=int(o.get("wall", 60)))
        except Exception as e:  # noqa: BLE001 - an unreachable worker is a failed dispatch, said plainly
            return {"ok": False, "why": f"{type(e).__name__}: {e}"}
        good = said == "sent" or str(said).startswith("already")
        return {"ok": good, "why": said, "jid": jid, "digest": (t or {}).get("digest"), "device": name,
                "project": o.get("project", "Gorilla.Fieldkit"), "wall": int(o.get("wall", 60))}

    def collect(self, h):
        if not h.get("ok"):
            return {"ok": False, "why": h.get("why")}
        from .. import orchestrate_work as ow
        from ..mandate import jobs
        jobs.wait(h["jid"], h["wall"] * 60)
        r = ow.verified_result(h["jid"], h["project"], h["digest"])
        if r is None:
            return {"ok": False, "why": "no result signed by a pinned worker key"}
        checks = ow.artefacts_check(r)
        arts = r.get("artefacts") or []
        outs = [{"name": a.get("name"), "sha256": a.get("sha256"), "bytes": a.get("bytes", -1), "path": where}
                for a, (_n, ok, where) in zip(arts, checks) if ok]
        ok = r.get("status") == "pass" and all(ok for _n, ok, _w in checks)
        return {"ok": ok, "outputs": outs, "host": r.get("host") or h["device"],
                "why": f"{r.get('status')} on {r.get('host')}; {len(outs)} of {len(checks)} artefact(s) verified"}


# -- preflight: the recipe's own checks, once, before stage 1 ---------------------------------------------------------
def own_checks(recipe, mode, offloads, disk=None, ram=None, longpath_rows=None, thermal=None):
    """-> [(name, status, line)]. Plain reads; nothing changes."""
    import shutil
    rows = []
    for s in recipe.sources:
        st, _v, why = recipe.pin(s)
        if st == "ok" or (st == "absent" and s.get("fetched_by")):
            rows.append((f"pin {s.get('id')}", OK, why))
        else:
            rows.append((f"pin {s.get('id')}", STOP, why))
    pf = recipe.preflight
    if pf.get("disk_gb"):
        where = Path(pf.get("path") or recipe.state_dir)
        while not where.exists() and where != where.parent:
            where = where.parent
        free = (disk or (lambda p: shutil.disk_usage(p).free))(where) / 2 ** 30
        rows.append(("disk", OK if free >= float(pf["disk_gb"]) else STOP,
                     f"{free:.0f} GB free on {where}, the recipe needs {pf['disk_gb']} GB"))
    if pf.get("ram_gb"):
        try:
            avail = (ram or (lambda: __import__("psutil").virtual_memory().available))() / 2 ** 30
            rows.append(("ram", OK if avail >= float(pf["ram_gb"]) else WARN,
                         f"{avail:.1f} GB RAM available, the recipe wants {pf['ram_gb']} GB (the hog frees idle models)"))
        except Exception as e:  # noqa: BLE001
            rows.append(("ram", WARN, f"not readable ({type(e).__name__})"))
    if longpath_rows is None:
        try:
            from ..core import longpath
            longpath_rows = longpath.machine()
        except Exception as e:  # noqa: BLE001
            longpath_rows = [("long paths", False, f"not readable ({type(e).__name__})")]
    bad = [f"{n}: {d}" for n, ok, d in longpath_rows if not ok]
    rows.append(("long paths", WARN if bad else OK, "; ".join(bad) or "on"))
    if thermal is None:
        from ..core import settings
        thermal = (settings.local_settings().get("thermal") or {}).get("build_target")
    rows.append(("thermal cap", OK if thermal else WARN,
                 f"governor target {thermal} C" if thermal else "no thermal.build_target in fieldkit.local.json"))
    if offloads:
        m = recipe.config_digest().get("mandate") if recipe.config_spec.get("mandate") else None
        rows.append(("mandate", OK if isinstance(m, dict) else WARN,
                     "in force for the offload jobs" if isinstance(m, dict)
                     else f"no mandate entry ({m}): offloaded stages fall back to this machine where they can"))
    return rows


class Emerge:
    def __init__(self, recipe, mode="iterate", say=print, fixer=None, offloader=None, inbox_add=None,
                 clock=time.monotonic, hold=False, guard=True, allow_fix=True, local_only=False, who=None,
                 checks=None, household=None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {sorted(MODES)}")
        self.recipe, self.mode, self.say = recipe, mode, say
        self.fixer = fixer or default_fixer
        self.household = household    # tests: household.json given, never this machine's
        self.offloader = offloader or LinkOffloader(household)
        self.inbox_add = inbox_add
        self.clock, self.hold, self.guard = clock, hold, guard
        self.allow_fix, self.local_only = allow_fix, local_only
        self.who = who or hostname()
        self.checks = checks          # tests: the own checks given, not read from this machine
        self.store = Store(recipe.state_dir / "cache")
        self.llm_calls = 0

    # -- what would run ----------------------------------------------------------------------------------------------
    def _eta(self, sid):
        from .. import testcost
        return testcost.budget(check_name(self.recipe.name, sid), last=5, minimum=1)

    def _docs_mode(self):
        d = self.recipe.docs_only
        src = next((s for s in self.recipe.sources if s.get("id") == d.get("source")), None) if d else None
        if not src or not src.get("git"):
            return False, ""
        try:
            last = json.loads((self.recipe.state_dir / "last-green.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, "no green run recorded yet"
        base = (last.get("heads") or {}).get(src["id"])
        if not base:
            return False, "no green run recorded yet"
        changed = (_git(src["git"], "diff", "--name-only", base) or "").splitlines()
        changed += [l[3:] for l in (_git(src["git"], "status", "--porcelain", "--untracked-files=no") or "").splitlines()]
        changed = sorted(set(c for c in changed if c))
        if not changed:
            return False, "nothing changed"
        from .. import testcost
        return testcost.is_docs_only(changed), f"{len(changed)} changed file(s)"

    def classify(self, keys, docs=False):
        rows = []
        tiers = MODES[self.mode]
        for s in self.recipe.stages:
            sid, key = s["id"], keys[s["id"]]
            tier = s.get("tier", "every")
            row = {"id": sid, "key": key, "tier": tier, "needs": self.recipe.needs(sid), "eta_s": self._eta(sid)}
            rec = self.store.get(sid, key)
            cached = None
            if rec and not s.get("always_run"):
                cached = next((h for h, e in rec["hosts"].items() if still_there(e.get("outputs") or [])
                               and (h == self.who or e.get("outputs"))), None)
                if cached and not self._verify_cached(s):
                    cached = None
            o = s.get("offload")
            here = platform_ok(s.get("platforms"))
            if tier not in tiers:
                row.update(action="tier-skip", why=f"tier {tier}: runs with --mode {tier}"
                           + (" (once per release, on the final build)" if tier == "release" else
                              " (once, on the installed build)"))
            elif docs and not s.get("privacy"):
                row.update(action="docs-skip", why="only documentation changed: the privacy stages run")
            elif cached:
                row.update(action="skip", why=f"key {key[:12]} built on {cached}, outputs verified")
            elif o and not self.local_only and (not here or o.get("prefer")):
                row.update(action="offload", why=f"to {target_text(o.get('device'), self.household)}"
                           + ("" if here else f" (needs {s.get('platforms')})"))
            elif not here:
                row.update(action="not-here", why=f"needs {s.get('platforms')}, this is {host()['system']}"
                           + (" (optional)" if s.get("optional") else ""))
            else:
                row.update(action="run", why="always runs" if s.get("always_run") else
                           ("key changed" if any(True for _ in (self.recipe.state_dir / "cache" / sid).glob("*.json"))
                            else "never built"))
            rows.append(row)
        return rows

    def _verify_cached(self, s):
        """The pipeline's own file and python checks still pass (its output checks passed when it was recorded)."""
        checks = [c for c in s.get("verify") or [] if not ({"output_contains", "output_lacks"} & set(c))]
        if not checks:
            return True
        from ..core.pipeline import Context
        ok, _msgs = self.recipe.pipe.verify(dict(s, verify=checks), Context(self.recipe.pipe))
        return ok is not False

    def plan(self):
        t0 = self.clock()
        keys = self.recipe.keys()
        docs, docs_why = self._docs_mode()
        rows = self.classify(keys, docs)
        checks = self.checks if self.checks is not None else own_checks(
            self.recipe, self.mode, [r for r in rows if r["action"] == "offload"])
        todo = [r for r in rows if r["action"] in ("run", "offload")]
        known = [r["eta_s"] for r in todo if r["eta_s"] is not None]
        return {"recipe": self.recipe.name, "mode": self.mode, "host": self.who, "docs_only": docs,
                "docs_why": docs_why, "stages": rows, "preflight": checks,
                "preflight_status": max((c[1] for c in checks), key=[OK, WARN, STOP].index, default=OK),
                "eta_s": sum(known), "eta_unknown": len(todo) - len(known),
                "hash_s": round(self.clock() - t0, 2), "baseline_minutes": self.recipe.baseline_minutes}

    # -- the run -----------------------------------------------------------------------------------------------------
    def _line(self, i, n, sid, word, detail=""):
        self.say(f"[{i}/{n}] {sid:<16} {word:<9} {detail}".rstrip())

    def _journal(self, event, **kw):
        with open(self.recipe.state_dir / "journal.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(t=_now(), event=event, recipe=self.recipe.name, **kw)) + "\n")

    def _inbox(self, title, why):
        add = self.inbox_add
        if add is None:
            from ..autopilot import inbox
            add = inbox.add
        try:
            return add(title, why, f"fieldkit emerge run {self.recipe.name} --mode {self.mode}", kind="build")
        except Exception as e:  # noqa: BLE001 - the report still says it
            self.say(f"  inbox not written: {e}")
            return None

    def _execute(self, s):
        """Run one stage here through the pipeline engine. -> (passed, seconds, outputs, tail)."""
        t0 = self.clock()
        rep = self.recipe.pipe._run(only=[s["id"]], force=True)
        secs = self.clock() - t0
        entry = (rep.get("stages") or [{}])[0]
        passed = entry.get("status") in ("done", "ran-unverified")
        res = entry.get("result") or {}
        tail = ""
        if isinstance(res, dict):
            log = res.get("log")
            if log and Path(log).is_file():
                tail = "\n".join(Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-60:])
            else:
                tail = "\n".join((res.get("stdout_tail") or []) + (res.get("stderr_tail") or [])) or \
                       json.dumps(res, default=str)[-3000:]
        tail += "".join(f"\n{m}" for m in entry.get("verify") or [] if m.startswith("FAIL"))
        if entry.get("triage"):
            tail += f"\ntriage: {json.dumps(entry['triage'], default=str)[:1500]}"
        outs = []
        if passed:
            try:
                outs = outputs_of(s)
            except ValueError as e:
                passed, tail = False, f"{tail}\n{e}"
        return passed, secs, outs, tail.strip()

    def run(self, only=None):
        # under a liveness beacon: heartbeat + every stage's container members (fieldkit liveness check sees it)
        from ..core import liveness
        with liveness.beacon("emerge", self.recipe.name, f"fieldkit emerge {self.recipe.name}"):
            return self._run_all(only)

    def _run_all(self, only=None):
        t_start, wall0 = _now(), self.clock()
        r = self.recipe
        b = {"bare": 0.0, "rework": 0.0, "tests": 0.0, "tools": 0.0, "llm": 0.0, "owner": 0.0}
        report = {"recipe": r.name, "mode": self.mode, "host": self.who, "started": t_start, "ok": True,
                  "stages": [], "fixes": [], "repro": [], "advisories": [], "inbox": None}
        self._journal("build-start", mode=self.mode)
        t = self.clock()
        pl = self.plan()
        b["tools"] += self.clock() - t
        n = len(pl["stages"])
        todo = [x for x in pl["stages"] if x["action"] in ("run", "offload")]
        self.say(f"emerge {r.name} ({self.mode}) on {self.who}: {n} stage(s), {len(todo)} to build, "
                 f"{sum(x['action'] == 'skip' for x in pl['stages'])} cached, "
                 f"{sum(x['action'] == 'offload' for x in pl['stages'])} offloaded; ETA {_mins(pl['eta_s'])}"
                 + (f" + {pl['eta_unknown']} never measured" if pl["eta_unknown"] else ""))
        for name, st, line in pl["preflight"]:
            if st != OK:
                self.say(f"  preflight {name}: {st.upper()} {line}")
        if pl["preflight_status"] == STOP:
            report.update(ok=False, refused="; ".join(l for _n, s, l in pl["preflight"] if s == STOP))
            self.say(f"REFUSED before stage 1: {report['refused']}")
            return self._finish(report, b, wall0, t_start)
        if self.hold:
            try:
                from ..buildh.window import hold_awake
                hold_awake(os.getpid())
            except Exception as e:  # noqa: BLE001
                self.say(f"  stay awake: not held ({e})")
        if not self.guard:
            self._loop(report, b, only)
            return self._finish(report, b, wall0, t_start)
        from ..core import preflight
        t = self.clock()
        try:
            with preflight.guard(f"emerge {r.name}", "build", say=self.say):
                b["tools"] += self.clock() - t
                self._loop(report, b, only)
        except preflight.Refused as e:
            report.update(ok=False, refused=str(e))
            self.say(f"REFUSED by the run guard: {e}")
        return self._finish(report, b, wall0, t_start)

    def _loop(self, report, b, only):
        from .. import testcost
        from ..autopilot import hardlines
        fixes = {}
        while True:
            t = self.clock()
            watch = watched_repos(self.recipe)
            base = snapshot(watch)
            self.recipe.forget()
            keys = self.recipe.keys()
            docs, _ = self._docs_mode()
            rows = self.classify(keys, docs)
            b["tools"] += self.clock() - t
            if only:
                rows = [x for x in rows if x["id"] in only]
            n, done_ids = len(rows), {x["id"] for x in rows if x["action"] in SKIPS}
            handles = {}
            for x in rows:            # independent offloads go first, so both machines work at once
                if x["action"] == "offload" and all(d in done_ids for d in x["needs"]):
                    handles[x["id"]] = self.offloader.dispatch(self.recipe, self.recipe.by_id[x["id"]], x["key"])
            failed = None
            for i, x in enumerate(rows, 1):
                sid, s = x["id"], self.recipe.by_id[x["id"]]
                ent = {"id": sid, "key": x["key"], "action": x["action"]}
                report["stages"] = [e for e in report["stages"] if e["id"] != sid] + [ent]
                if hardlines.STOP_FILE.exists():
                    report.update(ok=False, stopped="the owner's STOP file")
                    self.say(f"STOPPED before {sid}: the owner's STOP file")
                    return
                if x["action"] not in SKIPS and (why := moved(base, snapshot(watch))):
                    report.update(ok=False, refused=f"the checkout moved during the run ({why})")
                    ent["status"] = "aborted"
                    self.say(f"ABORTED before {sid}: the checkout moved during the run ({why}). Another writer used "
                             "it; work in a worktree (git worktree add ../wt-NAME -b BRANCH origin/main)")
                    return
                if x["action"] in SKIPS:
                    ent["status"] = x["action"]
                    self._line(i, n, sid, x["action"].upper(), x["why"])
                    continue
                if x["action"] == "not-here":
                    ent["status"] = "not-here"
                    self._line(i, n, sid, "NOT-HERE", x["why"])
                    if not s.get("optional"):
                        failed = (sid, x["key"], x["why"], False)
                        break
                    continue
                eta = f"eta {_mins(x['eta_s'])}" if x["eta_s"] is not None else "eta unknown (first run)"
                if x["action"] == "offload":
                    self._line(i, n, sid, "OFFLOAD", f"{x['why']}, {eta}")
                    t = self.clock()
                    h = handles.get(sid) or self.offloader.dispatch(self.recipe, s, x["key"])
                    res = self.offloader.collect(h)
                    secs = self.clock() - t
                    if res.get("ok"):
                        ent.update(status="pass", seconds=round(secs, 1), host=res.get("host"))
                        diffs = self.store.put(sid, x["key"], res.get("host") or s["offload"]["device"],
                                               res.get("outputs") or [], secs)
                        self._passed(i, n, s, x, secs, res.get("outputs") or [], diffs, report, b, testcost)
                        continue
                    self.say(f"    offload failed: {res.get('why')}")
                    if not platform_ok(s.get("platforms")):
                        ent["status"] = "failed"
                        b["rework"] += secs
                        failed = (sid, x["key"], f"offload to {s['offload'].get('device')}: {res.get('why')}", False)
                        break
                    self.say("    running it here instead")
                self._line(i, n, sid, "RUN", eta)
                self._journal("build-stage-start", stage=sid, key=x["key"])
                passed, secs, outs, tail = self._execute(s)
                testcost.record(check_name(self.recipe.name, sid), "pass" if passed else "fail", secs, key=x["key"])
                if passed:
                    bad_pin = self._pins_after(sid)     # before the record: a wrong pin is never cached as a pass
                    if bad_pin:
                        ent["status"] = "failed"
                        failed = (sid, x["key"], bad_pin, False)
                        break
                    ent.update(status="pass", seconds=round(secs, 1))
                    diffs = self.store.put(sid, x["key"], self.who, outs, secs)
                    self._passed(i, n, s, x, secs, outs, diffs, report, b, testcost, recorded=True)
                    continue
                b["tests" if s.get("advisory") else "rework"] += secs
                ent.update(status="failed", seconds=round(secs, 1), tail=tail[-2000:])
                self._line(i, n, sid, "FAILED", f"after {_mins(secs)}")
                self._journal("build-stop", stage=sid, key=x["key"], seconds=round(secs, 1))
                if s.get("advisory"):
                    report["advisories"].append({"stage": sid, "why": tail[-500:]})
                    self.say("    advisory stage: reported, the run goes on")
                    continue
                failed = (sid, x["key"], tail, s.get("fix", True))
                break
            if not failed:
                self._green()
                return
            sid, key, tail, fixable = failed
            n_fix = fixes.get(sid, 0)
            max_fix = int(self.recipe.by_id[sid].get("max_fix", 2))
            if not (fixable and self.allow_fix and n_fix < max_fix) or sum(fixes.values()) >= MAX_FIXES_PER_RUN:
                self._always(report, sid)
                report["ok"] = False
                report["stopped_at"] = sid
                why = (f"stage {sid} failed" + (f" after {n_fix} fix attempt(s)" if n_fix else "")
                       + f" (key {key[:12]}). Log tail:\n{tail[-1500:]}")
                report["inbox"] = self._inbox(f"emerge {self.recipe.name}: stage {sid} failed", why)
                self.say(f"STOPPED at {sid}: in the owner's inbox (fieldkit inbox), the log tail in the report")
                return
            fixes[sid] = n_fix + 1
            repos = fix_repos(self.recipe)
            before = snapshot(repos)
            self.say(f"    fix attempt {fixes[sid]}/{max_fix}: one autopilot model run with the log tail")
            t = self.clock()
            self.llm_calls += 1
            try:
                self.fixer(fix_prompt(self.recipe, sid, key, fixes[sid], max_fix, tail[-4000:], repos),
                           cwd=repos[0] if repos else None)
            except Exception as e:  # noqa: BLE001 - a fixer that crashed fixed nothing; the judge says so
                self.say(f"    the fix run failed: {e}")
            b["llm"] += self.clock() - t
            ok, why = judge(before, snapshot(repos))
            report["fixes"].append({"stage": sid, "attempt": fixes[sid], "ok": ok, "why": why})
            self.say(f"    fix {'accepted' if ok else 'REFUSED'}: {why}")
            if not ok:
                self._always(report, sid)
                report.update(ok=False, stopped_at=sid)
                report["inbox"] = self._inbox(f"emerge {self.recipe.name}: the fix for {sid} was refused",
                                              f"{why}\nLog tail:\n{tail[-1500:]}")
                return
            self.recipe = self.recipe.reload()

    def _always(self, report, failed_sid):
        """The stages marked `always: true` after the failed one still run (putting the power scheme back)."""
        ids = [s["id"] for s in self.recipe.stages]
        for s in self.recipe.stages[ids.index(failed_sid) + 1:]:
            if s.get("always") and platform_ok(s.get("platforms")):
                passed, secs, _o, _t = self._execute(s)
                report["stages"].append({"id": s["id"], "action": "always", "status": "pass" if passed else "failed",
                                         "seconds": round(secs, 1)})
                self.say(f"    always: {s['id']} {'done' if passed else 'FAILED'} after {_mins(secs)}")

    def _pins_after(self, sid):
        """A source this stage fetched must match its pin now (fail closed: a wrong pin is never fixed by a model)."""
        for src in self.recipe.sources:
            if src.get("fetched_by") == sid:
                st, _v, why = self.recipe.pin(src)
                if st != "ok":
                    self.say(f"    PIN {st.upper()}: {why}")
                    return f"pin {src.get('id')} after {sid}: {why}"
        return None

    def _passed(self, i, n, s, x, secs, outs, diffs, report, b, testcost, recorded=False):
        sid = s["id"]
        if not recorded:
            testcost.record(check_name(self.recipe.name, sid), "pass", secs, key=x["key"])
        b["tests" if s.get("bucket") == "tests" else "tools" if s.get("bucket") == "tools" else "bare"] += secs
        report.setdefault("bare_parts", {})[sid] = round(secs, 1)
        self._journal("build-stage-done", stage=sid, key=x["key"], seconds=round(secs, 1),
                      outputs=[o["name"] for o in outs])
        self._line(i, n, sid, "DONE", f"{_mins(secs)}, {len(outs)} output(s), key {x['key'][:12]}")
        for d in diffs:
            report["repro"].append(d)
            self.say(f"    REPRO DIFF {d['name']}: same key, different bytes on {', '.join(k for k in d if k not in ('stage', 'key', 'name'))}")

    def _green(self):
        heads = {s["id"]: _git(s["git"], "rev-parse", "HEAD") for s in self.recipe.sources
                 if s.get("git") and Path(s["git"]).exists()}
        (self.recipe.state_dir / "last-green.json").write_text(json.dumps({"at": _now(), "heads": heads}, indent=1),
                                                                encoding="utf-8")

    def _finish(self, report, b, wall0, t_start):
        wall = self.clock() - wall0
        report["finished"] = _now()
        report["llm_calls"] = self.llm_calls
        b["idle"] = max(0.0, wall - sum(b.values()))
        bare = b["bare"]
        work = bare + b["tests"]      # the deterministic work a hand script also does: the ratio's denominator
        ratio = round(wall / work, 2) if work >= 1 else None
        base = self.recipe.baseline_minutes
        ov = {"job": f"emerge {self.recipe.name}", "kind": "build", "start": t_start[:16], "end": report["finished"][:16],
              "wall_s": int(round(wall)), "bare_s": int(round(bare)) if bare else None,
              "bare_parts": report.get("bare_parts", {}), "ratio": ratio, "max_ratio": self.recipe.max_ratio,
              "level": "NOBASE" if ratio is None else ("OK" if ratio <= self.recipe.max_ratio else "WARN"),
              "buckets": {k: int(round(v)) for k, v in b.items()},
              "rework": {e["id"]: e.get("seconds", 0) for e in report["stages"] if e.get("status") == "failed"},
              "runs": {e["id"]: 1 for e in report["stages"] if e.get("status") in ("pass", "failed")},
              "counts": {"stages": len(report["stages"]),
                         "built": sum(e.get("status") == "pass" for e in report["stages"]),
                         "cached": sum(e.get("status") == "skip" for e in report["stages"]),
                         "fixes": len(report["fixes"]), "llm_calls": self.llm_calls, "owner_prompts": 0},
              "sessions": [], "baseline_s": int(base * 60) if base else None,
              "notes": [f"mode {self.mode}", f"host {self.who}", "ratio = wall / (bare + tests)"]
              + ([f"vs the pre-AI baseline {base} min: {wall / 60:.1f} min wall"] if base else [])}
        report["overhead"] = ov
        sd = self.recipe.state_dir
        (sd / "last-report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        (sd / "overhead.json").write_text(json.dumps(ov, indent=1), encoding="utf-8")
        with open(sd / "overhead.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(ov) + "\n")
        self._journal("build-done" if report["ok"] else "build-failed", seconds=int(round(wall)))
        self.say(f"{'GREEN' if report['ok'] else 'NOT GREEN'}: emerge {self.recipe.name} in {_mins(wall)} wall, "
                 f"{_mins(bare)} building ({ov['counts']['built']} built, {ov['counts']['cached']} cached), "
                 f"{self.llm_calls} model call(s), 0 owner prompts; overhead {ov['level']}"
                 + (f" ratio {ratio}" if ratio else "") + f"; report {sd / 'last-report.json'}")
        return report


def export(recipe):
    """Every recorded key with each host's outputs: what another machine compares with (fieldkit emerge repro)."""
    return {"recipe": recipe.name, "host": hostname(), "at": _now(),
            "records": [{"stage": x["stage"], "key": x["key"], "hosts": {h: [{"name": o["name"], "sha256": o["sha256"]}
                                                                             for o in e.get("outputs", [])]
                                                                         for h, e in x["hosts"].items()}}
                        for x in Store(recipe.state_dir / "cache").records()]}


def compare(mine, theirs):
    """-> {same, different, only_here, only_there}: outputs of the SAME key on two machines must be the same bytes."""
    def flat(ex):
        out = {}
        for x in ex.get("records", []):
            for h, outs in x["hosts"].items():
                for o in outs:
                    out.setdefault((x["stage"], x["key"], o["name"]), {})[h] = o["sha256"]
        return out
    a, b = flat(mine), flat(theirs)
    res = {"same": [], "different": [], "only_here": [], "only_there": []}
    for k in sorted(set(a) | set(b)):
        if k not in b:
            res["only_here"].append(k)
        elif k not in a:
            res["only_there"].append(k)
        else:
            sa, sb = set(a[k].values()), set(b[k].values())
            (res["same"] if sa == sb and len(sa) == 1 else res["different"]).append(
                {"stage": k[0], "key": k[1], "name": k[2], "here": a[k], "there": b[k]})
    return res


if __name__ == "__main__":       # pragma: no cover
    sys.exit("use: fieldkit emerge")
