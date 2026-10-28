"""Test parity: every machine runs the same pinned versions with the same capabilities, checked before a test.

    fieldkit parity manifest [--json] [--hash]           this machine: versions + sha256 of the tools that matter,
                                                          capabilities, OS/arch; JSON in a stable key order
    fieldkit parity compare [--peer NAME] [--with FILE] [--json]
                                                          this machine against each other device of the household (a
                                                          signed `manifest` request over the link) or a saved manifest:
                                                          SAME / PARTIAL / DRIFT, each difference named
    fieldkit parity align [--version vX] [--apply] [--rollback]
                                                          bring this machine to the pin FROM THE RELEASE ITSELF: the
                                                          asset and SHA256SUMS downloaded into a new folder, sha256 and
                                                          GitHub's digest checked, the old binary kept for rollback
    fieldkit parity preflight [--label L] [--kind K]      the run guard's parity check alone (exit 0 ok / 1 warn / 2 stop)
    fieldkit parity baseline [--set]                      the machine state results are compared under (Defender
                                                          real-time, the scratch exclusion); --set records today's
    fieldkit parity doctor                                 pin, binaries, gh, household, preflight wiring

Owner, 2026-10-26: "update the binaries on both computers ... run it from the same version ... make sure you have the
same capabilities ... in a deterministic way with deterministic checks to make sure that before you start a test, the
tests will not be impacted by different sessions that can skew the results." Measured the same day: Windows ran
v0.1.152, the VAIO v0.1.151, the release was v0.1.154, and a `go.exe` of another session pinned a core during a test.

The pin is releases/parity-pin.yaml, changed by a commit: nothing here updates in the background (updates are pulled,
verified and deployed on our schedule). Linux and macOS installs need root: align prints the exact line for the session
there, it never runs sudo. The manifest carries provider NAMES only, never a key.

The preflight check (fieldkit/core/preflight.py STEPS) on every guarded run: the pin, shared ground (another session's
process in this checkout, running the pinned binary, or pinning a core), models loaded that the run did not load. It
WARNs; kinds bench and measure (STRICT_KINDS) are REFUSED on any of it. It puts the manifest hash in the environment
(FIELDKIT_PARITY_MANIFEST), keeps the manifest under state/parity/manifests/, and testcost writes the hash into every
result row: every result says what it ran on.

Machine state that changes every timing (the command post, 2026-10-26: the owner switched Defender real-time protection
off because MsMpEng ate ~4 cores, and Windows switches it back on by itself): the manifest's machine.defender says
real-time on / off / null (unknown, or not Windows) and whether the scratch root is excluded, read-only, never cached.
`parity baseline --set` records the state results are compared under; a run whose state differs is NOT COMPARABLE: the
preflight says so (WARN, STOP for bench/measure) and testcost writes comparable: false into its result row.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from .core import settings

SCHEMA = 1
ENV = "FIELDKIT_PARITY_MANIFEST"
ENV_COMPARABLE = "FIELDKIT_PARITY_COMPARABLE"
ENV_DEFENDER = "FIELDKIT_PARITY_DEFENDER"
PIN_FILE = settings.ROOT / "releases" / "parity-pin.yaml"
STRICT_KINDS = ("bench", "measure")
DRIFT, INFO = "drift", "info"
#: processes that work in a checkout (another session's tests, builds, agents)
WORKERS = ("python", "pytest", "go", "node", "git", "cargo", "rustc", "gorilla-opencode", "claude", "gemini", "codex",
           "antigravity", "make", "msbuild", "cl", "link")
CPU_BUSY_PCT = 80.0             # one core is 100
#: the release asset for each system; linux picks the package its package manager installs
ASSETS = {"windows": "gorilla-opencode.exe", "deb": "gorilla-opencode_{v}_amd64.deb",
          "rpm": "gorilla-opencode-{v}-1.x86_64.rpm", "linux": "gorilla-opencode-v{v}-linux-amd64",
          "darwin": "gorilla-opencode-{v}-mac-10.13-x86_64.zip"}


def state_dir():
    return Path(os.environ.get("FIELDKIT_PARITY_STATE") or (settings.state_root() / "state" / "parity"))


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 99, "", str(e)


def canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(m):
    """The manifest's hash: everything but the hash itself and the time it was taken."""
    return hashlib.sha256(canon({k: v for k, v in m.items() if k not in ("hash", "taken")}).encode()).hexdigest()


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def norm_version(text):
    """'v0.1.154' / 'gorilla-size 0.3.0' / 'go version go1.24.2 windows/amd64' -> '0.1.154' / '0.3.0' / '1.24.2'."""
    m = re.search(r"(\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?)", text or "")
    return m.group(1) if m else None


def pin(path=None):
    """-> {tool: {version, repo}} from releases/parity-pin.yaml; {} when there is none."""
    import yaml
    p = Path(path or PIN_FILE)
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.is_file() else {}


# ---- the manifest ---------------------------------------------------------------------------------------------------
def system():
    s = platform.system().lower()
    return "darwin" if s == "darwin" else "windows" if s == "windows" else "linux"


def opencode_path():
    env = os.environ.get("FIELDKIT_GORILLA_OPENCODE")
    if env:
        return Path(env)
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        p = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Gorilla OpenCode" / "gorilla-opencode.exe"
        if p.is_file():
            return p
    w = shutil.which("gorilla-opencode")
    return Path(w) if w else None


