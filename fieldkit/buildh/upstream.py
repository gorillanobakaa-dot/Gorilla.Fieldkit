"""upstream - which source is the latest STABLE release, and exactly where it comes from.

The model never chooses a source. This module decides, the same way every time:

  Firefox: Mozilla's official version list (product-details) gives the latest stable
           version; the source is the matching release TAG on
           github.com/mozilla-firefox/firefox. Never `main`: main is Nightly
           (159.0a1 on 2026-09-30, while the stable release was 157.0).
  Kernel:  kernel.org's releases.json gives the latest stable version and its tarball;
           the tarball is checked against kernel.org's sha256sums.asc when fetched.

    fieldkit build-harness latest firefox|kernel [--json]
"""
import json
import re
import subprocess
import urllib.request

FIREFOX_VERSIONS = "https://product-details.mozilla.org/1.0/firefox_versions.json"
FIREFOX_REPO = "https://github.com/mozilla-firefox/firefox"
KERNEL_RELEASES = "https://www.kernel.org/releases.json"
_STABLE_FIREFOX = re.compile(r"^\d+\.\d+(\.\d+)?$")        # 157.0, 155.0.1 - no a1/b1/esr


def _get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "fieldkit-build-harness"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def firefox_tag(version):
    """157.0 -> FIREFOX_157_0_RELEASE. Refuses anything that is not a stable version."""
    if not _STABLE_FIREFOX.match(version or ""):
        raise ValueError(f"{version!r} is not a stable Firefox version (Nightly, beta and ESR are refused)")
    return "FIREFOX_" + version.replace(".", "_") + "_RELEASE"


def remote_commit(repo, tag, runner=subprocess.run):
    """The commit a tag points to on the remote (peeled for annotated tags), or None."""
    r = runner(["git", "ls-remote", "--tags", repo, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
               capture_output=True, text=True, timeout=120)
    refs = dict(reversed(line.split("\t")) for line in r.stdout.split("\n") if "\t" in line)
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")


def latest_firefox(versions=None, repo=FIREFOX_REPO, runner=subprocess.run):
    versions = versions if versions is not None else _get_json(FIREFOX_VERSIONS)
    version = versions["LATEST_FIREFOX_VERSION"]
    tag = firefox_tag(version)
    commit = remote_commit(repo, tag, runner)
    if not commit:
        raise ValueError(f"Mozilla lists {version} as the latest stable release, but {repo} has no tag {tag} yet")
    return {"product": "firefox", "version": version, "tag": tag, "commit": commit, "source": repo,
            "why": f"Mozilla's version list names {version} as the latest stable release; built from its "
                   f"release tag, never from main (Nightly)"}


def latest_kernel(releases=None):
    releases = releases if releases is not None else _get_json(KERNEL_RELEASES)
    version = releases["latest_stable"]["version"]
    entry = next((r for r in releases["releases"] if r["version"] == version), None)
    if not entry or not entry.get("source"):
        raise ValueError(f"kernel.org lists {version} as latest stable but gives no source tarball for it")
    if entry.get("moniker") not in (None, "stable"):
        raise ValueError(f"kernel {version} is {entry.get('moniker')}, not stable")
    return {"product": "kernel", "version": version, "tarball": entry["source"],
            "sums": entry["source"].rsplit("/", 1)[0] + "/sha256sums.asc", "source": "https://www.kernel.org",
            "why": f"kernel.org's releases.json names {version} as latest_stable"}


def latest(product, **kw):
    if product == "firefox":
        return latest_firefox(**kw)
    if product == "kernel":
        return latest_kernel(**kw)
    raise ValueError(f"unknown product {product!r}: firefox or kernel")
