"""The project registry (P41a): projects/<id>/project.yaml -> a project, by id or by cwd. Never walks the filesystem:
the cwd lookup is a string match on the path's folder names against each project's cwd_markers."""
import fnmatch
import re
import subprocess
from fieldkit import proc as fkproc
from pathlib import Path

import yaml

from ..core import settings

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ROOT / "projects"

REQUIRED = ("id", "name", "purpose", "repos", "recipes", "cookbook", "entry", "tests", "platforms", "cwd_markers")
LISTS = ("repos", "recipes", "cookbook", "entry", "tests", "platforms", "cwd_markers")
PLATFORMS = {"linux", "windows", "macos"}
# a machine path: a home folder, a drive letter or ~/ (the registry carries ${DOCUMENTS}/${LOCAL:..} only)
HOME_PATH = re.compile(r"(^|[\s\"'(=:])(/home/|/Users/|~/|[A-Za-z]:[\\/])")


def load_all(root=None):
    out = []
    for p in sorted(Path(root or PROJECTS).glob("*/project.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            d = {"_error": f"not valid YAML: {e}"}
        d["_file"] = p
        out.append(d)
    return out


def get(pid, root=None):
    for d in load_all(root):
        if d.get("id") == pid:
            return d
    return None


def by_cwd(cwd, root=None):
    """-> the project whose marker matches the deepest folder of cwd, else None. String match only."""
    parts = [x for x in re.split(r"[\\/]+", str(cwd)) if x]
    best = None
    for d in load_all(root):
        for m in d.get("cwd_markers") or []:
            for i, part in enumerate(parts):
                if fnmatch.fnmatchcase(part, str(m)) and (best is None or i > best[0]):
                    best = (i, d)
    return best[1] if best else None


def repo_path(proj, name):
    """-> the local folder of the project's repo NAME on this machine, or None (not configured / not here)."""
    for r in proj.get("repos") or []:
        if r.get("name") == name:
            try:
                p = Path(settings.expand(r.get("local") or "", strict=True))
            except settings.SettingsError:
                return None
            return p if r.get("local") else None
    return None


def _slug(url):
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url.strip())
    return m.group(1).lower() if m else None


def remote_slug(path):
    """-> owner/name of the origin remote of the git folder PATH (offline), else None."""
    try:
        r = fkproc.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return _slug(r.stdout) if r.returncode == 0 else None


def gh_slug(name):
    """-> the canonical owner/name GitHub reports for NAME, else None (gh missing, offline or not signed in)."""
    try:
        r = fkproc.run(["gh", "repo", "view", name, "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip().lower() if r.returncode == 0 and r.stdout.strip() else None


def check_github(proj, remote=remote_slug, gh=gh_slug):
    """A repo's github name must equal its local clone's origin (offline); with no clone, `gh repo view` says; with
    neither, it is not checked. 2026-11-16: Fieldkit was registered as .../Fieldkit, the repo is .../Gorilla.Fieldkit."""
    probs = []
    for r in proj.get("repos") or []:
        want = r.get("github")
        if not want:
            continue
        base = repo_path(proj, r.get("name"))
        have = remote(base) if base and base.is_dir() else None
        how = "its local clone's origin remote"
        if have is None:
            have, how = gh(want), "`gh repo view`"
        if have is not None and have != str(want).lower():
            probs.append(f"{proj['id']}: repo {r.get('name')}: github {want} is not what {how} says ({have})")
    return probs


def check(root=None, recipes=None, github=True):
    """-> list of problem strings; [] when every project.yaml is sound. recipes: names `emerge list` knows."""
    if recipes is None:
        from ..emerge import recipe
        recipes = set(recipe.available())
    problems, ids = [], set()
    docs = load_all(root)
    if not docs:
        return ["no project.yaml found"]
    for d in docs:
        f = d["_file"].parent.name
        if "_error" in d:
            problems.append(f"{f}: {d['_error']}")
            continue
        for k in REQUIRED:
            if not d.get(k):
                problems.append(f"{f}: missing field {k}")
        for k in LISTS:
            if d.get(k) and not isinstance(d[k], list):
                problems.append(f"{f}: {k} must be a list")
        pid = d.get("id")
        if pid != f:
            problems.append(f"{f}: id {pid!r} must equal its folder name")
        if pid in ids:
            problems.append(f"{f}: duplicate id {pid}")
        ids.add(pid)
        if len(PLATFORMS - set(d.get("platforms") or [])) and not d.get("platforms_why") and set(d.get("platforms") or []) != {"linux", "windows", "macos"} \
                and len(d.get("platforms") or []) == 1:
            problems.append(f"{f}: a one-system project needs platforms_why")
        for p in d.get("platforms") or []:
            if p not in PLATFORMS:
                problems.append(f"{f}: unknown platform {p}")
        names = {r.get("name") for r in d.get("repos") or [] if isinstance(r, dict)}
        for r in d.get("recipes") or []:
            if r not in recipes:
                problems.append(f"{f}: recipe {r} is not in `fieldkit emerge list`")
        for c in d.get("cookbook") or []:
            if not isinstance(c, dict) or not c.get("path") or c.get("repo") not in names:
                problems.append(f"{f}: cookbook entry {c!r} needs a path and a repo named in repos")
                continue
            base = repo_path(d, c["repo"])
            if base and base.is_dir() and not (base / c["path"]).exists():
                problems.append(f"{f}: cookbook path {c['path']} does not exist in repo {c['repo']}")
        for t in d.get("tests") or []:
            if not isinstance(t, dict) or not t.get("cmd") or not t.get("paths"):
                problems.append(f"{f}: test entry {t!r} needs cmd and paths")
        for e in d.get("entry") or []:
            if not isinstance(e, dict) or not e.get("cmd"):
                problems.append(f"{f}: entry {e!r} needs cmd")
        if github:
            problems += check_github(d)
        for line_no, line in enumerate(d["_file"].read_text(encoding="utf-8").splitlines(), 1):
            if HOME_PATH.search(line.split("#", 1)[0]):
                problems.append(f"{f}:{line_no}: a machine path (use ${{DOCUMENTS}} / ${{LOCAL:..}})")
    return problems
