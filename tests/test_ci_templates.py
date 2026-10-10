"""CI master copies in fieldkit/build/ci (2026-10-10): every Fieldkit command a workflow runs must exist, and every
Fieldkit file it names must be there, so a copied workflow cannot fail on a typo after a 30-minute kernel build."""
import re
import shlex
from pathlib import Path

import pytest
import yaml

from fieldkit.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = sorted((ROOT / "fieldkit" / "build" / "ci").glob("*.yml"))


def _commands(text):
    """Logical shell lines (continuations joined) that run fieldkit."""
    joined = re.sub(r"\s*\\\n\s*", " ", text)
    for line in joined.splitlines():
        line = line.strip()
        for part in re.split(r"\s*(?:&&|;|\|\|)\s*", line):
            part = part.strip().lstrip("(").rstrip(")")
            if part.startswith("fieldkit "):
                yield part


def test_there_is_a_template():
    assert TEMPLATES, "fieldkit/build/ci has no workflow"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_every_fieldkit_command_in_the_workflow_parses(path):
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    runs = [s.get("run", "") for job in wf["jobs"].values() for s in job["steps"]]
    cmds = [c for r in runs for c in _commands(r)]
    assert len(cmds) >= 5, cmds
    p = build_parser()
    for c in cmds:
        argv = [a.split("#")[0] for a in shlex.split(c, comments=True)][1:]
        try:
            p.parse_args(argv)
        except SystemExit:
            pytest.fail(f"{path.name}: `{c}` is not a valid fieldkit command")


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_every_fieldkit_file_the_workflow_names_exists(path):
    text = path.read_text(encoding="utf-8")
    named = set(re.findall(r"\.fieldkit/([\w./-]+\.(?:ya?ml|py|json))", text))
    assert named
    for rel in named:
        assert (ROOT / rel).is_file(), f"{path.name} names .fieldkit/{rel}, which does not exist"


def test_the_kernel_release_spec_proves_what_it_publishes():
    spec = yaml.safe_load((ROOT / "releases" / "debian-kernel.yaml").read_text(encoding="utf-8"))
    assert spec["tests"] and spec["privacy"] is True
    assets = {a["asset"] for a in spec["artifacts"]}
    assert {"linux-image-*.deb", "linux-headers-*.deb"} <= assets
    assert all(a["local"].startswith("${ENV:KOUT}/") for a in spec["artifacts"])
    assert "${ENV:RELEASE_TAG}" == spec["tag"]


def test_a_misspelt_command_would_be_caught():
    cmds = list(_commands("export X=1\nfieldkit release-page compse --out x \\\n  --layman y\n(cd a && fieldkit doctor --for k) || true\n"))
    assert cmds == ["fieldkit release-page compse --out x --layman y", "fieldkit doctor --for k"]
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(shlex.split(cmds[0])[1:])
    p.parse_args(shlex.split(cmds[1])[1:])
