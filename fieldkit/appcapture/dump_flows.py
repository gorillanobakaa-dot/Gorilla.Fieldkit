"""Runs under the MITMPROXY interpreter (python -I dump_flows.py FLOWS [MAXBODY]): flows -> JSON lines on stdout.
Raw on purpose, one pipe to the parent; the parent (appcapture.show) redacts before anything is printed."""
import json
import sys

from mitmproxy import io

limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
with open(sys.argv[1], "rb") as fh:
    for f in io.FlowReader(fh).stream():
        r = getattr(f, "request", None)
        if r is None:
            continue
        s = f.response

        def body(m):
            try:
                t = m.get_text(strict=False) or ""
            except Exception:
                t = "<undecodable %d bytes>" % len(m.raw_content or b"")
            return t[:limit]
        print(json.dumps({
            "method": r.method, "host": r.host, "path": r.path, "ts": r.timestamp_start,
            "req_headers": dict(r.headers), "req_body": body(r), "req_len": len(r.raw_content or b""),
            "status": s.status_code if s else None,
            "resp_headers": dict(s.headers) if s else {}, "resp_body": body(s) if s else "",
        }))
