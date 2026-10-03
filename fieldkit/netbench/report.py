"""Run the selected benches, write the result (JSON for machines, YAML summary for people), print it, compare two.

Result files go to the bench folder as netbench-<label>-<YYYYmmdd-HHMMSS>.json and .yaml: a new pair per run,
never an edit of an older one. Every metric is {status: MEASURED, median, min, max, spread, values} or
{status: UNMEASURED, why}; a number is only ever printed for MEASURED.
"""
import dataclasses
import json
import time
from pathlib import Path

import yaml

from . import benches, browser, certs, fixtures, links, relay, servers

SCHEMA = "fieldkit.netbench/1"

# metric -> unit, per bench group (the order is the print order)
B1_KEYS = [("load_s", "s"), ("dcl_s", "s"), ("fcp_ms", "ms"), ("requests", "count"), ("body_bytes", "bytes"),
           ("wire_down_bytes", "bytes"), ("wire_up_bytes", "bytes"), ("connections", "count")]
B2_KEYS = [("down_mbps", "Mbit/s"), ("up_mbps", "Mbit/s"), ("origin_up_mbps", "Mbit/s"), ("down_ratio", "x nominal"),
           ("up_ratio", "x nominal")]
B3_KEYS = [("h3_requests", "count"), ("recv_buffer_granted", "1=yes"), ("send_buffer_granted", "1=yes"),
           ("recv_buffer_failures", "count"), ("send_buffer_failures", "count"), ("udp_setsockopt_failures", "count")]
B4_KEYS = [("after_article_private_mb", "MiB"), ("after_article_ws_mb", "MiB"), ("peak_private_mb", "MiB"),
           ("peak_ws_mb", "MiB"), ("growth_private_mb", "MiB"), ("peak_parent_private_mb", "MiB"),
           ("peak_socket_private_mb", "MiB"), ("peak_tab_private_mb", "MiB"), ("processes", "count")]
B5_KEYS = [("short_lived_idle_s", "s"), ("long_lived_idle_s", "s"), ("retry_interval_s", "s"), ("probe_count", "count"),
           ("keepalive_failures", "count"),
           ("new_connections_after_idle", "count")]

TITLES = {
    "B1": "Bytes, requests and load time of the article page set (cold, warm, after a restart)",
    "B2": "Download and upload throughput, HTTP/2 and HTTP/1.1",
    "B3": "HTTP/3 (QUIC) socket buffers granted on this system (F8)",
    "B4": "RAM of the browser processes during a fixed workload (F3, F7)",
    "B5": "TCP keepalive values on HTTP/1.1 connections and reuse after 90 s idle (F5, W1)",
}

LIMITATIONS = [
    "The link is emulated by a user-space relay. The browser's own TCP socket ends at the relay on loopback, so "
    "effects that need the browser's socket buffers to meet a long round trip (the F2 HTTP/2 send-buffer cap) are not "
    "reproduced; that needs kernel-level delay (study B6).",
    "Loss is a one-round-trip retransmission stall per lost segment with in-order delivery; TCP's congestion-window "
    "cut is not modelled, so throughput under loss is optimistic compared with real TCP without a PEP.",
    "HTTP/3 is not used through an HTTP proxy, so B1, B2, B4 and B5 run over HTTP/2 and HTTP/1.1 only; B3 talks to "
    "a local QUIC server directly (127.0.0.1) and checks only whether the buffer calls failed.",
    "DNS is not exercised (the proxy resolves names), so F1 (resolver threads) and S12 are not measured here.",
    "The restart visit follows a forced stop of the first browser (no graceful shutdown without driving it), after a "
    "3 s pause; a disk cache that has not flushed by then would be undercounted.",
    "Requests the browser makes to hosts outside the test set (its own background traffic, e.g. the bundled "
    "uBlock Origin's filter-list updates on a fresh profile) are refused by the relay and counted in "
    "browser_requests_refused_by_relay; on a real link they would cost bytes and time that B1 does not include.",
    "No video fixture: S2/S3 (media autoplay and preload) are not exercised by B1.",
    "Fonts are font-shaped blobs: fetched, then rejected by the font sanitiser; their bytes count, their rendering not.",
    "B4 has no about:memory dump (that needs the browser to be driven); it samples working set and private bytes "
    "of every process the run started, every 0.5 s.",
]


