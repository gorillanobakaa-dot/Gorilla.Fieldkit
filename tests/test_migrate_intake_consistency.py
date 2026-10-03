"""Migration control: S1 upstream intake (pristine N-1 against pristine N, read from git), S8 documents and tools
against the register and the build, and the decision briefs both produce (valid, recorded only by the maintainer)."""
import json
import subprocess
import zipfile

import pytest
import yaml

from fieldkit.briefs import producers, record, schema
from fieldkit.buildh import task
from fieldkit.migrate import consistency, intake, plan


def _git(w, *a):
    return subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True, text=True).stdout.strip()


def two_releases(tmp_path):
    r = tmp_path / "ff"
    for d in ("modules/libpref/init", "browser/app/profile", "browser/components/x", "toolkit/modules", "docshell/base",
              "toolkit/components/backgroundtasks"):
        (r / d).mkdir(parents=True)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "modules/libpref/init/all.js").write_text('pref("a.old", 1);\n', encoding="utf-8")
    (r / "browser/app/profile/firefox.js").write_text("", encoding="utf-8")
    (r / "modules/libpref/init/StaticPrefList.yaml").write_text("- name: dom.old.flag\n  type: bool\n  value: true\n", encoding="utf-8")
    (r / "toolkit/modules/ActorManagerParent.sys.mjs").write_text("let JSWINDOWACTORS = {\n  Foo: {\n  },\n};\n", encoding="utf-8")
    (r / "docshell/base/nsAboutRedirector.cpp").write_text('static const RedirEntry kRedirMap[] = {\n    {"config", "chrome://global/content/config.xhtml", 0},\n};\n', encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "155")
    old = _git(r, "rev-parse", "HEAD")
    (r / "modules/libpref/init/all.js").write_text('pref("a.old", 1);\npref("browser.ml.newthing", true);\n'
                                                   'pref("browser.weather.endpoint", "https://weather.vendor-test.net/api");\n'
                                                   'pref("layout.plain.thing", 2);\n', encoding="utf-8")
    (r / "modules/libpref/init/StaticPrefList.yaml").write_text("- name: dom.old.flag\n  type: bool\n  value: true\n"
                                                                "- name: dom.new.flag\n  type: bool\n  value: false\n", encoding="utf-8")
    (r / "browser/components/x/NetThing.sys.mjs").write_text('export async function go() {\n  return fetch("https://api.newhost-test.net/x");\n}\n', encoding="utf-8")
    (r / "browser/components/x/Quiet.sys.mjs").write_text("export const quiet = 1;\n", encoding="utf-8")
    (r / "toolkit/modules/ActorManagerParent.sys.mjs").write_text("let JSWINDOWACTORS = {\n  Foo: {\n  },\n  NewActor: {\n  },\n};\n", encoding="utf-8")
    (r / "docshell/base/nsAboutRedirector.cpp").write_text('static const RedirEntry kRedirMap[] = {\n    {"config", "chrome://global/content/config.xhtml", 0},\n'
                                                           '    {"newpage", "chrome://global/content/newpage.html", 0},\n};\n', encoding="utf-8")
    (r / "toolkit/components/backgroundtasks/BackgroundTask_ping.sys.mjs").write_text('fetch("https://ping.vendor-test.net/");\n', encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "157")
    return r, old, _git(r, "rev-parse", "HEAD")


