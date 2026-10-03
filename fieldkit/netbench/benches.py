"""B1-B5 (study section "Benches and harness checks"), each run `repeat` times per link profile.

B1  bytes and requests of the article page set, and its load times: cold (fresh profile), warm (the same page again
    in the same session) and restart (a new browser process on the same profile: what a cache keeps over a restart).
B2  download and upload throughput over HTTP/2 (www host) and HTTP/1.1 (h1 host), fixed sizes per link.
B3  HTTP/3 buffer check: an HTTP/3 connection to a local QUIC server with MOZ_LOG=nsHttp:5,UDPSocket:5, and the log
    searched for the "SetRecvBufferSize failed" / "SetSendBufferSize failed" lines (F8). Link-independent.
B4  RAM: working set and private bytes of every browser process during a fixed workload (the article, then a 30 s
    download into a page that reads slowly, F3).
B5  keepalive: MOZ_LOG=nsHttp:5,nsSocketTransport:5 while one HTTP/1.1 connection idles 90 s in the pool and a
    second one stays busy for 85 s (a response of one byte a second); the SetKeepaliveVals values, the switch to
    long-lived keepalive (Firefox makes it only for a busy connection, never for an idle pooled one), and whether
    the idle connection is reused after the idle (F5, W1).

All times are taken by the harness clock when the page's message reaches the unshaped report host, so they do not
depend on the browser's timer precision (resistFingerprinting coarsens it).
"""
import re
import statistics
import time
from pathlib import Path

from . import browser, links

WWW = "https://www.netbench.test"
H1 = "https://h1.netbench.test"
REPORT = "https://report.netbench.test"
SHAPED = ("www.netbench.test", "h1.netbench.test", "h1b.netbench.test")
BENCHES = ("B1", "B2", "B3", "B4", "B5")
B4_BLOB = 256 * 1024 * 1024
B4_SECONDS = 30
B4_GAP_MS = 250
B5_IDLE = 90                     # the switch to long-lived keepalive comes at about 72 s
B5_BUSY = 85                     # seconds a second HTTP/1.1 connection stays busy (one byte a second)
SETTLE = 5                       # seconds the launcher waits before the first step (start-up work settles)


class Unmeasured(Exception):
    pass


def summary(values, unit, reps, reasons=None):
    """Fail closed: MEASURED only when every repetition gave a number."""
    vals = [v for v in values if v is not None]
    if len(values) == reps and len(vals) == reps and reps > 0:
        return {"status": "MEASURED", "unit": unit, "median": statistics.median(vals), "min": min(vals),
                "max": max(vals), "spread": max(vals) - min(vals), "values": vals}
    why = [r for r in (reasons or []) if r] or [f"{reps - len(vals)} of {reps} repetition(s) gave no value"]
    return {"status": "UNMEASURED", "unit": unit, "why": "; ".join(dict.fromkeys(why))[:600], "partial": vals}


def collect(rows, keys, reps):
    """rows: one dict per repetition ({metric: value} or {"error": why}) -> {metric: summary}."""
    out = {}
    reasons = [r.get("error") for r in rows]
    for key, unit in keys:
        vals = [r.get(key) if "error" not in r else None for r in rows]
        why = list(reasons) + [r.get(f"{key}_why") for r in rows]
        out[key] = summary(vals, unit, reps, why)
    return out


