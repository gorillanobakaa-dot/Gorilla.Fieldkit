"""fieldkit skills: every Agent Skills spec rule (agentskills.io, parity cases from skills-ref's own tests), the doors
check, align, vendor, the installer's third-party rules, the sync row and the adopted skills' commands.
Run: python -m pytest -q tests/test_skills.py"""
import json
import re
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import install_skills
from fieldkit import skills
from fieldkit.core import sync
from fieldkit.skills import adopted, doors, spec

ROOT = Path(__file__).resolve().parents[1]


def mk(tmp_path, folder, front, body="# T\n", name="SKILL.md"):
    d = tmp_path / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(f"---\n{front}\n---\n{body}", encoding="utf-8")
    return d


def errors(d, strict=False):
    return [(r, m) for r, lv, m in spec.validate(d, strict) if lv == "error"]


def rules(d, strict=False):
    return {r for r, lv, _ in spec.validate(d, strict) if lv == "error"}


# -- the spec, rule by rule ---------------------------------------------------------------------------------------------

def test_valid_minimal_and_all_fields(tmp_path):
    assert spec.validate(mk(tmp_path, "my-skill", "name: my-skill\ndescription: A test skill")) == []
    full = ("name: my-skill\ndescription: A test skill\nlicense: Apache-2.0\ncompatibility: Requires Python 3.11+\n"
            "metadata:\n  author: example-org\n  version: \"1.0\"\nallowed-tools: Bash(git:*) Read")
    assert spec.validate(mk(tmp_path / "b", "my-skill", full)) == []


@pytest.mark.parametrize("name,rule_text", [
    ("MySkill", "lowercase"), ("-my-skill", "start or end"), ("my-skill-", "start or end"),
    ("my--skill", "consecutive"), ("my_skill", "letters, digits and hyphens"), ("НАВЫК", "lowercase")])
def test_bad_names(tmp_path, name, rule_text):
    d = mk(tmp_path, name, f"name: {name}\ndescription: A test skill")
    assert any(rule_text in m for _, m in errors(d)), errors(d)


def test_name_length_and_folder(tmp_path):
    long = "a" * 65
    assert any("65 characters" in m for _, m in errors(mk(tmp_path, long, f"name: {long}\ndescription: x")))
    ok = "a" * 64
    assert errors(mk(tmp_path, ok, f"name: {ok}\ndescription: x")) == []
    assert rules(mk(tmp_path, "other-folder", "name: correct-name\ndescription: x")) == {"name-folder"}


@pytest.mark.parametrize("name", ["技能", "мой-навык", "навык"])
def test_i18n_names_valid_like_skills_ref_with_a_portability_warning(tmp_path, name):
    found = spec.validate(mk(tmp_path, name, f"name: {name}\ndescription: x"))
    assert [lv for _, lv, _ in found] == ["warn"] and found[0][0] == "name-ascii"


def test_keys_description_compatibility_limits(tmp_path):
    assert rules(mk(tmp_path, "a", "name: a\ndescription: x\nversion: 1")) == {"keys"}
    assert rules(mk(tmp_path, "b", "name: b")) == {"description"}
    assert rules(mk(tmp_path, "c", "description: x")) == {"name"}
    assert rules(mk(tmp_path, "d", "name: d\ndescription: ''")) == {"description"}
    assert rules(mk(tmp_path, "e", f"name: e\ndescription: {'x' * 1025}")) == {"description"}
    assert errors(mk(tmp_path, "f", f"name: f\ndescription: {'x' * 1024}")) == []
    assert rules(mk(tmp_path, "g", f"name: g\ndescription: x\ncompatibility: {'c' * 501}")) == {"compatibility"}
    assert errors(mk(tmp_path, "h", f"name: h\ndescription: x\ncompatibility: {'c' * 500}")) == []
    br = mk(tmp_path, "i", "name: i\ndescription: when a<b holds")
    assert [(r, lv) for r, lv, _ in spec.validate(br)] == [("description-brackets", "warn")]
    assert rules(br, strict=True) == {"description-brackets"}


def test_metadata_strings_license_allowed_tools(tmp_path):
    assert rules(mk(tmp_path, "a", "name: a\ndescription: x\nmetadata:\n  version: 1.0")) == {"metadata"}
    assert rules(mk(tmp_path, "b", "name: b\ndescription: x\nmetadata: [a, b]")) == {"metadata"}
    assert rules(mk(tmp_path, "c", "name: c\ndescription: x\nlicense: ''")) == {"license"}
    assert rules(mk(tmp_path, "d", "name: d\ndescription: x\nallowed-tools: [Read, Bash]")) == {"allowed-tools"}


