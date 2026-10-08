"""leakgate-proposal: reviewers' rows merged into proposed dispositions the owner can read and approve at a real
terminal. A model may PROPOSE, never approve: approval is null on every entry, a row bringing its own approval is
refused, the file lands only where `leakgate-dispositions` reads proposals, and nothing is ever overwritten."""
import json

import pytest

from fieldkit import cli
from fieldkit.buildh import task
from fieldkit.leakgate import dispositions as ld

SOURCE = [{"key": "browser/components/asrouter/modules/CFRMessageProvider.sys.mjs", "what": "CFR messages",
           "reachable": "no", "disposition": "dead-caller-cut", "reason": "never loaded. Pref lock, not a source cut",
           "evidence": ["<install>/browser/omni.ja:defaults/preferences/firefox.js:758"]}]
HOSTS = [{"host": "merino.services.mozilla.com", "set": "vendor", "what": "Merino", "reachable": "no",
          "disposition": "dead-caller-cut", "reason": "callers off", "evidence": ["x.sys.mjs:644"]},
         {"host": "new.example", "set": "new-since-N-1", "what": "a new host", "reachable": "user click only",
          "disposition": "user-initiated", "reason": "a link", "evidence": ["y.js:1"]}]
CRATES = [{"key": "crate:wgpu-sync", "what": "crate", "reachable": "no", "disposition": "not-a-sender", "reason": "no net",
           "evidence": ["Cargo.lock"]},
          {"key": "pe:xul.dll", "what": "imports", "reachable": "no", "disposition": "OWNER-DECISION", "reason": "ws2_32",
           "evidence": ["xul.dll imports"]}]
OVERRIDE = [{"host": "merino.services.mozilla.com", "disposition": "OPEN", "reachable": "YES after one about:config change",
             "reason": "NEEDS FIX: five fetches have no lock", "evidence": ["TemporaryMerinoClientShim.sys.mjs:644"]}]


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "leakgate" / "t1"
    d = root / "20261004-165832"
    d.mkdir(parents=True)
    (d / "source-inventory.json").write_text(json.dumps({"inventory": {SOURCE[0]["key"]: {
        "component": "Messaging/ASRouter", "apis": ["remote asset URL x.cdn.mozilla.net"]}}}), encoding="utf-8")
    (d / "test-results.json").write_text(json.dumps({"BUILD": "20261004224302"}), encoding="utf-8")
    files = {}
    for name, rows in (("source", SOURCE), ("hosts", HOSTS), ("crates", CRATES), ("override", OVERRIDE)):
        files[name] = tmp_path / f"{name}.result.json"
        files[name].write_text(json.dumps(rows), encoding="utf-8")
    return d, files


def test_the_rows_become_unapproved_proposals_beside_the_run_with_the_review(run):
    d, f = run
    r = ld.propose("t1", d, [f["source"], f["hosts"], f["crates"]], [f["override"]], label="build26")
    assert r["json"] == str(d.parent / "proposed-dispositions-build26.json") and r["entries"] == 5
    data = json.loads(open(r["json"], encoding="utf-8").read())
    assert all(e["approval"] is None for e in data.values())
    m = data["host:merino.services.mozilla.com"]
    assert m["disposition"] == "OPEN" and m["proposal"]["needs_fix"] and m["component"] == "Embedded vendor host (omni.ja)"
    assert m["evidence"] == "NEEDS FIX: five fetches have no lock | TemporaryMerinoClientShim.sys.mjs:644"
    s = data[SOURCE[0]["key"]]
    assert s["component"] == "Messaging/ASRouter" and s["proposal"]["build"] == "20261004224302" and s["proposal"]["run"] == d.name
    assert data["host:new.example"]["component"] == "Embedded host new since release N-1"
    assert r["open"] == ["host:merino.services.mozilla.com"] and r["counts"]["binary"] == {"OWNER-DECISION": 1}
    review = open(r["review"], encoding="utf-8").read()
    assert "**Nothing here is approved.**" in review and "## 1. NEEDS FIX (1)" in review and "Pref lock" in review
    assert "## 2. Source files (SOURCE_POLICY) - 1" in review and "leakgate-dispositions t1" in review
    with pytest.raises(ld.Invalid, match="never overwritten"):                    # the owner may have approved from it
        ld.propose("t1", d, [f["source"]], label="build26")


def test_the_blanket_approval_still_never_takes_what_needs_a_fix(run, tmp_path, monkeypatch):
    d, f = run
    r = ld.propose("t1", d, [f["source"], f["hosts"], f["crates"]], [f["override"]], label="b")
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    got = ld.approve_from(tmp_path / "dispositions.json", r["json"], say=lambda m: None)
    assert "host:merino.services.mozilla.com" not in got["approved"] and "pe:xul.dll" not in got["approved"]
    assert len(got["approved"]) == 3


@pytest.mark.parametrize("bad, why", [
    ({"disposition": "fine"}, "unknown disposition"),
    ({"evidence": []}, "no evidence"),
    ({"reason": ""}, "no reason"),
    ({"approval": {"by": "model"}}, "carries an approval"),
])
def test_a_bad_row_is_refused_and_nothing_is_written(run, tmp_path, bad, why):
    d, f = run
    f["crates"].write_text(json.dumps([dict(CRATES[0], **bad)]), encoding="utf-8")
    with pytest.raises(ld.Invalid, match=why):
        ld.propose("t1", d, [f["crates"]], label="x")
    assert not list(d.parent.glob("proposed-dispositions-*.json")) and not list(d.parent.glob("REVIEW-*"))


def test_two_reviewers_on_one_item_an_override_for_nothing_and_an_unknown_source_file_are_refused(run, tmp_path):
    d, f = run
    with pytest.raises(ld.Invalid, match="written twice"):
        ld.propose("t1", d, [f["hosts"], f["hosts"]], label="x")
    with pytest.raises(ld.Invalid, match="an override for an item no result file has"):
        ld.propose("t1", d, [f["crates"]], [f["override"]], label="x")
    f["source"].write_text(json.dumps([dict(SOURCE[0], key="no/such/File.sys.mjs")]), encoding="utf-8")
    with pytest.raises(ld.Invalid, match="not in the run's source inventory"):
        ld.propose("t1", d, [f["source"]], label="x")
    with pytest.raises(ld.Invalid, match="label"):
        ld.propose("t1", d, [f["crates"]], label="../escape")


def test_the_command_writes_the_proposal_and_says_nothing_is_approved(run, monkeypatch, capsys):
    d, f = run
    from fieldkit.buildh import buildrun, install
    monkeypatch.setattr(task, "STATE", d.parent.parent.parent)
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": "W"})
    monkeypatch.setattr(buildrun, "_owner_root", lambda t: None)
    monkeypatch.setattr(install, "find_install", lambda: None)
    assert cli.main(["build-harness", "leakgate-proposal", "t1", str(f["crates"]), "label=cli"]) == 0
    out = capsys.readouterr().out
    assert "NOTHING IS APPROVED" in out and (d.parent / "proposed-dispositions-cli.json").is_file()
    assert cli.main(["build-harness", "leakgate-proposal", "t1", str(f["crates"]), "label=cli"]) == 2   # exists: refused
    assert cli.main(["build-harness", "leakgate-proposal", "t1"]) == 2                                  # no result files