class Bench:
    """Runs the benches on one lab + one install copy. `lab` is a started servers.Lab."""

    def __init__(self, lab, exe, mode_prefs, say=print):
        self.lab = lab
        self.exe = exe
        self.mode_prefs = mode_prefs
        self.say = say
        self.n = 0

    # --- plumbing ------------------------------------------------------------------------------------------------
    def prefs(self, extra=None):
        p = dict(browser.HARNESS_PREFS)
        p["network.proxy.http_port"] = self.lab.ports["relay"]
        p["network.proxy.ssl_port"] = self.lab.ports["relay"]
        p.update(self.mode_prefs)
        p.update(extra or {})
        return p

    def rid(self, tag):
        self.n += 1
        return f"{tag}-{self.n}-{int(time.time())}"

    def visit(self, rid, steps, profile, timeout, env=None, grace=1.0, sample=False):
        """Launch the browser on the first step and wait until the last step asks for `next`. -> (run, browser, ok)."""
        st = self.lab.origin.new_run(rid, steps)
        b = browser.Browser(self.exe, profile, f"{REPORT}/go?run={rid}&step={steps[0][0]}&wait={SETTLE}", env_extra=env)
        if sample:
            b.start_sampling()
        end = time.perf_counter() + timeout + SETTLE
        ok = False
        while time.perf_counter() < end:
            if st.done.wait(1.0):
                ok = True
                break
            if not b.alive():
                break
        if ok:
            time.sleep(grace)
        st.final = self.lab.snap()
        b.stop()
        return st, b, ok

    @staticmethod
    def _why(st, ok, b, timeout):
        if ok:
            return None
        errs = [m["data"].get("e") for m in st.marks if m["ev"] == "error"]
        if errs:
            return f"page error: {errs[0]}"
        if not b.alive() and time.perf_counter() - b.started < timeout - 2:
            seen = sorted({(m["step"], m["ev"]) for m in st.marks})
            return f"the browser exited early (marks seen: {seen[:6]})"
        seen = sorted({(m["step"], m["ev"]) for m in st.marks})
        return f"timed out after {timeout:.0f} s (marks seen: {seen[:8]})"

    def step_metrics(self, st, step):
        s, d, l = st.mark(step, "start"), st.mark(step, "dcl"), st.mark(step, "load")
        if not s or not l:
            raise Unmeasured(f"step {step}: no {'start' if not s else 'load'} mark")
        # bytes and requests: the whole step, from its start to the next step's start (or the end of the visit), so
        # a request that does not hold up the load event (the favicon) is counted the same way every run
        names = [n for n, _ in st.steps]
        nxt = st.mark(names[names.index(step) + 1], "start") if names.index(step) + 1 < len(names) else None
        end = nxt["snap"] if nxt else (st.final or l["snap"])
        reqs = [r for r in self.lab.origin.requests[s["snap"]["requests"]:end["requests"]] if r["role"] in ("www", "h1")]
        rel0, rel1 = s["snap"]["relay"], end["relay"]

        def delta(key):
            return sum(rel1.get(h, {}).get(key, 0) - rel0.get(h, {}).get(key, 0) for h in SHAPED)
        paint = (l["data"] or {}).get("paint") or {}
        fcp = paint.get("first-contentful-paint")
        return {
            "load_s": l["t"] - s["t"],
            "dcl_s": (d["t"] - s["t"]) if d else None,
            "dcl_s_why": None if d else "no DOMContentLoaded mark",
            "fcp_ms": fcp if isinstance(fcp, (int, float)) and fcp > 0 else None,
            "fcp_ms_why": None if isinstance(fcp, (int, float)) and fcp > 0 else "the browser exposed no first-contentful-paint (headless, or resistFingerprinting)",
            "requests": len(reqs),
            "body_bytes": sum(r["resp_body"] for r in reqs),
            "wire_down_bytes": delta("down"),
            "wire_up_bytes": delta("up"),
            "connections": delta("tunnels"),
            "paths": sorted(r["path"] for r in reqs),
        }

    def timeout_for(self, link, payload_bytes, extra=60):
        if link is None:
            return extra + 60
        return extra + 3 * payload_bytes * 8 / link.down_bps + 40 * link.rtt

    # --- B1 ------------------------------------------------------------------------------------------------------
    def b1(self, link, article_bytes):
        prof = browser.new_profile(self.prefs())
        out = {}
        try:
            to = self.timeout_for(link, article_bytes, 90)
            rid = self.rid("b1a")
            settle = settle_for(link)
            art = lambda r, s: f"{WWW}/wiki/Gorilla?run={r}&step={s}&settle={settle}"   # noqa: E731
            st, b, ok = self.visit(rid, [("cold", art(rid, "cold")), ("warm", art(rid, "warm"))], prof, to, grace=3.0)
            why = self._why(st, ok, b, to)
            for step in ("cold", "warm"):
                try:
                    out[step] = self.step_metrics(st, step)
                except Unmeasured as e:
                    out[step] = {"error": why or str(e)}
            rid = self.rid("b1b")
            st, b, ok = self.visit(rid, [("restart", art(rid, "restart"))], prof, to, grace=1.0)
            why = self._why(st, ok, b, to)
            try:
                out["restart"] = self.step_metrics(st, "restart")
            except Unmeasured as e:
                out["restart"] = {"error": why or str(e)}
        finally:
            browser.discard(prof)
        return out

    # --- B2 ------------------------------------------------------------------------------------------------------
    def b2(self, link):
        down = links.transfer_size(link.down_bps if link else 400e6)
        up = links.transfer_size(link.up_bps if link else 400e6, hi=32 * 1024 * 1024)
        prof = browser.new_profile(self.prefs())
        out = {}
        try:
            to = 90 + (3 * (down * 8 / link.down_bps + up * 8 / link.up_bps) + 60 * link.rtt if link else 60)
            rid = self.rid("b2")
            url = lambda base, s: f"{base}/b2.html?run={rid}&step={s}&down={down}&up={up}"   # noqa: E731
            st, b, ok = self.visit(rid, [("h2", url(WWW, "h2")), ("h1", url(H1, "h1"))], prof, to)
            why = self._why(st, ok, b, to)
            for step in ("h2", "h1"):
                m = {k: st.mark(step, k) for k in ("dl-begin", "dl-done", "ul-begin", "ul-done")}
                if not all(m.values()):
                    out[step] = {"error": why or f"{step}: marks missing {[k for k, v in m.items() if not v]}"}
                    continue
                got_down = (m["dl-done"]["data"] or {}).get("bytes")
                got_up = (m["ul-done"]["data"] or {}).get("bytes")
                r = {"down_bytes": down, "up_bytes": up}
                r["down_mbps"] = down * 8 / (m["dl-done"]["t"] - m["dl-begin"]["t"]) / 1e6 if got_down == down else None
                r["down_mbps_why"] = None if got_down == down else f"the page read {got_down} of {down} bytes"
                r["up_mbps"] = up * 8 / (m["ul-done"]["t"] - m["ul-begin"]["t"]) / 1e6 if got_up == up else None
                r["up_mbps_why"] = None if got_up == up else f"the origin received {got_up} of {up} bytes"
                r["origin_up_mbps"] = (m["ul-done"]["data"] or {}).get("origin_mbps") if got_up == up else None
                r["origin_up_mbps_why"] = r["up_mbps_why"]
                if link:
                    r["down_ratio"] = r["down_mbps"] / (link.down_bps / 1e6) if r["down_mbps"] else None
                    r["up_ratio"] = r["up_mbps"] / (link.up_bps / 1e6) if r["up_mbps"] else None
                protos = {q["proto"] for q in self.lab.origin.requests
                          if q["path"] in ("/sink", f"/blob/{down}") and f"{step}" in q["target"]}
                r["protocols_seen"] = sorted(protos)
                out[step] = r
        finally:
            browser.discard(prof)
        return out

    # --- B4 ------------------------------------------------------------------------------------------------------
    def b4(self, link, article_bytes):
        prof = browser.new_profile(self.prefs())
        try:
            to = self.timeout_for(link, article_bytes, 90) + B4_SECONDS + 30
            rid = self.rid("b4")
            steps = [("article", f"{WWW}/wiki/Gorilla?run={rid}&step=article&settle={settle_for(link)}"),
                     ("workload", f"{WWW}/b4.html?run={rid}&step=workload&size={B4_BLOB}&secs={B4_SECONDS}&gap={B4_GAP_MS}")]
            st, b, ok = self.visit(rid, steps, prof, to, grace=1.0, sample=True)
            why = self._why(st, ok, b, to)
            la, wb, we = st.mark("article", "load"), st.mark("workload", "wl-begin"), st.mark("workload", "wl-end")
            if not (la and wb and we):
                return {"error": why or "marks missing (article load / workload begin / workload end)"}
            return memory_metrics(b.samples, la["t"], wb["t"], we["t"], (we["data"] or {}).get("bytes"))
        finally:
            browser.discard(prof)

    # --- B5 ------------------------------------------------------------------------------------------------------
    def b5(self, link):
        prof = browser.new_profile(self.prefs())
        try:
            env = {"MOZ_LOG": "timestamp,nsHttp:5,nsSocketTransport:5", "MOZ_LOG_FILE": str(Path(prof) / "nb.log")}
            to = 90 + B5_IDLE + (20 * link.rtt if link else 0) + (60 if link and link.down_bps < 1e6 else 0)
            rid = self.rid("b5")
            st, b, ok = self.visit(rid, [("idle", f"{H1}/b5.html?run={rid}&step=idle&idle={B5_IDLE}&busy={B5_BUSY}")], prof, to, env=env)
            why = self._why(st, ok, b, to)
            text = read_logs(prof, "nb.log*")
            r = keepalive_metrics(text)
            lm, rm = st.mark("idle", "pre-reuse"), st.mark("idle", "reuse")   # just before / after the fetch
            if lm and rm:
                t0 = lm["snap"]["relay"].get("h1.netbench.test", {}).get("tunnels", 0)
                t1 = rm["snap"]["relay"].get("h1.netbench.test", {}).get("tunnels", 0)
                r["new_connections_after_idle"] = t1 - t0
                r["h1_connections"] = t1
            else:
                r["new_connections_after_idle"] = None
                r["new_connections_after_idle_why"] = why or "no pre-reuse/reuse mark"
                r["h1_connections"] = None
                r["h1_connections_why"] = r["new_connections_after_idle_why"]
            if not text:
                r["error"] = why or "no MOZ_LOG file was written"
            return r
        finally:
            browser.discard(prof)

    # --- B3 ------------------------------------------------------------------------------------------------------
    def b3(self):
        if self.lab.h3_error or not self.lab.ports.get("h3"):
            return {"error": f"the local HTTP/3 server could not start: {self.lab.h3_error}"}
        port = self.lab.ports["h3"]
        extra = {"network.proxy.no_proxies_on": "h3.netbench.test",
                 "network.http.http3.disable_when_third_party_roots_found": False,
                 "network.http.http3.alt-svc-mapping-for-testing": f"h3.netbench.test;h3=:{port}"}
        prof = browser.new_profile(self.prefs(extra))
        try:
            env = {"MOZ_LOG": "timestamp,nsHttp:5,UDPSocket:5", "MOZ_LOG_FILE": str(Path(prof) / "nb.log")}
            rid = self.rid("b3")
            before = len(self.lab.origin.requests)
            st, b, ok = self.visit(rid, [("h3", f"https://h3.netbench.test:{port}/b3.html?run={rid}&step=h3")], prof, 120,
                                   env=env, grace=2.0)
            why = self._why(st, ok, b, 120)
            text = read_logs(prof, "nb.log*")
            reqs = self.lab.origin.requests[before:]
            r = h3_metrics(text, sum(1 for q in reqs if q["role"] == "h3"), sum(1 for q in reqs if q["role"] == "h3tcp"))
            if not ok and not r.get("h3_requests"):
                r["error"] = why
            return r
        finally:
            browser.discard(prof)


