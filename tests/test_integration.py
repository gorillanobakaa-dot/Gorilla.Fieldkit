"""fieldkit integration: every surface rule can fail, the clean fixture passes, --changed looks only at the diff."""
import json
import subprocess
import sys

import pytest

from fieldkit import cli
from fieldkit.integration import cli as icli, core, selftest

RULES = core.load_rules()
CASES = [(k, ct, r["id"]) for k, cfg in RULES.items() for ct, cap in cfg["capabilities"].items() for r in cap["surfaces"]]


@pytest.mark.parametrize("kind,ctype,rid", CASES)
def test_each_surface_omission_is_named_and_the_clean_fixture_passes(tmp_path, kind, ctype, rid):
    clean = tmp_path / "clean"
    clean.mkdir()
    selftest.build(clean, kind)
    assert core.run_check(clean)["ok"], core.run_check(clean)["gaps"]
    d = tmp_path / "omit"
    d.mkdir()
    selftest.build(d, kind, (ctype, rid))
    r = core.run_check(d)
    rule = next(x for x in RULES[kind]["capabilities"][ctype]["surfaces"] if x["id"] == rid)
    want = "*" if rule.get("scope") == "kind" else selftest.PRIMARY[kind][ctype]
    assert any(g["surface"] == rid and g["capability"] == want for g in r["gaps"])
    assert not r["ok"]


def test_every_rule_has_a_surface_number_a_reason_and_a_known_type():
    for kind, ctype, rid in CASES:
        rule = next(x for x in RULES[kind]["capabilities"][ctype]["surfaces"] if x["id"] == rid)
        assert rid[0] in "123456" and rule.get("what") and rule["type"] in (
            "word_in", "exists", "manifest", "systems", "block", "kind_has", "card_field", "card_path", "card_test",
            "card_platforms", "package_card")


@pytest.mark.parametrize("kind", ["opencode", "fieldkit"])
def test_self_test_exits_0(kind, capsys):
    assert icli.main(["self-test", "--kind", kind]) == 0
    out = capsys.readouterr().out
    assert "MISSED" not in out and out.rstrip().splitlines()[-1].startswith("NEXT:")


def test_check_exit_codes_lines_and_json(tmp_path, capsys):
    selftest.build(tmp_path, "opencode", ("helper", "2-howto"))
    assert icli.main(["check", "--repo", str(tmp_path)]) == 3
    out = capsys.readouterr().out
    assert "GAP helper:alphahelper [2-howto]" in out and "-> fix internal/howto/cards/alphahelper.md" in out
    assert out.rstrip().splitlines()[-1].startswith("NEXT:")
    assert icli.main(["check", "--repo", str(tmp_path), "--json"]) == 3
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["gaps"][0]["surface"] == "2-howto" and data["next"]
    assert icli.main(["check", "--repo", str(tmp_path / "nope")]) == 2
    (tmp_path / "empty").mkdir()
    assert icli.main(["check", "--repo", str(tmp_path / "empty")]) == 2


def test_clean_fixture_exits_0(tmp_path):
    selftest.build(tmp_path, "fieldkit")
    assert icli.main(["check", "--repo", str(tmp_path)]) == 0


def _git(root, *a):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                   capture_output=True)


def test_changed_checks_only_what_the_diff_adds(tmp_path):
    selftest.build(tmp_path, "opencode")
    (tmp_path / "internal/howto/cards/alphahelper.md").unlink()        # an OLD gap, not in the diff
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    assert core.run_check(tmp_path, "HEAD")["checked"] == []
    assert not core.run_check(tmp_path)["ok"]                          # the whole-repo check sees the old gap
    sums = tmp_path / "internal/toolbox/SHA256SUMS"
    with open(sums, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{'1' * 64}  windows-amd64/newhelper.exe\n")
    r = core.run_check(tmp_path, "HEAD")
    assert r["checked"] == ["helper:newhelper"]
    assert {g["capability"] for g in r["gaps"]} >= {"newhelper"} and "alphahelper" not in {g["capability"] for g in r["gaps"]}


def test_changed_sees_a_new_or_edited_card_and_a_new_package(tmp_path):
    selftest.build(tmp_path, "fieldkit")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    tools = tmp_path / "fieldkit/desk/tools.yaml"
    tools.write_bytes(tools.read_bytes().replace(b"alpha title", b"alpha title changed"))
    (tmp_path / "fieldkit/newpkg").mkdir()
    (tmp_path / "fieldkit/newpkg/__init__.py").write_text("", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    assert set(core.run_check(tmp_path, "HEAD")["checked"]) == {"tool:alpha", "package:newpkg"}


def test_aliases_let_the_arsenal_name_a_helper_differently(tmp_path):
    selftest.build(tmp_path, "opencode", ("helper", "3-arsenal"))
    (tmp_path / "internal/arsenal").mkdir(parents=True)
    (tmp_path / "internal/arsenal/manifest.json").write_text(
        json.dumps({"series": [{"entries": [{"id": "astgrep", "detect": {"binaries": ["sg"]}}]}]}), encoding="utf-8")
    sums = tmp_path / "internal/toolbox/SHA256SUMS"
    sums.write_text(sums.read_text() + f"{'1' * 64}  windows-amd64/ast-grep.exe\n", encoding="utf-8")
    gaps = [g for g in core.run_check(tmp_path)["gaps"] if g["capability"] == "ast-grep" and g["surface"] == "3-arsenal"]
    assert gaps == []


def test_a_system_lacking_the_helper_must_be_stated_in_the_manifest(tmp_path):
    selftest.build(tmp_path, "opencode", ("helper", "5-systems"))
    sums = tmp_path / "internal/toolbox/SHA256SUMS"
    assert any(g["surface"] == "5-systems" for g in core.run_check(tmp_path)["gaps"])
    (tmp_path / "internal/arsenal").mkdir(parents=True, exist_ok=True)
    (tmp_path / "internal/arsenal/manifest.json").write_text(json.dumps({"series": [{"entries": [
        {"id": "alphahelper", "detect": {"binaries": ["alphahelper"]}, "absent_on": {"linux": "x", "macos": "y"}}]}]}),
        encoding="utf-8")
    assert not any(g["surface"] == "5-systems" for g in core.run_check(tmp_path)["gaps"])


def test_registered_in_the_cli():
    assert cli.main(["integration", "self-test", "--kind", "fieldkit"]) == 0
    r = subprocess.run([sys.executable, "-m", "fieldkit", "integration", "--help"], capture_output=True, text=True)
    assert "self-test" in r.stdout