def test_frontmatter_shape_and_unquoted_colon(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")
    assert rules(d) == {"frontmatter"}
    (d / "SKILL.md").write_text("---\nname: a\ndescription: x\n", encoding="utf-8")
    assert rules(d) == {"frontmatter"}
    # the fieldkit-gpudecode bug (2026-10-26): an unquoted ': ' inside a plain description
    assert rules(mk(tmp_path, "b", "name: b\ndescription: Also over SSH: `mac-ab` plays")) == {"yaml"}
    assert rules(mk(tmp_path, "c", "name: c\ndescription: >-\n  Also over SSH: `mac-ab` plays")) == set()
    assert rules(tmp_path / "missing") == {"folder"}
    (tmp_path / "empty").mkdir()
    assert rules(tmp_path / "empty") == {"skill-md"}


def test_lowercase_skill_md_accepted_with_warning(tmp_path):
    d = mk(tmp_path, "a", "name: a\ndescription: x", name="skill.md")
    assert [(r, lv) for r, lv, _ in spec.validate(d)] == [("skill-md", "warn")]


def test_body_lines_and_references_strict_and_not(tmp_path):
    d = mk(tmp_path, "a", "name: a\ndescription: x", body="line\n" * 500)
    assert [lv for r, lv, _ in spec.validate(d) if r == "lines"] == ["warn"]
    assert rules(d, strict=True) == {"lines"}
    assert spec.validate(mk(tmp_path / "ok", "a", "name: a\ndescription: x", body="line\n" * 499), True) == []
    body = "[r](references/REF.md) [gone](references/GONE.md) [out](../x.md) [web](https://example.com/a) [a](#top)\n"
    d = mk(tmp_path / "r", "a", "name: a\ndescription: x", body=body)
    (d / "references").mkdir()
    (d / "references" / "REF.md").write_text("x", encoding="utf-8")
    msgs = [m for r, _, m in spec.validate(d, True) if r == "reference"]
    assert len(msgs) == 2 and any("GONE" in m for m in msgs) and any("leaves" in m for m in msgs)


def test_every_fieldkit_skill_meets_the_spec_strictly_and_third_party_has_no_errors():
    for d in spec.skill_dirs(ROOT / "skills"):
        assert spec.validate(d, strict=True) == [], d.name
        meta = __import__("yaml").safe_load(spec.split_frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))[0])
        assert meta.get("license") == "AGPL-3.0-or-later" and meta.get("compatibility", "").startswith(skills.OURS)
    for d in spec.skill_dirs(ROOT / "third_party" / "anthropic-skills" / "skills"):
        assert errors(d) == [], d.name


# -- doors ------------------------------------------------------------------------------------------------------------

def test_doors_find_remote_loads_net_apis_vendor_sdks_and_installs():
    html = ('<script src="https://cdn.example-cdn.net/p5.min.js"></script>\n'
            '<link href="https://fonts.googleapis.com/css2?family=X" rel="stylesheet">\n'
            '<svg xmlns="http://www.w3.org/2000/svg"></svg>\n<a href="http://localhost:8000/x">\n'
            'fetch("/api/feedback")\n')
    found = doors.scan_text("v.html", html)
    assert [f["match"].split("/")[2] for f in found] == ["cdn.example-cdn.net", "fonts.googleapis.com"]
    py = ("import urllib.request\nfrom anthropic import Anthropic\ncmd = ['claude', '-p']\n"
          "URL = 'http://127.0.0.1:9/x'\nAPI = 'https://api.example.com/v1'\nos.system('pip install x')\n")
    what = [f["what"] for f in doors.scan_text("s.py", py)]
    assert what == ["net-api", "vendor-api", "vendor-api", "install"]
    prose = doors.scan_text("SKILL.md", "run `npx @scope/tool` from https://modelcontextprotocol.io/x\n")
    assert {f["kind"] for f in prose} == {"prose"} and len(prose) == 2
    assert doors.scan_text("logo.png", "https://x.org") == []