def settle_for(link):
    """Seconds the article waits after its load event: enough for a straggler request (favicon) on this link."""
    return round(max(1.0, 4 * link.rtt + 2), 1) if link else 1.0


def read_logs(prof, pattern):
    return "".join(f.read_text(encoding="utf-8", errors="replace") for f in sorted(Path(prof).glob(pattern)) if f.is_file())


KA_SHORT = re.compile(r"StartShortLivedTCPKeepalives\[(\w+)\] idle time\[(\d+)s\]")
KA_LONG = re.compile(r"StartLongLivedTCPKeepalives\[(\w+)\] idle time\[(\d+)s\]")
KA_VALS = re.compile(r"nsSocketTransport::SetKeepaliveVals \[(\w+)\] keepalive (enabled|disabled), idle time\[(\d+)s\] "
                     r"retry interval\[(\d+)s\] packet count\[(-?\d+)\]")
KA_FAIL = re.compile(r"SetKeepaliveVals failed|Failed setting TCP_KEEP|StartShortLivedTCPKeepalives failed|"
                     r"StartLongLivedTCPKeepalives failed")


def keepalive_metrics(text):
    """The keepalive values the browser set on its HTTP/1.1 connections, from its own log. The retry interval and
    probe count are read from the socket lines that carry the short-lived idle time (the HTTP/1.1 connections)."""
    short = [int(m.group(2)) for m in KA_SHORT.finditer(text)]
    long_ = [int(m.group(2)) for m in KA_LONG.finditer(text)]
    allv = [(m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5))) for m in KA_VALS.finditer(text)]
    vals = [v for v in allv if short and v[1] == short[0]] or ([] if short else allv)
    r = {"short_lived_idle_s": short[0] if short else None,
         "short_lived_idle_s_why": None if short else "no StartShortLivedTCPKeepalives line in the log",
         "long_lived_idle_s": long_[0] if long_ else None,
         "long_lived_idle_s_why": None if long_ else "no StartLongLivedTCPKeepalives line (the busy connection never switched)",
         "retry_interval_s": vals[0][2] if vals else None,
         "retry_interval_s_why": None if vals else "no nsSocketTransport::SetKeepaliveVals line in the log",
         "probe_count": vals[0][3] if vals else None,
         "probe_count_why": None if vals else "no nsSocketTransport::SetKeepaliveVals line in the log",
         "keepalive_failures": len(KA_FAIL.findall(text)),
         "idle_values_seen": sorted({v[1] for v in allv})}
    return r


