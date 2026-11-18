"""Verify a complete release candidate and promote a verified GitHub draft."""

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

import yaml

POLICY_PATH = ".github/fieldkit-release.yaml"
SLUG = re.compile(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+")


class Refused(ValueError):
    """A release cannot advance with the available evidence."""


def registered_policy(slug):
    from ..cookbook import registry

    for project in registry.load_all():
        policy = project.get("release")
        if policy and policy["repo"].lower() == slug.lower():
            return policy
    return None


def policy_at(repo, ref):
    """Read the policy blob from Git, independent of the working tree."""
    from .ship import git

    commit = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if commit.returncode:
        return None
    tree = git(repo, "ls-tree", commit.stdout.strip(), "--", POLICY_PATH)
    if tree.returncode:
        raise Refused(f"cannot inspect {POLICY_PATH} in {ref}")
    if not tree.stdout:
        return None
    entry, _, name = tree.stdout.strip().partition("\t")
    mode, kind, blob = entry.split()
    if mode not in ("100644", "100755") or kind != "blob" or name != POLICY_PATH:
        raise Refused(f"{POLICY_PATH} must be a regular file in {ref}")
    result = git(repo, "cat-file", "blob", blob)
    if result.returncode:
        raise Refused(f"cannot read {POLICY_PATH} in {ref}")
    return validate_policy(yaml.safe_load(result.stdout))


def validate_policy(data):
    policy = mapping(data)
    if type(policy.get("schema")) is not int or policy["schema"] != 1:
        raise Refused("release policy requires schema: 1")
    targets = mapping(policy.get("targets", {}))
    if policy.get("distribution") == "source" and not targets:
        return policy
    if policy.get("distribution") != "installers" or not targets:
        raise Refused("declare installer targets or explicit source distribution")
    for target, spec in targets.items():
        if not isinstance(target, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", target):
            raise Refused(f"invalid target: {target!r}")
        spec = mapping(spec)
        for field in ("platform", "arch", "installer", "workflow"):
            if not isinstance(spec.get(field), str) or not spec[field].strip():
                raise Refused(f"{target}: expected {field}")
        if not re.fullmatch(r"\.github/workflows/[a-zA-Z0-9_.-]+\.ya?ml", spec["workflow"]):
            raise Refused(f"{target}: workflow must name a GitHub Actions workflow")
        if "{version}" not in spec["installer"]:
            raise Refused(f"{target}: installer pattern must include {{version}}")
        try:
            re.compile(spec["installer"].replace("{version}", "0"))
        except re.error as exc:
            raise Refused(f"{target}: invalid installer pattern: {exc}") from exc
        if "minimum_os" in spec and (not isinstance(spec["minimum_os"], str)
                                     or not re.fullmatch(r"\d+(?:\.\d+)*", spec["minimum_os"])):
            raise Refused(f"{target}: minimum_os must be a quoted OS version")
        if "installer_member" in spec and (not isinstance(spec["installer_member"], str)
                                            or not spec["installer_member"].strip()):
            raise Refused(f"{target}: installer_member must name an archive member")
    return policy


def legacy_blocker(repo, slug, tag, base=None):
    """Refuse the legacy paths for an enrolled checkout, even if its policy is broken."""
    refs = [f"refs/tags/{tag}", "refs/remotes/origin/HEAD", "HEAD"]
    if base:
        refs.insert(1, f"refs/remotes/origin/{base}")
    else:
        refs.extend(("refs/remotes/origin/main", "refs/remotes/origin/master"))
    try:
        if any(policy_at(repo, ref) is not None for ref in refs) or (slug and registered_policy(slug)):
            return "publish with fieldkit ship gh-release TAG -- --candidate FILE"
    except (ValueError, OSError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        return f"cannot verify release policy: {exc}"
    return ""


def read_mapping(path):
    return mapping(yaml.safe_load(path.read_text(encoding="utf-8")))


def mapping(data):
    if not isinstance(data, dict):
        raise Refused("expected a YAML or JSON mapping")
    return data


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def filename(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.+-]*", name):
        raise Refused(f"invalid asset name: {name!r}")
    return name


def check_assets(policy, candidate, directory, tag):
    version = candidate["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9][a-zA-Z0-9.+-]*", version) or tag != f"v{version}":
        raise Refused("the tag must be v followed by the exact product version")
    names = candidate.get("assets", [])
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise Refused("assets must be a list of filenames")
    if len({name.casefold() for name in names}) != len(names):
        raise Refused("duplicate asset names")
    assets = {}
    for name in names:
        path = directory / filename(name)
        if not path.is_file() or path.stat().st_size == 0:
            raise Refused(f"missing or empty asset: {name}")
        assets[name] = file_digest(path)
    targets = mapping(policy.get("targets", {}))
    if policy.get("distribution") == "source" and not targets and not assets:
        return assets, {}
    if policy.get("distribution") != "installers" or not targets:
        raise Refused("declare installer targets or explicit source distribution")
    installers = {}
    for target, spec in targets.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", target):
            raise Refused(f"invalid target: {target!r}")
        pattern = spec["installer"].replace("{version}", re.escape(version))
        matches = [name for name in assets if re.fullmatch(pattern, name)]
        if len(matches) != 1:
            raise Refused(f"{target}: expected exactly one installer, found {len(matches)}")
        installers[target] = matches[0]
    if len(set(installers.values())) != len(installers):
        raise Refused("each target needs its own installer")
    return assets, installers


class GitHub:
    def __init__(self, slug, run=subprocess.run):
        self.slug = slug
        self.run = run

    def gh(self, *args):
        result = self.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", timeout=900)
        if result.returncode:
            raise Refused((result.stderr or result.stdout).strip()[-500:])
        return result.stdout

    def api(self, path, *args):
        endpoint = f"repos/{self.slug}"
        return json.loads(self.gh("api", f"{endpoint}/{path}" if path else endpoint, *args))

    def release(self, tag):
        # Authenticated listing also finds drafts. A failed request cannot be taken as absence.
        pages = self.api("releases?per_page=100", "--paginate", "--slurp")
        matches = [row for page in pages for row in page if row["tag_name"] == tag]
        if len(matches) > 1:
            raise Refused(f"multiple releases use {tag}")
        return matches[0] if matches else None

    def proof(self, target, run_id, workflow, commit):
        if type(run_id) is not int or run_id <= 0:
            raise Refused(f"{target}: expected a numeric GitHub Actions run ID")
        row = self.api(f"actions/runs/{run_id}")
        if (row.get("head_sha") != commit or row.get("conclusion") != "success"
                or row.get("event") not in ("push", "workflow_dispatch")
                or row.get("path", "").split("@", 1)[0] != workflow
                or (row.get("head_repository") or {}).get("full_name", "").lower() != self.slug.lower()):
            raise Refused(f"{target}: run {run_id} is not a successful release check for {commit}")
        with tempfile.TemporaryDirectory(prefix="fieldkit-release-proof-") as folder:
            self.gh("run", "download", str(run_id), "-R", self.slug, "-n", f"install-proof-{target}", "-D", folder)
            return mapping(json.loads((Path(folder) / "proof.json").read_text(encoding="utf-8")))


def check_proofs(gh, policy, candidate, commit, assets, installers):
    runs = mapping(candidate.get("runs", {}))
    if set(runs) != set(installers):
        raise Refused("provide one trusted install run for every target")
    covered = set()
    for target, installer in installers.items():
        spec = policy["targets"][target]
        proof = gh.proof(target, runs[target], spec["workflow"], commit)
        required = {"target": target, "installer": installer, "version": candidate["version"],
                    "commit": commit, "installed": True, "platform": spec["platform"], "arch": spec["arch"]}
        if spec.get("installer_member"):
            required["installer_member"] = spec["installer_member"]
        if any(proof.get(k) != v for k, v in required.items()) or proof.get("installed") is not True:
            raise Refused(f"{target}: install proof does not match the release")
        minimum = spec.get("minimum_os")
        if minimum and not re.fullmatch(re.escape(minimum) + r"(?:\.\d+)*", str(proof.get("os_version", ""))):
            raise Refused(f"{target}: install proof must cover OS {minimum}")
        observed = mapping(proof.get("assets"))
        if installer not in observed or any(assets.get(name) != digest for name, digest in observed.items()):
            raise Refused(f"{target}: package hashes differ from the install run")
        covered.update(observed)
    if covered != set(assets):
        raise Refused("assets missing from build evidence: " + ", ".join(sorted(set(assets) - covered)))


def online_assets(row, assets):
    observed = {}
    for asset in row["assets"]:
        name = asset["name"]
        if name in observed or name not in assets or asset.get("state") != "uploaded" or (
                asset.get("digest") != f"sha256:{assets[name]}"):
            raise Refused(f"online asset is unexpected or its hash cannot be confirmed: {name}")
        observed[name] = assets[name]
    return observed


def check_page(candidate, directory, assets):
    from ..build import relfiles

    title = candidate["title"]
    notes_path = directory / filename(candidate["notes"])
    notes = notes_path.read_text(encoding="utf-8").strip()
    if not isinstance(title, str) or not title.strip() or not notes:
        raise Refused("a title and release page are required")
    if assets:
        problems = relfiles.check_table(notes, assets, {name: (directory / name).stat().st_size for name in assets})
        if problems:
            raise Refused("; ".join(problems))
    return title, notes_path, notes


def promote(gh, tag, candidate, directory, assets, authorize, *, hotfix="", gate=None):
    from . import releasegate

    title, notes_path, notes = check_page(candidate, directory, assets)
    row = gh.release(tag)
    if row is None:
        gh.gh("release", "create", tag, "-R", gh.slug, "--draft", "--verify-tag", "--latest=false",
              "--title", title, "--notes-file", str(notes_path))
        row = gh.release(tag)
        if row is None or not row["draft"]:
            raise Refused("the new draft could not be confirmed")
    release_id = row["id"]
    row = gh.api(f"releases/{release_id}")
    if row["tag_name"] != tag or row["name"] != title or (row.get("body") or "").strip() != notes:
        raise Refused(f"existing release {tag} does not match the candidate page")
    present = online_assets(row, assets)
    if row["draft"]:
        for name in sorted(assets.keys() - present.keys()):
            path = directory / name
            if file_digest(path) != assets[name]:
                raise Refused(f"asset changed after local validation: {name}")
            gh.gh("release", "upload", tag, str(path), "-R", gh.slug)
        row = gh.api(f"releases/{release_id}")
        if (online_assets(row, assets) != assets or not row["draft"] or row["name"] != title
                or (row.get("body") or "").strip() != notes or row["tag_name"] != tag or row["prerelease"]):
            raise Refused("the draft is incomplete or changed during upload")
        authorize()
        verdict = (gate or releasegate.guard)(gh.slug, tag, hotfix)
        if not verdict["ok"]:
            raise Refused(verdict["error"])
        gh.api(f"releases/{release_id}", "-X", "PATCH", "-F", "draft=false", "-f", "make_latest=true")
    elif present != assets:
        raise Refused(f"published release {tag} is incomplete")
    latest = gh.api("releases/latest")
    if latest["id"] != release_id or latest["tag_name"] != tag or online_assets(latest, assets) != assets:
        raise Refused(f"GitHub Latest does not match the complete release {tag}")
    return latest["html_url"]


def verify(repo, gh, tag, path):
    from .ship import git

    if not SLUG.fullmatch(gh.slug) or not re.fullmatch(r"v[0-9][a-zA-Z0-9.+-]*", tag):
        raise Refused("expected a GitHub repository and a version tag")
    path = Path(path)
    if not path.is_absolute():
        path = Path(repo) / path
    commit = git(repo, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise Refused("the candidate tag must exist locally and on GitHub")
    remote = gh.api(f"commits/{tag}")
    if remote.get("sha") != commit:
        raise Refused("the local tag and GitHub tag point to different commits")
    default = quote(gh.api("")["default_branch"], safe="")
    base = gh.api(f"commits/{default}")["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", base):
        raise Refused("the GitHub default branch commit could not be confirmed")
    if base != commit and gh.api(f"compare/{commit}...{base}")["status"] != "ahead":
        raise Refused("the candidate tag must be on the GitHub default branch history")
    policy = policy_at(repo, commit)
    if policy is None:
        # Keep the pilot contract until its repositories carry their own policy.
        policy = registered_policy(gh.slug)
        if policy is None:
            raise Refused("no release policy declared in the tagged tree")
    candidate = read_mapping(path)
    assets, installers = check_assets(policy, candidate, path.parent, tag)
    check_proofs(gh, policy, candidate, commit, assets, installers)
    check_page(candidate, path.parent, assets)
    return candidate, path.parent, assets


def publish(repo, slug, tag, path, authorize, *, hotfix="", gate=None):
    gh = GitHub(slug)
    candidate, directory, assets = verify(repo, gh, tag, path)
    return promote(gh, tag, candidate, directory, assets, authorize, hotfix=hotfix, gate=gate)
