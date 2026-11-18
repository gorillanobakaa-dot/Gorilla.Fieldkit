"""Run a project's install check and record the candidate bytes on that runner."""

import argparse
import json
import os
import platform
import re
import subprocess
from pathlib import Path

import yaml

from ..core import host
from . import releasecandidate as candidate
from .ship import git


def observe_host():
    machine = host.host()
    system = host.this_platform()
    version = machine["release"]
    if system == "macos":
        version = platform.mac_ver()[0]
    elif system == "linux":
        version = host._os_release().get("VERSION_ID", version)
    arch = machine["machine"].lower()
    return system, machine["distro"], {"amd64": "x86_64", "aarch64": "arm64"}.get(arch, arch), version


def record(repo, target, version, installer, assets, output, command, *, env=None,
           observe=observe_host, run=subprocess.run):
    """The checked-in command must install FIELDKIT_INSTALLER and verify FIELDKIT_VERSION."""
    env = dict(os.environ if env is None else env)
    if env.get("GITHUB_ACTIONS") != "true" or env.get("GITHUB_EVENT_NAME") not in ("push", "workflow_dispatch"):
        raise candidate.Refused("install proof requires a push or dispatch GitHub Actions run")
    commit = git(repo, "rev-parse", "--verify", "HEAD").stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{40}", commit) or env.get("GITHUB_SHA") != commit:
        raise candidate.Refused("the checkout differs from the Actions run commit")
    policy = candidate.policy_at(repo, commit)
    spec = (policy or {}).get("targets", {}).get(target)
    if not spec:
        raise candidate.Refused(f"no installer policy for {target} in the Actions run commit")
    workflow, _, _ = env.get("GITHUB_WORKFLOW_REF", "").partition("@")
    if workflow != f"{env.get('GITHUB_REPOSITORY')}/{spec['workflow']}":
        raise candidate.Refused(f"{target}: unexpected Actions workflow")
    if not re.fullmatch(r"[0-9][a-zA-Z0-9.+-]*", version):
        raise candidate.Refused("expected the exact product version without the v prefix")
    installer = Path(installer).resolve(strict=True)
    if not re.fullmatch(spec["installer"].replace("{version}", re.escape(version)), installer.name):
        raise candidate.Refused(f"{target}: unexpected installer name {installer.name}")
    if spec.get("installer_member"):
        raise candidate.Refused("install proof currently requires a standalone installer")
    system, distro, arch, os_version = observe()
    minimum = spec.get("minimum_os")
    if (spec["platform"] not in (system, distro) or arch != spec["arch"]
            or (minimum and not re.fullmatch(re.escape(minimum) + r"(?:\.\d+)*", os_version))):
        raise candidate.Refused(f"{target}: runner reports {system}/{distro} {arch} {os_version}")
    paths = {installer.name: installer}
    for asset in assets:
        path = Path(asset).resolve(strict=True)
        if path.name.casefold() in {name.casefold() for name in paths}:
            raise candidate.Refused(f"duplicate proof asset: {path.name}")
        paths[path.name] = path
    for name, path in paths.items():
        candidate.filename(name)
        if not path.is_file() or path.stat().st_size == 0:
            raise candidate.Refused(f"missing or empty proof asset: {name}")
    candidate.install_inputs(target, spec, paths, version)
    digests = {name: candidate.file_digest(path) for name, path in paths.items()}
    env.update(FIELDKIT_INSTALLER=str(installer), FIELDKIT_VERSION=version)
    run(command, cwd=repo, env=env, check=True, timeout=1800)
    if any(candidate.file_digest(path) != digests[name] for name, path in paths.items()):
        raise candidate.Refused("candidate bytes changed during the install check")
    proof = {"target": target, "version": version, "commit": commit, "platform": spec["platform"],
             "arch": arch, "os_version": os_version, "installed": True, "installer": installer.name,
             "assets": digests}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(proof, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--target", required=True)
    parser.add_argument("--version", required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--installer", type=Path)
    inputs.add_argument("--directory", type=Path, help="choose the installer by its policy filename in this directory")
    parser.add_argument("--asset", action="append", default=[], type=Path)
    parser.add_argument("--cover-directory", action="store_true", help="also record other files in --directory")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide the install and verification command after --")
    try:
        if args.directory:
            tagged = candidate.policy_at(args.repo, "HEAD") or {}
            spec = tagged.get("targets", {}).get(args.target)
            if spec is None:
                raise candidate.Refused(f"no installer policy for {args.target}")
            pattern = spec["installer"].replace("{version}", re.escape(args.version))
            files = list(args.directory.iterdir())
            matches = [path for path in files if re.fullmatch(pattern, path.name)]
            if len(matches) != 1:
                raise candidate.Refused(f"{args.target}: expected one matching installer, found {len(matches)}")
            args.installer = matches[0]
            if args.cover_directory:
                args.asset.extend(path for path in files if path != args.installer)
        record(args.repo, args.target, args.version, args.installer, args.asset, args.output, command)
    except (ValueError, OSError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        parser.exit(3, f"Install proof refused: {exc}\n")
    print(f"INSTALL PROOF: {args.target} {args.version} -> {args.output}")


if __name__ == "__main__":
    main()
