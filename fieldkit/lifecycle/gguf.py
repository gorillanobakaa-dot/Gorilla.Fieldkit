"""Before a local model is loaded: is it dense or MoE, how many parameters work per token, how fast on THIS machine (P21).

    fieldkit models gguf FILE|URL [--file NAME] [--bandwidth GBPS] [--json]
    fieldkit models bandwidth [--measure]          the measured memory bandwidth (cached in state/models/)

WHY (2026-11-09): a dense 27B (15.5 GB, arch qwen35) ran at about 1 token/s on the L15 (i7-1255U, DDR4-3200); a MoE
with 3B active parameters of the same family should give 10-18 t/s. LM Studio does not say dense or MoE. Decoding
reads every ACTIVE weight once per token, so speed is about memory bandwidth / bytes read per token.

Reads only the GGUF header (spec: ggml docs/gguf.md): magic, version, tensor count, key/value metadata, tensor infos.
A local file is read from disk; an https URL (a Hugging Face `resolve/main/FILE` link, or a repo link that holds
one .gguf) with HTTP Range requests, as many bytes as the header needs and never the whole file; the byte count is
printed. Network only for a URL the caller typed (who learns what: that host sees the request and your address).
Nothing is downloaded to disk, nothing is loaded, no model is called. The speed is an estimate, never a promise.
"""
import json
import re
import struct
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

MAGIC = b"GGUF"
CHUNK = 1 << 20                  # remote reads come in 1 MiB aligned blocks
MAX_HEADER = 256 << 20           # fail closed: a header larger than this is not a model header
MAX_TENSORS = 2_000_000
MAX_KV = 200_000
MAX_STR = 64 << 20
KEEP_ARRAY = 64                  # arrays longer than this keep only their length (tokenizer tables)
FAST_TPS, USABLE_TPS = 15.0, 5.0

# ggml_type -> (elements per block, bytes per block)
GGML = {0: (1, 4, "F32"), 1: (1, 2, "F16"), 2: (32, 18, "Q4_0"), 3: (32, 20, "Q4_1"), 6: (32, 22, "Q5_0"),
        7: (32, 24, "Q5_1"), 8: (32, 34, "Q8_0"), 9: (32, 40, "Q8_1"), 10: (256, 84, "Q2_K"), 11: (256, 110, "Q3_K"),
        12: (256, 144, "Q4_K"), 13: (256, 176, "Q5_K"), 14: (256, 210, "Q6_K"), 15: (256, 292, "Q8_K"),
        16: (256, 66, "IQ2_XXS"), 17: (256, 74, "IQ2_XS"), 18: (256, 98, "IQ3_XXS"), 19: (256, 50, "IQ1_S"),
        20: (32, 18, "IQ4_NL"), 21: (256, 110, "IQ3_S"), 22: (256, 82, "IQ2_S"), 23: (256, 136, "IQ4_XS"),
        24: (1, 1, "I8"), 25: (1, 2, "I16"), 26: (1, 4, "I32"), 27: (1, 8, "I64"), 28: (1, 8, "F64"),
        29: (256, 56, "IQ1_M"), 30: (1, 2, "BF16"), 34: (256, 54, "TQ1_0"), 35: (256, 66, "TQ2_0"),
        39: (32, 17, "MXFP4")}
FILE_TYPE = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
             12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
             19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL",
             26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16"}
_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


class GgufError(ValueError):
    """Not a readable GGUF header; the message says why."""


# ---- sources: a sequential reader that counts the bytes it really fetched ------------------------------------------

class LocalSource:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.pos = 0
        self.fetched = 0
        self.size = Path(path).stat().st_size

    def read(self, n):
        if n < 0 or self.pos + n > MAX_HEADER:
            raise GgufError(f"header larger than {MAX_HEADER >> 20} MiB: not a model header")
        self.f.seek(self.pos)
        b = self.f.read(n)
        if len(b) != n:
            raise GgufError("file ends inside the header (truncated or not a GGUF)")
        self.pos += n
        self.fetched += n
        return b

    def skip(self, n):
        self.pos += n

    def close(self):
        self.f.close()


