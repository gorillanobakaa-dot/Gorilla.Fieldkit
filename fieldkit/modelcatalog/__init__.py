# Version: 1.0.0 · updated 26-11-13
"""`fieldkit modelcatalog verify`: is a client's model list what the real app gets from the backend?

    fieldkit modelcatalog verify --provider antigravity (--app APP | --reference FLOWS) \\
        [--client-flows FLOWS] [--client-src DIR] [--catalogue FILE] [--probe FILE] [--json]

  reference   the REAL app's traffic: `fieldkit appcapture start/show APP`, or a flows file / JSON-lines dump
  client      what OUR client sends: its own capture (--client-flows), its source (--client-src, read only), the model
              list it shows (--catalogue: JSON list or one id per line), and a one-prompt probe result (--probe: JSON
              list of {"model", "status", "body"}, from the client's own probe command)

CHECKS (each exits 3 when it finds something; data in providers.yaml)
  empty-body            the client's catalogue request has an empty / different body than the app's
  client-sees-fewer     the client's catalogue response lacks ids the app's response has
  invented-id           an id literal in the client's source that the backend never reported
  catalogue-not-reported  an id in the model list the client shows that the backend did not report
  hardcoded-ua          a UA with a typed-in version in client source, or a UA version unlike the app's
  probe-404             a one-prompt probe got HTTP 404 / "Requested entity was not found" for a listed model

WHY (2026-11-13): an agent guessed ids, injected four fake catalogue entries and bumped the UA version; the picker then
said "Reported by the backend" for models the backend never reported and every prompt returned 404. The real app's
catalogue request had a body the agent never sent. Never prints a token or a body: ids and counts only."""
import argparse
import json
import fnmatch
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent


def provider(name):
    d = yaml.safe_load((HERE / "providers.yaml").read_text(encoding="utf-8"))["providers"]
    if name not in d:
        raise KeyError(f"unknown provider {name!r}: known {sorted(d)}")
    return d[name]


def ids_in(obj, id_re):
    """Every model id in a JSON value: dict keys and string values that fully match the id pattern."""
    rx = re.compile(id_re)
    out = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(k, str) and rx.fullmatch(k):
                    out.add(k)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and rx.fullmatch(x):
            out.add(x)
    walk(obj)
    return out


def _json(s):
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


def list_calls(flows, prov):
    return [f for f in flows if prov["list_path"] in f["path"].split("?")[0]]


def backend_ids(flows, prov):
    ids = set()
    for f in list_calls(flows, prov):
        ids |= ids_in(_json(f.get("resp_body")), prov["id_regex"])
    return ids


def _shape(body):
    j = _json(body)
    return j if isinstance(j, dict) else None


def check_body(ref, client, prov):
    out = []
    rcalls = [_shape(f["req_body"]) for f in list_calls(ref, prov)]
    rcalls = [r for r in rcalls if r]
    for c in list_calls(client, prov):
        cb = _shape(c.get("req_body"))
        if not c.get("req_body") or cb is None:
            out.append(("empty-body", "client's catalogue request has an empty body; the app sends "
                        f"keys {sorted(rcalls[0]) if rcalls else '(none captured)'}"))
        elif rcalls and cb != rcalls[0] and not any(cb == r for r in rcalls):
            out.append(("empty-body", f"client's catalogue request body keys {sorted(cb)} differ from the app's {sorted(rcalls[0])}"))
    return out


def check_fewer(ref, client, prov):
    missing = backend_ids(ref, prov) - backend_ids(client, prov)
    return [("client-sees-fewer", f"the client's catalogue reply lacks {len(missing)} id(s) the app's has: {sorted(missing)}")] \
        if missing and backend_ids(client, prov) else []


def check_catalogue(catalogue, backend, prov):
    return [("catalogue-not-reported", f"the client lists {sorted(set(catalogue) - backend)} but the backend never reported them")] \
        if set(catalogue) - backend else []


def _src_files(src, prov):
    suf = set(prov["source_suffixes"])
    for p in sorted(Path(src).rglob("*")):
        s = "/" + p.relative_to(src).as_posix()
        if p.is_file() and p.suffix in suf and not any(k in s for k in prov["source_skip"]):
            if any(fnmatch.fnmatch(p.name.lower(), g.lower()) for g in prov["source_globs"]):
                yield p


