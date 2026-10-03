"""fieldkit docs (Gorilla.Documentation.IBM.Style): the group map, the checks, prep, and the committed docs.

The last tests run `fieldkit docs check` against the documents committed in docs/dual-track/, so a
regression in the docs (a missing section, an invented number, a command that no longer exists, a home
path, "the owner") fails the suite like a regression in code. They skip cleanly when the docs are absent.
A stale group (code changed after the last render) is a warning here, not a failure: refreshing it needs a
model to rewrite the JSON. `fieldkit docs check --strict` is the release-gate form that fails on it.
"""
import json
from pathlib import Path

import pytest

from fieldkit import cli
from fieldkit.core import settings
from fieldkit.core.pipeline import Pipeline
from fieldkit.gdocs import checks, dualtrack, stages
from fieldkit.gdocs import groups as G
from fieldkit.gdocs import workflow as W

PARSER = cli.build_parser()
DOCS_PRESENT = G.GROUPS_FILE.is_file() and G.OUT.is_dir()


def _have_dual_track():
    try:
        dualtrack.path()
        return True
    except dualtrack.DualTrackMissing:
        return False


# -- the group map ------------------------------------------------------------------------------
def _tree(tmp_path, files):
    for f in files:
        p = tmp_path / "pkg" / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {f}\nX = 1\n", encoding="utf-8")
    return tmp_path / "pkg"


def test_globs_match_one_folder_level(tmp_path):
    pkg = _tree(tmp_path, ["core/a.py", "core/sub/b.py", "top.py", "build/k.py"])
    assert G.files_of({"name": "c", "sources": ["core/*.py"]}, pkg) == ["core/a.py"]
    assert G.files_of({"name": "t", "sources": ["*.py"]}, pkg) == ["top.py"]
    assert G.files_of({"name": "b", "sources": ["build/k.py", "build/*.py"]}, pkg) == ["build/k.py"]


def test_coverage_reports_orphans_duplicates_and_empty_groups(tmp_path):
    pkg = _tree(tmp_path, ["__init__.py", "__main__.py", "a.py", "b.py", "core/__init__.py", "core/c.py"])
    gs = [{"name": "one", "sources": ["a.py", "core/*.py"]}, {"name": "two", "sources": ["a.py"]},
          {"name": "none", "sources": ["nothing/*.py"]}]
    cov = G.coverage(gs, pkg)
    assert not cov["ok"]
    assert cov["orphans"] == ["b.py"]                       # __init__ / __main__ are never required
    assert cov["duplicates"] == {"a.py": ["one", "two"]}
    assert cov["empty_groups"] == ["none"]


def test_staging_flattens_paths_so_dual_track_sees_every_file(tmp_path):
    pkg = _tree(tmp_path, ["build/__init__.py", "build/k.py", "top.py"])
    out = G.stage({"name": "g", "sources": ["build/*.py", "top.py"]}, tmp_path / "st", pkg)
    assert sorted(p.name for p in out.iterdir()) == ["build____init__.py", "build__k.py", "top.py"]


def test_status_fresh_then_stale(tmp_path, monkeypatch):
    pkg = _tree(tmp_path, ["a.py"])
    monkeypatch.setattr(G, "PKG", pkg)
    g = {"name": "g", "sources": ["a.py"]}
    out = tmp_path / "out"
    assert G.status(g, out)["state"] == "never"
    G.write_state(g, {"sources": G.hashes(g)}, out)
    assert G.status(g, out)["state"] == "fresh"
    (pkg / "a.py").write_text("X = 2\n", encoding="utf-8")
    st = G.status(g, out)
    assert st["state"] == "stale" and st["changed"] == ["a.py"]


def test_hash_ignores_crlf(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_bytes(b"x = 1\ny = 2\n")
    b.write_bytes(b"x = 1\r\ny = 2\r\n")
    assert G.sha256(a) == G.sha256(b)


def test_pick_refuses_unknown_names():
    with pytest.raises(G.GroupError):
        G.pick([{"name": "a", "sources": ["x"]}], ["b"])


# -- the checks -----------------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "fieldkit docs check --strict", "fieldkit next gorilla-documentation-ibm-style",
    "fieldkit build-harness leakgate --only canary", "fieldkit kernel localversion --base 7.1.2 --tags a b",
    "fieldkit triage build.log --set auto", "fieldkit agent undo RUN_ID", "fieldkit pipeline run NAME --only fill",
    "fieldkit office scrub report.docx --check --term WORD",
])
def test_real_commands_parse(cmd):
    assert checks.check_command(cmd, PARSER) is None


@pytest.mark.parametrize("cmd, why", [
    ("fieldkit build triage x.log", "no subcommand 'build'"),
    ("fieldkit docs publish", "no action 'publish'"),
    ("fieldkit docs render exam --validate", "no option --validate"),
    ("fieldkit kernel compile", "no action 'compile'"),
])
def test_invented_commands_fail(cmd, why):
    assert why in (checks.check_command(cmd, PARSER) or "")


