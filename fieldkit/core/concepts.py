"""One definition per concept the Firefox cookbook's tools share (P38, owner order 2026-11-16).

Firefox 160 failed at the SEAMS between tools, never inside one: two tools each had their own idea of the same thing
(what a closed step is, which branch is the base, which machine a stage is measured on, which upstream commit a
version is). Each answer lives here once; rebase-set, the migration gates, `fieldkit ship`, the emerge preflight, the
recipes and `fieldkit firefox` all call it. A tool that needs one of these and computes it itself is a seam waiting
to break: call this module.

    closed_step(step) / open_steps(steps, final=...)   what a port step being "closed" means
    default_branch(repo)                               the base branch of a repository: origin/HEAD, never assumed
    measured_on(rows)                                  which machine runs the stages, so whose disk/RAM a preflight reads
    upstream_tag(version) / upstream_ref(version, src) the upstream release tag and commit, derived from the version
    PLATFORMS, platform_of_group(name), excluded_on(spec, platform)   the .linux / .windows / .mac groups
    product_name(version)                              the one product name on every system
"""
import re
import subprocess
from pathlib import Path

# -- port steps ----------------------------------------------------------------------------------------------------
#: a step is closed when its work is in the tree (done) or what it would patch is gone upstream (obsolete; `verify`
#: re-judges it from the tree). "proposed" is a model's answer nobody accepted yet: open.
CLOSED_STATUSES = ("done", "obsolete")


def closed_step(step):
    return (step or {}).get("status") in CLOSED_STATUSES


def open_steps(steps, final=True):
    """-> the steps that are not closed. final=False leaves out the `final*` steps (they run after the port, at the
    build; the S3 gate judges the port only). rebase-set counts them (final=True): its replay is of the whole task."""
    return [s for s in steps or [] if not closed_step(s) and (final or not str(s.get("id", "")).startswith("final"))]


# -- repositories --------------------------------------------------------------------------------------------------
def _git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def default_branch(repo=".", fallback=None):
    """-> the base branch of `repo`: origin/HEAD (what GitHub calls the default branch). gorilla-firefox is `master`,
    Fieldkit is `main` (2026-11-16: `fieldkit ship` assumed main for gorilla-firefox). When origin/HEAD is not set
    locally, the remote is asked once (`git remote set-head origin --auto`), then the one of main/master that exists.
    `fallback` only when none of that answers."""
    head = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if not head and _git(repo, "remote", "get-url", "origin"):
        _git(repo, "remote", "set-head", "origin", "--auto")
        head = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if head:
        return head.split("/", 1)[1] if head.startswith("origin/") else head
    for name in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}"):
            return name
    for name in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"):
            return name
    return fallback


# -- where a stage runs --------------------------------------------------------------------------------------------
def measured_on(rows):
    """-> (local, devices): does any stage to build run on THIS machine, and the offload devices of the others. An
    emerge plan whose every stage is offloaded must not measure this machine's disk and RAM (2026-11-16:
    `emerge plan firefox-windows` on the VAIO read the VAIO's 9.5 GB for stages the L15 runs). The offloaded stage
    runs `emerge run ... --local` on its device, whose own preflight measures that machine."""
    todo = [r for r in rows or [] if r.get("action") in ("run", "offload")]
    local = any(r["action"] == "run" for r in todo)
    devices = sorted({str((r.get("device") or r.get("why") or "")).strip() for r in todo if r["action"] == "offload"})
    return local or not todo, [d for d in devices if d]


# -- upstream ------------------------------------------------------------------------------------------------------
_VERSION = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(esr)?$")


def upstream_tag(version):
    """-> Mozilla's release tag for a Firefox version: 160.0 -> FIREFOX_160_0_RELEASE, 160 -> FIREFOX_160_0_RELEASE,
    160.0.1 -> FIREFOX_160_0_1_RELEASE, 140.5.0esr -> FIREFOX_140_5_0esr_RELEASE. Never written in a recipe."""
    m = _VERSION.match(str(version or "").strip())
    if not m:
        raise ValueError(f"not a Firefox version: {version!r}")
    major, minor, patch, esr = m.groups()
    parts = [major, minor or "0"] + ([patch] if patch else [])
    return "FIREFOX_" + "_".join(parts) + (esr or "") + "_RELEASE"


def upstream_ref(version, src):
    """-> (commit or None, how): the upstream commit of `version` in the git tree `src`, from its release tag. None
    with the command that fetches the tag when the tree lacks it (fail closed: an unknown base is not a base)."""
    tag = upstream_tag(version)
    sha = _git(src, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}") if Path(src).exists() else None
    if sha:
        return sha, f"tag {tag}"
    return None, f"no tag {tag} in {src} (git -C {src} fetch origin tag {tag} --no-tags)"


