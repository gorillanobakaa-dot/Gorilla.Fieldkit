"""P22 fieldkit models tune: each rule on a made-up host and model, and the case that flips it. No lms, no network, no model."""
import json
import struct

import pytest

from fieldkit.lifecycle import tune as t

R = t.load_rules()


def host(**kw):
    h = {"os": "linux", "cpu": {"model": "x", "logical": 12, "physical": 10, "performance": 2, "efficiency": 8},
         "gpus": [{"name": "Iris Xe", "vendor": "intel", "vram_gb": 0.0, "free_gb": None, "shared": True}],
         "ram_gb": 64.0, "ram_free_gb": 50.0, "bandwidth_gbps": 21.9, "notes": []}
    h.update(kw)
    return h


def info(**kw):
    i = {"tensor_bytes": 20 * 2 ** 30, "layers": 40, "heads_kv": 4, "heads": 16, "embedding": 2048, "key_length": 128,
         "attention_interval": None, "context": 262144, "mtp_tensors": 0}
    i.update(kw)
    return i


def test_context_is_the_job_not_the_model_maximum():
    p = t.decide(host(), info())
    assert p["settings"]["context"] == 32768          # the model allows 262144


def test_context_never_above_a_small_model_maximum():
    assert t.decide(host(), info(context=8192))["settings"]["context"] == 8192


def test_context_shrinks_until_the_kv_cache_fits():
    p = t.decide(host(ram_free_gb=4.0), info(tensor_bytes=2 ** 30), wanted_ctx=262144)
    c = p["settings"]["context"]
    assert 4096 <= c < 262144 and c % 1024 == 0
    assert t.kv_bytes_per_token(info()) * c <= 4.0 * R["context"]["kv_budget_share"] * 2 ** 30


def test_wanted_context_is_honoured_when_it_fits():
    assert t.decide(host(), info(), wanted_ctx=16384)["settings"]["context"] == 16384


def test_hybrid_attention_pays_for_fewer_layers():
    assert t.kv_bytes_per_token(info(attention_interval=4)) == t.kv_bytes_per_token(info()) // 4


def test_threads_are_the_performance_cores():
    assert t.pick_threads(host(), R) == 2


def test_threads_without_core_types_are_all_cores():
    h = host(cpu={"model": "x", "logical": 8, "physical": 4, "performance": 4, "efficiency": 0})
    assert t.pick_threads(h, R) == 4


def test_integrated_gpu_is_not_offloaded_to():
    assert t.pick_gpu(host(), info(), 20.0, R)[0] == 0.0


def test_discrete_gpu_that_fits_takes_everything():
    g = {"name": "RTX", "vendor": "nvidia", "vram_gb": 24.0, "free_gb": 24.0, "shared": False}
    assert t.pick_gpu(host(gpus=[g]), info(), 10.0, R)[0] == 1.0


def test_discrete_gpu_that_does_not_fit_takes_a_share():
    g = {"name": "RTX", "vendor": "nvidia", "vram_gb": 9.0, "free_gb": 9.0, "shared": False}
    assert t.pick_gpu(host(gpus=[g]), info(), 16.0, R)[0] == 0.5


def test_tiny_vram_is_not_worth_it():
    g = {"name": "old", "vendor": "nvidia", "vram_gb": 1.0, "free_gb": 1.0, "shared": False}
    assert t.pick_gpu(host(gpus=[g]), info(), 4.0, R)[0] == 0.0


def test_kv_cache_is_quantized_only_when_ram_is_tight():
    assert t.pick_kv_type(host(ram_gb=64.0), 20.0, 4.0, R)[0] == "f16"
    assert t.pick_kv_type(host(ram_gb=32.0), 20.0, 4.0, R)[0] == "q8_0"


def test_mtp_only_when_the_file_has_it_and_ram_is_left():
    assert t.decide(host(), info(mtp_tensors=3))["settings"]["mtp"] is True
    assert t.decide(host(), info(mtp_tensors=0))["settings"]["mtp"] is False
    assert t.decide(host(ram_free_gb=21.0), info(mtp_tensors=3))["settings"]["mtp"] is False


def test_warning_when_the_weights_exceed_free_ram():
    assert t.decide(host(ram_free_gb=8.0), info())["warnings"]
    assert not t.decide(host(), info())["warnings"]


def test_flash_attention_follows_the_rule_data():
    assert t.decide(host(), info())["settings"]["flash_attention"] is True
    assert t.decide(host(), info(), rules=dict(R, flash_attention=False))["settings"]["flash_attention"] is False


# ---- host readers ---------------------------------------------------------------------------------------------------

def test_cpu_list():
    assert t.cpu_list("0-3,8,10-11") == 7 and t.cpu_list("") == 0


def test_parse_lscpu():
    d = t.parse_lscpu("CPU(s):  12\nModel name:  i7\nCore(s) per socket:  10\nSocket(s):  1\n")
    assert d == {"model": "i7", "logical": 12, "physical": 10}


def test_windows_efficiency_classes():
    rec = lambda c: struct.pack("<IIBB", 0, 12, 0, c) + b"\0\0"      # noqa: E731 - 12-byte records
    cls = t.parse_efficiency_classes(rec(1) * 2 + rec(0) * 8)
    assert cls == {1: 2, 0: 8}


