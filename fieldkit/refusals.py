# Version: 1.0.0 · updated 26-11-02-13-00
"""fieldkit refusals: a refusal repeated by any harness tool becomes a defect row, never an inbox item (plan R14, defect 8:
the train said "head moved" five times and nobody triaged it).

    fieldkit refusals list [--json]      every reason key, how often, and whether its defect row is open
    fieldkit refusals record TOOL TEXT   (tools call record() themselves; this is for a script)
    fieldkit refusals sync               P23: one ledger for both computers over the cable (rows tagged with their machine)
    fieldkit refusals close KEY --test F::T   close a defect only with its test; a read-only false positive needs the
                                         test to pass now. A job refused a read-only command opens a defect at once and
                                         `fieldkit task start` will not start that brief again until it is closed.

Train, ship, guard, goal and task write each refusal to state/refusals.jsonl: tool, reason key (heads, ids and numbers
stripped, so "head moved (abc -> def)" is one reason), full text, time, session. The same key twice appends ONE row to
the table of state/coordination/ZERO-OPEN-PROBLEMS.md (next number, the problem, OPEN, "repeated refusal: KEY (N times)").
record() never raises: a ledger that cannot be written must not turn one refusal into two failures.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .core.settings import state_root

ROOT = Path(__file__).resolve().parents[1]
LEDGER = "refusals.jsonl"
PROBLEMS = Path("coordination") / "ZERO-OPEN-PROBLEMS.md"
REPEAT = 2                                               # the same key this many times opens the row
MARK = "repeated refusal: "
_HEADER = ("# Open problems - the one list\n\nStatus: OPEN / PR / DONE.\n\n"
           "| # | Problem | Status | Proof needed / where |\n|---|---|---|---|\n")


def state(root=None):
    return state_root(root or ROOT) / "state"


def key_of(tool, text):
    """The reason key: the text with commit heads, ids, numbers and quoted names stripped, so the same refusal on
    another head or PR is the same key."""
    t = (text or "").lower()
    t = re.sub(r"\b[0-9a-f]{7,40}\b", "H", t)
    t = re.sub(r"\bt-\d{8}-\d{6}-[0-9a-f]+\b", "ID", t)
    t = re.sub(r"\d+(\.\d+)?", "N", t)
    t = re.sub(r"[\"'`][^\"'`]*[\"'`]", "Q", t)
    t = re.sub(r"\s+", " ", t).strip()
    return f"{tool}: {t[:100]}"


def _rows(root):
    p = state(root) / LEDGER
    out = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _open_row(root, key, n, text, tool):
    """Append the one row for `key` to the open-problems table unless it is already there. -> the row number or 0."""
    p = state(root) / PROBLEMS
    body = p.read_text(encoding="utf-8") if p.is_file() else _HEADER
    if f"{MARK}{key} (" in body:
        return 0
    lines = body.split("\n")
    nums = [int(m.group(1)) for x in lines for m in [re.match(r"\|\s*(\d+)\s*\|", x)] if m]
    num = max(nums, default=0) + 1
    last = max((i for i, x in enumerate(lines) if x.startswith("|")), default=len(lines) - 1)
    why = " ".join((text or "").split())[:160].replace("|", "/")
    lines.insert(last + 1, f"| {num} | A repeated refusal by {tool}: {why} | OPEN | {MARK}{key} ({n} times) |")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    return num


# ---- P23: one ledger for both computers, defects with a state, read-only refusals ---------------------------------------
POLICY = Path(__file__).with_name("refusals_policy.yaml")
DEFECTS = "refusal-defects.json"
SYNC = "refusals-sync.json"                   # {"sent": {device: [row ids]}, "since": last link inbox number read}
LEDGER1 = "LEDGER1 "                          # a link note's text: LEDGER1 [rows]
CHUNK = 30 * 1024                             # a link request is at most 64 KB, signed and stamped: stay well under
JOB_ID = re.compile(r"T-\d{8}-\d{6}-[0-9a-f]+")
_machine_cache = []


def policy():
    try:
        import yaml
        return yaml.safe_load(POLICY.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - no policy: nothing is read-only, no minutes are guessed
        return {}


def is_readonly(command):
    c = " ".join((command or "").split())
    return bool(c) and any(re.match(p, c, re.I) for p in policy().get("readonly", []))


def is_stop(text):
    """The harness's own deliberate stop (refusals_policy.yaml stops: rotation, budget, checkpoint): it refuses reads on
    purpose, so it is never a read-only false positive and never a defect."""
    t = " ".join((text or "").split())
    return bool(t) and any(re.match(p, t, re.I) for p in policy().get("stops", []))


def machine():
    """This computer's tag ([VAIO-LNX], [L15-WIN]); the host name when the household has none. Cached."""
    if not _machine_cache:
        try:
            from .peers import machine as m
            tag = m.name(save=False)["tag"]
        except Exception:  # noqa: BLE001
            import socket
            tag = socket.gethostname()
        _machine_cache.append(tag)
    return _machine_cache[0]