def default_out():
    from ..core import settings
    return Path(settings.expand("${LOCAL:firefox.root}")) / "bench"


def pick_benches(spec):
    names = [b.strip().upper() for b in spec.split(",") if b.strip()] if spec else list(benches.BENCHES)
    bad = [b for b in names if b not in benches.BENCHES]
    if bad:
        raise ValueError(f"unknown bench(es) {bad}; known: {', '.join(benches.BENCHES)}")
    return [b for b in benches.BENCHES if b in names]


def run_all(install_dir, bench_names, mode="normal", link_list=None, reps=3, label=None, out_dir=None,
            say=print, max_wait=3600):
    """The whole run. -> (result dict, json path, yaml path)."""
    install_dir = Path(install_dir)
    info = browser.build_info(install_dir)
    mode_p = links.mode_prefs(mode)
    link_list = link_list or links.pick_links(None)
    label = label or f"build{info['build_id']}"
    out_dir = Path(out_dir or default_out())
    stamp = time.strftime("%Y%m%d-%H%M%S")
    say(f"netbench {label}: {info['codename']} {info['version']} BuildID {info['build_id']}, profile {mode}, "
        f"benches {','.join(bench_names)}, links {','.join(l.name for l in link_list)}, {reps} repetition(s)")
    if not browser.wait_not_running(install_dir, max_wait=max_wait, say=say):
        raise RuntimeError(f"the browser is still running from {install_dir}; nothing was copied or measured")
    files = fixtures.build()
    man = fixtures.manifest(files)
    work = browser.throwaway.profile("gnetbench_", user_js=None)
    copy_root = None
    lab = None
    raw = {b: {} for b in bench_names}
    cal = {}
    started = time.time()
    try:
        cert = certs.make(work / "certs")
        copy_root, copy = browser.copy_install(install_dir, cert["ca"], say=say)
        rel = relay.Relay()
        lab = servers.Lab(files, cert, rel, want_h3="B3" in bench_names).start()
        bench = benches.Bench(lab, copy / "firefox.exe", mode_p, say=say)
        article = man["article_set_bytes_compressed"]
        for link in link_list:
            if not any(b in bench_names for b in ("B1", "B2", "B4", "B5")):
                break
            rel.set_link(dataclasses.replace(link, loss=0.0), seed=7)     # calibrate rate and delay; loss off
            c = lab.call(relay.calibrate(lab.ports["relay"], link), 900)
            c["loss_during_calibration"] = 0.0
            c["relay_limited"] = relay_limited(c)
            cal[link.name] = c
            say(f"  link {link.name}: relay measured rtt {c.get('rtt_ms', 0):.1f} ms, down {c.get('down_mbps') or 0:.3f} "
                f"Mbit/s, up {c.get('up_mbps') or 0:.3f} Mbit/s" + ("  (RELAY-LIMITED)" if c["relay_limited"] else ""))
            for name in ("B1", "B2", "B4", "B5"):
                if name not in bench_names:
                    continue
                rows = []
                for rep in range(reps):
                    rel.set_link(link, seed=100 + rep)
                    fn = {"B1": lambda: bench.b1(link, article), "B2": lambda: bench.b2(link),
                          "B4": lambda: bench.b4(link, article), "B5": lambda: bench.b5(link)}[name]
                    try:
                        r = fn()
                    except Exception as e:                       # noqa: BLE001 - one failed repetition is UNMEASURED
                        r = {"error": f"{type(e).__name__}: {e}"}
                    rows.append(r)
                    say(f"    {name} {link.name} rep {rep + 1}/{reps}: {short(name, r)}")
                raw[name][link.name] = rows
        if "B3" in bench_names:
            rel.set_link(None)
            rows = []
            for rep in range(reps):
                try:
                    r = bench.b3()
                except Exception as e:                           # noqa: BLE001
                    r = {"error": f"{type(e).__name__}: {e}"}
                rows.append(r)
                say(f"    B3 local rep {rep + 1}/{reps}: {short('B3', r)}")
            raw["B3"]["local"] = rows
        refused = rel.refused_hosts()
    finally:
        if lab:
            lab.stop()
        for d in (copy_root, work):
            if d:
                browser.discard(d)
        for d in dict.fromkeys(browser.UNREMOVED):
            say(f"  note: could not remove the throwaway folder {d}")
    result = {
        "schema": SCHEMA, "label": label, "when": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - started),
        "build": info, "profile": mode, "mode_prefs": mode_p, "not_emulated": links.NOT_EMULATED[mode],
        "harness_prefs": {k: v for k, v in browser.HARNESS_PREFS.items()},
        "install_prefs": browser.install_prefs(install_dir), "repetitions": reps,
        "links": {l.name: dict(l.as_dict(), calibration=cal.get(l.name)) for l in link_list},
        "fixtures": {"digest": man["digest"], "article_set_bytes_compressed": man["article_set_bytes_compressed"],
                     "files": man["files"]},
        "benches": assemble(raw, reps),
        "browser_requests_refused_by_relay": refused,
        "limitations": LIMITATIONS,
        "raw": raw,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / f"netbench-{label}-{stamp}.json"
    jp.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    yp = out_dir / f"netbench-{label}-{stamp}.yaml"
    yp.write_text(yaml_summary(result, jp.name), encoding="utf-8")
    return result, jp, yp