class RemoteSource:
    """HTTP Range reader. Only blocks the parser asks for are fetched; skipped arrays cost nothing."""

    def __init__(self, url, opener=None):
        if urllib.parse.urlparse(url).scheme != "https":
            raise GgufError("only https URLs are read")
        self.url = url
        self.opener = opener or urllib.request.urlopen
        self.pos = 0
        self.fetched = 0
        self.size = None
        self.blocks = {}

    def _block(self, i):
        if i not in self.blocks:
            lo = i * CHUNK
            req = urllib.request.Request(self.url, headers={"Range": f"bytes={lo}-{lo + CHUNK - 1}",
                                                            "User-Agent": "fieldkit-models-gguf"})
            try:
                with self.opener(req, timeout=60) as r:
                    status = getattr(r, "status", 200)
                    data = r.read(CHUNK + 1)
                    cr = r.headers.get("Content-Range") if hasattr(r, "headers") else None
            except OSError as e:
                raise GgufError(f"cannot read {self.url}: {e}") from e
            if status == 200 and len(data) > CHUNK:
                raise GgufError("the server ignored the Range request (it would send the whole file): refused")
            if cr and "/" in cr and cr.rsplit("/", 1)[1].isdigit():
                self.size = int(cr.rsplit("/", 1)[1])
            self.blocks[i] = data
            self.fetched += len(data)
        return self.blocks[i]

    def read(self, n):
        if n < 0 or self.pos + n > MAX_HEADER:
            raise GgufError(f"header larger than {MAX_HEADER >> 20} MiB: not a model header")
        out = bytearray()
        p, end = self.pos, self.pos + n
        while p < end:
            blk = self._block(p // CHUNK)
            off = p - (p // CHUNK) * CHUNK
            if off >= len(blk):
                raise GgufError("the file ends inside the header (truncated or not a GGUF)")
            take = blk[off:off + end - p]
            out += take
            p += len(take)
        self.pos = end
        return bytes(out)

    def skip(self, n):
        self.pos += n

    def close(self):
        pass


# ---- the parser ------------------------------------------------------------------------------------------------------

def _u32(s):
    return struct.unpack("<I", s.read(4))[0]


def _u64(s):
    return struct.unpack("<Q", s.read(8))[0]


def _str(s):
    n = _u64(s)
    if n > MAX_STR:
        raise GgufError("a string in the header is absurdly long: not a model header")
    return s.read(n).decode("utf-8", "replace")


def _value(s, t):
    if t in _SCALAR:
        fmt = _SCALAR[t]
        return struct.unpack(fmt, s.read(struct.calcsize(fmt)))[0]
    if t == 8:
        return _str(s)
    if t == 9:
        et, n = _u32(s), _u64(s)
        if n > MAX_HEADER:
            raise GgufError("an array in the header is absurdly long: not a model header")
        if et in _SCALAR and n > KEEP_ARRAY:
            s.skip(n * struct.calcsize(_SCALAR[et]))          # numeric tables: never fetched
            return {"array_len": n}
        vals = [_value(s, et) for _ in range(n)]
        return vals if n <= KEEP_ARRAY else {"array_len": n}
    raise GgufError(f"unknown value type {t}: not a GGUF this reader knows")


def parse(s):
    """-> {version, kv, tensors: [{name, dims, type, offset}], header_bytes}. Raises GgufError."""
    if s.read(4) != MAGIC:
        raise GgufError("not a GGUF file (magic is not GGUF)")
    version = _u32(s)
    if version not in (2, 3):
        raise GgufError(f"GGUF version {version} is not read (2 and 3 are)")
    n_t, n_kv = _u64(s), _u64(s)
    if n_t > MAX_TENSORS or n_kv > MAX_KV:
        raise GgufError("tensor or key count is absurd: not a model header")
    kv = {}
    for _ in range(n_kv):
        k = _str(s)
        kv[k] = _value(s, _u32(s))
    tensors = []
    for _ in range(n_t):
        name = _str(s)
        nd = _u32(s)
        if nd > 8:
            raise GgufError("a tensor with more than 8 dimensions: not a model header")
        dims = [_u64(s) for _ in range(nd)]
        ty, off = _u32(s), _u64(s)
        tensors.append({"name": name, "dims": dims, "type": ty, "offset": off})
    return {"version": version, "kv": kv, "tensors": tensors, "header_bytes": s.pos}


# ---- the rules (pure; each has a test that flips it) ----------------------------------------------------------------

def tensor_elems(t):
    n = 1
    for d in t["dims"]:
        n *= d
    return n


def tensor_bytes(t):
    if t["type"] not in GGML:
        raise GgufError(f"tensor {t['name']}: ggml type {t['type']} is not in the table")
    blck, size, _ = GGML[t["type"]]
    return -(-tensor_elems(t) // blck) * size


def is_expert_tensor(name):
    """ffn_{gate,up,down}_exps (and fused gate_up_exps) hold every expert in one tensor; _shexp is the shared expert."""
    return "_exps" in name


def is_mtp_tensor(name):
    return "nextn" in name or ".mtp" in name or name.startswith("mtp")


def _kv_int(kv, *keys):
    for k in keys:
        v = kv.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    return None


def classify(kv, tensors):
    """dense or moe. MoE: expert_count > 0, or an expert tensor present, or 'moe' in the architecture name."""
    arch = str(kv.get("general.architecture", "unknown"))
    n_exp = _kv_int(kv, f"{arch}.expert_count") or 0
    has_exps = any(is_expert_tensor(t["name"]) for t in tensors)
    moe = n_exp > 0 or "moe" in arch.lower() or has_exps
    used = _kv_int(kv, f"{arch}.expert_used_count") or 0
    if moe and not n_exp:     # last dimension of an expert tensor is the number of experts
        for t in tensors:
            if is_expert_tensor(t["name"]) and t["dims"]:
                n_exp = t["dims"][-1]
                break
    return {"arch": arch, "moe": moe, "experts": n_exp, "experts_used": used,
            "experts_shared": _kv_int(kv, f"{arch}.expert_shared_count")
            or (1 if any("_shexp" in t["name"] for t in tensors) else 0)}


def sizes(tensors, cls):
    """Total and active parameters and bytes. Active: non-expert tensors + experts_used/experts of the expert tensors.
    bytes_per_token leaves out token_embd when a separate output matrix exists (a lookup reads one row, not the table)."""
    names = {t["name"] for t in tensors}
    total_p = total_b = act_p = act_b = 0.0
    exp_b = 0
    for t in tensors:
        p, b = tensor_elems(t), tensor_bytes(t)
        total_p += p
        total_b += b
        if cls["moe"] and is_expert_tensor(t["name"]) and cls["experts"] and cls["experts_used"]:
            share = min(cls["experts_used"], cls["experts"]) / cls["experts"]
            act_p += p * share
            act_b += b * share
            exp_b += b
        else:
            act_p += p
            act_b += b
    per_tok = act_b
    if "output.weight" in names:
        for t in tensors:
            if t["name"] == "token_embd.weight":
                per_tok -= tensor_bytes(t)
    return {"total_params": int(total_p), "active_params": int(act_p), "tensor_bytes": int(total_b),
            "active_bytes": int(act_b), "bytes_per_token": int(per_tok), "expert_bytes": exp_b}


def verdict(tps):
    return "FAST" if tps >= FAST_TPS else "USABLE" if tps >= USABLE_TPS else "SLOW"


def estimate_tps(bytes_per_token, gbps):
    if not bytes_per_token or not gbps:
        return None
    return gbps * 1e9 / bytes_per_token


def analyze(hdr, gbps=None):
    kv, ts = hdr["kv"], hdr["tensors"]
    cls = classify(kv, ts)
    sz = sizes(ts, cls)
    arch = cls["arch"]
    ft = kv.get("general.file_type")
    types = sorted({GGML[t["type"]][2] for t in ts if t["type"] in GGML})
    res = dict(cls, **sz)
    res.update({
        "kind": "MoE" if cls["moe"] else "dense",
        "name": kv.get("general.name"), "layers": _kv_int(kv, f"{arch}.block_count"),
        "embedding": _kv_int(kv, f"{arch}.embedding_length"),
        "context": _kv_int(kv, f"{arch}.context_length"),
        "file_type": FILE_TYPE.get(ft, f"type {ft}") if isinstance(ft, int) else "unknown",
        "tensor_types": types, "tensors": len(ts),
        "bits_per_weight": round(sz["tensor_bytes"] * 8 / sz["total_params"], 2) if sz["total_params"] else None,
        "heads_kv": _kv_int(kv, f"{arch}.attention.head_count_kv"), "heads": _kv_int(kv, f"{arch}.attention.head_count"),
        "key_length": _kv_int(kv, f"{arch}.attention.key_length"),
        "attention_interval": _kv_int(kv, f"{arch}.full_attention_interval"),
        "mtp_tensors": sum(1 for t in ts if is_mtp_tensor(t["name"])),
        "vision_file": arch == "clip" or any(k.startswith("clip.vision") for k in kv),
        "split": "split.count" in kv and (_kv_int(kv, "split.count") or 1) > 1,
        "header_bytes": hdr.get("header_bytes"),
    })
    if gbps:
        tps = estimate_tps(sz["bytes_per_token"], gbps)
        res.update({"bandwidth_gbps": round(gbps, 1), "tokens_per_s": round(tps, 1) if tps else None,
                    "verdict": verdict(tps) if tps else "unknown"})
    return res


# ---- bandwidth: measured once, cached in state/ -----------------------------------------------------------------------

def cache_path():
    from .friction import state_dir
    return state_dir() / "models" / "bandwidth.json"


def measure_bandwidth(mib=128, rounds=4):
    """Copy benchmark, GB/s counting bytes read plus bytes written (STREAM convention). Best of `rounds`."""
    src, dst = bytearray(mib << 20), bytearray(mib << 20)
    src[::4096] = b"\x01" * len(src[::4096])
    best = 0.0
    for _ in range(rounds):
        t0 = time.perf_counter()
        dst[:] = src
        dt = time.perf_counter() - t0
        best = max(best, 2 * (mib << 20) / dt / 1e9)
    return best


def bandwidth(force=False, path=None):
    p = Path(path) if path else cache_path()
    if not force and p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("gbps"):
                return d["gbps"], "cached"
        except (OSError, ValueError):
            pass
    g = measure_bandwidth()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"gbps": round(g, 2), "method": "python memcpy, read+write", "t": int(time.time())}),
                     encoding="utf-8")
    except OSError:
        pass
    return g, "measured now"


