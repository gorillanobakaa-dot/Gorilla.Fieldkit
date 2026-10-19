"""fieldkit telemetry (owner rule 2026-10-18: telemetry off and cut out, every project, every machine)."""
from fieldkit.core import telemetry as tm


def test_the_source_scan_finds_sdks_endpoints_and_collectors_and_honors_a_reasoned_mark(tmp_path):
    (tmp_path / "app.py").write_text("import sentry_sdk\nsentry_sdk.init(dsn='x')\n", encoding="utf-8")  # telemetry-ok: test fixture
    (tmp_path / "web.js").write_text("fetch('https://www.google-analytics.com/collect')\n", encoding="utf-8")  # telemetry-ok: test fixture
    (tmp_path / "refuse.js").write_text("const BLOCKED = 'sentry.io' // telemetry-ok: the blocklist that refuses it\n",
                                        encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("import posthog from 'posthog-js'\n", encoding="utf-8")  # telemetry-ok: test fixture
    (tmp_path / "clean.rs").write_text("fn main() { println!(\"hi\"); }\n", encoding="utf-8")
    r = tm.scan(tmp_path)
    assert sorted((f["file"], f["kind"]) for f in r["findings"]) == [("app.py", "sdk"), ("app.py", "sdk"), ("web.js", "endpoint")]
    assert [m["file"] for m in r["marked"]] == ["refuse.js"]         # named, never silently passed


def test_the_machine_rows_need_every_opt_out_and_every_block():
    env = {k: v for k, v in tm.MACHINE_ENV.items()}
    rows = tm.machine_rows(env=env.get, paths=[])
    assert all(r["ok"] for r in rows if r["check"].startswith("opt-out variable"))
    env.pop("DOTNET_CLI_TELEMETRY_OPTOUT")
    rows = tm.machine_rows(env=env.get, paths=[])
    assert [r["check"] for r in rows if not r["ok"] and r["check"].startswith("opt-out")] == \
        ["opt-out variable DOTNET_CLI_TELEMETRY_OPTOUT=1"]


def test_the_fix_script_backs_up_never_recreates_a_key_and_checks_itself():
    s = tm.fix_script([], paths=[r"C:\VS\VC\Tools\MSVC\14.44\bin\Hostx64\x64\vctip.exe"])
    assert "New-Item -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\VisualStudio\\SQM' -Force" not in s   # wipes values
    assert "reg.exe export" in s and "New-NetFirewallRule" in s and "-Direction Outbound" in s
    assert "=== FINAL VERDICT: PASS" in s and "FAIL firewall" in s
    # 2026-10-18 first run: a missing parent key threw and one try skipped every change; now each step stands alone
    assert "function Ensure-Key" in s and s.count("Step '") >= 2 + len(tm.MACHINE_ENV) + 1


def test_vctip_is_found_by_the_toolset_layout(tmp_path):
    p = tmp_path / "BuildTools" / "VC" / "Tools" / "MSVC" / "14.44.1" / "bin" / "Hostx64" / "x64"
    p.mkdir(parents=True)
    (p / "vctip.exe").write_bytes(b"")
    assert tm.vctip_paths([str(tmp_path)]) == [str(p / "vctip.exe")]


def test_module_paths_count_and_data_and_generated_copies_do_not(tmp_path):
    # 2026-10-18 sweep: OpenCode linked go.opentelemetry.io through a dependency; gate evidence in state/ is data  telemetry-ok: test fixture
    (tmp_path / "go.mod").write_text("require go.opentelemetry.io/otel v1.35.0 // indirect\n", encoding="utf-8")  # telemetry-ok: test fixture
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "events.json").write_text('{"host": "incoming.telemetry.mozilla.org"}', encoding="utf-8")  # telemetry-ok: test fixture
    (tmp_path / "x.filled.json").write_text('{"q": "sentry.io"}', encoding="utf-8")  # telemetry-ok: test fixture
    r = tm.scan(tmp_path)
    assert [(f["file"], f["kind"]) for f in r["findings"]] == [("go.mod", "module")]


def test_a_built_program_is_scanned_for_the_module_paths_it_carries(tmp_path):
    exe = tmp_path / "app.exe"
    exe.write_bytes(b"\x00MZ...\x00go.opentelemetry.io/otel/trace\x00...go.opentelemetry.io/otel/metric\x00")  # telemetry-ok: test fixture
    r = tm.scan_artifact(exe)
    assert r["findings"] == [{"kind": "module", "match": "go.opentelemetry.io/", "count": 2}]  # telemetry-ok: test fixture
    clean = tmp_path / "clean.exe"
    clean.write_bytes(b"\x00MZ hello world\x00")
    assert tm.scan_artifact(clean)["findings"] == []


def test_go_sum_hashes_are_not_linkage_but_go_mod_requires_are(tmp_path):
    # 2026-10-19: go mod tidy keeps go.opentelemetry.io hashes for grpc's own tests; nothing is linked  telemetry-ok: names it to explain a rule
    (tmp_path / "go.sum").write_text("go.opentelemetry.io/otel v1.0.0 h1:abc=\n", encoding="utf-8")  # telemetry-ok: test fixture
    assert tm.scan(tmp_path)["findings"] == []
    (tmp_path / "go.mod").write_text("require go.opentelemetry.io/otel v1.0.0\n", encoding="utf-8")  # telemetry-ok: test fixture
    assert [f["file"] for f in tm.scan(tmp_path)["findings"]] == ["go.mod"]


def test_a_library_that_phones_home_by_default_is_flagged_until_the_file_turns_it_off(tmp_path):
    # 2026-10-19: OpenCode's pfind.py opened chromadb with its defaults (PostHog telemetry); no line said posthog  telemetry-ok: names it to explain a rule
    f = tmp_path / "brain.py"
    f.write_text("import chromadb\nc = chromadb.PersistentClient(path='x')\n", encoding="utf-8")
    got = tm.scan(tmp_path)["findings"]
    assert len(got) == 1 and got[0]["line"] == 1 and "by default" in got[0]["text"]
    f.write_text("import chromadb\nfrom chromadb.config import Settings\n"
                 "c = chromadb.PersistentClient(path='x', settings=Settings(anonymized_telemetry=False))\n", encoding="utf-8")
    assert tm.scan(tmp_path)["findings"] == []
