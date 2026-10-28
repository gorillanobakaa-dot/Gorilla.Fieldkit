"""A recipe (the ebuild): one declarative file per product, read once, hashed into a key per stage.

A recipe either names one of the existing build pipelines (fieldkit/build/pipelines/*.yaml, which keep the product
knowledge) and lays an overlay on its stages, or carries its stages inline in the same pipeline schema:

    name: gorilla-size
    pipeline: go-app                     # or `stages:` inline (the fieldkit/core/pipeline.py schema)
    vars: {src: "${DOCUMENTS}/Gorilla.Size"}
    baseline_minutes: 90                 # the pre-AI hand script's time for the same job (the overhead target)
    sources:                             # pinned: a file by sha256 (here or in the .manifest.json), a git tree by
      - {id: src, git: "{src}", track: tree}                                  # commit and/or its working state
      - {id: upstream, git: "{src}/src", base: 3c71aeac..., fetched_by: source}   # base: must stay under HEAD
      - {id: tarball, path: "{work}/linux-7.2.9.tar.xz"}                      # sha256 from the manifest
    patches: {dir: "{harness}/patches", glob: "*.patch"}
    config:                              # the make.conf / USE equivalent, read once per run
      mandate: Gorilla.Fieldkit          # this repository's entry of the mandate in force
      decisions: "{harness}"             # decisions/PRODUCT-DECISIONS.yaml under that folder
      files: ["{harness}/config/mozconfig.win64"]
      testcost: true                     # the owner's test-tier flags (fieldkit.local.json "testcost")
    toolchain: [[cargo, --version], [rustc, --version]]
    preflight: {disk_gb: 5, ram_gb: 2, path: "{src}"}
    docs_only: {source: src}             # a change of only documentation runs only the `privacy: true` stages
    extra_stages: [...]                  # stages added after the pipeline's (package checks, the leak gate)
    stages:                              # with `pipeline:`: an overlay per stage id
      build:
        inputs: ["{src}/src/**/*.rs", "{src}/Cargo.lock"]   # files hashed into the key (globs, ** recursive)
        outputs: ["{src}/target/release/gorilla-size.exe"]  # recorded by sha256; a skip re-verifies them
        needs: [test]                    # the stages whose keys feed this one (default: the stage before)
        uses: [sources, toolchain]       # which recipe parts feed the key (default: all five)
        tier: every                      # every | install (installed build) | release (once per release)
        offload: {device: vaio, ...}     # distcc-style: run on the other machine (see runner.py); device is
                                         # an id of fieldkit/peers/household.yaml (l15, vaio)
        always_run: false                # re-run every time (preflights, restoring the power scheme)
        advisory: false                  # a failure is reported, the run goes on (reproducibility checks)
        fix: true                        # on failure, one fix-on-failure model run per attempt
        max_fix: 2

The key is content-addressed and portable: the stage's template text (before machine paths are filled in), the
non-path vars, the pins, the patch set, the config, the toolchain versions, the declared inputs by their path
relative to their root, and the keys of the stages it needs. The same key on two machines means the same inputs, so
their outputs must be the same bytes (the reproducibility check).
"""
import glob
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

from ..core import settings
from ..core.pipeline import Pipeline, _fill, tree_state

HERE = Path(__file__).resolve().parent
RECIPES = HERE / "recipes"
PIPES = HERE.parent / "build" / "pipelines"
TIERS = ("every", "install", "release")
MODES = {"iterate": {"every"}, "install": {"every", "install"}, "release": {"every", "install", "release"}}
PARTS = ("sources", "patches", "config", "toolchain", "inputs")
OVERLAY = {"inputs", "outputs", "needs", "uses", "tier", "offload", "always_run", "advisory", "fix", "max_fix",
           "privacy", "bucket", "verify", "optional"}
_ABS = re.compile(r"^([A-Za-z]:[\\/]|/|\\\\|~)")


class RecipeError(ValueError):
    pass


def available():
    return sorted(p.stem for p in RECIPES.glob("*.yaml"))


def find(name):
    p = Path(name)
    if p.suffix in (".yaml", ".yml") and p.is_file():
        return p
    q = RECIPES / f"{name}.yaml"
    if q.is_file():
        return q
    raise RecipeError(f"no recipe {name!r}; have: {', '.join(available())}")


