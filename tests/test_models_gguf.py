"""P21 fieldkit models gguf: each rule on a tiny synthetic GGUF, and the case that flips it. No network, no download."""
import io
import json
import struct

import pytest

from fieldkit.lifecycle import gguf as g


def s(x):
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def kv_u32(k, v):
    return s(k) + struct.pack("<II", 4, v)


def kv_str(k, v):
    return s(k) + struct.pack("<I", 8) + s(v)


def kv_strarray(k, items):
    return s(k) + struct.pack("<IIQ", 9, 8, len(items)) + b"".join(s(i) for i in items)


def kv_f32array(k, n):
    return s(k) + struct.pack("<IIQ", 9, 6, n) + b"\0" * (4 * n)


def tensor(name, dims, ty=0):
    return s(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims) + struct.pack("<IQ", ty, 0)


def build(arch, kvs, tensors, version=3):
    body = kv_str("general.architecture", arch) + b"".join(kvs)
    return b"GGUF" + struct.pack("<IQQ", version, len(tensors), 1 + len(kvs)) + body + b"".join(tensors)


def dense_file():
    return build("qwen35", [kv_u32("qwen35.block_count", 2), kv_u32("general.file_type", 30),
                            kv_u32("qwen35.context_length", 4096)],
                 [tensor("token_embd.weight", [64, 100], 0), tensor("output.weight", [64, 100], 0),
                  tensor("blk.0.ffn_up.weight", [64, 256], 0)])


def moe_file(used=2, n=8, arch="qwen35moe", with_count=True):
    kvs = [kv_u32(f"{arch}.block_count", 2)]
    if with_count:
        kvs += [kv_u32(f"{arch}.expert_count", n), kv_u32(f"{arch}.expert_used_count", used)]
    return build(arch, kvs, [tensor("token_embd.weight", [64, 100]), tensor("output.weight", [64, 100]),
                             tensor("blk.0.attn_q.weight", [64, 64]),
                             tensor("blk.0.ffn_up_exps.weight", [64, 32, n]),
                             tensor("blk.0.ffn_up_shexp.weight", [64, 32])])


def hdr(data):
    return g.parse(g.LocalSource(write(data)))


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "_TMP", tmp_path, raising=False)
    global _DIR
    _DIR = tmp_path


def write(data, name="m.gguf"):
    p = _DIR / name
    p.write_bytes(data)
    return p


def test_dense_is_dense_and_all_params_are_active():
    r = g.analyze(hdr(dense_file()))
    assert r["kind"] == "dense" and r["experts"] == 0
    assert r["active_params"] == r["total_params"] == 64 * 100 * 2 + 64 * 256
    assert r["file_type"] == "IQ4_XS"


def test_moe_by_expert_count_and_active_share():
    r = g.analyze(hdr(moe_file(used=2, n=8)))
    assert r["kind"] == "MoE" and r["experts"] == 8 and r["experts_used"] == 2
    exp = 64 * 32 * 8
    assert r["total_params"] - r["active_params"] == exp - exp // 4        # 6 of 8 experts idle
    assert r["active_params"] < r["total_params"]


def test_moe_by_architecture_name_alone():
    r = g.analyze(hdr(moe_file(with_count=False, n=4)))
    assert r["kind"] == "MoE" and r["experts"] == 4          # count read from the expert tensor's last dimension


def test_moe_by_tensor_alone_when_arch_name_has_no_moe():
    r = g.analyze(hdr(moe_file(arch="qwen35", n=4, used=1)))
    assert r["kind"] == "MoE"


def test_embedding_lookup_is_not_read_per_token_when_output_matrix_exists():
    r = g.analyze(hdr(moe_file()))
    assert r["active_bytes"] - r["bytes_per_token"] == 64 * 100 * 4


def test_tied_embedding_stays_in_bytes_per_token():
    d = build("llama", [], [tensor("token_embd.weight", [64, 100])])
    r = g.analyze(hdr(d))
    assert r["bytes_per_token"] == r["active_bytes"] == 64 * 100 * 4


def test_quant_bytes_use_block_sizes():
    t = {"name": "x", "dims": [256, 2], "type": 23}              # IQ4_XS: 136 bytes per 256
    assert g.tensor_bytes(t) == 2 * 136
    assert g.tensor_bytes({"name": "x", "dims": [32], "type": 8}) == 34


def test_unknown_tensor_type_fails_closed():
    with pytest.raises(g.GgufError):
        g.tensor_bytes({"name": "x", "dims": [1], "type": 9999})


@pytest.mark.parametrize("tps,v", [(15.0, "FAST"), (14.9, "USABLE"), (5.0, "USABLE"), (4.9, "SLOW"), (1.0, "SLOW")])
def test_verdict_thresholds(tps, v):
    assert g.verdict(tps) == v


def test_speed_is_bandwidth_over_bytes_per_token():
    r = g.analyze(hdr(dense_file()), gbps=1.0)
    assert r["tokens_per_s"] == round(1e9 / r["bytes_per_token"], 1)
    big = g.analyze(hdr(dense_file()), gbps=1e6)
    assert big["verdict"] == "FAST"


def test_2026_11_09_pair_shapes():
    """The real pair, by their published shape: a dense 27B IQ4_XS and a 35B MoE with 8 of 256 experts."""
    bpw = 4.25
    dense = 27e9 * bpw / 8
    moe_active = 3e9 * bpw / 8
    assert g.verdict(g.estimate_tps(dense, 15)) == "SLOW"          # about 1 t/s measured
    assert g.verdict(g.estimate_tps(moe_active, 15)) == "USABLE"