def relay_limited(c):
    """True when the relay did not deliver the link it emulates: a rate under 90% or a round trip over 125%."""
    if not c or c.get("error"):
        return True
    return bool((c.get("down_ratio") or 0) < 0.9 or (c.get("up_ratio") or 0) < 0.9 or (c.get("rtt_ratio") or 0) > 1.25)


def assemble(raw, reps):
    out = {}
    for name, per_link in raw.items():
        keys = {"B1": B1_KEYS, "B2": B2_KEYS, "B3": B3_KEYS, "B4": B4_KEYS, "B5": B5_KEYS}[name]
        res = {}
        for ln, rows in per_link.items():
            if name == "B1":
                groups = ("cold", "warm", "restart")
                res[ln] = {g: benches.collect([r.get(g, {"error": r.get("error")}) if "error" not in r else r
                                               for r in rows], keys, reps) for g in groups}
            elif name == "B2":
                res[ln] = {g: benches.collect([r.get(g, {"error": r.get("error")}) if "error" not in r else r
                                               for r in rows], keys, reps) for g in ("h2", "h1")}
            else:
                group = {"B3": "h3", "B4": "workload", "B5": "idle"}[name]
                res[ln] = {group: benches.collect(rows, keys, reps)}
        out[name] = {"title": TITLES[name], "results": res}
    return out