def _size_path():
    try:
        from .desk import size
        return size.locate()
    except Exception:  # noqa: BLE001
        return None


def tool_paths():
    """-> {tool: (path or None, version args)}: the tools whose versions decide what a test ran on."""
    which = lambda n: Path(shutil.which(n)) if shutil.which(n) else None        # noqa: E731
    return {"gorilla-opencode": (opencode_path(), ["--version"]), "gorilla-size": (_size_path(), ["--version"]),
            "python": (Path(sys.executable), ["--version"]), "go": (which("go"), ["version"]),
            "git": (which("git"), ["--version"]), "gh": (which("gh"), ["--version"]),
            "node": (which("node"), ["--version"])}


def _cache():
    try:
        return json.loads((state_dir() / "cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def probe_tool(path, vargs, cache, run=_run):
    """-> {version, sha256, bytes} for one binary. Cached by (path, size, mtime): a binary that did not change is not
    hashed or started again, so the preflight stays fast; a replaced one is measured afresh."""
    if not path or not Path(path).is_file():
        return None
    st = Path(path).stat()
    key = f"{path}|{st.st_size}|{st.st_mtime_ns}"
    if key in cache:
        return dict(cache[key])
    rc, out, err = run([str(path), *vargs])
    first = ((out or err).splitlines() or [""])[0]
    row = {"version": norm_version(first) if rc == 0 else None, "sha256": sha256(path), "bytes": st.st_size}
    cache[key] = row
    return dict(row)


def fieldkit_state(run=_run):
    root = str(settings.ROOT)
    rc, head, _ = run(["git", "-C", root, "rev-parse", "HEAD"])
    _, branch, _ = run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"])
    rc2, dirty, _ = run(["git", "-C", root, "status", "--porcelain", "--untracked-files=no"])
    return {"commit": head if rc == 0 else None, "branch": branch or None, "dirty": bool(dirty) if rc2 == 0 else None}


def opencode_config(home=None):
    """Gorilla OpenCode's capabilities from its config: provider NAMES that can answer (a key or a sign-in is set),
    agent models, MCP server names. The key values are read only to say set / not set and never leave this function."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path(home or Path.home()) / ".config")) / "gorilla-opencode"
    try:
        cfg = json.loads((base / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"providers": [], "agents": {}, "mcp": [], "config": False}
    provs = set()
    for name, p in (cfg.get("providers") or {}).items():
        if isinstance(p, dict) and not p.get("disabled") and (str(p.get("apiKey") or "").strip()
                                                              or (base / f"{name}-oauth.json").is_file()):
            provs.add(name)
    agents = {k: (v or {}).get("model") for k, v in sorted((cfg.get("agents") or {}).items()) if isinstance(v, dict)}
    return {"providers": sorted(provs), "agents": agents, "mcp": sorted((cfg.get("mcpServers") or {}).keys()),
            "config": True}


def capabilities(home=None, cache=None):
    home = Path(home or Path.home())
    skills = sorted(p.parent.name for p in (home / ".claude" / "skills").glob("*/SKILL.md"))
    out = {"skills": skills}
    try:
        out["cards"], out["local_cards"] = card_ids(cache if cache is not None else {})
    except Exception:  # noqa: BLE001
        out["cards"], out["local_cards"] = None, None
    out["opencode"] = opencode_config(home)
    try:
        from .core import longpath
        out["long_paths"] = {"os": longpath.long_paths_enabled(), "git": longpath.git_longpaths()}
    except Exception:  # noqa: BLE001
        out["long_paths"] = None
    out["mandates"] = mandates_in_force(cache if cache is not None else {})
    out["mandate_files"] = mandate_files()
    return out


def card_ids(cache, files=None):
    """-> (shared card ids, local card ids). Parsing the cards takes 1.3 s (measured 2026-10-28, VAIO), so the ids are
    cached by the card files' path, size and time: an edited card file is read afresh."""
    from .desk import platformcheck
    files = [Path(f) for f in (files or [platformcheck.HERE / "tools.yaml", platformcheck.ROOT / "local" / "tools.yaml"])]
    key = "cards|" + "|".join(f"{f}:{f.stat().st_size}:{f.stat().st_mtime_ns}" if f.is_file() else f"{f}:-"
                              for f in files)
    if key not in cache:
        cards = platformcheck.cards(files)
        cache[key] = [sorted(c["id"] for c in cards if "local" not in Path(c["_file"]).parts and c.get("id")),
                      sorted(c["id"] for c in cards if "local" in Path(c["_file"]).parts and c.get("id"))]
    return list(cache[key][0]), list(cache[key][1])


def mandate_files():
    """-> {name: {sha256, expires}} of every sealed mandate file here: two machines holding different copies of the same mandate
    refuse each other's jobs (2026-10-26: the VAIO had owner-wide.yaml ea2c3f75..., Windows the renewed 1bc7be7e...)."""
    try:
        from .mandate import mandate
        return {p.stem: {"sha256": sha256(p), "expires": str(mandate.load(p).get("expires"))}
                for p in mandate.mandates()}
    except Exception:  # noqa: BLE001
        return None


def mandates_in_force(cache):
    """Names of the mandates in force. Checking a signature takes seconds (measured 5.4 s), so the answer is cached by
    the mandate files' size and time and by today's date (a mandate runs out at midnight)."""
    try:
        from .mandate import mandate
        files = mandate.mandates()
        key = "mandates|" + time.strftime("%Y-%m-%d") + "|" + "|".join(
            f"{f}:{f.stat().st_size}:{f.stat().st_mtime_ns}" for f in files)
        if key not in cache:
            cache[key] = sorted(p.stem for p in mandate.in_force())
        return list(cache[key])
    except Exception:  # noqa: BLE001
        return None


def defender(run=_run, sysname=None, excl=None):
    """-> {realtime: True | False | None, scratch_exclusion: True | False | None}. Read-only and never cached: Windows
    turns real-time protection back on by itself. Get-MpComputerStatus answers without admin (measured 2026-10-26);
    the exclusion list needs admin, so fieldkit/scratch reads it or the owner script's proof, else None."""
    if (sysname or system()) != "windows":
        return {"realtime": None, "scratch_exclusion": None}
    rc, out, _ = run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                      "(Get-MpComputerStatus).RealTimeProtectionEnabled"], timeout=60)
    word = (out.splitlines() or [""])[-1].strip().lower() if rc == 0 else ""
    rt = True if word == "true" else False if word == "false" else None
    if excl is None:
        try:
            from . import scratch
            excl = scratch.exclusion().get("present")
        except Exception:  # noqa: BLE001
            excl = None
    return {"realtime": rt, "scratch_exclusion": excl}


