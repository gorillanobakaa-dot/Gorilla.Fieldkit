"""I8: a review fix becomes a triage signature, so the next similar failure needs no reviewer.

    fieldkit triage-learn --set SET --id ID --log-line LINE --pattern REGEX --cause TEXT --fix TEXT [--repair CMD ...]
    fieldkit triage-learn --audit [--set SET]

# WHY THIS EXISTS

docs/HARNESS-PLAN.md I8 and rule 5 ("the same failure is never diagnosed twice"): build-log triage already turns a
diagnosed failure into data (build/signatures/<set>.yaml), but a fix found in a review (a reviewer model or a person
reading a failed job) stayed in the review. `learn` records that fix as a signature, and refuses to record one that
cannot do its job:

  - the failing log line must be selected by the new signature (the line is stored in the entry as `log_line`);
  - the same line must not already be known under another id (that failure was diagnosed before: use that entry);
  - the id must be new in every set; the pattern must compile; cause and fix must say something.

`--repair CMD ...` stores the deterministic fix as an argv list. A signature with a `repair` is one the harness fixes
alone; one without is advice, and the failure still goes to a reviewer. `needs_reviewer(log)` says which, so a job
can check that the next similar failure was repaired without one. `audit` replays every stored `log_line` against its
own signature (a pattern edited later that stops selecting its line is a failure).
"""
import re
from datetime import date

from . import triage


def _slug_ok(sig_id):
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{2,60}", sig_id or ""))


def _entry(sig_id, pattern, cause, fix, log_line, check, repair, whole_log, today):
    q = lambda s: "'" + s.replace("'", "''") + "'"
    lines = [f"- id: {sig_id}", f"  pattern: {q(pattern)}", f"  check: {q(check) if check else 'null'}",
             "  source: review", f"  learned: {today}", f"  log_line: {q(log_line)}"]
    if whole_log:
        lines.append("  whole_log: true")
    if repair:
        lines.append("  repair: [" + ", ".join(q(a) for a in repair) + "]")
    lines += ["  cause: >-", "    " + " ".join(cause.split()), "  fix: >-", "    " + " ".join(fix.split())]
    return "\n".join(lines) + "\n"


def learn(set_name, sig_id, pattern, cause, fix, log_line, check=None, repair=None, today=None):
    """-> {"ok", "why" | "id", "set", "file"}. Appends to build/signatures/<set>.yaml only when every rule holds."""
    path = triage.SIG_DIR / f"{set_name}.yaml"
    if not path.is_file():
        return {"ok": False, "why": f"no signature set {set_name!r}; have {triage.available_sets()}"}
    if not _slug_ok(sig_id):
        return {"ok": False, "why": f"id {sig_id!r}: lowercase letters, digits and dashes, 3 to 61 characters"}
    if len((cause or "").split()) < 3 or len((fix or "").split()) < 3:
        return {"ok": False, "why": "cause and fix must each say what happened and what to do (3 words or more)"}
    if repair is not None and (not isinstance(repair, (list, tuple)) or not repair or not all(isinstance(a, str) for a in repair)):
        return {"ok": False, "why": "repair is an argv list of strings (the program and its arguments)"}
    log_line = (log_line or "").strip()
    if not log_line:
        return {"ok": False, "why": "give the failing log line the signature must select (--log-line)"}
    try:
        rx = re.compile(pattern, re.I | re.M)
    except re.error as e:
        return {"ok": False, "why": f"pattern does not compile: {e}"}
    if not rx.search(log_line):
        return {"ok": False, "why": "the pattern does not match the log line: it would not select the failure it is for"}
    if sig_id in {s["id"] for s in triage.load_signatures("auto")}:
        return {"ok": False, "why": f"id {sig_id!r} already exists in a signature set"}
    before = triage.triage_text(log_line, "auto")
    if before["verdict"] == "known":
        m = before["matches"][0]
        return {"ok": False, "why": f"already known as {m['id']} ({m['set']}): that failure was diagnosed before; "
                                    f"edit that entry instead of adding a second"}
    whole = not triage.extract_errors(log_line)
    old = path.read_text(encoding="utf-8")
    new = old.rstrip("\n") + "\n\n" + _entry(sig_id, pattern, cause, fix, log_line, check, repair, whole,
                                              today or date.today().isoformat())
    path.write_text(new, encoding="utf-8")
    try:
        after = triage.triage_text(log_line, set_name)
        if not any(m["id"] == sig_id for m in after["matches"]):
            raise ValueError("the new signature does not select its own log line")
        triage.load_signatures(set_name)
    except Exception as e:  # noqa: BLE001 - put the file back exactly as it was
        path.write_text(old, encoding="utf-8")
        return {"ok": False, "why": f"not recorded: {e}"}
    return {"ok": True, "id": sig_id, "set": set_name, "file": str(path), "repair": bool(repair)}


def needs_reviewer(log_text, set_name="auto"):
    """-> {"needs_reviewer": bool, "why", "id", "repair"}. False only when a known signature carries a repair."""
    res = triage.triage_text(log_text, set_name)
    if res["verdict"] == "success":
        return {"needs_reviewer": False, "why": "the log shows success", "id": None, "repair": None}
    if res["verdict"] == "known":
        sigs = {s["id"]: s for s in triage.load_signatures(set_name)}
        for m in res["matches"]:
            r = sigs[m["id"]].get("repair")
            if r:
                return {"needs_reviewer": False, "why": f"known as {m['id']} with a stored repair", "id": m["id"],
                        "repair": list(r)}
        return {"needs_reviewer": True, "why": f"known as {res['matches'][0]['id']} but it has no stored repair "
                                               f"(advice only)", "id": res["matches"][0]["id"], "repair": None}
    return {"needs_reviewer": True, "why": f"verdict {res['verdict']}: no signature selects it", "id": None,
            "repair": None}


def audit(set_name="auto"):
    """-> [{"id", "set", "ok", "why"}] for every signature that stores a log_line: it must select that line."""
    rows = []
    for s in triage.load_signatures(set_name):
        line = s.get("log_line")
        if not line:
            continue
        got = triage.triage_text(line, s["set"])
        ok = any(m["id"] == s["id"] for m in got["matches"])
        rows.append({"id": s["id"], "set": s["set"], "ok": ok,
                     "why": "selects its log line" if ok else f"no longer selects its log line (verdict {got['verdict']})"})
    return rows
