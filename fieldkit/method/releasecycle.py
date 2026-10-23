# Version: 1.0.0 · updated 26-10-23-14-10
"""One release, the whole cycle, the same way every time: the pipeline engine driven by a project's release file.

    fieldkit release-cycle PROJECT plan  --var version=vX.Y.Z    the stages and where each runs
    fieldkit release-cycle PROJECT next  --var version=vX.Y.Z    the ONE next thing: DO / BLOCKED / CANNOT HERE / DONE
    fieldkit release-cycle PROJECT run   --var version=vX.Y.Z    run from where it stopped; each stage proven by its artefact
    fieldkit release-cycle PROJECT reset [STAGE]

Owner, 2026-10-23: a release used to take 80-odd minutes; with about 20 projects and the owner routing every step it
takes days. "When the user says release, he means the full cycle": everything merged into main, the binaries for every
system on the release page, the dual-track notes, screenshots embedded, and nothing left behind on a stray branch.
Design: local/PROJECT-MANDATE-PLAN.md section 15. The steps already existed as tools (ship audit, the build pipelines,
release-page, gh); this chains them, and each stage is DONE only when its artefact is there:

  audit-start   on the base, clean, equal to origin; no remote branch with work the base lacks; no open pull request
  build         the release file's build commands; every asset named in it exists, not empty
  checksums     SHA256SUMS over the assets, matching every file
  screenshots   each capture command; each file a real PNG of some size, in the project's gallery
  notes         layman and developer files, the page composed with the layman track first and every screenshot in it
  on-base       the screenshots and notes are on origin/<base>, and the base differs from the built commit only there
  publish       the release made at origin/<base> with every asset (an existing release only gets what it lacks)
  verify-page   the published page: every asset, both tracks in order, every screenshot linked
  leftovers     ship audit again: no stray branch, open pull request, extra worktree or local branch is left

The release file, local/releases/<project>.yaml (machine paths, so never published):
    project: gorilla-opencode
    repo: /home/USER/Documents/Gorilla.Opencode
    base: main
    out: "{repo}/Compiled.Builds/{version}"                 # {version} v0.1.151, {v} 0.1.151
    build: [[bash, scripts/build-deb.sh, "{v}"], ...]       # run in the repo, in order
    assets: ["gorilla-opencode_{v}_amd64.deb", ...]          # what must exist in out, and on the page
    checksums: "SHA256SUMS-{version}.txt"
    gallery: docs/screenshots/gallery/{version}
    screenshots: [{name: x.png, proves: "...", cmd: [...]}]  # cmd writes {repo}/{gallery}/{name}
    notes: {layman: ..., developer: ..., opening: ...}       # paths in the repo; the page is composed from them
"""
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

import yaml

from ..core import settings

STAGES = ["audit-start", "build", "checksums", "screenshots", "notes", "on-base", "publish", "verify-page",
          "leftovers"]
PNG = b"\x89PNG\r\n\x1a\n"
MIN_SHOT = 10_000


def release_file(project):
    return settings.ROOT / "local" / "releases" / f"{project}.yaml"


def _fill(value, d):
    if isinstance(value, list):
        return [_fill(v, d) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, d) for k, v in value.items()}
    if isinstance(value, str):
        for _ in range(3):
            value = re.sub(r"\{(\w+)\}", lambda m: str(d.get(m.group(1), m.group(0))), value)
    return value