def short(name, r):
    """One line per repetition while the run goes."""
    if "error" in r and len(r) == 1:
        return "UNMEASURED: " + str(r["error"])[:160]
    f = lambda v, fmt: ("-" if v is None else fmt.format(v))   # noqa: E731
    if name == "B1":
        return "  ".join(f"{s}: " + (f"load {f(r[s].get('load_s'), '{:.2f}')} s, {r[s].get('requests')} req, "
                                     f"{f(r[s].get('wire_down_bytes'), '{:,}')} B down" if "error" not in r[s]
                                     else "UNMEASURED " + str(r[s]["error"])[:80])
                         for s in ("cold", "warm", "restart") if s in r)
    if name == "B2":
        return "  ".join(f"{p}: " + (f"down {f(r[p].get('down_mbps'), '{:.2f}')} up {f(r[p].get('up_mbps'), '{:.2f}')} Mbit/s"
                                     if "error" not in r[p] else "UNMEASURED " + str(r[p]["error"])[:80])
                         for p in ("h2", "h1") if p in r)
    if name == "B3":
        return (f"h3 requests {r.get('h3_requests')}, recv-buffer failures {r.get('recv_buffer_failures')}, "
                f"send-buffer failures {r.get('send_buffer_failures')}" + (f"  UNMEASURED {r['error']}" if r.get("error") else ""))
    if name == "B4":
        return (f"peak private {f(r.get('peak_private_mb'), '{:.0f}')} MiB, growth {f(r.get('growth_private_mb'), '{:.0f}')} MiB, "
                f"peak working set {f(r.get('peak_ws_mb'), '{:.0f}')} MiB")
    if name == "B5":
        return (f"short idle {r.get('short_lived_idle_s')} s, long idle {r.get('long_lived_idle_s')} s, retry "
                f"{r.get('retry_interval_s')} s, new connections after idle {r.get('new_connections_after_idle')}"
                + (f"  UNMEASURED {r['error']}" if r.get("error") else ""))
    return str(r)[:160]


def fmt(m):
    if m.get("status") != "MEASURED":
        return "UNMEASURED (" + str(m.get("why", ""))[:140] + ")"
    med = m["median"]
    unit = m["unit"]
    num = f"{med:,.0f}" if unit in ("bytes",) or (isinstance(med, int) and unit == "count") else f"{med:,.3f}"
    spread = f"{m['spread']:,.0f}" if unit == "bytes" else f"{m['spread']:,.3f}"
    return f"{num} {unit} (spread {spread})"


def lines(result):
    out = [f"netbench {result['label']}: BuildID {result['build']['build_id']} ({result['build']['version']}), "
           f"profile {result['profile']}, {result['repetitions']} repetition(s), median (spread = max - min)"]
    for ln, l in result["links"].items():
        c = l.get("calibration") or {}
        if c:
            out.append(f"  link {ln}: nominal {l['down_mbps']}/{l['up_mbps']} Mbit/s {l['rtt_ms']} ms loss {l['loss']}; "
                       f"relay measured {c.get('down_mbps') or 0:.3f}/{c.get('up_mbps') or 0:.3f} Mbit/s, "
                       f"rtt {c.get('rtt_ms') or 0:.1f} ms" + ("  RELAY-LIMITED" if relay_limited(c) else ""))
    for name, b in result["benches"].items():
        out.append(f"{name}  {b['title']}")
        for ln, groups in b["results"].items():
            for g, metrics in groups.items():
                out.append(f"  {ln} / {g}")
                for k, m in metrics.items():
                    out.append(f"    {k:28s} {fmt(m)}")
    ref = result.get("browser_requests_refused_by_relay") or {}
    out.append(f"browser requests to outside hosts, refused by the relay: {sum(ref.values())} "
               + (str(dict(sorted(ref.items(), key=lambda x: -x[1])[:8])) if ref else ""))
    return out


def yaml_summary(result, json_name):
    rows = []
    for name, b in result["benches"].items():
        for ln, groups in b["results"].items():
            vals = {}
            for g, metrics in groups.items():
                for k, m in metrics.items():
                    vals[f"{g}.{k}"] = (round(m["median"], 4) if isinstance(m["median"], float) else m["median"]) \
                        if m["status"] == "MEASURED" else "UNMEASURED: " + str(m.get("why", ""))[:200]
            rows.append({"id": f"NETBENCH-{result['label']}-{name}-{ln}", "when": result["when"],
                         "browser": f"{result['build']['codename']} {result['build']['version']}, BuildID {result['build']['build_id']}",
                         "tool": f"fieldkit build-harness netbench ({name}: {b['title']})",
                         "profile": result["profile"], "link": ln, "repetitions": result["repetitions"],
                         "statistic": "median of the repetitions", "values": vals, "evidence": json_name})
    head = (f"# Network bench record ({SCHEMA}), local servers only (127.0.0.1). Medians; the spread and every\n"
            f"# repetition are in {json_name}. UNMEASURED means the bench could not measure it: no number is given.\n")
    return head + yaml.safe_dump(rows, sort_keys=False, allow_unicode=True, width=120)


