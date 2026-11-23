# Version: 1.0.0 · updated 26-11-23-12-00
"""CI runner adapters (CI-SPEED-PLAN C8): one plan, many test runners.

    fieldkit ci plan --adapter pytest|go|firefox|kernel --collect F [--raw-durations F] [--check] [--slice I/N --os OS]

An adapter has three parts, all pure text in, text out (the project's CI feeds them the real command output):
  collect(text)    -> the units of work (sorted, unique)
  durations(text)  -> {unit: seconds} from the runner's own report
  slice(units)     -> the commands that run exactly those units
The plan itself (N per OS, the balance, the C2 checks, the C3 slot budget) is ciplan.plan / ciplan.check, unchanged:
the adapters only supply the units and their weights, so every project gets the same checks.

  pytest   unit = test file (today's behavior); collect = `pytest --collect-only -q`; durations = junit
  go       unit = `pkg::TestName`; collect = `go test -list . ./...`; slice = `-run '^(A|B)$'` per package;
           durations = `go test -json` (top-level tests only)
  firefox  unit = test path from the mach manifests; slice = an explicit list, no --this-chunk; durations = mozlog
  kernel   build-only stub: one unit `kernel-build`, no tests, no durations
"""
import json
import re
import xml.etree.ElementTree as ET

from fieldkit.method import ciplan


def _units(items):
    return sorted({i for i in items if i})


class Pytest:
    name = "pytest"

    def collect(self, text):
        """`pytest --collect-only -q` lines `file::test` -> the test files (the unit of today's plan)."""
        return _units(ln.split("::", 1)[0].strip().replace("\\", "/") for ln in text.splitlines()
                      if "::" in ln and not ln.startswith(("=", " ")))

    def durations(self, text):
        """junit XML text -> {test file: seconds}."""
        out = {}
        for nid, s in _junit_nodes(text).items():
            f = nid.split("::", 1)[0]
            out[f] = round(out.get(f, 0.0) + s, 3)
        return out

    def slice(self, units):
        return [["pytest", *units]] if units else []


def _junit_nodes(text):
    out = {}
    for case in ET.fromstring(text).iter("testcase"):
        nid = ciplan.node_id(case)
        if nid and case.find("skipped") is None:
            out[nid] = out.get(nid, 0.0) + float(case.get("time") or 0)
    return out


class Go:
    name = "go"
    _skip = re.compile(r"^(ok|FAIL|\?|---|===|PASS|exit status)\b")

    def collect(self, text):
        """`go test -list . ./...`: test names, then `ok  <TAB>pkg<TAB>time` closes the package."""
        out, pending = [], []
        for ln in text.splitlines():
            ln = ln.rstrip()
            parts = ln.split()
            if parts and parts[0] == "ok" and len(parts) >= 2:
                out += [f"{parts[1]}::{t}" for t in pending]
                pending = []
            elif re.match(r"^(Test|Example|Fuzz|Benchmark)\w*$", ln):
                pending.append(ln)
        return _units(out)

    def durations(self, text):
        """`go test -json` lines -> {pkg::Test: seconds}; subtests ride inside their parent."""
        out = {}
        for ln in text.splitlines():
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            t = e.get("Test")
            if e.get("Action") in ("pass", "fail") and t and "/" not in t and e.get("Package"):
                out[f"{e['Package']}::{t}"] = round(float(e.get("Elapsed") or 0), 3)
        return out

    def slice(self, units):
        by_pkg = {}
        for u in units:
            pkg, _, name = u.partition("::")
            by_pkg.setdefault(pkg, []).append(name)
        return [["go", "test", pkg, "-run", "^(" + "|".join(names) + ")$"] for pkg, names in sorted(by_pkg.items())]


class Firefox:
    name = "firefox"

    def collect(self, text):
        """The mach test list: JSON (`[{"path": ...}]` or `{"tests": [...]}`) or one path per line."""
        text = text.strip()
        if text[:1] in "[{":
            d = json.loads(text)
            d = d.get("tests", []) if isinstance(d, dict) else d
            return _units((t.get("path") if isinstance(t, dict) else t) for t in d)
        return _units(ln.strip().replace("\\", "/") for ln in text.splitlines() if ln.strip() and not ln.startswith("#"))

    def durations(self, text):
        """mozlog JSON lines: test_end.time - test_start.time (ms) -> seconds."""
        start, out = {}, {}
        for ln in text.splitlines():
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            t = e.get("test")
            if e.get("action") == "test_start" and t:
                start[t] = e.get("time", 0)
            elif e.get("action") == "test_end" and t in start:
                out[t] = round(max(e.get("time", 0) - start[t], 0) / 1000.0, 3)
        return out

    def slice(self, units):
        return [["./mach", "test", *units]] if units else []


class Kernel:
    name = "kernel"

    def collect(self, text):
        return ["kernel-build"]

    def durations(self, text):
        return {}

    def slice(self, units):
        return [["build"]] if units else []


ADAPTERS = {a.name: a for a in (Pytest(), Go(), Firefox(), Kernel())}


def blocks(adapter, units, raw_durations=None):
    """-> the weights block ciplan.plan reads: recorded seconds, the rest at the p90 (or the degraded unit weight)."""
    rec = adapter.durations(raw_durations) if raw_durations else {}
    guess = ciplan.p90(rec.values()) if rec else None
    return {"run": None, "weights": {o: {u: rec.get(u, guess if guess is not None else 10.0) for u in units}
                                     for o in ciplan.OSES}}


def plan(adapter, units, raw_durations, cfg, floor=1):
    """-> (pl, problems): the same ciplan.plan and ciplan.check (C2 union + 1.25x spread, C3 slot budget)."""
    pl = ciplan.plan(units, blocks(adapter, units, raw_durations), cfg, floor=floor)
    return pl, ciplan.check(units, pl, cfg)