def load(path, version):
    """-> the release file with {repo} {version} {v} {base} {gallery} filled. Refuses what is missing."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no release file {p}: write it once per project (see fieldkit release-cycle --help)")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version or ""):
        raise ValueError(f"the version must look like v1.2.3, got {version!r}")
    r = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    for k in ("project", "repo", "assets", "notes"):
        if not r.get(k):
            raise ValueError(f"{p.name} lacks {k}")
    d = {"version": version, "v": version[1:], "base": r.get("base", "main"), "repo": r["repo"]}
    d["gallery"] = _fill(r.get("gallery", "docs/screenshots/gallery/{version}"), d)
    r = _fill(r, d)
    r.update(base=d["base"], gallery=d["gallery"], version=version, v=d["v"])
    r["out"] = r.get("out") or str(Path(r["repo"]) / "dist" / version)
    r["checksums"] = r.get("checksums") or f"SHA256SUMS-{version}.txt"
    return r


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def _gh(args, run=subprocess.run):
    r = run(["gh", *args], capture_output=True, text=True)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _slug(repo):
    from . import shiprelease
    return shiprelease.repo_slug(repo)


def _r(ctx):
    return load(ctx.vars["release_file"], ctx.vars.get("version"))


def _state(ctx, name, data=None):
    p = Path(ctx.state_dir) / f"{name}.json"
    if data is not None:
        p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


# ---- stages ----------------------------------------------------------------------------------------------------------
def audit_start(ctx, gh=None):
    from . import shiprelease
    r = _r(ctx)
    a = shiprelease.audit(r["repo"], r["base"], **({"gh": gh} if gh else {}))
    want = {"checkout", "branches", "pulls"}
    bad = [x for x in a["rows"] if x["check"] in want and x["state"] != "OK"]
    _state(ctx, "audit-start", {"rows": a["rows"], "head": _git(r["repo"], "rev-parse", "HEAD").stdout.strip()})
    return {"ok": not bad, "detail": "; ".join(f"{x['check']}: {x['say']} (RUN: {x['run']})" for x in bad)
            or f"on {r['base']}, clean, every branch's work on {r['base']}, no open pull request"}


def verify_audit_start(ctx):
    r, st = _r(ctx), _state(ctx, "audit-start")
    rows = {x["check"]: x for x in st.get("rows", [])}
    ok = all(rows.get(c, {}).get("state") == "OK" for c in ("checkout", "branches", "pulls"))
    return {"ok": ok and st.get("head") == _git(r["repo"], "rev-parse", "HEAD").stdout.strip(),
            "detail": "audit clean at this commit" if ok else "the audit is not clean"}


def build(ctx, run=subprocess.run):
    r = _r(ctx)
    Path(r["out"]).mkdir(parents=True, exist_ok=True)
    started = time.time() - 2                             # file times are coarse on some file systems
    for cmd in r.get("build") or []:
        p = run(cmd, cwd=r["repo"], capture_output=True, text=True)
        if p.returncode != 0:
            last = next((ln for ln in reversed(((p.stdout or "") + (p.stderr or "")).splitlines()) if ln.strip()), "")
            return {"ok": False, "detail": f"{' '.join(cmd)}: exit {p.returncode}: {last[:200]}"}
    _state(ctx, "build", {"commit": _git(r["repo"], "rev-parse", "HEAD").stdout.strip(), "started": started})
    return {"ok": True, "detail": f"{len(r.get('build') or [])} build command(s) ran"}


def verify_build(ctx):
    """Every asset exists, is not empty, and was made by THIS build: a file left from an earlier release (an asset
    name without its version, such as gorilla-opencode.exe) is not this release's."""
    r = _r(ctx)
    started = _state(ctx, "build").get("started")
    if started is None:
        return {"ok": False, "detail": "the build stage has not run"}
    missing, stale = [], []
    for a in r["assets"]:
        p = Path(r["out"]) / a
        if not p.is_file() or p.stat().st_size == 0:
            missing.append(a)
        elif p.stat().st_mtime < started:
            stale.append(a)
    problems = ([f"missing or empty in {r['out']}: {', '.join(missing)}"] if missing else []) + \
               ([f"older than this build (left from an earlier one): {', '.join(stale)}"] if stale else [])
    return {"ok": not problems, "detail": "; ".join(problems) or f"all {len(r['assets'])} assets made by this build"}


def checksums(ctx):
    r = _r(ctx)
    lines = [f"{_sha(Path(r['out']) / a)}  {a}" for a in r["assets"]]
    (Path(r["out"]) / r["checksums"]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "detail": f"{r['checksums']}: {len(lines)} file(s)"}


def verify_checksums(ctx):
    r = _r(ctx)
    f = Path(r["out"]) / r["checksums"]
    if not f.is_file():
        return {"ok": False, "detail": f"{f.name} missing"}
    named = {}
    for ln in f.read_text(encoding="utf-8").splitlines():
        sha, _, name = ln.partition("  ")
        named[name] = sha
    bad = [a for a in r["assets"] if named.get(a) != _sha(Path(r["out"]) / a)]
    return {"ok": not bad, "detail": f"not matching {f.name}: {', '.join(bad)}" if bad else f"{f.name} matches every asset"}


def _shots(r):
    return [(s, Path(r["repo"]) / r["gallery"] / s["name"]) for s in r.get("screenshots") or []]


def screenshots(ctx, run=subprocess.run):
    r = _r(ctx)
    (Path(r["repo"]) / r["gallery"]).mkdir(parents=True, exist_ok=True)
    for s, path in _shots(r):
        if path.is_file():
            continue                                      # a shot already taken is kept: re-run only what is missing
        p = run(s["cmd"], cwd=r["repo"], capture_output=True, text=True)
        if p.returncode != 0:
            return {"ok": False, "detail": f"{s['name']}: {' '.join(s['cmd'])}: exit {p.returncode}"}
    return {"ok": True, "detail": f"{len(_shots(r))} screenshot(s)"}