def test_a_wrong_command_may_be_named_to_say_it_does_not_exist():
    md = "The docstring says `fieldkit build triage`; the real subcommand is `fieldkit triage`.\n"
    assert checks.commands_in(md) == []
    assert checks.commands_in("Run `fieldkit build triage` now.\n") == ["fieldkit build triage"]


def test_numbers_must_be_sourced_or_marked_not_measured():
    allowed = checks.allowed_numbers(["TEXT_LIMIT = 5_000_000", "cap 75.0", "version 155.0.1"])
    md = ("Files over 5,000,000 bytes are skipped. The cap is 75 C. Build 155.0.1.\n"
          "It takes 42 seconds.\nIt takes 43 seconds (not measured).\n"
          "**Step 12:** run `fieldkit thermal watch --seconds 600`\n")
    found = checks.number_findings(md, allowed)
    assert len(found) == 1 and "number 42" in found[0]


def test_web_addresses_must_be_in_the_source():
    found = checks.url_findings("See https://example.org/a and http://localhost:1234/v1.", "http://localhost:1234/v1")
    assert len(found) == 1 and "example.org" in found[0]


def test_banned_words_and_privacy():
    md = ("## Big\nAsk the owner. Claude wrote it. Gemma is measured.\n"
          "A file in C:\\Users\\somebody\\x and `the owner` in code is a quote.\n"  # privacy-scan: allow
          "It says \"until the owner writes it in\".\n")
    f = checks.privacy_findings(md, terms=[], corpus="names stay unexpected until the owner writes it in")
    assert sum("the owner" in x for x in f) == 1                 # prose only; code span and verbatim quote pass
    assert any("Claude" in x for x in f) and not any("Gemma" in x for x in f)
    assert any("windows-user-path" in x for x in f)


def test_a_fence_after_step_label_is_a_finding():
    md = "## Tasks\n**Step 1:** ```bash\nls\n```\n"
    f = checks._common(md, "developer", set(), "", PARSER, [], {})
    assert any("code fence must start its own line" in x for x in f)


def test_thin_layman_document_fails_with_every_reason():
    md = "# T\n\n## Should You Run This?\n\nYes.\n\n## Key Concepts\n\n| Name | What | Comparison |\n|---|---|---|\n| `x` | a thing |  |\n"
    f, m = checks.check_layman(md, set(), "", PARSER, [])
    text = "\n".join(f)
    for need in ("section missing: Worst Case", "section missing: Glossary", "has 1 words; the floor is 60",
                 "without a real-world comparison", "how to open PowerShell", "document has"):
        assert need in text, need


def test_schema_check():
    schema = {"type": "object", "required": ["a", "c"], "properties": {
        "a": {"type": "string"}, "b": {"type": "array", "items": {"type": "object", "required": ["basis"],
                                                               "properties": {"basis": {"enum": ["x", "y"]}}}}}}
    errs = W.schema_errors({"a": 1, "b": [{"basis": "z"}, {}]}, schema)
    assert any("missing required 'c'" in e for e in errs)
    assert any("$.a: expected string" in e for e in errs)
    assert any("'z' is not one of" in e for e in errs)
    assert any("$.b[1]: missing required 'basis'" in e for e in errs)
    assert W.schema_errors({"a": "s", "c": None}, schema) == []


