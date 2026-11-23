"""Read-only audit of the scratch scope used by Capture's clean gate."""
import json
import os
import socket
import subprocess
from fieldkit import proc as fkproc
import time
from collections import Counter
from pathlib import Path

from . import cleanslate, footprint, retention
from ..desk import size

ROOT_LIMIT = 32
DETAIL_LIMIT = 8
VISIT_LIMIT = 20_000
SCAN_SECONDS = 8
SIZE_SECONDS = 6
SIZE_ARG_LIMIT = 16_000


def _measure(paths):
    exe = size.locate()
    if not exe:
        return {}, "Gorilla Size is unavailable"
    try:
        proc = fkproc.run(
            [str(exe), "--json", "--strict", "--depth", "0", "--number", "0", *paths],
            capture_output=True, encoding="utf-8", timeout=SIZE_SECONDS,
        )
        if proc.returncode not in (0, 3):
            return {}, f"Gorilla Size exited {proc.returncode}"
        data = json.loads(proc.stdout)
        return {retention.normalized(row["path"]): row["totals"] for row in data["roots"]}, None
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        return {}, f"Gorilla Size measurement unavailable ({type(exc).__name__})"


def local():
    started = time.monotonic()
    remaining = VISIT_LIMIT

    def visit():
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or time.monotonic() - started >= SCAN_SECONDS:
            raise OSError("Capture metadata budget exhausted")

    configured = cleanslate.scratch_roots(include_missing=True)
    rows = []
    candidates = []
    protected = cleanslate._protected()
    filtered = cleanslate._temp_item_roots()
    full = []
    for root in sorted(configured[:ROOT_LIMIT], key=lambda p: (len(p.parts), p in filtered, str(p))):
        row = {"path": str(root), "exists": None, "files": None, "bytes": None,
               "cleanup": "unverified", "errors": []}
        rows.append(row)
        try:
            footprint.local_root(root)
            if "*" in root.name:
                retention.check_root(root.parent)
                with os.scandir(root.parent) as children:
                    next(children, None)
                row.update(exists=False, cleanup="missing")
                continue
            retention.check_root(root)
            row["exists"] = True
        except FileNotFoundError:
            row.update(exists=False, cleanup="missing")
            continue
        except (OSError, ValueError) as exc:
            row["errors"].append(str(exc)[:256])
            continue
        if any(retention.within(root, path) for path in protected):
            row["cleanup"] = "protected"
        elif any(retention.within(root, path) for path in full):
            row["cleanup"] = "covered-by-parent"
        else:
            candidates.append(root)
            if root not in filtered:
                full.append(root)

    errors = []
    if len(configured) > ROOT_LIMIT:
        errors.append(f"{len(configured) - ROOT_LIMIT} policy roots exceeded the report limit")
    try:
        dispositions = cleanslate.dispositions()
    except (OSError, ValueError, TypeError) as exc:
        dispositions = {}
        errors.append(f"Capture dispositions unavailable ({type(exc).__name__})")
    try:
        keep = cleanslate.keep_list()
        items = cleanslate.scan_items(candidates, keep=keep, protected=protected, visit=visit)
        classified = cleanslate.check(rows=items, keep=keep, disp=dispositions, check_store=False)
    except (OSError, ValueError, RecursionError) as exc:
        items = []
        classified = {}
        errors.append(f"Capture classification unavailable ({type(exc).__name__})")

    buckets = {}
    for section in ("scripts", "wipeable", "retained", "errors"):
        for item in classified.get(section, []):
            buckets.setdefault(item["root"], []).append((section, item))
    top = [item for item in items if item.get("top") == item["path"]]
    by_root = {}
    for item in top:
        by_root.setdefault(item["root"], []).append(item)
    measure_paths = []
    arg_size = 0
    for item in top:
        if item["reason"] is None and item["kind"] == "folder":
            arg_size += len(item["path"].encode("utf-16-le", "surrogatepass")) // 2 + 3
            if arg_size <= SIZE_ARG_LIMIT:
                measure_paths.append(item["path"])
    measured, size_error = _measure(measure_paths) if measure_paths else ({}, None)
    if size_error:
        errors.append(size_error)

    candidate_names = {str(root) for root in candidates}
    for row in rows:
        path = row["path"]
        if path not in candidate_names:
            continue
        entries = buckets.get(path, [])
        totals = Counter(section for section, _ in entries)
        details = [(section, item) for section, item in entries if section != "errors"]
        row.update(dispositions=dict(totals), items=[
            {"path": item["path"], "status": section, "reason": item.get("reason")}
            for section, item in details[:DETAIL_LIMIT]
        ], items_omitted=max(0, len(details) - DETAIL_LIMIT))
        row["errors"].extend(item["reason"][:256] for section, item in entries[:DETAIL_LIMIT]
                             if section == "errors")
        if totals["errors"] and not row["errors"]:
            row["errors"].append("Capture reported unreadable or unsafe paths")
        samples = by_root.get(path, [])
        row.update(files=sum(item["files"] for item in samples), bytes=sum(item["bytes"] for item in samples),
                   size_probes=0)
        for item in samples:
            result = measured.get(retention.normalized(item["path"]))
            if result is None:
                continue
            row["size_probes"] += 1
            if (result["files"], result["bytes"]) != (item["files"], item["bytes"]) or result["unreadable"]:
                if len(row["errors"]) < DETAIL_LIMIT:
                    row["errors"].append("Gorilla Size did not confirm Capture's metadata")
        omitted = any(item.get("reason") for item in samples)
        row["measurement"] = "observed only" if omitted or row["errors"] else "complete"
        row["cleanup"] = ("unverified" if row["errors"] or not classified or errors else
                          "needs-disposition" if totals["scripts"] else
                          "has-dispositions" if totals["wipeable"] else
                          "retained" if totals["retained"] else "empty")
    return {"schema": 1, "host": socket.gethostname(), "platform": os.sys.platform,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "roots": rows, "errors": errors, "size_calls": int(bool(measure_paths) and not (
                size_error and size_error == "Gorilla Size is unavailable")),
            "unverified": ["live Capture ledger and external references were not checked",
                           "recorded dispositions are subject to the normal Capture cleanup gate"]}


