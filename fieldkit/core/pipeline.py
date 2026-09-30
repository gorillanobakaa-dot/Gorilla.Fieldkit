"""The pipeline engine: staged, resumable, deterministic.

Generalised from the Gorilla.firefox Windows harness (preflight -> deps ->
source -> patches -> build -> package), which proved the shape on a real
multi-hour build: stages run in order, each is independently runnable and
idempotent, and a failure late on never means redoing the early stages.

A pipeline is data (YAML or JSON), not code:

    name: example
    vars: {version: "7.1.2", src: "${LOCAL:kernel.workdir}/linux-{version}"}
    stages:
      - id: config
        platforms: [debian]            # optional; windows | linux | debian
        run:  {cmd: [make, -C, "{src}", olddefconfig]}
        #  or {python: "fieldkit.build.kernel:apply_config", args: {...}}
        verify:                        # proves the artefact, not the exit code
          - {file_contains: {path: "{src}/.config", pattern: "^CONFIG_EROFS_FS=y"}}
          # also: files_exist, output_contains / output_lacks (regex on what
          # the stage printed), python (a check function returning {ok, detail})
        triage: kernel                 # signature set used if this stage fails
        timeout: 600

Rules the engine enforces:
- Verify the artefact, never the exit code: a stage is only "done" when its
  verify checks pass. A stage with no verify is re-run every time.
- A stage is skipped as up to date only if it passed before, its resolved
  definition is unchanged (fingerprint) and its verify checks still pass.
- A stage for another platform is reported as "not-this-platform", which
  stops the run unless the stage says `optional: true`. Gaps cannot hide.
- A stage marked `always: true` runs even after an earlier failure (clean-up
  such as putting the power scheme back after a thermal-capped build).
- Every run writes a JSON report; agents read that, not the console.
"""
import glob
import hashlib
import importlib
import json
import re
import time
from pathlib import Path

from . import settings
from .host import host, platform_ok
from .proc import Runner

_LOCAL_VAR = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class PipelineError(ValueError):
    pass


def _fill(value, vars_):
    """Substitute {name} for known pipeline vars; leave any other braces alone."""
    if isinstance(value, dict):
        return {k: _fill(v, vars_) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, vars_) for v in value]
    if isinstance(value, str):
        for _ in range(5):                       # vars may refer to vars
            new = _LOCAL_VAR.sub(lambda m: str(vars_[m.group(1)]) if m.group(1) in vars_ else m.group(0), value)
            if new == value:
                break
            value = new
    return value


def _call(dotted, *args, **kwargs):
    mod, _, fn = dotted.partition(":")
    if not fn:
        raise PipelineError(f"python target must be 'module:function', got {dotted!r}")
    return getattr(importlib.import_module(mod), fn)(*args, **kwargs)


class Context:
    """What a python stage receives."""

    def __init__(self, pipeline, dry_run=False):
        self.pipeline = pipeline
        self.vars = pipeline.vars
        self.host = host()
        self.runner = pipeline.runner
        self.dry_run = dry_run
        self.state_dir = pipeline.state_dir


