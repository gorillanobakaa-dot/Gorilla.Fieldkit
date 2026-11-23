"""P39: the person chooses coding agent -> provider -> model -> reasoning, on either computer, from what works NOW.

Owner, 2026-11-16: "give the user a choice. Which AI would you like to use? ... Not only the model, but the provider as
well." and, on the refusal that was built instead, "not to block the fucking Claude."

Data: choice.yaml (one source). Measured live, never assumed:
  installed  agentfind on this computer; `peers link agents` for the other one (menu(peer=...))
  usable     the agent quota guard (models.unavailable_agents, fed by note_failure from the same error patterns the
             model guard uses, ending at the reset time) and the provider's local server when it is a local LLM
  models     the agent's own list where it can be asked without side effects (codex cache, `agy models`), the local
             server's list (LM Studio, Ollama), else the data file; stale names fail check()
Headless (no person): the owner's recorded choice per job type, else an inbox item. Never a hidden fallback.
Every probe (finder, runner, url reader, clock) is a parameter, so a test fakes each system.
"""
from __future__ import annotations

import json
import subprocess
from fieldkit import proc as fkproc
import sys
import time
from pathlib import Path

import yaml

DATA = Path(__file__).with_name("choice.yaml")


def load(path=None) -> dict:
    return yaml.safe_load(Path(path or DATA).read_text(encoding="utf-8")) or {}


def _state(name):
    from .friction import state_dir
    return state_dir() / name


# ---- live probes (each replaceable) -----------------------------------------------------------------------------
def _http_json(url, timeout=2.0):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:      # 127.0.0.1 only (choice.yaml endpoints)
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - not running is an answer
        return None


def local_models(provider, pdata, http=_http_json):
    """-> (serving: bool, [model ids]) of a local LLM server, or (False, []) when it is not running."""
    url = pdata.get("endpoint") or ""
    if not url.startswith(("http://127.0.0.1", "http://localhost")):
        return False, []                                              # fail closed: a local option is never remote
    j = http(url)
    if j is None:
        return False, []
    if provider == "ollama":
        return True, [m.get("name") or m.get("model") for m in (j.get("models") or []) if isinstance(m, dict)]
    return True, [m.get("id") for m in (j.get("data") or []) if isinstance(m, dict) and m.get("id")]


def codex_cache_models(home=None):
    """Codex CLI's own model list (it refreshes ~/.codex/models_cache.json from OpenAI); -> {slug: [efforts]}."""
    p = Path(home or Path.home()) / ".codex" / "models_cache.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for m in d.get("models") or []:
        if m.get("visibility", "list") != "list" or not m.get("slug"):
            continue
        out[m["slug"]] = [e.get("effort") for e in (m.get("supported_reasoning_levels") or []) if e.get("effort")]
    return out