H3_RECV_FAIL = "SetRecvBufferSize failed"
H3_SEND_FAIL = "SetSendBufferSize failed"
UDP_OPT_FAIL = re.compile(r"nsUDPSocket::SetSocketOption \[this=\w+\] failed for type (\d+)")
H3_CONN = re.compile(r"HttpConnectionUDP::Init|Http3Session::Init|Http3Session::Http3Session")


def h3_metrics(text, h3_requests, tcp_requests):
    conns = len(H3_CONN.findall(text))
    recv_fail = text.count(H3_RECV_FAIL)
    send_fail = text.count(H3_SEND_FAIL)
    r = {"h3_requests": h3_requests, "tcp_requests": tcp_requests, "h3_connection_log_lines": conns,
         "recv_buffer_failures": recv_fail, "send_buffer_failures": send_fail,
         "udp_setsockopt_failures": len(UDP_OPT_FAIL.findall(text)), "log_bytes": len(text)}
    if not text:
        r["error"] = "no MOZ_LOG file was written"
    elif not h3_requests:
        r["error"] = ("no request reached the local HTTP/3 server, so the buffer calls never ran "
                      f"({tcp_requests} request(s) over TCP; {conns} HTTP/3 connection line(s) in the log)")
    # 1 = the 64 MB receive / 4 MB send buffer call succeeded (no failure line after an HTTP/3 connection)
    proven = bool(h3_requests and conns)        # an HTTP/3 connection was set up AND the log covers that code
    r["recv_buffer_granted"] = (1 if recv_fail == 0 else 0) if proven else None
    r["send_buffer_granted"] = (1 if send_fail == 0 else 0) if proven else None
    if h3_requests and not conns:
        r["recv_buffer_granted_why"] = r["send_buffer_granted_why"] = (
            "HTTP/3 requests arrived but the log shows no HTTP/3 connection set-up, so a missing failure line proves nothing")
    return r