def row_id(r):
    return hashlib.sha1("|".join(str(r.get(k, "")) for k in ("machine", "t", "key", "text", "session")).encode("utf-8")
                        ).hexdigest()[:12]


def _load(root, name, default):
    try:
        return json.loads((state(root) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(root, name, data):
    d = state(root)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / name)


def defects(root=None):
    """-> {key: defect}. A defect: {key, tool, status open|closed, readonly, blocks, jobs, machines, count, opened, closed,
    test}."""
    return _load(root, DEFECTS, {})


def _counted(root, key):
    """The ledger rows of `key` that count toward a defect: every one but the harness's deliberate stops (is_stop)."""
    return [r for r in _rows(root) if r.get("key") == key and not r.get("stop") and not is_stop(r.get("text"))]


def _evaluate(root, key):
    """Open (or reopen) the defect for `key` when the ledger, both computers together, says so. -> the defect or None."""
    rows = _counted(root, key)
    if not rows:
        return None
    ro = [r for r in rows if r.get("readonly") and r.get("job")]
    if len(rows) < REPEAT and not ro:
        return None
    d = defects(root)
    cur = d.get(key)
    if cur and cur["status"] == "closed" and len(rows) <= int(cur.get("closed_count", 0)):
        return cur                                     # nothing new since the close; a newer row reopens it
    jobs = sorted({r["job"] for r in ro})
    new = {"key": key, "tool": rows[0].get("tool"), "status": "open", "readonly": bool(ro), "blocks": bool(ro),
           "jobs": jobs, "machines": sorted({r.get("machine") or "?" for r in rows}), "count": len(rows),
           "opened": (cur or {}).get("opened") or time.strftime("%Y-%m-%dT%H:%M:%S"), "closed": "", "test": "",
           "last": rows[-1].get("text", "")}
    if cur and cur["status"] == "closed":
        new["reopened"] = int(cur.get("reopened", 0)) + 1
    d[key] = new
    _save(root, DEFECTS, d)
    _open_row(root, key, len(rows), rows[-1].get("text", ""), rows[0].get("tool"))
    return new


def record(tool, text, session=None, root=None, command=None, job=None, machine_tag=None, lost_min=None, readonly=None):
    """Write one refusal; open the defect when its key has now been seen REPEAT times (on either computer), or at once
    when a job was refused a read-only `command`. -> the open-problems row number opened (0 when none). Never raises."""
    try:
        key = key_of(tool, text)
        job = job or (JOB_ID.search(os.environ.get("FIELDKIT_JOB_DIR", "")) or [None])[0]
        row = {"tool": tool, "key": key, "text": " ".join((text or "").split())[:600], "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "session": session or os.environ.get("CLAUDE_SESSION_ID") or str(os.getpid()),
               "machine": machine_tag or machine()}
        if command:
            row["command"] = " ".join(command.split())[:300]
            row["readonly"] = is_readonly(command)
        if readonly:
            row["readonly"] = True
        if is_stop(text):                              # a deliberate stop: kept for overhead, never a defect
            row["readonly"], row["stop"] = False, True
        if job:
            row["job"] = job
        if lost_min is not None:
            row["lost_min"] = lost_min
        row["id"] = row_id(row)
        d = state(root)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        before = _open_rows(root)
        _evaluate(root, key)
        return 0 if key in before else _open_rows(root).get(key, 0)
    except Exception:
        pass
    return 0


def _open_rows(root):
    """-> {key: problem row number} for every repeated-refusal row in the table."""
    p = state(root) / PROBLEMS
    out = {}
    try:
        for x in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\|\s*(\d+)\s*\|.*" + re.escape(MARK) + r"(.*?) \(\d+ times\)", x)
            if m:
                out[m.group(2)] = int(m.group(1))
    except OSError:
        pass
    return out


def blocking_defect(brief_hash, tasks, root=None):
    """-> the open read-only defect that a task with this brief hash hit, else None: the task runner does not start the
    brief again until the cause is known and the defect is closed with a test."""
    for dfx in defects(root).values():
        if dfx.get("status") == "open" and dfx.get("blocks") and not is_stop(dfx.get("last")):   # a stop opened before the fix
            if any((tasks.get(j) or {}).get("hash") == brief_hash for j in dfx.get("jobs", [])):
                return dfx
    return None


def test_exists(test):
    """tests/x.py::test_y -> true when the file defines test_y (a method name is searched the same way)."""
    f, _, name = (test or "").partition("::")
    name = name.split("::")[-1]
    p = Path(f) if Path(f).is_absolute() else ROOT / f
    try:
        return bool(name) and p.suffix == ".py" and re.search(rf"def {re.escape(name)}\b", p.read_text(encoding="utf-8")) is not None
    except OSError:
        return False


def _pytest(test):
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", test], cwd=ROOT, capture_output=True, text=True, timeout=600)
    return r.returncode == 0, (r.stdout or "")[-300:]


def close(key, test, root=None, run=None):
    """Close the defect `key`. A false positive (a read-only refusal) is closed only with a test that exists and PASSES
    now (the read passes); any defect records the test name. -> (ok, why)."""
    d = defects(root)
    cur = d.get(key)
    if not cur:
        return False, f"no defect {key!r}"
    if not test_exists(test):
        return False, f"test {test!r} not found: a defect is closed with a test that exists (file::test_name)"
    if cur.get("readonly"):
        ok, out = (run or _pytest)(test)
        if not ok:
            return False, f"test {test} does not pass: {out}"
    cur.update(status="closed", blocks=False, test=test, closed_count=len(_counted(root, key)), closed=time.strftime("%Y-%m-%dT%H:%M:%S"))
    _save(root, DEFECTS, d)
    p = state(root) / PROBLEMS
    try:
        body = p.read_text(encoding="utf-8").split("\n")
        p.write_text("\n".join(x.replace("| OPEN |", "| DONE |") if f"{MARK}{key} (" in x else x for x in body),
                     encoding="utf-8")
    except OSError:
        pass
    return True, f"closed with {test}"


# -- the cable: LEDGER1 notes over `fieldkit peers link`, deduplicated by row id, each row tagged with its machine ----------
def unsent(root, device):
    sent = set(_load(root, SYNC, {}).get("sent", {}).get(device, []))
    me = machine()
    out = []
    for r in _rows(root):
        if r.get("machine") not in (None, "", me):
            continue                                   # a row that came from the other computer goes no further
        r = dict(r, machine=me)
        r["id"] = r.get("id") or row_id(r)
        if r["id"] not in sent:
            out.append(r)
    return out


def chunks(rows):
    cur = []
    for r in rows:
        if cur and len(LEDGER1 + json.dumps(cur + [r])) > CHUNK:
            yield cur
            cur = []
        cur.append(r)
    if cur:
        yield cur


def push(root=None, call=None, devices=None, timeout=5):
    """Send every row of this computer the far one has not had. -> {device: rows sent}. Never raises."""
    out = {}
    try:
        from .zero import channels as ch
        call = call or ch.link_call()
        devs = devices if devices is not None else ch.Cable().devices()
        sy = _load(root, SYNC, {})
        for dev in devs:
            rows = unsent(root, dev)
            n = 0
            for chunk in chunks(rows):
                r = call({"verb": "message", "text": LEDGER1 + json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))},
                         device=dev, timeout=timeout)
                if not r.get("ok"):
                    break
                sy.setdefault("sent", {}).setdefault(dev, []).extend(x["id"] for x in chunk)
                n += len(chunk)
            out[dev] = n
        if any(out.values()):
            _save(root, SYNC, sy)
    except Exception:  # noqa: BLE001
        pass
    return out