def command_models(exe, args, run=fkproc.run):
    """First column of each line of `exe ARGS` (10 s cap), lines that look like a model id."""
    try:
        r = run([exe, *args], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        tok = line.split("\t")[0].split()[0] if line.strip() else ""
        if tok and all(c.isalnum() or c in "-._/:" for c in tok) and any(c.isdigit() for c in tok):
            out.append(tok)
    return out


# ---- the agent quota guard ----------------------------------------------------------------------------------------
def note_failure(agent, text, returncode=1, now=None):
    """An agent run ended: if its output is a quota / limit error (models.yaml availability.patterns, the model guard's
    own), block the AGENT until the reset time the message names, else the cooldown. -> the block record, or None."""
    from . import models
    f = models.classify_failure(text or "", returncode, now)
    if not f:
        return None
    return models.mark_agent_unavailable(agent, f["reason"][:200], until=f["until"], now=now)


# ---- the menu -----------------------------------------------------------------------------------------------------
def _cache_get(key, minutes, now):
    try:
        c = json.loads(_state("choice-cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rec = c.get(key)
    return rec["v"] if rec and now - rec.get("at", 0) < 60 * minutes else None


def _cache_put(key, v, now):
    p = _state("choice-cache.json")
    try:
        c = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        c[key] = {"at": now, "v": v}
        p.write_text(json.dumps(c, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass


def agent_models(name, a, data, found, run=fkproc.run, home=None, now=None, refresh=False):
    """-> (models: [str], source: str, reasoning_by_model: {}) for one installed agent."""
    now = time.time() if now is None else now
    how = a.get("live", "none")
    src_agent = a.get("live_from")
    if how == "codex-cache":
        cm = codex_cache_models(home)
        if cm:
            return list(cm), "live: codex models cache", cm
    if how == "command" or src_agent:
        key = f"cmd:{src_agent or name}"
        hit = None if refresh else _cache_get(key, data.get("cache_minutes", 60), now)
        if hit is None:
            exe = (found.get(src_agent or name) or {}).get("path")
            la = a if not src_agent else (data.get("agents") or {}).get(src_agent, {})
            hit = command_models(exe, la.get("live_cmd") or ["models"], run) if exe else []
            if hit:
                _cache_put(key, hit, now)
        if hit:
            return hit, f"live: `{src_agent or name} models`", {}
    if a.get("models"):
        return list(a["models"]), "data file (agent aliases)" if how != "none" else "data file", {}
    return [], "data file", {}


def menu(data=None, finder=None, http=_http_json, run=fkproc.run, home=None, now=None, refresh=False,
         installed=None):
    """-> rows grouped agent > provider > model > reasoning, each with `usable` and, when not, `why` and `until`.

    installed: {agent finder name: {installed, path, version}} from the other computer (`peers link agents`); None =
    this computer (agentfind)."""
    from . import models as _models
    from ..peers import agentfind
    d = data or load()
    now = time.time() if now is None else now
    if installed is None:
        names = [a.get("finder") for a in (d.get("agents") or {}).values() if a.get("finder") in agentfind.AGENTS]
        rows = (finder or agentfind.find_all)(names)
        installed = {r["agent"]: r for r in rows}
    blocked = _models.unavailable_agents(now)
    bad_models = _models.unavailable_models(now)
    provs = d.get("providers") or {}
    local = {}
    for p, pd in provs.items():
        if pd.get("private"):
            local[p] = local_models(p, pd, http)
    out = []
    for name, a in (d.get("agents") or {}).items():
        f = installed.get(a.get("finder") or name) or {}
        row = {"agent": name, "title": a.get("title", name), "installed": bool(f.get("installed")),
               "version": f.get("version", ""), "path": f.get("path", ""), "usable": True, "why": "", "until": None,
               "reasoning": list(a.get("reasoning") or []), "providers": []}
        if not row["installed"]:
            row.update(usable=False, why="not installed on this computer")
        elif name in blocked:
            b = blocked[name]
            row.update(usable=False, why=f"{b['source']}: {b['reason']}", until=b["until"])
        live, src, effort = agent_models(name, a, d, installed, run, home, now, refresh) if row["installed"] else ([], "", {})
        for p in a.get("providers") or []:
            pd = provs.get(p) or {}
            prow = {"provider": p, "title": pd.get("title", p), "private": bool(pd.get("private")),
                    "who_can_act": pd.get("who_can_act", ""), "who_learns": pd.get("who_learns", ""),
                    "usable": row["usable"], "why": row["why"], "models": [], "source": ""}
            if pd.get("private"):
                serving, ms = local.get(p, (False, []))
                prow["models"], prow["source"] = ms, "live: local server"
                if not serving:
                    prow.update(usable=False, why=f"{pd.get('title', p)} is not running on this computer")
                elif not ms:
                    prow.update(usable=False, why="the local server has no model loaded")
            else:
                remote = [x for x in a.get("providers") or [] if not (provs.get(x) or {}).get("private")]
                own = live if len(remote) == 1 else [m for m in live if _belongs(m, p, provs)]
                prow["models"] = own or list(pd.get("models") or [])
                prow["source"] = src if own else "data file"
            prow["model_rows"] = [_model_row(m, bad_models, effort.get(m) or row["reasoning"]) for m in prow["models"]]
            row["providers"].append(prow)
        out.append(row)
    return out


def _model_row(m, bad_models, reasoning):
    """One model: usable unless its quota is out (availability) or the spend guard refuses it (mandate models_allowed)."""
    from . import models as _models
    if m.lower() in bad_models:
        return {"model": m, "usable": False, "why": bad_models[m.lower()].get("reason", "unavailable"), "reasoning": reasoning}
    g = _models.check_model(m, log=False)
    return {"model": m, "usable": g["ok"], "why": "" if g["ok"] else f"spend guard: {g['why']}", "reasoning": reasoning}


def _belongs(model, provider, provs):
    """Does a live model id (from a multi-provider agent) belong to this provider? By its data-file list."""
    return model in (provs.get(provider) or {}).get("models", [])


def until_text(ts):
    return time.strftime("back at %Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def render(rows, where="this computer"):
    lines = [f"WHICH CODING AGENT, PROVIDER, MODEL AND REASONING? (what works now on {where})"]
    n = 0
    for r in rows:
        state = "ready" if r["usable"] else f"NOT NOW: {r.get('why', '')}" + (f" ({until_text(r['until'])})" if r.get("until") else "")
        lines.append(f"  {r.get('title', r['agent'])} [{r['agent']}] {str(r.get('version', ''))[:24]}  {state}")
        if not r["installed"]:
            continue
        for p in r["providers"]:
            tag = "  PRIVATE: stays on this computer" if p["private"] else ""
            pstate = "" if p["usable"] else f"  NOT NOW: {p['why']}"
            lines.append(f"    - {p.get('title', p['provider'])}{tag}{pstate}")
            if not p["private"] and p.get("who_learns"):
                lines.append(f"        who learns: {p['who_learns']}")
            for m in p["model_rows"]:
                if p["usable"] and m["usable"]:
                    n += 1
                    lines.append(f"        {n:>2}. {m['model']:<34} reasoning: {', '.join(m['reasoning'])}"
                                 + f"   ({p.get('source', '')})" * (m is p["model_rows"][0]))
                else:
                    lines.append(f"            {m['model']:<34} NOT NOW: {m['why'] or p['why']}")
    return "\n".join(lines)


def options(rows):
    """The numbered, usable (agent, provider, model, reasoning levels) in the order render() shows them."""
    out = []
    for r in rows:
        if not (r["installed"] and r["usable"]):
            continue
        for p in r["providers"]:
            if not p["usable"]:
                continue
            for m in p["model_rows"]:
                if m["usable"]:
                    out.append({"agent": r["agent"], "provider": p["provider"], "model": m["model"],
                                "reasoning": m["reasoning"], "private": p["private"]})
    return out


# ---- remembered choices per job type (the owner's, for headless jobs) ---------------------------------------------
def remembered(job_type=None):
    try:
        d = json.loads(_state("choices.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return d.get(job_type) if job_type else d


def remember(job_type, agent, provider, model, reasoning, data=None):
    d = data or load()
    a = (d.get("agents") or {}).get(agent)
    if not a:
        raise ValueError(f"unknown agent {agent!r}; one of {', '.join(d.get('agents') or {})}")
    if provider not in (a.get("providers") or []):
        raise ValueError(f"{agent} does not drive provider {provider!r}; it drives {', '.join(a.get('providers') or [])}")
    if model in (d.get("stale") or []):
        raise ValueError(f"{model!r} is a stale model name (choice.yaml stale:)")
    if reasoning not in (a.get("reasoning") or []):
        raise ValueError(f"reasoning {reasoning!r} is not one of {agent}'s: {', '.join(a.get('reasoning') or [])}")
    cur = remembered() or {}
    cur[job_type] = {"agent": agent, "provider": provider, "model": model, "reasoning": reasoning,
                     "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _state("choices.json").write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return cur[job_type]


def _tty():
    """A person at a terminal (stdin and stdout both a TTY)."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def pick(job_type, rows=None, interactive=None, ask=input, out=print, inbox=None, **kw):
    """The person picks from the menu; headless uses the owner's recorded choice for the job type, else an inbox item.

    -> {"ok": True, agent, provider, model, reasoning, "how"} or {"ok": False, "why", "menu"}."""
    rows = menu(**kw) if rows is None else rows
    opts = options(rows)
    rec = remembered(job_type) if job_type else None
    interactive = _tty() if interactive is None else interactive
    if interactive:
        out(render(rows))
        if rec:
            out(f"  remembered for {job_type}: {rec['agent']} / {rec['provider']} / {rec['model']} / {rec['reasoning']}"
                " (press Enter to take it)")
        try:
            ans = ask("  number [reasoning]: ").strip()
        except EOFError:
            ans = ""
        if not ans and rec:
            return {"ok": True, **{k: rec[k] for k in ("agent", "provider", "model", "reasoning")}, "how": "remembered, confirmed"}
        parts = ans.split()
        if parts and parts[0].isdigit() and 1 <= int(parts[0]) <= len(opts):
            o = opts[int(parts[0]) - 1]
            lv = parts[1] if len(parts) > 1 and parts[1] in o["reasoning"] else (
                "medium" if "medium" in o["reasoning"] else o["reasoning"][0])
            return {"ok": True, "agent": o["agent"], "provider": o["provider"], "model": o["model"], "reasoning": lv,
                    "how": "picked"}
        return {"ok": False, "why": "no option picked", "menu": render(rows)}
    if rec:
        ok = any(o["agent"] == rec["agent"] and o["provider"] == rec["provider"] for o in opts)
        if ok:
            return {"ok": True, **{k: rec[k] for k in ("agent", "provider", "model", "reasoning")},
                    "how": f"the owner's recorded choice for {job_type}"}
        why = (f"the owner's recorded choice for {job_type} ({rec['agent']} / {rec['provider']}) does not work now")
    else:
        why = f"no person present and no recorded choice for job type {job_type or '(none)'}"
    if inbox:
        inbox(f"Choose the agent for {job_type or 'headless jobs'}", why,
              f"fieldkit models remember {job_type or 'JOB_TYPE'} AGENT PROVIDER MODEL REASONING")
    return {"ok": False, "why": why + "; nothing started (no hidden fallback). The owner records one: "
            f"fieldkit models remember {job_type or 'JOB_TYPE'} AGENT PROVIDER MODEL REASONING", "menu": render(rows)}


def resolve(job_type, origin="cli", device=None, interactive=None, inbox=None, **kw):
    """No agent/model/reasoning given: show the menu (of the computer the job runs on) and let the person pick; with no
    person, the owner's recorded choice for the job type, else an inbox item. -> a validate_agent_choice result."""
    from . import models
    rows = kw.pop("rows", None)
    if rows is None and device:
        from ..peers import link
        r = link.call({"verb": "probe", "name": "choice"}, timeout=45, device=device)
        rows = r.get("data") if r.get("ok") else None
        if rows is None:
            return {"ok": False, "why": f"the other computer did not send its menu: {r.get('error')}"}
    if inbox is None:
        from ..autopilot import inbox as _ib
        inbox = lambda t, w, c: _ib.add(t, w, c, kind="decision")     # noqa: E731
    p = pick(job_type, rows=rows, interactive=interactive, inbox=inbox, **kw)
    if not p["ok"]:
        return {"ok": False, "needs_choice": True, "why": p["why"], "menu": p.get("menu", "")}
    return models.validate_agent_choice(p["agent"], p["model"], p["reasoning"], role="worker",
                                        origin=f"{origin} ({p['how']}, provider {p['provider']})")


def check(data=None):
    """Findings: stale names in the data file or a remembered choice; providers an agent names that do not exist."""
    d = data or load()
    stale = set(d.get("stale") or [])
    provs = d.get("providers") or {}
    bad = []
    for p, pd in provs.items():
        bad += [f"provider {p}: stale model {m}" for m in pd.get("models") or [] if m in stale]
        if pd.get("private") and not str(pd.get("endpoint", "")).startswith(("http://127.0.0.1", "http://localhost")):
            bad.append(f"provider {p} is marked private but its endpoint is not this computer")
    for a, ad in (d.get("agents") or {}).items():
        bad += [f"agent {a}: unknown provider {p}" for p in ad.get("providers") or [] if p not in provs]
        bad += [f"agent {a}: stale model {m}" for m in ad.get("models") or [] if m in stale]
        if not ad.get("reasoning"):
            bad.append(f"agent {a}: no reasoning levels")
    for jt, rec in (remembered() or {}).items():
        if rec.get("model") in stale:
            bad.append(f"remembered choice {jt}: stale model {rec['model']}")
    return bad


def main(argv):
    """fieldkit models menu [--json] [--refresh] [--check] [--device NAME] | models remember JOB A P M R"""
    argv = list(argv)
    if "--help" in argv or "-h" in argv:
        if argv[:1] == ["remember"]:
            print("usage: fieldkit models remember JOB_TYPE AGENT PROVIDER MODEL REASONING\n"
                  "  record the owner's choice for headless jobs of JOB_TYPE (checked against choice.yaml and the spend guard)")
        else:
            print("usage: fieldkit models menu [--json] [--refresh] [--check] [--device NAME]\n"
                  "  --device NAME  measure the menu on the other computer over the cable\n"
                  "  --refresh      measure again instead of the cached live lists\n"
                  "  --check        check choice.yaml (stale names, unknown providers); exit 3 on findings\n"
                  "  --json         JSON")
        return 0
    if argv[:1] == ["remember"]:
        if len(argv) < 6:
            print("models remember JOB_TYPE AGENT PROVIDER MODEL REASONING")
            return 2
        try:
            rec = remember(*argv[1:6])
        except ValueError as e:
            print(f"REFUSED: {e}")
            return 2
        print(f"REMEMBERED {argv[1]}: {rec['agent']} / {rec['provider']} / {rec['model']} / {rec['reasoning']}")
        print("NEXT: headless jobs of this type start with it; a person is still shown the menu")
        return 0
    if "--check" in argv:
        bad = check()
        for b in bad:
            print(f"FINDING  {b}")
        print("CHECK OK: no stale model name, every provider known" if not bad else f"{len(bad)} finding(s)")
        return 3 if bad else 0
    where = "this computer"
    if "--device" in argv:
        i = argv.index("--device")
        dev = argv[i + 1] if i + 1 < len(argv) else None
        from ..peers import link
        r = link.call({"verb": "probe", "name": "choice"}, timeout=45, device=dev)   # measured THERE, not here
        if not r.get("ok"):
            print(f"ERROR  the other computer did not answer: {r.get('error')}")
            return 1
        where, rows = f"{r.get('host') or dev} (over the cable)", r.get("data") or []
    else:
        rows = menu(refresh="--refresh" in argv)
    if "--json" in argv:
        print(json.dumps({"where": where, "agents": rows, "options": options(rows)}, indent=1))
        return 0
    print(render(rows, where))
    print("NEXT: start with fieldkit task start BRIEF --agent A --model M --thinking R (a person at a terminal is asked "
          "from this menu); headless: fieldkit models remember JOB_TYPE A P M R")
    return 0
