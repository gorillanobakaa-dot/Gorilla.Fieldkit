"""Verify a complete release candidate and promote a verified GitHub draft."""

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

import yaml


class Refused(ValueError):
    """A release cannot advance with the available evidence."""


def registered_policy(slug):
    from ..cookbook import registry

    for project in registry.load_all():
        policy = project.get("release")
        if policy and policy["repo"].lower() == slug.lower():
            return policy
    return None


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


def promote(gh, tag, candidate, directory, assets, gate):
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
        gate()
        gh.api(f"releases/{release_id}", "-X", "PATCH", "-F", "draft=false", "-f", "make_latest=true")
    elif present != assets:
        raise Refused(f"published release {tag} is incomplete")
    latest = gh.api("releases/latest")
    if latest["id"] != release_id or latest["tag_name"] != tag or online_assets(latest, assets) != assets:
        raise Refused(f"GitHub Latest does not match the complete release {tag}")
    return latest["html_url"]


def publish(repo, slug, tag, path, gate):
    from .ship import git

    path = Path(path)
    if not path.is_absolute():
        path = Path(repo) / path
    commit = git(repo, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise Refused("the candidate tag must exist locally and on GitHub")
    gh = GitHub(slug)
    remote = gh.api(f"commits/{tag}")
    if remote.get("sha") != commit:
        raise Refused("the local tag and GitHub tag point to different commits")
    default = quote(gh.api("")["default_branch"], safe="")
    base = gh.api(f"commits/{default}")["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", base):
        raise Refused("the GitHub default branch commit could not be confirmed")
    if base != commit and gh.api(f"compare/{commit}...{base}")["status"] != "ahead":
        raise Refused("the candidate tag must be on the GitHub default branch history")
    policy = registered_policy(slug)
    if policy is None:
        result = git(repo, "show", f"{commit}:.github/fieldkit-release.yaml")
        if result.returncode:
            raise Refused("no release policy declared in the tagged tree")
        policy = mapping(yaml.safe_load(result.stdout))
    candidate = read_mapping(path)
    assets, installers = check_assets(policy, candidate, path.parent, tag)
    check_proofs(gh, policy, candidate, commit, assets, installers)
    return promote(gh, tag, candidate, path.parent, assets, gate)