# --- compare ---------------------------------------------------------------------------------------------------------
def resolve(name, out_dir=None):
    p = Path(name)
    if p.is_file():
        return p
    d = Path(out_dir or default_out())
    hits = sorted(d.glob(f"netbench-{name}-*.json"))
    if not hits:
        raise FileNotFoundError(f"no result file {name!r} and none labelled so in {d}")
    return hits[-1]


def compare(a, b, out_dir=None):
    """-> rows {bench, link, group, metric, unit, a, b, delta, pct, verdict}."""
    pa, pb = resolve(a, out_dir), resolve(b, out_dir)
    A = json.loads(pa.read_text(encoding="utf-8"))
    B = json.loads(pb.read_text(encoding="utf-8"))
    rows = []
    for name in sorted(set(A["benches"]) | set(B["benches"])):
        ra = A["benches"].get(name, {}).get("results", {})
        rb = B["benches"].get(name, {}).get("results", {})
        for ln in sorted(set(ra) | set(rb)):
            for g in sorted(set(ra.get(ln, {})) | set(rb.get(ln, {}))):
                ma, mb = ra.get(ln, {}).get(g, {}), rb.get(ln, {}).get(g, {})
                for k in list(dict.fromkeys(list(ma) + list(mb))):
                    x, y = ma.get(k, {"status": "ABSENT"}), mb.get(k, {"status": "ABSENT"})
                    row = {"bench": name, "link": ln, "group": g, "metric": k, "unit": x.get("unit") or y.get("unit"),
                           "a": x.get("median") if x.get("status") == "MEASURED" else None,
                           "b": y.get("median") if y.get("status") == "MEASURED" else None}
                    if row["a"] is None or row["b"] is None:
                        row["verdict"] = f"cannot compare (A {x.get('status')}, B {y.get('status')})"
                        row["delta"] = row["pct"] = None
                    else:
                        row["delta"] = row["b"] - row["a"]
                        row["pct"] = (row["delta"] / row["a"] * 100) if row["a"] else None
                        noise = max(x.get("spread", 0), y.get("spread", 0))
                        row["verdict"] = "within spread" if abs(row["delta"]) <= noise else "changed"
                    rows.append(row)
    return {"a": str(pa), "b": str(pb), "a_build": A["build"], "b_build": B["build"], "a_profile": A["profile"],
            "b_profile": B["profile"], "rows": rows}


def compare_lines(c):
    out = [f"A: {c['a']}  (BuildID {c['a_build']['build_id']}, profile {c['a_profile']})",
           f"B: {c['b']}  (BuildID {c['b_build']['build_id']}, profile {c['b_profile']})",
           "delta = B - A; 'within spread' means the change is no larger than the larger spread of the two runs"]
    last = None
    for r in c["rows"]:
        head = (r["bench"], r["link"], r["group"])
        if head != last:
            out.append(f"{r['bench']} {r['link']} / {r['group']}")
            last = head
        if r["delta"] is None:
            out.append(f"    {r['metric']:28s} {r['verdict']}")
            continue
        pct = f"{r['pct']:+.1f}%" if r["pct"] is not None else "n/a"
        out.append(f"    {r['metric']:28s} {r['a']:>14,.3f} -> {r['b']:>14,.3f} {r['unit']:9s} {r['delta']:+,.3f} ({pct})  {r['verdict']}")
    return out