def baseline_path():
    return state_dir() / "baseline.json"


def baseline():
    try:
        return json.loads(baseline_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def set_baseline(state):
    baseline_path().parent.mkdir(parents=True, exist_ok=True)
    row = {"defender": state, "set": time.strftime("%Y-%m-%d %H:%M:%S")}
    baseline_path().write_text(json.dumps(row, indent=1, sort_keys=True), encoding="utf-8")
    return row


def not_comparable(state, base):
    """-> [one line per machine-state value that differs from the baseline]; [] without a baseline."""
    if not base:
        return []
    word = {True: "on", False: "off", None: "unknown"}
    return [f"Defender {k.replace('_', ' ')} {word.get(state.get(k), state.get(k))}, baseline "
            f"{word.get(v, v)}: NOT COMPARABLE with baseline results"
            for k, v in sorted((base.get("defender") or {}).items()) if state.get(k) != v]


def manifest(run=_run, home=None, save=True):
    """-> this machine's manifest (stable key order when dumped with canon); its hash in "hash"."""
    cache = _cache()
    tools = {n: probe_tool(p, a, cache, run) for n, (p, a) in tool_paths().items()}
    m = {"schema": SCHEMA, "machine": {"host": socket.gethostname(), "os": system(), "release": platform.release(),
                                       "arch": platform.machine().lower(), "defender": defender(run)},
         "tools": tools, "fieldkit": fieldkit_state(run), "capabilities": capabilities(home, cache),
         "taken": time.strftime("%Y-%m-%d %H:%M:%S")}
    m["hash"] = digest(m)
    if save:
        try:
            d = state_dir() / "manifests"
            d.mkdir(parents=True, exist_ok=True)
            (state_dir() / "cache.json").write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
            (d / f"{m['hash'][:16]}.json").write_text(json.dumps(m, indent=1, sort_keys=True), encoding="utf-8")
        except OSError:
            pass
    return m


def from_toolset(ts, host=""):
    """A PARTIAL manifest from an older peer's hello toolset (fieldkit/peers/link_ops.toolset_local)."""
    ts = ts or {}
    sysname = {"win32": "windows", "darwin": "darwin"}.get(ts.get("os"), "linux" if ts.get("os") else None)
    return {"schema": 0, "partial": True, "machine": {"host": host, "os": sysname},
            "tools": {"python": {"version": ts.get("python")} if ts.get("python") else None,
                      "git": {"version": ts.get("git")} if ts.get("git") else None},
            "fieldkit": {"commit": ts.get("link_commit")}}


# ---- compare --------------------------------------------------------------------------------------------------------
def _get(m, path):
    cur = m
    for k in path.split("."):
        if cur is None:
            return None                                 # a tool that is not installed: absent, which is a value
        if not isinstance(cur, dict) or k not in cur:
            return ...                                  # not reported (an older peer)
        cur = cur[k]
    return cur


#: (what, where in the manifest, level, how two values are made comparable)
RULES = [
    ("gorilla-opencode", "tools.gorilla-opencode.version", DRIFT, None),
    ("gorilla-size", "tools.gorilla-size.version", DRIFT, None),
    ("fieldkit commit", "fieldkit.commit", DRIFT, "commit"),
    ("fieldkit dirty", "fieldkit.dirty", DRIFT, None),
    ("python", "tools.python.version", INFO, None),       # Windows scoop and Debian differ; 3.11+ is the floor
    ("go", "tools.go.version", DRIFT, None),             # the same source builds different binaries
    ("git", "tools.git.version", INFO, None),
    ("gh", "tools.gh.version", INFO, None),
    ("node", "tools.node.version", INFO, None),
    ("skills installed", "capabilities.skills", DRIFT, None),
    ("cards", "capabilities.cards", DRIFT, None),
    ("local cards", "capabilities.local_cards", INFO, None),
    ("providers", "capabilities.opencode.providers", DRIFT, None),
    ("agent models", "capabilities.opencode.agents", INFO, None),   # a test names its model; defaults may differ
    ("mcp servers", "capabilities.opencode.mcp", DRIFT, None),
    ("mandates in force", "capabilities.mandates", DRIFT, None),
    ("mandate files", "capabilities.mandate_files", DRIFT, None),
    ("os", "machine.os", INFO, None),
    ("arch", "machine.arch", INFO, None),
    ("defender real-time", "machine.defender.realtime", INFO, None),
    ("scratch exclusion", "machine.defender.scratch_exclusion", INFO, None),
]


def _show(v):
    if v is None:
        return "absent"
    if isinstance(v, list):
        return f"{len(v)} item(s)"
    if isinstance(v, dict):
        return canon(v)
    return str(v)


def _list_delta(a, b):
    a, b = set(a or []), set(b or [])
    parts = []
    if a - b:
        parts.append("only here: " + ", ".join(sorted(a - b)[:12]) + (" ..." if len(a - b) > 12 else ""))
    if b - a:
        parts.append("only there: " + ", ".join(sorted(b - a)[:12]) + (" ..." if len(b - a) > 12 else ""))
    return "; ".join(parts)


def pinned_for(want, m):
    """The version the pin asks of THIS machine: one version for all, or per target (targets: {os or host: version}),
    so a toolchain kept back on purpose (go1.24 for the Mac 10.13 build) is pinned, not forced to one version.
    -> None when the pin names no version for this machine."""
    if isinstance(want, dict) and isinstance(want.get("targets"), dict):
        mach = m.get("machine") or {}
        t = want["targets"]
        v = t.get(mach.get("host")) or t.get(mach.get("os"))
        return norm_version(str(v)) if v else None
    return norm_version(str((want or {}).get("version") if isinstance(want, dict) else want))


def _matches(have, want_v):
    """'1.24.4' satisfies a pin of '1.24' or '1.24.4'; '1.240.0' does not satisfy '1.24'."""
    return bool(have) and (have == want_v or have.startswith(want_v + "."))


def per_target(p):
    """Tools the pin governs per target: two machines differing on them is expected, the pin decides."""
    return {t for t, w in (p or {}).items() if isinstance(w, dict) and isinstance(w.get("targets"), dict)}


def against_pin(m, p, where="this machine"):
    """-> [diff rows] for each pinned tool whose version here is not the pin. A tool pinned with `align: report` is
    reported only: parity align moves Gorilla OpenCode alone, never a toolchain."""
    rows = []
    for tool, want in sorted((p or {}).items()):
        want_v = pinned_for(want, m)
        if want_v is None:
            continue
        have = _get(m, f"tools.{tool}.version")
        if have is ... and m.get("partial"):
            continue                                    # an older peer did not say: listed as NOT REPORTED
        have = None if have is ... else have
        if not _matches(have, want_v):
            fix = "fieldkit parity align" if tool == "gorilla-opencode" else f"install {tool} {want_v} by hand (report only)"
            rows.append({"what": f"{tool} pin", "level": DRIFT, "here": have, "there": want_v,
                         "detail": f"{where} runs {have or 'nothing'}, the pin is {want_v}: {fix}"})
    return rows


def compare(mine, theirs, p=None):
    """-> {verdict SAME | PARTIAL | DRIFT, diffs: [{what, level, here, there, detail}], missing: [what]}."""
    diffs, missing = [], []
    governed = per_target(p)
    for what, path, level, how in RULES:
        if what in governed:
            level = INFO                                # differs by design; against_pin judges each machine
        a, b = _get(mine, path), _get(theirs, path)
        if a is ... and b is ...:
            continue                                    # neither says (both from before the field existed)
        if b is ... or a is ...:
            if theirs.get("partial") or a is ...:
                missing.append(what)
            continue
        if how == "commit":
            same = bool(a and b) and (a.startswith(b) or b.startswith(a))
        else:
            fa, fb = (how(a), how(b)) if callable(how) else (a, b)
            same = fa == fb
        if not same:
            detail = _list_delta(a, b) if isinstance(a, list) or isinstance(b, list) else f"{_show(a)} here, {_show(b)} there"
            diffs.append({"what": what, "level": level, "here": a, "there": b, "detail": detail})
    host = (theirs.get("machine") or {}).get("host") or "the peer"
    diffs += against_pin(theirs, p, host)               # this machine's own pin rows: against_pin(mine, p)
    verdict = DRIFT.upper() if any(d["level"] == DRIFT for d in diffs) else "PARTIAL" if missing else "SAME"
    return {"verdict": verdict, "diffs": diffs, "missing": missing}


def verdict(mine, p, rows, named=False):
    """The one verdict over this machine's pin and every device that answered: DRIFT, PARTIAL, SAME, or ALONE (no
    other device answered: nothing to compare, never a failure unless a device was named)."""
    present = [r for r in rows if r["state"] == "ANSWERS"]
    got = {r["verdict"] for r in present}
    v = ("DRIFT" if "DRIFT" in got or against_pin(mine, p) else "PARTIAL" if "PARTIAL" in got
         else "SAME" if present else "ALONE")
    code = 3 if v == "DRIFT" or (named and not present) else 1 if v == "PARTIAL" else 0
    return v, code


def peer_manifest(name, call_fn=None):
    """-> (manifest or None, why). A peer on an older Fieldkit answers only hello: a PARTIAL manifest from its toolset."""
    from .peers import link
    call_fn = call_fn or link.call
    r = call_fn({"verb": "manifest"}, device=name)
    if r.get("ok") and isinstance(r.get("manifest"), dict):
        return r["manifest"], "manifest"
    if "unknown verb" in str(r.get("error", "")):
        h = call_fn({"verb": "hello"}, device=name)
        if h.get("ok"):
            return from_toolset(h.get("toolset"), h.get("host", name)), "hello only (an older Fieldkit there: PARTIAL)"
        return None, h.get("error") or "no answer"
    return None, r.get("error") or "no answer"


def compare_household(mine, p, peer=None, call_fn=None):
    """-> [{device, state ANSWERS | ABSENT, verdict, diffs, missing, via}]. A device that does not answer is ABSENT
    (the Mac is usually off): reported, never a failure, unless it was named with --peer."""
    from .peers import link
    h = link.household() or {}
    devs = [link.device(h, peer)] if peer else link.others(h)
    out = []
    for d in devs:
        if not d:
            out.append({"device": peer, "state": "UNKNOWN", "verdict": None, "diffs": [], "missing": [],
                        "via": "not a device of this household"})
            continue
        theirs, via = peer_manifest(d["name"], call_fn)
        if theirs is None:
            out.append({"device": d["name"], "state": "ABSENT", "verdict": None, "diffs": [], "missing": [], "via": via})
            continue
        out.append({"device": d["name"], "state": "ANSWERS", "via": via, **compare(mine, theirs, p)})
    return out


# ---- the run guard's check ------------------------------------------------------------------------------------------
def _tree(proc_mod, pid=None):
    try:
        me = proc_mod.Process(pid or os.getpid())
        return {me.pid} | {a.pid for a in me.parents()} | {c.pid for c in me.children(recursive=True)}
    except Exception:  # noqa: BLE001
        return {os.getpid()}


def _toplevel(cwd=None):
    rc, out, _ = _run(["git", "-C", str(cwd or os.getcwd()), "rev-parse", "--show-toplevel"])
    return os.path.normcase(os.path.abspath(out)) if rc == 0 and out else None


def _inside(path, top):
    if not path or not top:
        return False
    p, top = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(top))
    return p == top or p.startswith(top.rstrip("\\/") + os.sep)