def render(report):
    lines = [f"CLEAN SLATE AUDIT on {report['host']} ({report['elapsed_ms']} ms, {report['size_calls']} Gorilla Size calls)"]
    for row in report["roots"]:
        counts = f"{row['files']} files, {row['bytes']} bytes" if row["files"] is not None else "size unavailable"
        lines.append(f"  {row['cleanup']:<20} {counts:<30} {row['path']}")
        lines.extend(f"    {error}" for error in row["errors"])
    lines.extend(f"  {error}" for error in report["errors"])
    lines.extend(f"  Unverified: {note}" for note in report["unverified"])
    return lines


def partial(report):
    return bool(report["errors"] or any(row["errors"] for row in report["roots"]))


def _ssh_audit(device, previous_host):
    """One noninteractive SSH call to the peer with a fixed remote command."""
    import ipaddress
    import re

    try:
        address = str(ipaddress.ip_address(device["address"][0]))
        user = device.get("ssh_user")
        if user and not re.fullmatch(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*", user):
            raise ValueError("invalid SSH user")
        argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "UpdateHostKeys=no", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
                "-o", "ClearAllForwardings=yes", "-o", "ControlPath=none", "-o", "ProxyJump=none",
                "-o", "ProxyCommand=none"]
        if user:
            argv.extend(["-l", user])
        argv.extend(["--", address, "python3", "-B", "-m", "fieldkit.capture.audit"])
        proc = fkproc.run(argv, input=json.dumps(previous_host) + "\n", capture_output=True,
                              encoding="utf-8", timeout=25)
        if proc.returncode:
            return {"error": (proc.stderr.strip() or f"SSH exited {proc.returncode}")[:256]}
        answer = json.loads(proc.stdout)
        if not isinstance(answer, dict) or not isinstance(answer.get("host"), str):
            raise ValueError("peer returned an invalid audit response")
        return answer
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired) as exc:
        return {"error": f"SSH audit failed ({type(exc).__name__})"}


def remote(device_name, via):
    from ..peers import link

    hosts = []
    routes = {}
    try:
        device = link.device_named(link.household(), device_name)
    except ValueError as exc:
        device = None
        error = str(exc)

    for route in ("cable", "ssh") if via in ("both", "auto") else (via,):
        if route == "ssh" and via == "auto" and hosts:
            continue                                  # the cable answered: ssh is the fallback only
        if device is None:
            routes[route] = {"error": error}
            continue
        previous_host = hosts[0]["host"] if hosts else None
        if route == "cable":
            reply = link.cable_probe(device_name, "clean-slate-audit")
            answer = reply.get("data", {}) if reply.get("ok") else {"error": reply.get("error")}
        else:
            answer = _ssh_audit(device, previous_host)
        if answer.get("schema") == 1 and answer.get("host") and isinstance(answer.get("roots"), list):
            hosts.append(answer)
            routes[route] = {"host": answer["host"], "status": "audited"}
        elif previous_host and answer.get("already_audited") and answer.get("host") == previous_host:
            routes[route] = {"host": previous_host, "status": "already audited"}
        else:
            routes[route] = {"error": answer.get("error") or "peer returned an invalid audit report"}
    return {"schema": 1, "hosts": hosts, "routes": routes}


def _ssh_probe():
    import sys

    previous_host = json.loads(sys.stdin.readline(512))
    if previous_host == socket.gethostname():
        print(json.dumps({"host": previous_host, "already_audited": True}))
    else:
        print(json.dumps(local()))


def cmd(args):
    import sys

    if not args.device:
        if args.via:
            print("capture audit: --via requires --device", file=sys.stderr)
            return 2
        report = local()
        print(json.dumps(report) if args.json else "\n".join(render(report)))
        return 3 if partial(report) else 0

    via = args.via or "auto"                          # cable first, ssh only when the cable fails (owner 2026-11-23)
    if via not in ("cable", "ssh", "both", "auto"):
        print("capture audit: --via is one of auto, cable, ssh, both", file=sys.stderr)
        return 2
    report = remote(args.device, via)
    if args.json:
        print(json.dumps(report))
    else:
        for route, result in report["routes"].items():
            print(f"{route}: {result.get('error') or result['status']}")
        for host in report["hosts"]:
            print("\n".join(render(host)))
    return 3 if any("error" in r for r in report["routes"].values()) or any(map(partial, report["hosts"])) else 0


if __name__ == "__main__":
    _ssh_probe()