def test_nvidia_smi_and_drm_and_windows_video():
    g = t.parse_nvidia_smi("NVIDIA RTX 4090, 24564, 23000\n")
    assert g[0]["vram_gb"] == 24.0 and not g[0]["shared"]
    d = t.parse_drm([("0x8086", "card0", None), ("0x10de", "card1", 8 * 2 ** 30)])
    assert d[0]["shared"] and not d[1]["shared"] and d[1]["vram_gb"] == 8.0
    w = t.parse_win_video('[{"Name":"Intel(R) Iris(R) Xe","VramBytes":0},{"Name":"NVIDIA GeForce","VramBytes":6442450944}]')
    assert w[0]["shared"] and w[1]["vram_gb"] == 6.0


# ---- lms: flags are read from --help, never guessed ----------------------------------------------------------------------

HELP = "Options:\n  --gpu <v>\n  --context-length <n>\n  --ttl <s>\n  -y, --yes\n"


def test_only_flags_the_help_prints_are_passed():
    s = t.decide(host(), info(mtp_tensors=2))["settings"]
    argv, skipped = t.build_load("m/k", s, HELP, R)
    assert argv[:2] == ["m/k", "-y"] and "--context-length" in argv and "--gpu" in argv and "--ttl" in argv
    assert "--cpu-threads" not in argv and "threads" in skipped and "mtp" in skipped


def test_a_flag_name_inside_another_flag_is_not_a_match():
    assert t.supported_flag("--gpu-offload x", ["--gpu"]) is None
    assert t.supported_flag("  --gpu <v>", ["--gpu"]) == "--gpu"


def fake_lms(tmp_path, loaded=True, preloaded=False):
    """ps lists the model once a load ran (state file), or from the start when preloaded."""
    p = tmp_path / "lms"
    st = tmp_path / "state"
    if preloaded:
        st.write_text("x")
    p.write_text(f"""#!/bin/sh
case "$1" in
 load) if [ "$2" = "--help" ]; then printf '{HELP.replace(chr(10), "\\n")}'; else echo loaded >> "{tmp_path}/loads"; {'touch "' + str(st) + '"' if loaded else ':'}; fi;;
 ps) if [ -e "{st}" ]; then echo "IDENTIFIER m/k"; else echo none; fi;;
esac
""")
    p.chmod(0o755)
    return str(p)


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="shell stub")
def test_apply_verifies_with_lms_ps(tmp_path):
    s = t.decide(host(), info())["settings"]
    ok, lines = t.apply("m/k", s, R, lms=fake_lms(tmp_path))
    assert ok and any("VERIFIED" in x for x in lines) and any("NOT PASSED" in x for x in lines)
    (tmp_path / "b").mkdir()
    ok, lines = t.apply("m/k", s, R, lms=fake_lms(tmp_path / "b", loaded=False))
    assert not ok and any("NOT VERIFIED" in x for x in lines)


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="shell stub")
def test_a_model_already_loaded_is_never_loaded_twice(tmp_path):
    s = t.decide(host(), info())["settings"]
    ok, lines = t.apply("m/k", s, R, lms=fake_lms(tmp_path, preloaded=True))
    assert ok and any("ALREADY LOADED" in x for x in lines) and not (tmp_path / "loads").exists()


def test_apply_without_lms_says_so(monkeypatch):
    monkeypatch.setattr(t, "find_lms", lambda: None)
    ok, lines = t.apply("m/k", {}, R)
    assert not ok and "lms" in lines[0]


# ---- runs --------------------------------------------------------------------------------------------------------------------

def test_runs_are_recorded_and_reported(tmp_path):
    p = tmp_path / "r.jsonl"
    t.record_run("m", 315.0, 9.6, 16000, p)
    t.record_run("m", 9.2, 7.7, None, p)
    rep = t.report_runs(t.read_runs(p))
    assert rep["m"]["runs"] == 2 and rep["m"]["first_word_s_best"] == 9.2 and rep["m"]["first_word_s_last"] == 9.2
    with pytest.raises(ValueError):
        t.record_run("m", -1, 1, None, p)


def test_resolve_model_needs_exactly_one_match(tmp_path):
    d = tmp_path / "pub" / "repo"
    d.mkdir(parents=True)
    (d / "Qwen-Q4.gguf").write_bytes(b"x")
    (d / "mmproj-Qwen.gguf").write_bytes(b"x")
    f, key = t.resolve_model("qwen", [tmp_path])
    assert f.name == "Qwen-Q4.gguf" and key == "pub/repo/Qwen-Q4"
    with pytest.raises(ValueError):
        t.resolve_model("nothing", [tmp_path])


def test_command_end_to_end_on_a_synthetic_gguf(tmp_path, capsys, monkeypatch):
    from tests.test_models_gguf import moe_file
    f = tmp_path / "m.gguf"
    f.write_bytes(moe_file())
    monkeypatch.setattr(t, "probe_host", lambda: host())
    assert t.main(["tune", str(f), "--json"]) == 0
    out = json.loads(capsys.readouterr().out.split("NEXT:")[0])
    assert out["settings"]["context"] == 32768 and out["settings"]["threads"] == 2 and out["applied"] is False


def test_a_gpu_that_shares_system_ram_is_not_offloaded_to_whatever_it_reports():
    g = {"name": "APU", "vendor": "amd", "vram_gb": 8.0, "free_gb": None, "shared": True}
    assert t.pick_gpu(host(gpus=[g]), info(), 4.0, R)[0] == 0.0