_PRIMED = {}
PRIME_MAX_S = 30.0               # a first reading older than this is taken again


def _start_sample():
    import psutil
    live = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            p.cpu_percent(None)
            live.append(p)
        except (psutil.Error, OSError):
            pass
    return live


def preflight_prime():
    """The run guard calls this before its checks: the CPU sample's first reading, so its window passes while the
    other checks run (fieldkit/core/preflight.py _prime)."""
    _PRIMED.clear()
    _PRIMED.update(at=time.monotonic(), live=_start_sample())


def sample_procs(sample_s=0.5):
    """-> [{pid, name, cwd, exe, cpu}] of every process, cpu over sample_s seconds at least; cwd and exe for WORKERS
    only. A first reading taken by preflight_prime is used once (its window already running), else taken here."""
    import psutil
    at, live = _PRIMED.pop("at", None), _PRIMED.pop("live", None)
    if live is None or time.monotonic() - at > PRIME_MAX_S:       # a stale first reading misses new processes
        at, live = time.monotonic(), _start_sample()
    time.sleep(max(0.0, sample_s - (time.monotonic() - at)))
    procs = []
    for p in live:
        try:
            row = {"pid": p.pid, "name": (p.info.get("name") or "").lower(), "cpu": p.cpu_percent(None),
                   "cwd": None, "exe": None}
            if row["name"].rsplit(".", 1)[0] in WORKERS:
                try:
                    row["cwd"], row["exe"] = p.cwd(), p.exe()
                except (psutil.Error, OSError):
                    pass
            procs.append(row)
        except (psutil.Error, OSError):
            pass
    return procs