# ---- input: file, URL, Hugging Face repo --------------------------------------------------------------------------------

def hf_files(repo_url, opener=None):
    """The .gguf files of a Hugging Face repo page URL (https://huggingface.co/OWNER/REPO)."""
    u = urllib.parse.urlparse(repo_url)
    parts = [p for p in u.path.split("/") if p]
    if u.netloc != "huggingface.co" or len(parts) < 2:
        raise GgufError("not a Hugging Face repo link")
    api = f"https://huggingface.co/api/models/{parts[0]}/{parts[1]}"
    try:
        with (opener or urllib.request.urlopen)(urllib.request.Request(api, headers={"User-Agent": "fieldkit-models-gguf"}),
                                                timeout=60) as r:
            info = json.loads(r.read(4 << 20))
    except (OSError, ValueError) as e:
        raise GgufError(f"cannot list {api}: {e}") from e
    return parts[0], parts[1], sorted(x["rfilename"] for x in info.get("siblings", []) if x["rfilename"].endswith(".gguf"))


def open_source(src, file=None, opener=None):
    """-> (source, label). A path, an https .gguf URL, or a Hugging Face repo link (+ file)."""
    if re.match(r"https?://", src):
        if src.rstrip("/").endswith(".gguf"):
            return RemoteSource(src, opener), src
        owner, repo, files = hf_files(src, opener)
        pick = [f for f in files if f == file] if file else [f for f in files if "mmproj" not in f.lower()]
        if len(pick) != 1:
            raise GgufError(f"{len(files)} .gguf file(s) in {owner}/{repo}; name one with --file:\n  " + "\n  ".join(files))
        url = f"https://huggingface.co/{owner}/{repo}/resolve/main/{urllib.parse.quote(pick[0])}"
        return RemoteSource(url, opener), url
    p = Path(src)
    if not p.is_file():
        raise GgufError(f"{src}: no such file")
    return LocalSource(p), str(p)