def check_source(src, backend, prov):
    out = []
    id_lit = re.compile(r'["`\'](' + prov["id_regex"] + r')["`\']')
    ua_lit = re.compile(prov["source_ua_literal"])
    for p in _src_files(src, prov):
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if any(m in line for m in prov["source_allow_markers"]):
                continue
            if ua_lit.search(line):
                out.append(("hardcoded-ua", f"{p.name}:{n} a UA literal with a typed-in version; read it from the installed app"))
            for m in id_lit.findall(line):
                if backend and m not in backend:
                    out.append(("invented-id", f"{p.name}:{n} id {m!r} is not in the backend's reply"))
    return out


def check_ua(ref, client, prov):
    rx = re.compile(prov["ua_version_regex"])

    def versions(flows):
        return {m.group(1) for f in flows for k, v in (f.get("req_headers") or {}).items()
                if k.lower() == prov["ua_header"] for m in [rx.search(v)] if m}
    rv, cv = versions(ref), versions(client)
    return [("hardcoded-ua", f"client UA version {sorted(cv)} is not the app's {sorted(rv)}")] if rv and cv and not (cv <= rv) else []


def check_probe(results, prov):
    out = []
    for r in results:
        txt = str(r.get("body", ""))
        if str(r.get("status")) == "404" or any(t in txt for t in prov["not_found_text"]):
            out.append(("probe-404", f"model {r.get('model')!r} answered {r.get('status')}: a listed model must work"))
    return out


def verify(prov_name, ref, client=None, catalogue=None, src=None, probe=None):
    prov = provider(prov_name)
    backend = backend_ids(ref, prov)
    f = []
    if not list_calls(ref, prov):
        f.append(("no-reference", f"the reference has no {prov['list_path']} call: capture the real app first (fieldkit appcapture)"))
    if client:
        f += check_body(ref, client, prov) + check_fewer(ref, client, prov) + check_ua(ref, client, prov)
    if catalogue is not None:
        f += check_catalogue(catalogue, backend, prov)
    if src:
        f += check_source(src, backend, prov)
    if probe is not None:
        f += check_probe(probe, prov)
    return {"provider": prov_name, "backend_ids": sorted(backend),
            "findings": [{"check": c, "detail": d} for c, d in f]}


def load_flows(path):
    p = Path(path)
    if p.suffix in (".json", ".jsonl"):
        txt = p.read_text(encoding="utf-8")
        j = _json(txt)
        return j if isinstance(j, list) else [json.loads(x) for x in txt.splitlines() if x.strip()]
    from fieldkit import appcapture
    return appcapture.read_flows(p)


def load_catalogue(path):
    txt = Path(path).read_text(encoding="utf-8")
    j = _json(txt)
    return [str(x) for x in j] if isinstance(j, list) else [x.strip() for x in txt.splitlines() if x.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit modelcatalog")
    ap.add_argument("verb", choices=["verify"])
    ap.add_argument("--provider", default="antigravity")
    ap.add_argument("--app")
    ap.add_argument("--reference")
    ap.add_argument("--client-flows")
    ap.add_argument("--client-src")
    ap.add_argument("--catalogue")
    ap.add_argument("--probe")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        ref = a.reference
        if not ref and a.app:
            from fieldkit import appcapture
            st = appcapture.load_state(a.app)
            ref = st["flows"] if st else None
        if not ref:
            print("modelcatalog: pass --app APP (a running or finished appcapture) or --reference FLOWS")
            return 2
        res = verify(a.provider, load_flows(ref), load_flows(a.client_flows) if a.client_flows else None,
                     load_catalogue(a.catalogue) if a.catalogue else None, a.client_src,
                     json.loads(Path(a.probe).read_text(encoding="utf-8")) if a.probe else None)
    except (KeyError, OSError, RuntimeError, ValueError) as e:
        print(f"modelcatalog: {e}")
        return 1
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"backend reported {len(res['backend_ids'])} model id(s)")
        for f in res["findings"]:
            print(f"FINDING [{f['check']}] {f['detail']}")
        print("modelcatalog verify: " + ("findings above" if res["findings"] else "OK, the client matches the real app"))
        print("NEXT: " + ("copy the real request shape from `fieldkit appcapture show APP`; never invent ids or versions"
                          if res["findings"] else "carry on"))
    return 3 if res["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