def shared_ground(procs=None, mine=None, checkout=None, binary=None, sample_s=0.5, proc_mod=None):
    """-> [one line per thing another session holds]: a worker process in this checkout, the pinned binary running,
    a process pinning a core. procs = [{pid, name, cwd, exe, cpu}] (tests pass them; else measured with psutil,
    cpu over sample_s seconds); `mine` = this process's ancestors and descendants, never counted."""
    if procs is None:
        procs = sample_procs(sample_s)
    mine = mine if mine is not None else _tree(proc_mod or __import__("psutil"))
    top = checkout if checkout is not None else _toplevel()
    binary = os.path.normcase(str(binary)) if binary else None
    lines = []
    for r in sorted(procs, key=lambda r: r["pid"]):
        if r["pid"] in mine or r["pid"] in (0, 4) or r["name"] in ("system idle process", "idle", "system"):
            continue
        if r.get("cpu") is not None and r["cpu"] >= CPU_BUSY_PCT:
            lines.append(f"{r['name']} pid {r['pid']} uses {r['cpu']:.0f}% of a core")
        if binary and r.get("exe") and os.path.normcase(r["exe"]) == binary:
            lines.append(f"{r['name']} pid {r['pid']} runs the pinned binary")
        elif top and r.get("cwd") and _inside(r["cwd"], top):
            lines.append(f"{r['name']} pid {r['pid']} works in this checkout ({r['cwd']})")
    return lines


def loaded_models(ctx, rows=None):
    """-> names of local models loaded that the run did not declare (ctx["models"]). rows: [{name}] of the processes
    already walked (sample_procs), else walked here."""
    try:
        from . import hog
        from .hog import probe
        rows = rows if rows is not None else probe.processes(top=400)[0]
        got = probe.lms(rows, hog.config())
        names = [m["id"] for m in got.get("models", [])]
    except Exception:  # noqa: BLE001
        return []
    ours = set(ctx.get("models") or [])
    return [n for n in names if n not in ours]


