"""TPFanControl.ini: switch the `Active=` mode safely, and verify TPFanControl.exe against a pinned SHA-256.

Kiro's version had a "pin table" with an invalid 55-character hash and never compared it. Here no pin is invented:
the caller gives the pin (`--exe FILE --sha256 HEX64`, e.g. from the build's SHA256SUMS), a wrong or malformed
one fails. The ini is edited as bytes (the file's own code page and line endings stay), only when it has exactly one
`Active=N` line and, with --require-current, that line holds the value you expect. Nothing is written without --apply.
Restarting the service afterwards is the fan-control kit's job (skill fieldkit-fan-control-test).

    fieldkit repair tpfan-config-verify INI [--set-active N] [--require-current N] [--exe FILE --sha256 HEX] [--apply] [--json]
Exit: 0 verified (and switched with --apply), 1 error, 3 refused or would change (without --apply).
"""
import argparse
import hashlib
import re
from pathlib import Path

from ._common import emit, write_bytes

ACTIVE = re.compile(rb"(?m)^Active=(\d+)(\r?)$")


def current_active(ini):
    found = ACTIVE.findall(ini)
    if len(found) != 1:
        raise ValueError(f"the ini has {len(found)} 'Active=N' lines, expected exactly one")
    return int(found[0][0])


def switch_active(ini, target, require_current=None):
    cur = current_active(ini)
    if require_current is not None and cur != require_current:
        raise ValueError(f"Active={cur}, not the expected Active={require_current}; not touching it")
    return ACTIVE.sub(lambda m: b"Active=" + str(target).encode() + m.group(2), ini)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair tpfan-config-verify", description=__doc__.split("\n")[0])
    ap.add_argument("ini")
    ap.add_argument("--set-active", type=int)
    ap.add_argument("--require-current", type=int)
    ap.add_argument("--exe")
    ap.add_argument("--sha256")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if bool(a.exe) != bool(a.sha256):
        emit(a.json, "FAIL", "--exe and --sha256 go together", "give both, or neither")
        return 1
    ini_p = Path(a.ini)
    if not ini_p.is_file():
        emit(a.json, "FAIL", f"ini not found: {ini_p}", "pass the TPFanControl.ini path")
        return 1
    data = {}
    if a.exe:
        want = a.sha256.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", want):
            emit(a.json, "FAIL", "--sha256 is not 64 hex digits", "copy the line from SHA256SUMS")
            return 1
        try:
            got = sha256_file(a.exe)
        except OSError as e:
            emit(a.json, "FAIL", f"cannot read {a.exe}: {e}", "pass the TPFanControl.exe path")
            return 1
        data["exe_sha256"] = got
        if got != want:
            emit(a.json, "FAIL", f"{Path(a.exe).name} hash {got[:12]}... differs from the pin {want[:12]}...", "do not use this binary", data)
            return 3
    raw = ini_p.read_bytes()
    try:
        cur = current_active(raw)
        data["active"] = cur
        if a.set_active is None:
            emit(a.json, "PASS", f"{ini_p.name} has Active={cur}" + (" and the exe matches its pin" if a.exe else ""), "none", data)
            return 0
        new = switch_active(raw, a.set_active, a.require_current)
    except ValueError as e:
        emit(a.json, "FAIL", str(e), "look at the Active= line in the ini", data)
        return 3
    if new == raw:
        emit(a.json, "PASS", f"{ini_p.name} already has Active={a.set_active}", "none", data)
        return 0
    if not a.apply:
        emit(a.json, "FAIL", f"{ini_p.name} would change Active={cur} to Active={a.set_active}", "add --apply", data)
        return 3
    write_bytes(ini_p, new)
    emit(a.json, "PASS", f"{ini_p.name} now has Active={a.set_active}", "restart TPFanControl (skill fieldkit-fan-control-test)", data)
    return 0