# -- platform groups -----------------------------------------------------------------------------------------------
#: the three systems of the cookbook and the suffix of a patch group that belongs to one of them
PLATFORMS = ("linux", "windows", "mac")


def platform_of_group(name):
    """-> 'linux' | 'windows' | 'mac' for a group named 23.REBASE.160.linux and the like, None for a shared group."""
    tail = str(name).rsplit(".", 1)[-1].lower()
    return tail if tail in PLATFORMS else None


def excluded_on(spec, platform):
    """-> the patch names a group's policy entry leaves out on `platform` (exclude + exclude_on.<platform>)."""
    spec = spec or {}
    return set(spec.get("exclude") or []) | set((spec.get("exclude_on") or {}).get(platform) or [])


def group_applies(name, spec, platform):
    """A group applies on `platform` when it is enabled, is not another system's group, and its `platforms` (if any)
    name the platform."""
    spec = spec or {}
    if spec.get("status", "enabled") != "enabled":
        return False
    own = platform_of_group(name)
    if own and own != platform:
        return False
    plats = spec.get("platforms")
    return not plats or platform in plats


# -- the product -----------------------------------------------------------------------------------------------------
#: owner, 2026-11-16: "the Firefox will always be called Gorilla Unleashed across all the versions, followed by the
#: number of the version ... the same name across all" the systems. The data is fieldkit/firefox/cookbook.yaml naming.
_NAMING = None


def naming():
    global _NAMING
    if _NAMING is None:
        import yaml
        f = Path(__file__).resolve().parent.parent / "firefox" / "cookbook.yaml"
        _NAMING = yaml.safe_load(f.read_text(encoding="utf-8"))["naming"]
    return _NAMING


PRODUCT = "Gorilla Unleashed"          # the same value as cookbook.yaml naming.name (test_firefox_cookbook_e2e checks)


def _v(version):
    v = str(version or "").strip()
    if not v:
        raise ValueError("a product name needs the version")
    return v


def _os(os_name):
    if os_name not in PLATFORMS:
        raise ValueError(f"os must be one of {PLATFORMS}: {os_name!r}")
    return os_name


def product_name(version):
    n = naming()
    return n["display"].format(name=n["name"], version=_v(version))


def artifact_name(version, os_name, arch, ext):
    """Gorilla-Unleashed-160.0-windows-x86_64.zip: every release file on every system."""
    return naming()["file"].format(version=_v(version), os=_os(os_name), arch=arch, ext=ext.lstrip("."))


def release_tag(version, os_name):
    return naming()["tag"].format(version=_v(version), os=_os(os_name))


def batch_tag(version):
    """v160.0.0: the one release of a version that carries every system's files (owner 2026-11-17)."""
    return naming()["batch_tag"].format(version=_v(version))


def batch_title(version):
    n = naming()
    return n["batch_title"].format(name=n["name"], version=_v(version))


def release_title(version, os_name):
    n = naming()
    return n["title"].format(name=n["name"], version=_v(version), OS=n["os_title"][_os(os_name)])


def _scheme(pattern):
    """The scheme as a regular expression ({version} a Firefox version, {os} one of PLATFORMS, {arch}/{ext} a word)."""
    n = naming()
    out, parts = "", re.split(r"(\{\w+\})", pattern)
    for p in parts:
        key = p[1:-1] if p.startswith("{") and p.endswith("}") else None
        out += {"version": r"\d+\.\d+(?:\.\d+)?(?:esr)?", "os": "(?:" + "|".join(PLATFORMS) + ")",
                "OS": "(?:" + "|".join(map(re.escape, n["os_title"].values())) + ")", "name": re.escape(n["name"]),
                "arch": r"[A-Za-z0-9_]+", "ext": r"[A-Za-z0-9.]+"}.get(key, re.escape(p)) if key else re.escape(p)
    return "^" + out + "$"


def name_problems(kind, value):
    """-> [problem] for one outward name. kind: file | tag | title | text (text: only the old names are refused)."""
    n, v, out = naming(), str(value or ""), []
    for old in n["old_names"]:
        if re.search(old, v, re.I):
            out.append(f"{kind} {v!r}: old product name /{old}/; it is {n['name']!r}")
    if kind in ("file", "tag", "title") and not re.match(_scheme(n[kind]), v) and \
            not (n.get(f"batch_{kind}") and re.match(_scheme(n[f"batch_{kind}"]), v)):   # the all-systems release
        out.append(f"{kind} {v!r} is not in the one scheme {n[kind]!r}")
    return out