# -- prep and render (real dual_track.py, offline) ----------------------------------------------------
@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A tiny Fieldkit-shaped tree: fieldkit/tool.py, docs/groups.yaml, MEASUREMENTS.md."""
    root = tmp_path / "fk"
    pkg = root / "fieldkit"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "sub" / "tool.py").write_text('"""Add numbers."""\n\n\ndef add(a, b):\n    return a + b\n', encoding="utf-8")
    docs = root / "docs"
    (docs / "dual-track").mkdir(parents=True)
    (docs / "groups.yaml").write_text("groups:\n  - name: tools\n    title: Tools\n    sources: [\"sub/*.py\"]\n",
                                      encoding="utf-8")
    (docs / "dual-track" / "MEASUREMENTS.md").write_text("# Verified\n- 3 tests passed.\n", encoding="utf-8")
    for k, v in {"ROOT": root, "PKG": pkg, "DOCS": docs, "OUT": docs / "dual-track", "STAGING": docs / "_staging",
                 "GROUPS_FILE": docs / "groups.yaml", "MEASUREMENTS": docs / "dual-track" / "MEASUREMENTS.md"}.items():
        monkeypatch.setattr(G, k, v)
    monkeypatch.setattr(W, "_CORPUS", {})
    return root


@pytest.mark.skipif(not _have_dual_track(), reason="dual_track.py not on this machine")
def test_prep_writes_the_brief_and_fixes_then_run(sandbox):
    rows = W.prep(["tools"])
    assert rows[0]["status"] == "prepared", rows
    env = json.loads((sandbox / "docs/dual-track/tools/tools_layman.prep.json").read_text(encoding="utf-8"))
    assert env["then_run"] == "fieldkit docs render tools"
    assert "--validate" not in env["then_run"]
    assert "GORILLA WRITER BRIEF" in env["instructions"] and "the maintainer" in env["instructions"]
    assert env["write_completion_to"] == "docs/dual-track/tools/tools_layman.filled.json"
    assert env["gorilla"]["sources"] == G.hashes(G.load()[0])
    text = json.dumps(env)
    assert str(Path.home()) not in text and str(Path.home()).replace("\\", "/") not in text
    assert W.prep(["tools"])[0]["status"] == "current"          # idempotent while nothing changed
    fills = W.fill_status(["tools"])
    assert [r["ok"] for r in fills] == [False, False] and fills[0]["problems"] == ["not written yet"]


@pytest.mark.skipif(not _have_dual_track(), reason="dual_track.py not on this machine")
def test_render_fails_closed_and_keeps_the_group_stale(sandbox):
    W.prep(["tools"])
    for t in W.TRACKS:
        W.filled_path(G.load()[0], t).write_text(json.dumps({"title": "x"}), encoding="utf-8")
    rows = W.render(["tools"])
    assert rows[0]["ok"] is False and rows[0]["findings"]
    assert G.status(G.load()[0])["state"] == "never"           # a failed render never records fresh hashes


@pytest.mark.skipif(not _have_dual_track(), reason="dual_track.py not on this machine")
def test_render_refuses_answers_written_for_older_sources(sandbox):
    W.prep(["tools"])
    g = G.load()[0]
    for t in W.TRACKS:
        W.filled_path(g, t).write_text(json.dumps({"title": "x"}), encoding="utf-8")
    (sandbox / "fieldkit" / "sub" / "tool.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")
    rows = W.render(["tools"])
    assert rows[0]["ok"] is False and "do not match the current sources" in rows[0]["findings"][0]
    assert not W.md_path(g, "layman").exists()                  # nothing was rendered


# -- the pipeline -----------------------------------------------------------------------------------------
def test_pipeline_loads_and_next_starts_with_plan(tmp_path):
    from fieldkit.core import next as nxt
    p = Pipeline.load(cli.PIPE_DIR / "gorilla-documentation-ibm-style.yaml", strict=False, state_dir=tmp_path)
    assert [s["id"] for s in p.stages] == ["plan", "prep", "fill", "render", "check", "index"]
    d = nxt.decide(p)
    assert d["kind"] == "DO" and d["command"] == "fieldkit pipeline run gorilla-documentation-ibm-style --only plan"


def test_fill_stage_names_the_files_to_write(sandbox, monkeypatch):
    monkeypatch.setattr(dualtrack, "module", lambda: _FakeDT())
    (sandbox / "docs/dual-track/tools").mkdir(parents=True)
    for t in W.TRACKS:
        (sandbox / f"docs/dual-track/tools/tools_{t}.prep.json").write_text('{"json_schema": {}}', encoding="utf-8")
    r = stages.fill(None, groups="tools")
    assert not r["ok"] and "BLOCKED" in r["detail"]
    assert "docs/dual-track/tools/tools_layman.filled.json" in r["detail"] and "WRITER_BRIEF.md" in r["detail"]


class _FakeDT:
    _CODE_VALIDATION = {"layman": [], "developer": []}

    @staticmethod
    def _extract_json(s):
        return json.loads(s)

    @staticmethod
    def validate_json(data, req):
        return {}


# -- the committed documents ----------------------------------------------------------------------------
@pytest.mark.skipif(not DOCS_PRESENT, reason="docs/groups.yaml or docs/dual-track absent")
def test_every_fieldkit_module_is_in_exactly_one_group():
    cov = G.coverage(G.load())
    assert cov["ok"], f"add to docs/groups.yaml: orphans {cov['orphans']}, duplicates {cov['duplicates']}, " \
                      f"empty {cov['empty_groups']}"


def _groups_with_docs():
    if not DOCS_PRESENT:
        return []
    return [g["name"] for g in G.load() if any(W.md_path(g, t).is_file() for t in W.TRACKS)]


@pytest.mark.skipif(not DOCS_PRESENT, reason="docs/groups.yaml or docs/dual-track absent")
@pytest.mark.parametrize("name", _groups_with_docs())
def test_committed_docs_pass_the_gorilla_checks(name):
    g = G.pick(G.load(), [name])[0]
    r = W.check_group(g, PARSER)
    assert not r["findings"], "\n".join(r["findings"])


def test_brief_ships_with_the_package():
    text = W.BRIEF.read_text(encoding="utf-8")
    for rule in ("British English", "not measured", "the maintainer", "PowerShell", "real-world comparison",
                 "claim_sources", "What this cannot do"):
        assert rule in text
    assert settings.ROOT / "fieldkit" / "gdocs" / "WRITER_BRIEF.md" == W.BRIEF