def parse_note(text):
    i = (text or "").find(LEDGER1 + "[")
    if i < 0:
        return []
    try:
        rows = json.loads(text[i + len(LEDGER1):].strip())
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("key") and r.get("machine")] if isinstance(rows, list) else []


def ingest(root=None, items=None):
    """Merge the other computer's rows from the link inbox into this ledger (once, by id). Defects are re-evaluated with
    both computers' rows together. -> number of rows added. Never raises."""
    try:
        sy = _load(root, SYNC, {})
        since = int(sy.get("since") or 0)
        if items is None:
            from .peers import link
            items = link.inbox(0, since)
        have = {r.get("id") or row_id(r) for r in _rows(root)}
        me, added, keys = machine(), 0, set()
        d = state(root)
        d.mkdir(parents=True, exist_ok=True)
        for it in items:
            since = max(since, int(it.get("n") or 0))
            for r in parse_note(it.get("text", "")):
                if r["machine"] == me:
                    continue
                r["id"] = r.get("id") or row_id(r)
                if r["id"] in have:
                    continue
                have.add(r["id"])
                with open(d / LEDGER, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(r, via="cable")) + "\n")
                added += 1
                keys.add(r["key"])
        if since != int(sy.get("since") or 0):
            sy["since"] = since
            _save(root, SYNC, sy)
        for k in keys:
            _evaluate(root, k)
        return added
    except Exception:  # noqa: BLE001
        return 0


def sync(root=None, call=None, devices=None, items=None):
    """Both ways: read what came, send what is new. -> (rows added here, {device: rows sent})."""
    return ingest(root, items), push(root, call, devices)