def test_accept_rules_match_file_prefix_what_and_substring():
    f = {"file": "skills/a/scripts/x.py", "what": "net-api", "match": "webbrowser.open"}
    assert doors.accepted(f, [{"file": "skills/a/", "what": "net-api", "match": "webbrowser", "why": "w"}])
    assert not doors.accepted(f, [{"file": "skills/a/", "what": "url", "match": "webbrowser"}])
    assert not doors.accepted(f, [{"file": "skills/a/scripts/y.py", "match": ""}])
    assert not doors.accepted(f, [{"file": "skills/a", "match": ""}])          # no slash: exact file only


def test_the_adopted_tree_has_no_unaccepted_door_and_no_telemetry():
    rows = skills.door_rows()
    assert rows and not [r for r in rows if r["kind"] == "door" and not r["accepted"]]
    r = subprocess.run([sys.executable, "-m", "fieldkit", "telemetry", "scan", str(ROOT / "third_party" / "anthropic-skills")],
                       capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0 and "0 finding(s)" in r.stdout, r.stdout + r.stderr


def test_adopted_tree_matches_the_manifest_and_keeps_licenses():
    man = skills.manifest()
    t = ROOT / "third_party" / "anthropic-skills"
    assert sorted(p.name for p in (t / "skills").iterdir()) == sorted(man["skills"])
    assert re.fullmatch(r"[0-9a-f]{40}", man["commit"])
    for n in man["skills"]:
        assert "Apache License" in (t / "skills" / n / "LICENSE.txt").read_text(encoding="utf-8")
    assert man["commit"] in (t / "CHANGES.md").read_text(encoding="utf-8")
    for n in list(man["reference_only"]) + list(man["refused"]) + list(man["dropped"]):
        assert not (t / "skills" / n).exists(), n
    for r in man["strip"]:
        if r.get("remove"):
            assert not (t / r["file"]).exists()


# -- vendor -----------------------------------------------------------------------------------------------------------

def test_vendor_rebuilds_from_upstream_and_fails_closed(tmp_path, monkeypatch):
    up = tmp_path / "up"
    for n in ("keep", "drop"):
        mk(up / "skills", n, f"name: {n}\ndescription: x")
        (up / "skills" / n / "LICENSE.txt").write_text("Apache License", encoding="utf-8")
    (up / "skills" / "keep" / "v.html").write_text('<link href="https://fonts.googleapis.com/x">\n<p>\n', encoding="utf-8")
    (up / "NOTICES.md").write_text("n", encoding="utf-8")
    third = tmp_path / "third"
    third.mkdir()
    man = {"upstream": "u", "commit": "c" * 40, "keep_files": ["NOTICES.md"], "skills": {"keep": {"install": True}},
           "strip": [{"file": "skills/keep/v.html", "delete_lines": "fonts\\.googleapis", "why": "fonts"}]}
    (third / "ADOPTION.yaml").write_text(json.dumps(man), encoding="utf-8")
    monkeypatch.setattr(skills, "THIRD", third)
    monkeypatch.setattr(skills, "MANIFEST", third / "ADOPTION.yaml")
    assert skills.main(["vendor", str(up)]) == 1 and not (third / "skills").exists()      # preview changes nothing
    assert skills.main(["vendor", str(up), "--apply"]) == 0
    assert [p.name for p in (third / "skills").iterdir()] == ["keep"]
    assert (third / "skills" / "keep" / "v.html").read_text(encoding="utf-8") == "<p>\n"
    assert "fonts" in (third / "CHANGES.md").read_text(encoding="utf-8") and (third / "NOTICES.md").is_file()
    man["strip"][0]["delete_lines"] = "no-such-line"
    (third / "ADOPTION.yaml").write_text(json.dumps(man), encoding="utf-8")
    with pytest.raises(ValueError, match="matched nothing"):
        skills.main(["vendor", str(up), "--apply"])


# -- align ------------------------------------------------------------------------------------------------------------

def test_align_adds_license_and_compatibility_once_and_refreshes_only_its_own_line():
    text = "---\nname: fieldkit-x\ndescription: d\n---\n# body\n"
    once = skills.align_text(text, "fieldkit-x", {"fieldkit-x": ["linux", "windows"]})
    assert once.startswith("---\nname: fieldkit-x\n") and "license: AGPL-3.0-or-later\n" in once
    assert "on Linux, Windows" in once and once.endswith("---\n# body\n")
    assert skills.align_text(once, "fieldkit-x", {"fieldkit-x": ["linux", "windows"]}) is None
    again = skills.align_text(once, "fieldkit-x", {})
    assert again and "on Linux" not in again and again.count("compatibility:") == 1
    human = "---\nname: x\ndescription: d\nlicense: MIT\ncompatibility: Needs a GPU\n---\n"
    assert skills.align_text(human, "x", {}) is None


# -- installer and sync -----------------------------------------------------------------------------------------------

@pytest.fixture
def inst(tmp_path, monkeypatch):
    src = tmp_path / "repo"
    mk(src / "skills", "fieldkit-a", "name: fieldkit-a\ndescription: x")
    third = src / "third_party" / "anthropic-skills"
    for n in ("tp-one", "tp-two"):
        mk(third / "skills", n, f"name: {n}\ndescription: x")
    (third / "ADOPTION.yaml").write_text(json.dumps({"skills": {
        "tp-one": {"install": True}, "tp-two": {"install": True, "skip_targets": [".claude"]}}}), encoding="utf-8")
    monkeypatch.setattr(install_skills, "SRC", src / "skills")
    monkeypatch.setattr(install_skills, "LOCAL_SRC", src / "local" / "skills")
    monkeypatch.setattr(install_skills, "THIRD", third)
    monkeypatch.setattr(install_skills, "CARDS", [src / "tools.yaml"])
    (tmp_path / "home" / ".agents").mkdir(parents=True)
    monkeypatch.setattr(install_skills, "TARGETS", [tmp_path / "home" / ".claude" / "skills",
                                                    tmp_path / "home" / ".agents" / "skills"])
    return tmp_path / "home", third


def test_installer_third_party_receipts_skip_targets_and_foreign_names(inst):
    home, third = inst
    (home / ".agents" / "skills" / "tp-one").mkdir(parents=True)              # someone else's folder, same name
    assert install_skills.main([]) == 0
    claude = sorted(p.name for p in (home / ".claude" / "skills").iterdir())
    assert claude == [".fieldkit-skills.json", "fieldkit-a", "tp-one"]          # tp-two kept out of .claude
    assert not any((home / ".agents" / "skills" / "tp-one").iterdir())         # the foreign folder untouched
    assert (home / ".agents" / "skills" / "tp-two" / "SKILL.md").is_file()
    states = {(t.parent.name, n): s for t, n, s in install_skills.status()}
    assert states[(".agents", "tp-one")] == "foreign" and states[(".claude", "tp-one")] == "current"
    assert install_skills.main(["--check"]) == 0
    (third / "skills" / "tp-one" / "SKILL.md").write_text("---\nname: tp-one\ndescription: y\n---\n", encoding="utf-8")
    assert install_skills.main(["--check"]) == 1                                # outdated is a failing check
    # no longer adopted: taken back out of every target that has it in its receipt, foreign folder still untouched
    (third / "ADOPTION.yaml").write_text(json.dumps({"skills": {"tp-two": {"install": True}}}), encoding="utf-8")
    install_skills.main([])
    assert not (home / ".claude" / "skills" / "tp-one").exists()
    assert (home / ".agents" / "skills" / "tp-one").is_dir()
    install_skills.main(["--remove"])
    assert sorted(p.name for p in (home / ".claude" / "skills").iterdir()) == []
    assert sorted(p.name for p in (home / ".agents" / "skills").iterdir()) == ["tp-one"]


def test_a_windows_only_skill_stays_off_linux_and_an_old_copy_is_recycled(inst, monkeypatch, capsys):
    home, third = inst
    src = third.parents[1]
    mk(src / "skills", "fieldkit-win", "name: fieldkit-win\ndescription: x")
    (src / "tools.yaml").write_text(json.dumps({"tools": [
        {"id": "w", "platforms": ["windows"], "skill": "skills/fieldkit-win/SKILL.md"},
        {"id": "a", "platforms": ["windows", "linux"]}]}), encoding="utf-8")
    monkeypatch.setattr(install_skills, "recycle", lambda p: (False, "no gorilla-size"))
    monkeypatch.setattr(sys, "platform", "win32")
    install_skills.main([])
    assert (home / ".claude" / "skills" / "fieldkit-win" / "SKILL.md").is_file()      # on Windows: installed
    monkeypatch.setattr(sys, "platform", "linux")
    assert [s.name for s in install_skills.sources()[0]] == ["fieldkit-a"]
    states = {(t.parent.name, n): s for t, n, s in install_skills.status()}
    assert states[(".claude", "fieldkit-win")] == "off-os"
    assert install_skills.main(["--check"]) == 1                                     # off-os fails the check
    install_skills.main([])                                                          # recycle fails: kept, never rmtree
    assert (home / ".claude" / "skills" / "fieldkit-win").is_dir()
    assert "kept" in capsys.readouterr().out
    bin_ = home / "bin"
    bin_.mkdir()
    monkeypatch.setattr(install_skills, "recycle", lambda p: (p.rename(bin_ / f"{p.parent.parent.name}-{p.name}"), "")
                        and (True, ""))
    install_skills.main([])
    for t in (".claude", ".agents"):
        assert not (home / t / "skills" / "fieldkit-win").exists()
    assert (bin_ / ".claude-fieldkit-win" / "SKILL.md").is_file()                     # in the bin, not deleted
    assert install_skills.main(["--check"]) == 0
    assert (home / ".claude" / "skills" / "fieldkit-a").is_dir()


def test_same_compares_bytes_at_every_depth(tmp_path):
    a, b = tmp_path / "a" / "d", tmp_path / "b" / "d"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "f").write_bytes(b"one")
    (b / "f").write_bytes(b"two")
    assert not install_skills.same(tmp_path / "a", tmp_path / "b")
    (b / "f").write_bytes(b"one")
    assert install_skills.same(tmp_path / "a", tmp_path / "b")