def sha256_file(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def portable(value):
    """A var value that is the same on every machine: not an absolute path, not a ${...} machine setting."""
    return isinstance(value, (int, float, bool)) or (isinstance(value, str) and not _ABS.match(value)
                                                    and "${" not in value)


def _git(path, *args, timeout=60):
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _base(pattern):
    """The folder a glob is relative to: its path up to the first segment with a wildcard."""
    parts = Path(pattern).parts
    out = []
    for p in parts:
        if any(c in p for c in "*?["):
            break
        out.append(p)
    return Path(*out) if out else Path(".")


class HashCache:
    """sha256 of input files by (size, mtime_ns): a tree that did not change is not read again."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.dirty = False

    def sha(self, p):
        st = os.stat(p)
        k = str(Path(p).resolve())
        hit = self.data.get(k)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return hit[2]
        h = sha256_file(p)
        self.data[k] = [st.st_size, st.st_mtime_ns, h]
        self.dirty = True
        return h

    def save(self):
        if self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
            self.dirty = False


def files_of(patterns):
    """-> [(pattern base, file)] for every file the globs match, sorted, each once."""
    seen, out = set(), []
    for pat in patterns or []:
        base = _base(pat)
        hits = glob.glob(pat, recursive=True) if any(c in pat for c in "*?[") else ([pat] if Path(pat).exists()
                                                                                      else [])
        for h in sorted(hits):
            if Path(h).is_file() and h not in seen:
                seen.add(h)
                out.append((base, h))
    return out


class Recipe:
    def __init__(self, spec, source="<memory>", overrides=None, strict=True, state_root=None, run=subprocess.run):
        if not isinstance(spec, dict) or "name" not in spec or not ({"pipeline", "stages"} & set(spec)):
            raise RecipeError(f"{source}: a recipe needs 'name' and 'pipeline' or 'stages'")
        self.spec, self.source, self.strict, self._run = spec, str(source), strict, run
        self.name = spec["name"]
        self.overrides = dict(overrides or {})
        self.state_dir = Path(state_root or settings.state_root() / "state" / "emerge") / self.name
        self.state_dir.mkdir(parents=True, exist_ok=True)
        overlay = spec.get("stages") if spec.get("pipeline") else {}
        if spec.get("pipeline"):
            pfile = PIPES / f"{spec['pipeline']}.yaml"
            if not pfile.is_file():
                raise RecipeError(f"{source}: no pipeline {spec['pipeline']!r} in {PIPES}")
            pspec = settings.read_file(pfile)
            raw_stages = [dict(s) for s in pspec["stages"]] + [dict(s) for s in spec.get("extra_stages") or []]
            vars_ = dict(pspec.get("vars") or {})
            self.description = spec.get("description") or pspec.get("description", "")
        else:
            raw_stages = [dict(s) for s in spec["stages"]]
            vars_ = {}
            self.description = spec.get("description", "")
        vars_.update(spec.get("vars") or {})
        for sid, ov in (overlay or {}).items():
            odd = set(ov or {}) - OVERLAY
            if odd:
                raise RecipeError(f"{source}: stage {sid}: unknown overlay key(s) {sorted(odd)}; known: {sorted(OVERLAY)}")
        for s in raw_stages:
            s.update((overlay or {}).get(s.get("id"), {}) or {})
            if "tier" in s and s["tier"] not in TIERS:
                raise RecipeError(f"{source}: stage {s['id']}: tier must be one of {TIERS}")
        unknown = set(overlay or {}) - {s.get("id") for s in raw_stages}
        if unknown:
            raise RecipeError(f"{source}: overlay for stages the pipeline does not have: {sorted(unknown)}")
        # the template text of each stage, before any machine path is filled in: the portable part of the key
        self.templates = {s["id"]: json.dumps(s, sort_keys=True) for s in raw_stages}
        self.pipe = Pipeline({"name": self.name, "vars": vars_, "stages": raw_stages}, source=self.source,
                             state_dir=self.state_dir / "pipeline", overrides=self.overrides, strict=strict)
        self.vars = self.pipe.vars
        self.stages = self.pipe.stages
        self.by_id = {s["id"]: s for s in self.stages}
        for s in self.stages:
            for n in s.get("needs") or []:
                if n not in self.by_id:
                    raise RecipeError(f"{source}: stage {s['id']} needs {n!r}, which is not a stage")
            if s.get("offload"):            # one name per machine: the household's id, alias or tag
                from ..peers import household
                try:
                    household.resolve((s["offload"] or {}).get("device"))
                except ValueError as e:
                    raise RecipeError(f"{source}: stage {s['id']}: offload: {e}") from None
        self.sources = self._resolve(spec.get("sources") or [])
        self.patches = self._resolve(spec.get("patches") or {})
        self.config_spec = self._resolve(spec.get("config") or {})
        self.toolchain_spec = spec.get("toolchain") or []
        self.preflight = self._resolve(spec.get("preflight") or {})
        self.docs_only = spec.get("docs_only") or {}
        self.baseline_minutes = spec.get("baseline_minutes")
        self.max_ratio = float(spec.get("max_ratio", 1.5))
        self.manifest_path = Path(self.source).with_suffix(".manifest.json") if self.source != "<memory>" else None
        self.hashes = HashCache(self.state_dir / "hashcache.json")
        self._memo = {}

    @classmethod
    def load(cls, name, **kw):
        path = find(name)
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")), source=path, **kw)

    def reload(self):
        """The recipe read again from its file (after a fix changed it)."""
        if self.source == "<memory>":
            return self
        return Recipe(yaml.safe_load(Path(self.source).read_text(encoding="utf-8")), source=self.source,
                      overrides=self.overrides, strict=self.strict, state_root=self.state_dir.parent, run=self._run)

    def _resolve(self, value):
        return _fill(settings.expand(value, strict=self.strict), self.vars)

    # -- the parts of the key, each read once ----------------------------------------------------------------------
    def manifest(self):
        if self.manifest_path and self.manifest_path.is_file():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except ValueError:
                return {}
        return {}

    def pin(self, src):
        """-> (state, value, detail): state ok | absent | mismatch | unpinned. value is what goes into the key."""
        if src.get("git"):
            path = Path(src["git"])
            top = _git(path, "rev-parse", "--show-toplevel") if path.exists() else None
            # a folder inside ANOTHER repository is not a pinned tree (2026-10-26: _sources/debian-kernel answered
            # with Fieldkit's own HEAD)
            head = _git(path, "rev-parse", "HEAD") if top and Path(top).resolve() == path.resolve() else None
            if head is None:
                return "absent", None, f"{path}: no git tree here" + (
                    f" yet (stage {src['fetched_by']} makes it)" if src.get("fetched_by") else "")
            want = str(src.get("commit") or "")
            if want and not head.startswith(want) and not want.startswith(head):
                return "mismatch", head, f"{path}: HEAD {head[:12]}, the recipe pins {want[:12]}"
            base = str(src.get("base") or "")      # the pinned upstream under our own commits (the patch set)
            if base:
                anc = self._run(["git", "-C", str(path), "merge-base", "--is-ancestor", base, "HEAD"],
                                capture_output=True, timeout=120)
                if anc.returncode != 0:
                    return "mismatch", head, f"{path}: the pinned base {base[:12]} is not under HEAD {head[:12]}"
                return "ok", tree_state(path), f"{path}: {head[:12]} on the pinned base {base[:12]}"
            if src.get("track", "commit" if want else "tree") == "tree":
                return "ok", tree_state(path), f"{path}: {head[:12]} + working state"
            return "ok", head, f"{path}: {head[:12]}"
        if src.get("path"):
            p = Path(src["path"])
            want = src.get("sha256") or self.manifest().get(src.get("id"), {}).get("sha256")
            if not p.is_file():
                return "absent", None, f"{p}: missing" + (
                    f" (stage {src['fetched_by']} fetches it)" if src.get("fetched_by") else "")
            if not want:
                return "unpinned", None, f"{p}: no sha256 in the recipe or its manifest (fieldkit emerge manifest)"
            got = sha256_file(p)
            if got != want:
                return "mismatch", got, f"{p}: sha256 {got[:16]}, pinned {want[:16]}"
            return "ok", got, f"{p}: sha256 {got[:16]}"
        return "unpinned", None, f"source {src.get('id')}: needs git: or path:"

    def sources_digest(self):
        if "sources" not in self._memo:
            self._memo["sources"] = {s.get("id"): self.pin(s)[1] for s in self.sources}
        return self._memo["sources"]

    def patches_digest(self):
        if "patches" not in self._memo:
            p = self.patches
            if not p.get("dir"):
                self._memo["patches"] = None
            else:
                pat = str(Path(p["dir"]) / "**" / p.get("glob", "*"))
                self._memo["patches"] = digest([[Path(f).relative_to(b).as_posix(), self.hashes.sha(f)]
                                                for b, f in files_of([pat])])
        return self._memo["patches"]

    def config_digest(self):
        if "config" not in self._memo:
            c, out = self.config_spec, {}
            if c.get("mandate"):
                out["mandate"] = mandate_entry(c["mandate"])
            if c.get("decisions"):
                reg = Path(c["decisions"]) / "decisions" / "PRODUCT-DECISIONS.yaml"
                out["decisions"] = sha256_file(reg) if reg.is_file() else "absent"
            for f in c.get("files") or []:
                out[f"file:{Path(f).name}"] = self.hashes.sha(f) if Path(f).is_file() else "absent"
            if c.get("testcost"):
                from .. import testcost
                out["testcost"] = testcost.flags()
            self._memo["config"] = out
        return self._memo["config"]

    def toolchain(self):
        if "toolchain" not in self._memo:
            out = {}
            for argv in self.toolchain_spec:
                try:
                    r = self._run(list(argv), capture_output=True, text=True, timeout=60, encoding="utf-8",
                                  errors="replace")
                    line = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
                    out[" ".join(argv)] = line[0] if line and r.returncode == 0 else f"exit {r.returncode}"
                except (OSError, subprocess.TimeoutExpired) as e:
                    out[" ".join(argv)] = f"absent ({type(e).__name__})"
            self._memo["toolchain"] = out
        return self._memo["toolchain"]

    def inputs_digest(self, stage):
        return digest([[Path(f).relative_to(b).as_posix(), self.hashes.sha(f)]
                       for b, f in files_of(stage.get("inputs"))])

    def needs(self, sid):
        s = self.by_id[sid]
        if "needs" in s:
            return list(s["needs"] or [])
        ids = [x["id"] for x in self.stages]
        i = ids.index(sid)
        return [ids[i - 1]] if i else []

    def keys(self):
        """-> {stage id: key}, in stage order (a stage's key holds the keys of the stages it needs)."""
        if "keys" in self._memo:
            return self._memo["keys"]
        pv = {k: v for k, v in self.vars.items() if portable(v)}
        out = {}
        for s in self.stages:
            sid = s["id"]
            uses = s["uses"] if s.get("uses") is not None else PARTS      # `uses: []` = only its needs
            parts = {"recipe": self.name, "stage": self.templates[sid], "vars": pv,
                     "needs": {n: out[n] for n in self.needs(sid)}}
            if "sources" in uses:
                parts["sources"] = self.sources_digest()
            if "patches" in uses:
                parts["patches"] = self.patches_digest()
            if "config" in uses:
                parts["config"] = self.config_digest()
            if "toolchain" in uses:
                parts["toolchain"] = self.toolchain()
            if "inputs" in uses and s.get("inputs"):
                parts["inputs"] = self.inputs_digest(s)
            out[sid] = digest(parts)
        self.hashes.save()
        self._memo["keys"] = out
        return out

    def forget(self):
        """Drop what was read, so the next keys() reads the trees again (after a stage changed them)."""
        self._memo.clear()


def mandate_entry(repo):
    """This repository's entry of the mandate in force; 'none' when no mandate is in force."""
    # The files are read, not verified: verify() checks seals and ACLs (10 s measured, 2026-10-26) and decides what
    # a session may DO; the key only needs what the mandate says. Offload jobs are verified by the job protocol.
    import datetime
    try:
        from ..mandate import mandate as M
        for path in M.mandates():
            m = M.load(path)
            if str(m.get("expires", "")) < datetime.date.today().isoformat():
                continue
            cfg = M.repo_cfg(m, repo)
            if cfg is not None:
                return cfg              # not its end date: a renewed mandate must not rebuild everything
    except Exception as e:  # noqa: BLE001 - a mandate that cannot be read is a key part that says so
        return f"unreadable ({type(e).__name__})"
    return "none"