def sync_quiet(root=None):
    """The room hook's call: never raises, a short timeout, nothing sent when nothing is new."""
    try:
        home = Path(os.environ.get("FIELDKIT_LINK_HOME") or (Path.home() / ".fieldkit" / "link"))
        if not (home / "household.json").is_file() and not (home / "link.json").is_file():
            return                                     # never paired: no cable, and the hook stays fast (no link import)
        ingest(root)
        from .zero import channels as ch
        if any(unsent(root, d) for d in ch.Cable().devices()):
            push(root, timeout=3)
    except Exception:  # noqa: BLE001
        pass


def lost(root=None):
    """-> [{key, tool, count, machines, minutes, hours, defect}] by hours lost, most first. The minutes per refusal are
    an ESTIMATE from refusals_policy.yaml (a row's own lost_min wins), not a measurement."""
    mins = policy().get("lost_minutes", {})
    by = {}
    for r in _rows(root):
        e = by.setdefault(r.get("key"), {"key": r.get("key"), "tool": r.get("tool"), "count": 0, "machines": set(), "minutes": 0.0})
        e["count"] += 1
        e["machines"].add(r.get("machine") or "?")
        e["minutes"] += float(r.get("lost_min") if r.get("lost_min") is not None else mins.get(r.get("tool"), mins.get("default", 5)))
    dfx = defects(root)
    out = []
    for e in by.values():
        out.append(dict(e, machines=sorted(e["machines"]), minutes=round(e["minutes"], 1), hours=round(e["minutes"] / 60, 2),
                        defect=(dfx.get(e["key"]) or {}).get("status", "-")))
    return sorted(out, key=lambda e: -e["minutes"])


def summary(root=None):
    """-> [{key, tool, count, last, row_open}] newest first; row_open reads the defect table."""
    by = {}
    for r in _rows(root):
        e = by.setdefault(r.get("key"), {"key": r.get("key"), "tool": r.get("tool"), "count": 0, "last": "", "machines": []})
        e["count"] += 1
        e["last"] = r.get("text", "")
        if (r.get("machine") or "?") not in e["machines"]:
            e["machines"].append(r.get("machine") or "?")
    p = state(root) / PROBLEMS
    body = p.read_text(encoding="utf-8").split("\n") if p.is_file() else []
    dfx = defects(root)
    for e in by.values():
        e["row_open"] = any(f"{MARK}{e['key']} (" in x and "| OPEN |" in x for x in body)
        e["defect"] = (dfx.get(e["key"]) or {}).get("status", "-")
        e["test"] = (dfx.get(e["key"]) or {}).get("test", "")
    return sorted(by.values(), key=lambda e: -e["count"])


def open_count(root=None):
    return sum(1 for e in summary(root) if e["row_open"])


def hook_text(root=None):
    """The room hook's line; "" when none is open. Never raises."""
    try:
        n = open_count(root)
    except Exception:
        return ""
    return f"{n} repeated refusals open - fieldkit refusals list" if n else ""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit refusals", description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="list: JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list")
    lp.add_argument("--json", action="store_true", dest="json_list")
    rp = sub.add_parser("record")
    rp.add_argument("tool")
    rp.add_argument("text")
    rp.add_argument("--command", help="the refused command (a read-only one opens a defect at once, for a job)")
    sub.add_parser("sync", help="both ways over the cable: take the other computer's rows, send ours")
    cp = sub.add_parser("close", help="close a defect with the test that proves it (file::test_name)")
    cp.add_argument("key")
    cp.add_argument("--test", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "record":
        num = record(a.tool, a.text, command=a.command)
        print(f"recorded; defect row {num} opened" if num else "recorded")
        return 0
    if a.cmd == "sync":
        added, sent = sync()
        print(f"took {added} rows; sent " + (", ".join(f"{n} to {d}" for d, n in sent.items()) or "nothing (no paired computer)"))
        print("NEXT: fieldkit refusals list")
        return 0
    if a.cmd == "close":
        ok, why = close(a.key, a.test)
        print(("CLOSED  " if ok else "NOT CLOSED  ") + why)
        return 0 if ok else 3
    s = summary()
    if a.json or a.json_list:
        print(json.dumps(s, indent=1))
        return 0
    for e in s:
        print(f"{e['count']:>3}x {'OPEN ' if e['row_open'] else '     '}{e['defect']:6} {','.join(e['machines']):10} {e['key']}"
              + (f"  [test {e['test']}]" if e["test"] else ""))
    if not s:
        print("no refusals recorded")
    print("NEXT: " + ("triage each OPEN defect; close it with `fieldkit refusals close KEY --test FILE::TEST` (a false positive needs a test proving the read passes)"
                      if open_count() else "nothing open"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