def _g(n):
    return f"{n / 1e9:.1f} B" if n >= 1e8 else f"{n / 1e6:.0f} M"


def render(r, label):
    out = [f"{label}", f"  architecture   {r['arch']}  ->  {r['kind']}" + (f" ({r['experts']} experts, {r['experts_used']} routed"
           + (f" + {r['experts_shared']} shared" if r["experts_shared"] else "") + " per token)" if r["moe"] else ""),
           f"  layers         {r['layers']}   embedding {r['embedding']}   context {r['context']}",
           f"  file type      {r['file_type']} ({r['bits_per_weight']} bits/weight; {', '.join(r['tensor_types'])})",
           f"  parameters     {_g(r['total_params'])} total, {_g(r['active_params'])} active per token",
           f"  read per token {r['bytes_per_token'] / 1e9:.2f} GB   (file tensors {r['tensor_bytes'] / 1e9:.2f} GB)"]
    if r["mtp_tensors"]:
        out.append(f"  MTP/draft      {r['mtp_tensors']} next-token-prediction tensors present")
    if r["vision_file"]:
        out.append("  vision         this is a vision projector (mmproj) file: it is loaded beside a text model")
    elif r["arch"] != "clip":
        out.append("  vision         a vision model needs its separate mmproj .gguf from the same repo")
    if r["split"]:
        out.append("  WARNING        split GGUF: this header lists only its own shard; totals here are partial")
    out.append(f"  header read    {r['header_read']:,} bytes over {r['source']}")
    if r.get("tokens_per_s") is not None:
        out.append(f"  speed          ESTIMATE {r['tokens_per_s']} tokens/s on this machine "
                   f"({r['bandwidth_gbps']} GB/s memory, {r['bandwidth_note']}); an estimate, not a promise")
        out.append(f"VERDICT: {r['verdict']} ({r['kind']}, {_g(r['active_params'])} active, ~{r['tokens_per_s']} t/s; "
                   f"FAST >= {FAST_TPS:g}, USABLE >= {USABLE_TPS:g}, else SLOW)")
    return out


