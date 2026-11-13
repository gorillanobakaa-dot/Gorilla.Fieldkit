"""`fieldkit modelcatalog verify` (fieldkit/modelcatalog) and the model-backend triage signatures: each check can fail."""
import json

import pytest

from fieldkit import modelcatalog as mc
from fieldkit.build import triage

PROV = mc.provider("antigravity")
REAL_IDS = ["gemini-4-hydrogen-medium", "gemini-4-argon-high", "gemini-pro-orchestrator-g1-pro", "claude-sonnet-5-5-thinking"]


def flow(body="", resp=None, ua="antigravity/hub/2.31.2 (aidev_client)", path="/v1internal:fetchAvailableModels"):
    return {"method": "POST", "host": "daily-cloudcode-pa.googleapis.com", "path": path, "req_len": len(body),
            "req_headers": {"User-Agent": ua}, "req_body": body, "status": 200,
            "resp_body": json.dumps({"models": {i: {"displayName": i} for i in resp or []}})}


REF = [flow('{"project":"aicode-consumers"}', REAL_IDS)]


def checks(res):
    return {f["check"] for f in res["findings"]}


def test_matching_client_passes():
    cl = [flow('{"project":"aicode-consumers"}', REAL_IDS)]
    assert mc.verify("antigravity", REF, cl, catalogue=REAL_IDS, probe=[{"model": "x", "status": 200, "body": "ok"}])["findings"] == []


def test_empty_body_request_fails():
    assert "empty-body" in checks(mc.verify("antigravity", REF, [flow("", REAL_IDS[:1])]))


def test_different_body_fails():
    assert "empty-body" in checks(mc.verify("antigravity", REF, [flow('{"metadata":{}}', REAL_IDS)]))


def test_client_seeing_fewer_models_fails():
    assert "client-sees-fewer" in checks(mc.verify("antigravity", REF, [flow('{"project":"aicode-consumers"}', REAL_IDS[2:])]))


def test_catalogue_entry_not_reported_fails():
    res = mc.verify("antigravity", REF, catalogue=REAL_IDS + ["orchestrator-argon"])
    assert checks(res) == {"catalogue-not-reported"} and "orchestrator-argon" in res["findings"][0]["detail"]


def test_ua_version_unlike_the_app_fails():
    assert "hardcoded-ua" in checks(mc.verify("antigravity", REF, [flow('{"project":"aicode-consumers"}', REAL_IDS, ua="antigravity/hub/9.9.9")]))


def test_probe_404_fails_and_not_found_text_fails():
    res = mc.verify("antigravity", REF, probe=[{"model": "m1", "status": 404, "body": ""},
                                               {"model": "m2", "status": 400, "body": "Requested entity was not found."}])
    assert [f["check"] for f in res["findings"]] == ["probe-404", "probe-404"]


def test_missing_reference_call_is_a_finding():
    assert "no-reference" in checks(mc.verify("antigravity", [flow(path="/other")]))


def test_source_scan_finds_invented_id_and_hardcoded_ua_but_allows_explicit_opt_in(tmp_path):
    d = tmp_path / "internal" / "auth"
    d.mkdir(parents=True)
    (d / "antigravity_models.go").write_text(
        'var fake = "orchestrator-argon"\n'
        'const ua = "antigravity/hub/2.31.2 (aidev_client)"\n'
        'var real = "gemini-4-hydrogen-medium"\n'
        'var extra = os.Getenv("ANTIGRAVITY_EXTRA_MODELS") // "gemini-9-made-up"\n')
    (d / "antigravity_models_test.go").write_text('var t = "gemini-3-fixture"\n')
    (d / "other.go").write_text('var o = "gemini-1-elsewhere"\n')
    res = mc.verify("antigravity", REF, src=tmp_path)
    got = {(f["check"], f["detail"].split()[0]) for f in res["findings"]}
    assert got == {("invented-id", "antigravity_models.go:1"), ("hardcoded-ua", "antigravity_models.go:2")}


def test_cli_exit_codes_and_no_secret_output(tmp_path, capsys):
    ref = tmp_path / "ref.jsonl"
    ref.write_text("\n".join(json.dumps(f) for f in REF))
    cat = tmp_path / "cat.txt"
    cat.write_text("gemini-4-hydrogen-medium\norchestrator-argon\n")
    assert mc.main(["verify", "--reference", str(ref), "--catalogue", str(cat)]) == 3
    assert "orchestrator-argon" in capsys.readouterr().out
    cat.write_text("gemini-4-hydrogen-medium\n")
    assert mc.main(["verify", "--reference", str(ref), "--catalogue", str(cat)]) == 0
    assert mc.main(["verify"]) == 2


@pytest.mark.parametrize("sid", ["antigravity-entity-not-found", "catalogue-fake-entries"])
def test_model_backend_signature_matches_its_sample(sid):
    sig = next(s for s in triage.load_signatures("model-backend") if s["id"] == sid)
    r = triage.triage_text(sig["sample"] + "\n", "model-backend")
    assert r["verdict"] == "known" and sid in [m["id"] for m in r["matches"]]
    assert "appcapture" in sig["fix"] or "modelcatalog" in sig["fix"]


def test_triage_auto_search_finds_the_404():
    r = triage.triage_text("Requested entity was not found. (HTTP 404)\n", "auto")
    assert r["verdict"] == "known"