def preflight_check(ctx=None, m=None, p=None, ground=None, models=None, quiet=None, base=...):
    """The run guard's check (fieldkit/core/preflight.py STEPS) -> (status, one line). Puts the manifest hash in
    os.environ[ENV] for every result written under this run."""
    ctx = ctx or {}
    strict = ctx.get("kind") in STRICT_KINDS
    # one process walk (its 0.5 s CPU sample) beside the manifest feeds both the shared-ground and the model check:
    # the same answers, without a second walk of every process (O5, 2026-10-28)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as ex:
        f_procs = ex.submit(sample_procs) if ground is None or models is None else None
        m = m if m is not None else manifest()
        procs = f_procs.result() if f_procs else None
    if ground is None:
        ground = shared_ground(procs=procs, binary=opencode_path())
    if models is None:
        models = loaded_models(ctx, rows=procs)
    p = p if p is not None else pin()
    os.environ[ENV] = m["hash"]
    problems = [d["detail"] for d in against_pin(m, p)]
    state = (m.get("machine") or {}).get("defender") or {}
    base = baseline() if base is ... else base
    off_base = not_comparable(state, base)
    os.environ[ENV_COMPARABLE] = "0" if off_base else "1" if base else ""
    os.environ[ENV_DEFENDER] = canon(state)
    problems += off_base
    problems += ground
    problems += [f"model {n} is loaded and this run did not load it" for n in models]
    if strict and quiet is None:
        try:
            from . import hog
            _f, found = hog.check(hog.config(), sample=True)
            quiet = [f"not quiet: {f['line']}" for f in found if f.get("severity") in ("warn", "act")]
        except Exception as e:  # noqa: BLE001
            quiet = [f"hog not read ({str(e)[:60]})"]
    problems += quiet or []
    word = {True: "on", False: "off", None: "unknown"}
    head = (f"parity: manifest {m['hash'][:12]}, Defender real-time {word.get(state.get('realtime'), '?')}"
            + ("" if base else ", no baseline"))
    if not problems:
        return "ok", head
    status = "stop" if strict else "warn"
    more = f" (+{len(problems) - 4} more)" if len(problems) > 4 else ""
    return status, head + ("; REFUSED for a " + ctx["kind"] if strict else "") + ": " + "; ".join(problems[:4]) + more


# ---- align ----------------------------------------------------------------------------------------------------------
def asset_for(sysname=None, which=shutil.which):
    s = sysname or system()
    if s == "linux":
        return "deb" if which("dpkg") else "rpm" if which("rpm") else "linux"
    return s


def _gh_json(args, run=_run):
    rc, out, err = run(["gh", *args], timeout=600)
    if rc != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {(err or out)[:300]}")
    return json.loads(out) if out.startswith(("{", "[")) else out


def fetch(tag, repo, name, folder, run=_run):
    """Download ONE release asset and its SHA256SUMS into a new empty folder and check both ways: the bytes equal
    SHA256SUMS and GitHub's own digest. -> (path, sha256). Raises RuntimeError on any mismatch."""
    from .build import relfiles
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        raise RuntimeError(f"{folder} is not empty: downloads go into a new empty folder")
    folder.mkdir(parents=True, exist_ok=True)
    view = _gh_json(["release", "view", tag, "-R", repo, "--json", "assets"], run)
    assets = {a["name"]: a for a in view.get("assets", [])}
    sums_name = next((n for n in assets if n.startswith("SHA256SUMS")), None)
    if name not in assets or not sums_name:
        raise RuntimeError(f"{tag} has no {name if name not in assets else 'SHA256SUMS'} (assets: {', '.join(assets)})")
    rc, out, err = run(["gh", "release", "download", tag, "-R", repo, "-p", name, "-p", sums_name, "-D", str(folder)],
                       timeout=3600)
    if rc != 0:
        raise RuntimeError(f"download failed: {(err or out)[:300]}")
    sums = relfiles.parse_sums(folder / sums_name)
    got = sha256(folder / name)
    if sums.get(name) != got:
        raise RuntimeError(f"{name}: sha256 {got} is not the {sums_name} value {sums.get(name)}")
    gd = (assets[name].get("digest") or "").removeprefix("sha256:")
    if gd and gd != got:
        raise RuntimeError(f"{name}: GitHub's digest {gd} differs from the downloaded bytes")
    return folder / name, got


def _in_use(path, proc_mod=None):
    import psutil
    proc_mod = proc_mod or psutil
    want = os.path.normcase(str(path))
    out = []
    for p in proc_mod.process_iter(["pid", "exe"]):
        try:
            if p.info.get("exe") and os.path.normcase(p.info["exe"]) == want:
                out.append(p.pid)
        except Exception:  # noqa: BLE001
            pass
    return out