def test_sync_skills_row_reports_then_installs(inst):
    row = sync.skills_row(False, install_skills)
    assert row["verdict"] == "STALE" and not row["ok"]
    row = sync.skills_row(True, install_skills)
    assert row["verdict"] == "INSTALLED" and row["ok"]
    assert sync.skills_row(False, install_skills)["verdict"] == "IN-SYNC"


def test_sync_names_why_an_off_os_copy_stays(inst, monkeypatch):
    home, _ = inst
    mk(home / ".claude" / "skills", "fieldkit-win", "name: fieldkit-win\ndescription: x")
    (home / "cards.yaml").write_text("- {id: w, skill: skills/fieldkit-win/SKILL.md, platforms: [windows]}\n",
                                     encoding="utf-8")
    monkeypatch.setattr(install_skills, "CARDS", [home / "cards.yaml"])
    monkeypatch.setattr(install_skills, "this_os", lambda plat=None: "linux")
    monkeypatch.setattr(install_skills, "recycle", lambda p: (False, "gorilla-size is not built here"))
    mk(install_skills.SRC, "fieldkit-win", "name: fieldkit-win\ndescription: x")
    row = sync.skills_row(True, install_skills)
    assert row["verdict"] == "ERROR" and "still stale" in row["why"]
    assert "not recycled: gorilla-size is not built here" in row["why"]