def test_intake_finds_what_the_new_release_adds_and_disposes_only_by_rule(tmp_path):
    r, old, new = two_releases(tmp_path)
    disp = {"host:api.newhost-test.net": {"approval": {"by": "owner"}}}
    o, n = intake.Rev(r, old), intake.Rev(r, new)
    try:
        d = intake.compute(o, n, disp, {"entries": []})
    finally:
        o.close()
        n.close()
    it = {(i["category"], i["name"]): i for i in d["items"]}
    assert it[("pref", "browser.ml.newthing")]["ai"] and it[("pref", "browser.ml.newthing")]["auto"] is None
    assert it[("pref", "browser.weather.endpoint")]["network"] and it[("pref", "browser.weather.endpoint")]["auto"] is None
    assert "not network-capable" in it[("pref", "layout.plain.thing")]["auto"]
    assert it[("pref", "dom.new.flag")]["auto"]                                  # a static pref: no URL, no network name
    assert it[("host", "weather.vendor-test.net")]["auto"] is None
    assert "already decided in the leak gate" in it[("host", "api.newhost-test.net")]["auto"]
    assert it[("module", "browser/components/x/NetThing.sys.mjs")]["network"]
    assert "no network API" in it[("module", "browser/components/x/Quiet.sys.mjs")]["auto"]
    assert ("actor", "NewActor") in it and ("about", "about:newpage") in it
    assert it[("backgroundtask", "toolkit/components/backgroundtasks/BackgroundTask_ping.sys.mjs")]["network"]
    cl = {c["id"]: c for c in d["clusters"]}
    assert d["clusters"][0]["category"] == "AI-ML"                              # AI first
    assert "INTAKE-pref-browser-weather" in cl and all(not it_["auto"] for c in d["clusters"] for it_ in d["items"] if it_["id"] in c["items"])