def install_windows(new, dest, old_version, want_sha, run=_run, in_use=_in_use):
    """Replace dest with new: refused while a process runs it; the old binary kept as dest.bak-v<old>; the new one
    proven by sha256 and its own --version; put back on any failure. -> dict."""
    dest = Path(dest)
    pids = in_use(dest)
    if pids:
        return {"ok": False, "why": f"{dest.name} is running (pid {', '.join(map(str, pids))}): not replaced"}
    backup = dest.with_name(f"{dest.name}.bak-v{old_version or 'unknown'}")
    if dest.is_file() and not backup.exists():
        shutil.copy2(dest, backup)
    tmp = dest.with_name(dest.name + ".parity-new")
    shutil.copy2(new, tmp)
    os.replace(tmp, dest)
    got = sha256(dest)
    rc, out, err = run([str(dest), "--version"])
    ver = norm_version(out or err) if rc == 0 else None
    if got != want_sha or not ver:
        if backup.is_file():
            shutil.copy2(backup, dest)
        return {"ok": False, "why": f"after the copy: sha256 {got[:16]}, --version {ver!r}; the old binary is back",
                "backup": str(backup)}
    return {"ok": True, "version": ver, "sha256": got, "backup": str(backup), "path": str(dest)}


def _record_align(row):
    state_dir().mkdir(parents=True, exist_ok=True)
    with open(state_dir() / "align.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(row, at=time.strftime("%Y-%m-%d %H:%M:%S"))) + "\n")


def align(version=None, repo=None, apply=False, sysname=None, run=_run, fetch_fn=fetch, install_fn=install_windows):
    """-> {state ALIGNED | PLAN | DONE | TODO | FAILED, ...}. TODO = the line the session on that machine runs."""
    p = pin().get("gorilla-opencode") or {}
    tag = version or p.get("version")
    repo = repo or p.get("repo")
    if not tag or not repo:
        return {"state": "FAILED", "why": f"no version/repo: give --version, or pin one in {PIN_FILE}"}
    tag = tag if tag.startswith("v") else "v" + tag
    want = norm_version(tag)
    s = sysname or system()
    kind = asset_for(s)
    name = ASSETS[kind].format(v=want)
    path = opencode_path()
    cur = probe_tool(path, ["--version"], {}, run) if path else None
    base = {"tag": tag, "repo": repo, "asset": name, "path": str(path) if path else None,
            "installed": (cur or {}).get("version"), "installed_sha256": (cur or {}).get("sha256")}
    if cur and cur["version"] == want and not apply:
        return {**base, "state": "ALIGNED", "why": "the installed version is the pin (align --apply also checks its "
                                                   "sha256 against the release)"}
    if not apply:
        return {**base, "state": "PLAN", "why": f"would fetch {name} from {repo} {tag}, check it, and install it"}
    folder = state_dir() / "downloads" / f"{tag}-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        f, sha = fetch_fn(tag, repo, name, folder, run=run)
    except RuntimeError as e:
        return {**base, "state": "FAILED", "why": str(e)}
    base.update(downloaded=str(f), sha256=sha)
    if s == "windows":
        if cur and cur["version"] == want and cur["sha256"] == sha:
            return {**base, "state": "ALIGNED", "why": "installed bytes equal the release's"}
        dest = path or Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Gorilla OpenCode" / "gorilla-opencode.exe"
        r = install_fn(f, dest, (cur or {}).get("version"), sha, run=run)
        out = {**base, **r, "state": "DONE" if r["ok"] else "FAILED"}
        _record_align(out)
        return out
    if kind == "linux" and cur and cur["version"] == want and cur["sha256"] == sha:
        return {**base, "state": "ALIGNED", "why": "installed bytes equal the release's"}
    if kind in ("deb", "rpm") and cur and cur["version"] == want:
        # the package's binary is the release's raw linux binary (the VAIO, 2026-10-26: d31a6f2b... both)
        return {**base, "state": "ALIGNED", "why": f"installed {want}; package checked against SHA256SUMS"}
    line = {"deb": f"sudo apt install {f}", "rpm": f"sudo dnf install {f}",
            "linux": f"install -m 0755 {f} {path or '/usr/local/bin/gorilla-opencode'}",
            "darwin": f"unzip -o {f} -d ~/Applications"}[kind]
    keep = f"cp -p {path} {path}.bak-v{(cur or {}).get('version')}" if path else ""
    out = {**base, "state": "TODO", "run": [x for x in (keep, line) if x],
           "why": "root is needed: the session on this machine runs these lines (Fieldkit never runs sudo)"}
    _record_align(out)
    return out


def rollback(run=_run):
    """Put back the binary the last Windows align replaced (its .bak-v<old>)."""
    try:
        rows = [json.loads(l) for l in (state_dir() / "align.jsonl").read_text(encoding="utf-8").splitlines() if l]
    except OSError:
        rows = []
    last = next((r for r in reversed(rows) if r.get("state") == "DONE" and r.get("backup")), None)
    if not last or not Path(last["backup"]).is_file():
        return {"ok": False, "why": "no align with a kept backup is recorded here"}
    dest = Path(last["path"])
    if _in_use(dest):
        return {"ok": False, "why": f"{dest.name} is running: not replaced"}
    shutil.copy2(last["backup"], dest)
    rc, out, err = run([str(dest), "--version"])
    return {"ok": rc == 0, "version": norm_version(out or err), "path": str(dest), "from": last["backup"]}


# ---- doctor ---------------------------------------------------------------------------------------------------------
def doctor():
    from .core import preflight
    rows = []
    p = pin()
    rows.append(("pin", bool(p.get("gorilla-opencode")), f"{PIN_FILE.name}: " + (canon(p) if p else "absent")))
    oc = opencode_path()
    rows.append(("gorilla-opencode found", bool(oc), str(oc)))
    rows.append(("gh (align downloads)", bool(shutil.which("gh")), shutil.which("gh") or "not on PATH"))
    try:
        import psutil  # noqa: F401
        rows.append(("psutil (shared ground)", True, "importable"))
    except ImportError:
        rows.append(("psutil (shared ground)", False, "pip install psutil"))
    rows.append(("preflight wiring", "fieldkit.parity:preflight_check" in preflight.STEPS, "core/preflight.py STEPS"))
    try:
        from .peers import link
        h = link.household()
        rows.append(("household", bool(h), f"{len(link.others(h))} other device(s)" if h else "none: compare --with"))
    except Exception as e:  # noqa: BLE001
        rows.append(("household", False, str(e)[:80]))
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        rows.append(("state folder", True, str(state_dir())))
    except OSError as e:
        rows.append(("state folder", False, str(e)))
    return rows


# ---- the command ----------------------------------------------------------------------------------------------------
def _diff_lines(r, indent="  "):
    out = [f"{indent}{d['level'].upper():5} {d['what']}: {d['detail']}" for d in r["diffs"]]
    if r.get("missing"):
        out.append(f"{indent}NOT REPORTED by the peer: {', '.join(r['missing'])}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit parity", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["manifest", "compare", "align", "preflight", "baseline", "doctor"])
    ap.add_argument("--set", action="store_true", help="baseline: record this machine's state now")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hash", action="store_true", help="manifest: print only the hash")
    ap.add_argument("--peer", help="compare: this device only (an absent named device is a failure)")
    ap.add_argument("--with", dest="with_file", help="compare: against a saved manifest JSON")
    ap.add_argument("--version", help="align: this tag instead of the pin")
    ap.add_argument("--repo", help="align: this OWNER/NAME instead of the pin's")
    ap.add_argument("--apply", action="store_true", help="align: download, check and install (else the plan)")
    ap.add_argument("--rollback", action="store_true", help="align: put back the binary the last align replaced")
    ap.add_argument("--label", default="parity preflight")
    ap.add_argument("--kind", default="test", help="preflight: test, build, gate, bench, measure (bench/measure: strict)")
    a = ap.parse_args(argv)
    if a.action == "manifest":
        m = manifest()
        print(m["hash"] if a.hash else json.dumps(m, indent=1, sort_keys=True, ensure_ascii=False))
        return 0
    if a.action == "compare":
        mine, p = manifest(), pin()
        if a.with_file:
            theirs = json.loads(Path(a.with_file).read_text(encoding="utf-8"))
            rows = [{"device": (theirs.get("machine") or {}).get("host") or a.with_file, "state": "ANSWERS",
                     "via": "file", **compare(mine, theirs, p)}]
        else:
            rows = compare_household(mine, p, a.peer)
        if a.json:
            print(json.dumps({"mine": mine["hash"], "pin": p, "devices": rows}, indent=1, sort_keys=True))
        else:
            print(f"PARITY  this machine {mine['machine']['host']} manifest {mine['hash'][:12]}, pin "
                  + ", ".join(f"{k} {(v.get('version') or canon(v.get('targets'))) if isinstance(v, dict) else v}"
                              for k, v in sorted(p.items())))
            for line in _diff_lines({"diffs": against_pin(mine, p), "missing": []}):
                print(line)
            for r in rows:
                print(f"  {r['device']:<20} {r['state']:<8} {r['verdict'] or '-':<8} via {r['via']}")
                for line in _diff_lines(r, "      "):
                    print(line)
        v, code = verdict(mine, p, rows, named=bool(a.peer))
        if not a.json:
            absent = [r["device"] for r in rows if r["state"] != "ANSWERS"]
            n = sum(r["state"] == "ANSWERS" for r in rows)
            print(f"VERDICT  {v}" + (f" ({n} device(s) compared" if rows else " (no other device")
                  + (f"; absent: {', '.join(absent)}" if absent else "") + ")")
        return code
    if a.action == "align":
        r = rollback() if a.rollback else align(a.version, a.repo, a.apply)
        if a.json:
            print(json.dumps(r, indent=1, sort_keys=True))
        else:
            for k in ("state", "tag", "asset", "path", "installed", "installed_sha256", "downloaded", "sha256",
                      "version", "backup", "from", "why"):
                if r.get(k):
                    print(f"  {k:17} {r[k]}")
            for line in r.get("run", []):
                print(f"  RUN               {line}")
        if a.rollback:
            return 0 if r.get("ok") else 3
        return {"ALIGNED": 0, "DONE": 0, "PLAN": 0, "TODO": 1}.get(r["state"], 3)
    if a.action == "baseline":
        now = defender()
        if a.set:
            set_baseline(now)
        base = baseline()
        print(f"  now       {canon(now)}")
        print(f"  baseline  {canon(base) if base else 'none: fieldkit parity baseline --set'}")
        bad = not_comparable(now, base)
        for line in bad:
            print(f"  {line}")
        print(f"VERDICT  {'NOT COMPARABLE' if bad else 'COMPARABLE' if base else 'NO BASELINE'}")
        return 3 if bad else 0
    if a.action == "preflight":
        st, line = preflight_check({"label": a.label, "kind": a.kind})
        print(f"{st.upper():5} {line}")
        return {"ok": 0, "warn": 1, "stop": 2}[st]
    rows = doctor()
    for name, ok, detail in rows:
        print(f"  {'OK' if ok else 'FAIL':4} {name:24} {detail}")
    bad = [r for r in rows if not r[1]]
    print(f"DOCTOR  {'OK' if not bad else f'{len(bad)} problem(s)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