def test_hook_runs_the_validator_only_for_the_watched_paths():
    hook = (ROOT / "hooks" / "pre-commit").read_text(encoding="utf-8")
    m = re.search(r"grep -qE '([^']+)'; then\n  if ! \"\$PY\" -m fieldkit skills validate", hook)
    assert m, "the skills block is missing from the hook"
    rx = re.compile(m.group(1))
    for p in ("skills/fieldkit-size/SKILL.md", "third_party/anthropic-skills/ADOPTION.yaml", "install_skills.py",
              "fieldkit/skills/spec.py"):
        assert rx.search(p) and p.startswith(skills.WATCHED), p
    for p in ("fieldkit/cli.py", "tests/test_skills.py", "docs/skills.md", "kits/x/skills/a.py"):
        assert not rx.search(p) and not p.startswith(skills.WATCHED), p


def test_validate_staged_is_silent_when_no_skill_changed(monkeypatch, capsys):
    monkeypatch.setattr(skills, "staged_paths", lambda repo=None: ["fieldkit/cli.py"])
    assert skills.main(["validate", "--staged"]) == 0 and capsys.readouterr().out == ""


# -- the adopted skills' commands -------------------------------------------------------------------------------------

def test_mcp_eval_and_skill_eval_are_explicit_opt_in(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls = []
    monkeypatch.setattr(adopted, "_run", lambda argv, cwd=None, env=None: calls.append((argv, env)) or 0)
    assert adopted.mcp_eval(["e.xml"]) == 2 and "no default endpoint" in capsys.readouterr().out
    assert adopted.mcp_eval(["e.xml", "--api-base", "http://127.0.0.1:9"]) == 2
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    assert adopted.mcp_eval(["e.xml", "--api-base", "http://127.0.0.1:9", "-t", "stdio"]) == 0
    argv, env = calls[-1]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9" and argv[-3:] == ["e.xml", "-t", "stdio"]
    assert adopted.run_eval(["--eval-set", "e.json", "--skill-path", "s"]) == 2 and len(calls) == 1
    assert adopted.run_eval(["--eval-set", "e.json", "--skill-path", "s", "--yes"]) == 0
    assert calls[-1][0][-4:] == ["--eval-set", "e.json", "--skill-path", "s"]


def test_package_is_deterministic_and_validates_first(tmp_path):
    d = mk(tmp_path / "src", "my-skill", "name: my-skill\ndescription: x")
    for rel in ("scripts/run.py", "evals/e.json", "scripts/__pycache__/x.pyc", "deep/evals/kept.json", ".DS_Store"):
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(rel, encoding="utf-8")
    assert adopted.package([str(d), str(tmp_path / "o1")]) == 0
    assert adopted.package([str(d), str(tmp_path / "o2")]) == 0
    one, two = (tmp_path / "o1" / "my-skill.skill").read_bytes(), (tmp_path / "o2" / "my-skill.skill").read_bytes()
    assert one == two
    z = zipfile.ZipFile(tmp_path / "o1" / "my-skill.skill")
    assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}      # no clock in the bytes
    names = z.namelist()
    z.close()
    assert names == ["my-skill/SKILL.md", "my-skill/deep/evals/kept.json", "my-skill/scripts/run.py"]
    bad = mk(tmp_path / "bad", "Bad", "name: Bad\ndescription: x")
    assert adopted.package([str(bad), str(tmp_path / "o3")]) == 1 and not (tmp_path / "o3").exists()