def memory_metrics(samples, t_article_load, t_begin, t_end, page_bytes):
    """Per-sample totals over all browser processes -> after-article, peak during the workload, growth."""
    if not samples:
        return {"error": "no memory samples were taken"}

    def total(row, idx, roles=None):
        return sum(v[idx] for v in row.values() if roles is None or v[0] in roles) / 2**20

    def at(t):
        best = min(samples, key=lambda s: abs(s[0] - t))
        return best[1] if abs(best[0] - t) < 2.0 else None
    base = at(t_begin)
    after = at(t_article_load)
    during = [row for t, row in samples if t_begin <= t <= t_end + 0.5]
    if not during or base is None or after is None:
        return {"error": "no memory sample inside the workload window"}
    peak = lambda idx, roles=None: max(total(r, idx, roles) for r in during)   # noqa: E731
    sock = any(v[0] == "socket" for r in during for v in r.values())
    return {
        "after_article_private_mb": total(after, 2), "after_article_ws_mb": total(after, 1),
        "peak_private_mb": peak(2), "peak_ws_mb": peak(1),
        "growth_private_mb": peak(2) - total(base, 2),
        "peak_parent_private_mb": peak(2, {"parent"}),
        "peak_socket_private_mb": peak(2, {"socket"}) if sock else None,
        "peak_socket_private_mb_why": None if sock else "no socket process ran (networking stayed in the parent)",
        "peak_tab_private_mb": peak(2, {"tab"}),
        "processes": max(len(r) for r in during),
        "page_read_bytes": page_bytes,
    }