def run(src, file=None, gbps=None, as_json=False, opener=None, out=print):
    try:
        s, label = open_source(src, file, opener)
        try:
            hdr = parse(s)
        finally:
            s.close()
        note = "given"
        if not gbps:
            gbps, note = bandwidth()
        r = analyze(hdr, gbps)
    except GgufError as e:
        out(f"ERROR  {e}")
        return 1
    r["source"] = "disk" if isinstance(s, LocalSource) else "HTTP range"
    r["header_read"] = s.fetched
    r["bandwidth_note"] = note
    if as_json:
        out(json.dumps(r, indent=1))
    else:
        for ln in render(r, label):
            out(ln)
        out("NEXT: " + ("load it" if r.get("verdict") in ("FAST", "USABLE") else
                        "prefer a MoE with few active parameters or a smaller quant; a dense model this size will crawl here"))
    return 0


def main(argv=None):
    argv = list(argv or [])
    if argv[:1] == ["bandwidth"]:
        g, note = bandwidth(force="--measure" in argv)
        print(f"memory bandwidth {g:.1f} GB/s ({note}; copy benchmark, read+write bytes)\n"
              "NEXT: fieldkit models gguf FILE|URL uses this number")
        return 0
    if argv[:1] != ["gguf"] or len(argv) < 2:
        return None
    if "--help" in argv or "-h" in argv:
        print("usage: fieldkit models gguf FILE|URL [--file NAME] [--bandwidth GBPS] [--json]\n"
              "  FILE|URL   a .gguf file, an https .gguf URL, or a Hugging Face repo link (header bytes only)\n"
              "  --file     which .gguf of a repo\n  --bandwidth  memory GB/s (default: measured once, cached)\n"
              "NEXT: read the VERDICT line before downloading or loading the model")
        return 0
    rest, file, gbps, as_json, src = argv[1:], None, None, False, None
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--json":
            as_json = True
        elif a == "--file" and i + 1 < len(rest):
            file, i = rest[i + 1], i + 1
        elif a == "--bandwidth" and i + 1 < len(rest):
            try:
                gbps, i = float(rest[i + 1]), i + 1
            except ValueError:
                print("ERROR  --bandwidth needs a number (GB/s)")
                return 2
        elif a.startswith("--"):
            print(f"ERROR  unknown option {a}")
            return 2
        else:
            src = a
        i += 1
    if not src:
        return None
    return run(src, file, gbps, as_json)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