def test_benchmark_runs_the_vendored_aggregator_offline(tmp_path):
    out = tmp_path / "benchmark.json"
    (tmp_path / "runs").mkdir()
    r = subprocess.run([sys.executable, "-m", "fieldkit", "skills", "benchmark", str(tmp_path / "runs"), "-o", str(out)],
                       capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "run_summary" in json.loads(out.read_text(encoding="utf-8"))


def test_prefix_diff_finds_a_changed_system_prompt(tmp_path):
    a = {"model": "m", "system": "Be concise.", "messages": [{"role": "user", "content": "hi"}]}
    b = {"model": "m", "system": "Be concise. Current time: 12:00", "messages": a["messages"] + [
        {"role": "assistant", "content": "hello"}, {"role": "user", "content": "more"}]}
    (tmp_path / "a.json").write_text(json.dumps(a), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(b), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "fieldkit", "prefix-diff", str(tmp_path / "a.json"),
                        str(tmp_path / "b.json"), "--json"], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert "system" in r.stdout, r.stdout + r.stderr
    assert subprocess.run([sys.executable, "-m", "fieldkit", "prefix-diff"], capture_output=True,
                          cwd=ROOT, timeout=60).returncode == 2


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_webtest_starts_a_local_server_runs_the_command_and_stops_it(tmp_path):
    port = _free_port()
    probe = tmp_path / "probe.py"
    probe.write_text("import socket,sys\nsocket.create_connection(('localhost', int(sys.argv[1])), timeout=5)\n"
                     "print('REACHED')\n", encoding="utf-8")
    server = f'"{sys.executable}" -m http.server {port} --bind 127.0.0.1 --directory "{tmp_path}"'
    r = subprocess.run([sys.executable, "-m", "fieldkit", "webtest", "--server", server, "--port", str(port),
                        "--timeout", "30", "--", sys.executable, str(probe), str(port)],
                       capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0 and "REACHED" in r.stdout, r.stdout + r.stderr
    with pytest.raises(OSError):                                    # stopped afterwards
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    assert adopted.webtest(["--server", "x", "--port", "1"]) == 2
    assert adopted.webtest(["--server", "x", "--", "true"]) == 2


def test_webtest_fails_when_the_port_never_opens_and_still_stops_the_server(tmp_path, capsys):
    port = _free_port()
    sleeper = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    t0 = __import__("time").monotonic()
    assert adopted.webtest(["--server", sleeper, "--port", str(port), "--timeout", "2", "--log-dir", str(tmp_path),
                            "--", sys.executable, "-c", "print('never')"]) == 1
    out = capsys.readouterr().out
    assert "did not open within 2 s" in out and "never" not in out.split("running")[0]
    pid = int(re.search(r"pid (\d+)", out).group(1))
    assert __import__("time").monotonic() - t0 < 30
    if sys.platform == "win32":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        assert str(pid) not in r.stdout
    else:
        with pytest.raises(ProcessLookupError):
            __import__("os").kill(pid, 0)