def test_mtp_and_vision_markers():
    d = build("qwen35moe", [kv_u32("qwen35moe.expert_count", 4), kv_u32("qwen35moe.expert_used_count", 1)],
              [tensor("blk.2.nextn.eh_proj.weight", [8, 8])])
    r = g.analyze(hdr(d))
    assert r["mtp_tensors"] == 1 and not r["vision_file"]
    c = build("clip", [kv_u32("clip.vision.block_count", 2)], [tensor("v.blk.0.w", [8, 8])])
    assert g.analyze(hdr(c))["vision_file"] is True


def test_long_arrays_keep_only_length_and_cost_no_bytes():
    d = build("llama", [kv_f32array("tokenizer.ggml.scores", 5000), kv_strarray("tokenizer.ggml.tokens", ["a", "b"])],
              [tensor("token_embd.weight", [8, 8])])
    src = g.LocalSource(write(d))
    h = g.parse(src)
    assert h["kv"]["tokenizer.ggml.scores"] == {"array_len": 5000}
    assert h["kv"]["tokenizer.ggml.tokens"] == ["a", "b"]
    assert src.fetched < len(d) - 5000 * 4 + 8


def test_bad_inputs_are_refused():
    for data, word in ((b"NOPE" + b"\0" * 40, "magic"), (dense_file()[:30], "truncated"),
                       (b"GGUF" + struct.pack("<I", 9) + b"\0" * 30, "version")):
        with pytest.raises(g.GgufError, match=word):
            g.parse(g.LocalSource(write(data)))
    absurd = b"GGUF" + struct.pack("<IQQ", 3, 10 ** 9, 0)
    with pytest.raises(g.GgufError, match="absurd"):
        g.parse(g.LocalSource(write(absurd)))


class FakeResp:
    def __init__(self, data, total, status=206):
        self.data, self.status = data, status
        self.headers = {"Content-Range": f"bytes 0-{len(data) - 1}/{total}"}

    def read(self, n=-1):
        return self.data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_opener(blob, ignore_range=False):
    calls = []

    def op(req, timeout=0):
        rng = req.get_header("Range")
        calls.append(rng)
        if ignore_range:
            return FakeResp(blob, len(blob), 200)
        lo, hi = map(int, rng.split("=")[1].split("-"))
        return FakeResp(blob[lo:hi + 1], len(blob))
    op.calls = calls
    return op


def test_remote_reads_only_header_blocks_by_range(monkeypatch):
    monkeypatch.setattr(g, "CHUNK", 64)
    d = moe_file()
    big = d + b"\0" * 5000                        # tensor data after the header must not be fetched
    op = fake_opener(big)
    src = g.RemoteSource("https://huggingface.co/o/r/resolve/main/m.gguf", op)
    h = g.parse(src)
    assert g.analyze(h)["kind"] == "MoE"
    assert src.fetched < len(big) and src.fetched <= len(d) + 64 and src.size == len(big)
    assert all(c.startswith("bytes=") for c in op.calls)


def test_server_that_ignores_range_is_refused(monkeypatch):
    monkeypatch.setattr(g, "CHUNK", 64)
    src = g.RemoteSource("https://h/x.gguf", fake_opener(dense_file() + b"\0" * 500, ignore_range=True))
    with pytest.raises(g.GgufError, match="Range"):
        g.parse(src)


def test_http_url_is_refused():
    with pytest.raises(g.GgufError, match="https"):
        g.RemoteSource("http://h/x.gguf")


def test_run_prints_verdict_and_bytes(tmp_path):
    p = write(moe_file())
    out = []
    assert g.run(str(p), gbps=0.0001, out=out.append) == 0
    text = "\n".join(out)
    assert "MoE" in text and "VERDICT: SLOW" in text and "header read" in text and "NEXT:" in text
    js = []
    g.run(str(p), gbps=1000.0, as_json=True, out=js.append)
    d = json.loads(js[0])
    assert d["kind"] == "MoE" and d["verdict"] == "FAST" and d["header_read"] > 0


def test_run_missing_file_is_an_error():
    out = []
    assert g.run("/nonexistent/x.gguf", gbps=1, out=out.append) == 1


def test_bandwidth_is_cached(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(g, "measure_bandwidth", lambda *a, **k: calls.append(1) or 12.5)
    p = tmp_path / "bw.json"
    assert g.bandwidth(path=p) == (12.5, "measured now")
    assert g.bandwidth(path=p) == (12.5, "cached") and len(calls) == 1
    g.bandwidth(force=True, path=p)
    assert len(calls) == 2


def test_models_main_routes_gguf(tmp_path, capsys):
    from fieldkit.lifecycle import models
    p = write(dense_file())
    assert models.main(["gguf", str(p), "--bandwidth", "20"]) == 0
    assert "VERDICT" in capsys.readouterr().out
    assert models.main(["gguf"]) == 2


@pytest.mark.skipif(not __import__("os").environ.get("FIELDKIT_NET_TESTS"), reason="network test: set FIELDKIT_NET_TESTS=1")
def test_real_repo_header_by_range():
    src = g.RemoteSource("https://huggingface.co/mlasli/Qwen3.8-27B-Heretic-Uncensored-IQ4_XS-GGUF/resolve/main/"
                         "qwen3.8-27b-heretic-uncensored-iq4_xs.gguf")
    assert g.analyze(g.parse(src))["kind"] == "dense" and src.fetched < 64 << 20