def verify_screenshots(ctx):
    r = _r(ctx)
    if not _shots(r):
        return {"ok": False, "detail": "the release file names no screenshot: a release page carries them (directive 13)"}
    bad = []
    for s, path in _shots(r):
        if not path.is_file() or path.stat().st_size < MIN_SHOT or path.read_bytes()[:8] != PNG:
            bad.append(s["name"])
    return {"ok": not bad, "detail": f"missing, not a PNG or too small: {', '.join(bad)}" if bad else
            f"{len(_shots(r))} screenshot(s) in {r['gallery']}"}


def _page(r):
    return Path(r["out"]) / f"{r['version']}-release-page.md"


def _shot_url(r, name):
    return f"https://raw.githubusercontent.com/{_slug(r['repo'])}/{r['version']}/{r['gallery']}/{name}"


def notes(ctx, run=subprocess.run):
    r = _r(ctx)
    n = {k: Path(r["repo"]) / v for k, v in r["notes"].items()}
    missing = [k for k in ("opening", "layman", "developer") if not n.get(k, Path("/nonexistent")).is_file()]
    if missing:
        return {"ok": False, "detail": f"write the notes first ({', '.join(missing)}): a person or a model in full "
                                       "words; fieldkit docs guide says how"}
    gallery = "\n\n".join(f"[![{s.get('proves', s['name'])}]({_shot_url(r, s['name'])})]({_shot_url(r, s['name'])})"
                          for s, _ in _shots(r))
    extra = Path(r["out"]) / "screenshots.md"
    extra.write_text("## Screenshots\n\n" + gallery + "\n", encoding="utf-8")
    p = run(["python", "-m", "fieldkit", "release-page", "compose", "--opening", str(n["opening"]), "--layman",
             str(n["layman"]), "--developer", str(n["developer"]), "--extra", str(extra), "--out", str(_page(r))],
            capture_output=True, text=True)
    return {"ok": p.returncode == 0, "detail": f"composed {_page(r).name}" if p.returncode == 0 else
            ((p.stdout or "") + (p.stderr or "")).strip()[-300:]}


def verify_notes(ctx):
    r = _r(ctx)
    page = _page(r)
    if not page.is_file():
        return {"ok": False, "detail": "no composed page"}
    text = page.read_text(encoding="utf-8")
    absent = [s["name"] for s, _ in _shots(r) if _shot_url(r, s["name"]) not in text]
    return {"ok": not absent, "detail": f"screenshots not on the page: {', '.join(absent)}" if absent else
            f"{page.name}: both tracks and every screenshot"}


def on_base(ctx):
    r = _r(ctx)
    _git(r["repo"], "fetch", "--quiet", "origin", r["base"])
    paths = [str(Path(r["gallery"]) / s["name"]) for s, _ in _shots(r)] + list(r["notes"].values())
    absent = [p for p in paths if _git(r["repo"], "cat-file", "-e", f"origin/{r['base']}:{p}").returncode != 0]
    if absent:
        return {"ok": False, "detail": f"not on origin/{r['base']} yet: {', '.join(absent[:5])}. Commit them on a "
                                       "branch, fieldkit ship push, then fieldkit ship merge (or a pull request)"}
    built = _state(ctx, "build").get("commit")
    allowed = set(paths)
    other = [f for f in _git(r["repo"], "diff", "--name-only", built or "HEAD", f"origin/{r['base']}").stdout.split()
             if f not in allowed]
    return {"ok": not other, "detail": f"origin/{r['base']} changed more than notes and screenshots since the build: "
            f"{', '.join(other[:5])}; build again" if other else f"notes and screenshots on origin/{r['base']}"}


def verify_on_base(ctx):
    return on_base(ctx)


def _published(r, gh=None):
    code, out, _ = (gh or _gh)(["release", "view", r["version"], "-R", _slug(r["repo"]), "--json", "assets,body"])
    return json.loads(out) if code == 0 and out.strip() else None


def _files(r):
    return [Path(r["out"]) / a for a in r["assets"]] + [Path(r["out"]) / r["checksums"]]


def publish(ctx, gh=None):
    gh = gh or _gh
    r = _r(ctx)
    slug, have = _slug(r["repo"]), _published(r, gh)
    if have is None:
        code, out, err = gh(["release", "create", r["version"], "-R", slug, "--target", r["base"], "--title",
                             r["version"], "--notes-file", str(_page(r))] + [str(f) for f in _files(r)])
        return {"ok": code == 0, "detail": "published" if code == 0 else err.strip()[-300:]}
    names = {a["name"] for a in have.get("assets") or []}
    lacking = [str(f) for f in _files(r) if f.name not in names]
    if lacking:                                           # never --clobber: what is on the page stays as it is
        code, _, err = gh(["release", "upload", r["version"], "-R", slug] + lacking)
        if code != 0:
            return {"ok": False, "detail": err.strip()[-300:]}
    return {"ok": True, "detail": f"{r['version']} was there; uploaded {len(lacking)} missing file(s)"}