REGISTER = """# the maintainer's register
release: "157.0"

decisions:

  - id: D-157-00
    title: never calls home
    decided: 2026-10-02
    by: maintainer
    provenance: test
    status: enforced
    why: test
    verify:
      - pref: {name: browser.safebrowsing.malware.enabled, value: false, locked: true}
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    o = tmp_path / "owner"
    (o / "working scripts").mkdir(parents=True)
    (o / "working scripts" / "audit_privacy_claims.py").write_text(
        'KEPT_DOORS = [\n    ("browser.safebrowsing.malware.enabled", "true", "kept"),\n    ("extensions.blocklist.enabled", "true", "kept"),\n]\n'
        'TELEMETRY = [\n    ("toolkit.telemetry.server", \'""\', "blank"),\n    ("toolkit.telemetry.enabled", "true", "a stale expectation"),\n]\n',
        encoding="utf-8")
    (o / "docs").mkdir()
    (o / "docs" / "WHY.md").write_text("The local malware list stays on.\n\nThe policy file re-locks it at runtime.\n", encoding="utf-8")
    (o / "decisions").mkdir()
    (o / "decisions" / "PRODUCT-DECISIONS.yaml").write_text(REGISTER, encoding="utf-8")
    inst = tmp_path / "inst"
    (inst / "browser").mkdir(parents=True)
    (inst / "application.ini").write_text("[App]\nBuildID=20261003000001\n", encoding="utf-8")
    with zipfile.ZipFile(inst / "omni.ja", "w") as z:
        z.writestr("greprefs.js", "")
    with zipfile.ZipFile(inst / "browser" / "omni.ja", "w") as z:
        z.writestr("defaults/preferences/firefox.js", 'pref("browser.safebrowsing.malware.enabled", false, locked);\n'
                   'pref("extensions.blocklist.enabled", true);\npref("toolkit.telemetry.server", "", locked);\n'
                   'pref("toolkit.telemetry.enabled", false, locked);\npref("privacy.clearOnShutdown.cookies", false);\n')
    w = tmp_path / "tree"
    w.mkdir()
    task.start("t1", "firefox-upgrade", w, [], meta={"upstream": {"version": "157.0"}})
    items = [
        {"id": "CONS-001", "kind": "contradiction", "topic": "Safe Browsing: the docs say on, the build locks it off",
         "origin": {"source": "a review", "section": "2"},
         "statements": [{"where": "docs/WHY.md", "quote": "The local malware list stays on.",
                         "asserts": [{"pref": {"name": "browser.safebrowsing.malware.enabled", "value": True}}]},
                        {"tool": {"file": "working scripts/audit_privacy_claims.py", "list": "KEPT_DOORS",
                                  "names": ["browser.safebrowsing.malware.enabled"]}}]},
        {"id": "CONS-002", "kind": "lost-layer", "topic": "the policy file is not in the install", "origin": {"source": "a review"},
         "statements": [{"where": "docs/WHY.md", "quote": "The policy file re-locks it at runtime.",
                         "asserts": [{"installed_present": ["distribution/policies.json"]}]}]},
        {"id": "CONS-004", "kind": "undecided", "topic": "cookies kept on shutdown", "origin": {"source": "a review"},
         "statements": [{"where": "docs/WHY.md", "quote": "The local malware list stays on.",
                         "asserts": [{"pref": {"name": "privacy.clearOnShutdown.cookies", "value": False}}]}]},
        {"id": "CONS-009", "kind": "contradiction", "topic": "a sentence that was removed", "origin": {"source": "a review"},
         "statements": [{"where": "docs/WHY.md", "quote": "This sentence is no longer in the file.", "asserts": []}]}]
    return o, w, inst, items


def test_consistency_verdicts_and_the_automatic_tool_items(world):
    o, w, inst, items = world
    r = consistency.evaluate(o, w, inst, task_id="t1", items=items)
    v = {x["id"]: x for x in r["items"]}
    assert v["CONS-001"]["verdict"] == "CONTRADICTED" and v["CONS-001"]["register"] == ["D-157-00"]
    assert v["CONS-002"]["verdict"] == "LOST-LAYER"
    assert v["CONS-004"]["verdict"] == "UNDECIDED"
    assert v["CONS-009"]["verdict"] == "STALE"
    auto = [x for x in r["items"] if x["auto"]]
    assert [x["id"] for x in auto] == ["CONS-TOOL-working-scripts-audit-privacy-claims-TELEMETRY"]
    assert "toolkit.telemetry.enabled" in auto[0]["statements"][0]["quote"]
    assert "toolkit.telemetry.server" not in auto[0]["statements"][0]["quote"]  # '""' is the empty string the build ships
    assert not any(x["closed"] for x in r["items"])


def test_consistency_seed_file_is_append_only(tmp_path):
    o = tmp_path / "owner"
    items = consistency.load(o, write_seed=True)
    assert [i["id"] for i in items] == [s["id"] for s in consistency.SEED]
    p = consistency.path_for(o)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["items"][0]["topic"] = "the maintainer's own wording"
    data["items"] = data["items"][:2]
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    again = consistency.load(o, write_seed=True)
    assert again[0]["topic"] == "the maintainer's own wording" and len(again) == len(consistency.SEED)
    assert b"\r\n" not in p.read_bytes()


def _ctx(o, w):
    return producers.Context("t1", owner=o, workdir=w, steps=[], find_install=False)


def test_briefs_for_contradictions_lost_layers_and_undecided_values_are_valid(world):
    o, w, inst, items = world
    m = plan.Migration("t1", owner=o, install=inst, tree="T1")
    m.put("consistency", consistency.evaluate(o, w, inst, task_id="t1", items=items))
    got = producers.collect(_ctx(o, w), kinds={"consistency", "lost-layer"})
    assert not got["problems"]
    b = {x["id"]: x for x in got["briefs"]}
    assert set(b) >= {"B-CONSISTENCY-CONS-001", "B-LOSTLAYER-CONS-002", "B-CONSISTENCY-CONS-004"}
    assert got["briefs"][0]["kind"] == "lost-layer"                              # a lost defence first
    for x in b.values():
        schema.validate(x)
    assert b["B-LOSTLAYER-CONS-002"]["recommendation"]["option"] == "restore"
    restore = schema.option(b["B-LOSTLAYER-CONS-002"], "restore")
    assert restore["verify"] == [{"installed_present": ["distribution/policies.json"]}]
    assert b["B-CONSISTENCY-CONS-004"]["recommendation"]["option"] == "record-as-is"
    keep = schema.option(b["B-CONSISTENCY-CONS-001"], "build-wins")
    assert keep.get("verify") is None                     # no statement agrees with the build: nothing to attach
    assert schema.option(b["B-CONSISTENCY-CONS-001"], "docs-win")["verify"] == [
        {"pref": {"name": "browser.safebrowsing.malware.enabled", "value": True}}]


def test_only_the_maintainer_records_and_the_recorded_answer_closes_the_item(world, monkeypatch):
    o, w, inst, items = world
    m = plan.Migration("t1", owner=o, install=inst, tree="T1")
    m.put("consistency", consistency.evaluate(o, w, inst, task_id="t1", items=items))
    ctx = _ctx(o, w)
    b = producers.find(ctx, "B-CONSISTENCY-CONS-004")
    with pytest.raises(task.Refused, match="real terminal"):
        record.decide("t1", b["id"], "record-as-is", "keep cookies", ctx=ctx)
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    record.mark_shown("t1", b)
    r = record.decide("t1", b["id"], "record-as-is", "people stay logged in; keep it and check it", ctx=ctx)
    assert "PRODUCT-DECISIONS.yaml new entry D-157-01" in r["recorded"]
    reg = yaml.safe_load((o / "decisions" / "PRODUCT-DECISIONS.yaml").read_text(encoding="utf-8"))
    new = next(e for e in reg["decisions"] if e["id"] == "D-157-01")
    assert new["status"] == "enforced" and new["verify"] == [{"pref": {"name": "privacy.clearOnShutdown.cookies", "value": False}}]
    again = {x["id"]: x for x in consistency.evaluate(o, w, inst, task_id="t1", items=items)["items"]}
    assert again["CONS-004"]["closed"] and again["CONS-004"]["decided"]["option"] == "record-as-is"


def test_intake_briefs_cover_their_items_and_recommend_cutting_network_code(world):
    o, w, inst, _ = world
    m = plan.Migration("t1", owner=o, install=inst, tree="T1")
    items = [{"id": "INTAKE-module-1", "category": "module", "name": "browser/components/x/NetThing.sys.mjs",
              "where": "browser/components/x/NetThing.sys.mjs", "detail": "network APIs: fetch(", "network": True, "ai": False,
              "auto": None, "hosts": ["api.newhost-test.net"]},
             {"id": "INTAKE-pref-2", "category": "pref", "name": "layout.thing", "where": "all.js", "detail": "layout.thing = 2",
              "network": True, "ai": False, "auto": None, "hosts": []}]
    m.put("intake", {"old": "a" * 40, "new": "b" * 40, "new_version": "157.0", "items": items, "counts": {},
                     "clusters": [{"id": "INTAKE-module-browser-components-x", "brief": "B-INTAKE-module-browser-components-x",
                                   "category": "module", "component": "browser/components/x", "items": ["INTAKE-module-1"]}]})
    got = producers.collect(_ctx(o, w), kinds={"intake"})
    assert not got["problems"] and len(got["briefs"]) == 1
    b = schema.validate(got["briefs"][0])
    assert b["target"]["intake"] == ["INTAKE-module-1"] and b["recommendation"]["option"] == "cut"
    assert "api.newhost-test.net" in b["affected"]["items"][0]["excerpt"]
    assert producers.kind_of(b["id"]) == "intake"


def test_migration_briefs_sort_beside_every_other_kind():
    kinds = {"decision": (), "patch": (True, False, -3, "05.PREFS/x.patch"), "lost-layer": (False, "CONS-002"),
             "consistency": (True, "CONS-001"), "intake": (False, True, "INTAKE-x"), "deferred": ()}
    bs = [{"kind": k, "id": f"B-{i}", "affected": {"counts": {}}, "target": {"_sort": s}} for i, (k, s) in enumerate(kinds.items())]
    ranked = sorted(bs, key=producers._rank)
    assert [b["kind"] for b in ranked] == ["decision", "lost-layer", "consistency", "patch", "intake", "deferred"]