class Pipeline:
    def __init__(self, spec, source="<memory>", state_dir=None, overrides=None, strict=True):
        spec = settings.expand(spec, strict=strict)
        if not isinstance(spec, dict) or "stages" not in spec or "name" not in spec:
            raise PipelineError(f"{source}: a pipeline needs 'name' and 'stages'")
        self.name = spec["name"]
        self.description = spec.get("description", "")
        self.source = source
        vars_ = dict(spec.get("vars") or {})
        vars_.update(overrides or {})
        self.vars = {k: _fill(v, vars_) for k, v in vars_.items()}
        ids = [s.get("id") for s in spec["stages"]]
        if None in ids or len(set(ids)) != len(ids):
            raise PipelineError(f"{source}: every stage needs a unique 'id'")
        for s in spec["stages"]:
            if "run" not in s:
                raise PipelineError(f"{source}: stage {s['id']} has no 'run'")
            if not ({"cmd", "python"} & set(s["run"])):
                raise PipelineError(f"{source}: stage {s['id']}: run needs 'cmd' or 'python'")
        self.stages = [_fill(s, self.vars) for s in spec["stages"]]
        self.state_dir = Path(state_dir or settings.ROOT / "state" / self.name)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "state.json"
        self.runner = Runner(self.state_dir, self.state_dir / "logs")

    @classmethod
    def load(cls, path, **kw):
        return cls(settings.read_file(path), source=str(path), **kw)

    # -- state ---------------------------------------------------------------
    def state(self):
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, st):
        self.state_file.write_text(json.dumps(st, indent=1), encoding="utf-8")

    @staticmethod
    def fingerprint(stage):
        return hashlib.sha256(json.dumps(stage, sort_keys=True).encode()).hexdigest()[:16]

    # -- verify --------------------------------------------------------------
    def verify(self, stage, ctx, output=None):
        """Run a stage's verify checks. Returns (ok, [messages]). No checks = (None, []).

        output: what the stage printed in this run (None when checking whether an
        earlier run is still up to date - an output check then fails, so stages
        proven only by their output always re-run, which is right for preflights).
        """
        checks = stage.get("verify") or []
        if not checks:
            return None, []
        msgs, ok = [], True
        for chk in checks:
            (kind, arg), = chk.items()
            if kind == "files_exist":
                for p in (arg if isinstance(arg, list) else [arg]):
                    good = bool(glob.glob(p)) if any(c in p for c in "*?[") else Path(p).exists()
                    msgs.append(f"{'ok  ' if good else 'FAIL'} exists: {p}")
                    ok &= good
            elif kind == "file_contains":
                p = Path(arg["path"])
                good = p.is_file() and re.search(arg["pattern"], p.read_text(encoding="utf-8", errors="replace"),
                                                 re.M) is not None
                msgs.append(f"{'ok  ' if good else 'FAIL'} {p.name} matches {arg['pattern']!r}")
                ok &= bool(good)
            elif kind == "output_contains":
                good = output is not None and re.search(arg, output, re.M) is not None
                msgs.append(f"{'ok  ' if good else 'FAIL'} output matches {arg!r}"
                            + ("" if output is not None else " (no output from this run)"))
                ok &= bool(good)
            elif kind == "output_lacks":
                good = output is not None and re.search(arg, output, re.M) is None
                msgs.append(f"{'ok  ' if good else 'FAIL'} output free of {arg!r}")
                ok &= bool(good)
            elif kind == "python":
                try:
                    res = _call(arg["target"], ctx, **(arg.get("args") or {}))
                except Exception as e:  # noqa: BLE001 - a crashing check is a failed check, not a crashed run
                    res = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
                good = bool(res.get("ok")) if isinstance(res, dict) else bool(res)
                msgs.append(f"{'ok  ' if good else 'FAIL'} {arg['target']}"
                            + (f": {res.get('detail')}" if isinstance(res, dict) and res.get("detail") else ""))
                ok &= good
            else:
                raise PipelineError(f"unknown verify kind {kind!r} in stage {stage['id']}")
        return ok, msgs

    # -- run -------------------------------------------------------------------
    def plan(self):
        """What would run, fully resolved, without running anything."""
        st = self.state()
        out = []
        for s in self.stages:
            prev = st.get(s["id"], {})
            out.append({"id": s["id"], "platforms": s.get("platforms") or ["any"],
                        "runs_here": platform_ok(s.get("platforms")), "run": s["run"],
                        "verify": s.get("verify") or [], "last_status": prev.get("status"),
                        "changed_since_last": prev.get("fingerprint") not in (None, self.fingerprint(s))})
        return {"pipeline": self.name, "host": host(), "vars": self.vars, "stages": out}

    def run(self, only=None, start=None, dry_run=False, force=False):
        st = self.state()
        ctx = Context(self, dry_run)
        report = {"pipeline": self.name, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "dry_run": dry_run,
                  "host": host()["system"], "stages": [], "ok": True}
        ids = [s["id"] for s in self.stages]
        for name in [n for n in (only or []) + ([start] if start else []) if n]:
            if name not in ids:
                raise PipelineError(f"no stage {name!r}; stages are {ids}")
        started = start is None
        failed = False
        for s in self.stages:
            sid = s["id"]
            if failed and not s.get("always"):
                continue
            if not started:
                started = sid == start
                if not started:
                    continue
            if only and sid not in only:
                continue
            entry = {"id": sid}
            report["stages"].append(entry)
            if not platform_ok(s.get("platforms")):
                entry["status"] = "not-this-platform"
                entry["detail"] = f"needs {s.get('platforms')}, this is {host()['system']}"
                if not s.get("optional"):
                    report["ok"] = False
                    report.setdefault("stopped_at", sid)
                    failed = True
                continue
            fp = self.fingerprint(s)
            prev = st.get(sid, {})
            if not force and prev.get("status") == "done" and prev.get("fingerprint") == fp:
                vok, vmsgs = self.verify(s, ctx)
                if vok:
                    entry.update(status="up-to-date", verify=vmsgs)
                    continue
            if dry_run:
                entry.update(status="would-run", run=s["run"])
                continue
            t0 = time.monotonic()
            run = s["run"]
            if "cmd" in run:
                res = self.runner.run(run["cmd"], cwd=run.get("cwd"), env=run.get("env"),
                                      timeout=s.get("timeout"), name=f"{self.name}-{sid}")
                entry["result"] = res.as_dict(tail=15)
                action_ok, log, output = res.ok, res.log, res.stdout + res.stderr
            else:
                try:
                    res = _call(run["python"], ctx, **(run.get("args") or {}))
                except Exception as e:  # noqa: BLE001 - a crash is a stage failure, reported, not swallowed
                    res = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
                entry["result"] = res
                action_ok = bool(res.get("ok")) if isinstance(res, dict) else bool(res)
                log = res.get("log") if isinstance(res, dict) else None
                output = None
            vok, vmsgs = self.verify(s, ctx, output)
            entry["verify"] = vmsgs
            entry["seconds"] = round(time.monotonic() - t0, 2)
            passed = action_ok and vok is not False
            entry["status"] = "done" if passed and vok else ("ran-unverified" if passed else "failed")
            st[sid] = {"status": entry["status"], "fingerprint": fp,
                       "finished": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._save(st)
            if not passed:
                if s.get("triage") and log:
                    from ..build import triage
                    entry["triage"] = triage.triage_file(log, s["triage"])
                report["ok"] = False
                report.setdefault("stopped_at", sid)
                failed = True
        report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (self.state_dir / "last-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        return report

    def reset(self, stage=None):
        st = self.state()
        if stage:
            st.pop(stage, None)
        else:
            st = {}
        self._save(st)