def verify_page(ctx, gh=None):
    r = _r(ctx)
    have = _published(r, gh)
    if have is None:
        return {"ok": False, "detail": f"{r['version']} is not on GitHub"}
    names = {a["name"] for a in have.get("assets") or []}
    lacking = [f.name for f in _files(r) if f.name not in names]
    body = have.get("body") or ""
    from ..gdocs import releasepage                       # the same rules release-page check applies before publishing
    unlinked = [s["name"] for s, _ in _shots(r) if _shot_url(r, s["name"]) not in body]
    problems = ([f"assets not on the page: {', '.join(lacking)}"] if lacking else []) + \
               ([f"screenshots not on the page: {', '.join(unlinked)}"] if unlinked else []) + \
               releasepage.check(body)
    return {"ok": not problems, "detail": "; ".join(problems) or
            f"{r['version']}: {len(names)} assets, both tracks in order, {len(_shots(r))} screenshot(s)"}


def leftovers(ctx, gh=None):
    from . import shiprelease
    r = _r(ctx)
    a = shiprelease.audit(r["repo"], r["base"], **({"gh": gh} if gh else {}))
    bad = [x for x in a["rows"] if x["check"] != "release" and x["state"] != "OK"]
    return {"ok": not bad, "detail": "; ".join(f"{x['check']}: {x['say']} (RUN: {x['run']})" for x in bad)
            or "nothing left behind: every branch, pull request, worktree and local branch accounted for"}


# ---- the pipeline ---------------------------------------------------------------------------------------------------
def spec(project, version, release=None):
    m = "fieldkit.method.releasecycle"
    st = lambda sid, fn, vfn, **k: dict({"id": sid, "run": {"python": f"{m}:{fn}"},
                                         "verify": [{"python": {"target": f"{m}:{vfn}"}}]}, **k)
    return {"name": f"release-cycle-{project}",
            "description": f"the whole release cycle of {project}",
            "vars": {"project": project, "version": version, "release_file": str(release or release_file(project))},
            "stages": [st("audit-start", "audit_start", "verify_audit_start"),
                       st("build", "build", "verify_build", timeout=10800),
                       st("checksums", "checksums", "verify_checksums"),
                       st("screenshots", "screenshots", "verify_screenshots"),
                       st("notes", "notes", "verify_notes"),
                       st("on-base", "on_base", "verify_on_base"),
                       st("publish", "publish", "verify_page"),
                       st("verify-page", "verify_page", "verify_page"),
                       st("leftovers", "leftovers", "leftovers")]}


def pipeline(project, version, state_root=None, release=None):
    from ..core.pipeline import Pipeline
    root = Path(state_root) if state_root else settings.ROOT / "state"
    pl = Pipeline(spec(project, version, release), source=f"release-cycle:{project}",
                  state_dir=root / "release-cycle" / project / version)
    fk = settings.self_cmd()                              # the full path: a person can paste it from any folder
    pl.run_command = f"{fk} release-cycle {project} run --var version={version}"
    pl.next_command = f"{fk} release-cycle {project} next --var version={version}"
    return pl


def main(argv=None, prog="fieldkit release-cycle"):
    import argparse
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("action", choices=["plan", "next", "run", "reset"])
    ap.add_argument("stage", nargs="?", help="run/reset: one stage only")
    ap.add_argument("--only", help="run: this stage only (what `next` prints)")
    ap.add_argument("--from", dest="start", help="run: from this stage on")
    ap.add_argument("--var", action="append", default=[], help="version=vX.Y.Z")
    ap.add_argument("--release-file", help="the project's release file (default local/releases/PROJECT.yaml)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    v = dict(kv.split("=", 1) for kv in a.var if "=" in kv)
    rf = Path(a.release_file) if a.release_file else release_file(a.project)
    try:
        load(rf, v.get("version"))
    except (FileNotFoundError, ValueError) as e:
        print(f"REFUSED: {e}")
        return 2
    pl = pipeline(a.project, v["version"], release=rf)
    if a.action == "plan":
        d = pl.plan()
        print(json.dumps(d, indent=1) if a.json else "\n".join(f"  {s['id']}" for s in d["stages"]))
        return 0
    if a.action == "next":
        from ..core import next as nxt
        d = nxt.decide(pl)
        print(json.dumps(d, indent=1) if a.json else "\n".join(nxt.lines(d)))
        return 0
    if a.action == "reset":
        pl.reset(a.stage)
        print(f"reset {a.stage or 'every stage'}")
        return 0
    stage = a.only or a.stage
    rep = pl.run(only=[stage] if stage else None, start=a.start)
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        for s in rep.get("stages", []):
            print(f"  {s.get('status', '?'):16} {s['id']}: {(s.get('result') or {}).get('detail', '') if isinstance(s.get('result'), dict) else ''}")
    return 0 if rep.get("ok") else 3
