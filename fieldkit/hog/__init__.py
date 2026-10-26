# Version: 1.1.0 · updated 26-10-26
"""The resource watchdog: memory hogs, leaks, idle local models, orphans, pile-ups, pinned CPU, heat, disk. Deterministic.

    fieldkit hog check [--json]           one pass: the findings, one summary line; exit 0 OK, 1 WARN, 2 ACT
    fieldkit hog act [--dry-run]          one pass, then the safe actions (unload idle models, stop registered orphans)
    fieldkit hog watch                    what the schedule runs every 5 minutes: sample, act, log, exit
    fieldkit hog preflight                the ONE preflight's checks now (every guarded run does this first; exit 2 = stop)
    fieldkit hog register PID [--cap 3h] [--why TEXT]   a process this harness started (the orphan rule may stop it)
    fieldkit hog log [N]                  the last N actions
    fieldkit hog doctor                   psutil, lms, the schedule, the room hook, the state folder
    fieldkit hog install [--apply]        the schedule: Windows task (5 min, as you), systemd user timer, launchd agent

WHY (owner, 2026-10-26 01:3x)
    LM Studio's llama-server held 43 GB for hours (Qwen3.6-35B-A3B loaded by a release session, IDLE, nobody using
    it): RAM 58.6/63.7 GB, the fans screamed and the owner woke up. Unloading it brought RAM to 15.3 GB. "We can't
    afford to burn this computer down ... Speed is crucial here." A slow machine makes every session slower, and
    sessions are paid by the hour.

THE RULES (numbers in thresholds.json; override in state/hog/thresholds.json; see rules.py)
    ram        used >= 80% warn, >= 90% act (used = total - available); commit charge 85% / 95%; top processes
    model      LM Studio (lms ps) or Ollama (/api/ps on loopback) model idle > 15 min (5 min when RAM is at the act
               line) -> unload. Idle = no request in LM Studio's server log (lms's own questions do not count) /
               Ollama's expires_at unmoved across samples. No log evidence = no unload. Reversible: it reloads.
    leak       working set never dropped across >= 6 samples over 30 min and grew >= 1 GB -> reported with its curve
    cpu        >= 95% of a core in every interval over 10 min -> reported (TextInputHost's loop)
    temp       >= 85 C warn, >= 95 C act (reported; fieldkit thermal monitor is what caps the CPU)
    disk       free < 10% warn, < 5% act (reported with the fieldkit size command that shows where it went)
    orphan     a REGISTERED process (pid and creation time both match) whose session process is gone, or older than
               its cap (4 h; none for one found in a live session's tree) -> stopped, with its own children. Nothing
               outside the registry is ever stopped, never by name (registry.py).
    pileup     a live parent holding > 20 children of one class (conhost, bash, python, node, powershell, cmd), or
               >= 10 of them started > 12 h before its newest one -> reported with its own parent, the count, their
               working set and the fix. Never stopped. Born 2026-10-26: Claude Desktop's NodeService held 33 consoles
               it never closed (61 conhost in Task Manager).

Every action: state/hog/actions.jsonl (what, why, numbers before/after) and one ROOM line (verb stop), which every
session, the command post included, sees on its next hook. The room hook also prints ONE line at a prompt when the
last watch found something (hook_line). Local only: nothing leaves the machine (probe.py).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import rules

HERE = Path(__file__).resolve().parent
DEFAULTS = HERE / "thresholds.json"


def state_dir():
    from .registry import state_dir as d
    return d()


def config():
    cfg = json.loads(DEFAULTS.read_text(encoding="utf-8"))
    local = state_dir() / "thresholds.json"
    if local.is_file():
        cfg.update(json.loads(local.read_text(encoding="utf-8")))
    return cfg


def history(cfg, now):
    p = state_dir() / "samples.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            h = json.loads(line)
        except ValueError:
            continue
        if now - h.get("t", 0) <= cfg["sample_keep_min"] * 60:
            out.append(h)
    return out


def save_sample(facts, hist, cfg):
    slim = {"t": facts["t"], "procs": [[p["pid"], p["ctime"], p["name"], p["rss"], p["cpu_s"]] for p in facts["procs"]],
            "ollama": {m["name"]: m.get("expires_at") for m in (facts.get("ollama") or {}).get("models") or []}}
    keep = [h for h in hist if facts["t"] - h["t"] <= cfg["sample_keep_min"] * 60] + [slim]
    p = state_dir() / "samples.jsonl"
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(h) + "\n" for h in keep), encoding="utf-8")
    os.replace(tmp, p)


def check(cfg=None, facts=None, hist=None, reg=None, sample=False):
    from . import probe, registry
    cfg = cfg or config()
    reg = registry.load() if reg is None else reg
    live = facts is None
    if facts is None:
        facts = probe.gather(cfg, keep={(e["pid"], e["ctime"]) for e in reg})
    hist = history(cfg, facts["t"]) if hist is None else hist
    found = rules.evaluate(facts, hist, reg, cfg)
    if live:                        # stale scratch jobs: reported with the orphans, never deleted by the hog
        try:
            from .. import scratch
            found += scratch.hog_findings()
        except Exception:
            pass
        try:                        # idle agent sessions and the RAM they hold: reported, archived by the command post
            from ..lifecycle import sweep
            found += sweep.hog_findings()
        except Exception:
            pass
    if sample:
        save_sample(facts, hist, cfg)
        reg = registry.prune(facts["alive"])
    last = {"t": facts["t"], "severity": rules.worst(found), "summary": rules.summary(found), "findings": found,
            "ram": facts.get("ram"), "lms": {k: v for k, v in (facts.get("lms") or {}).items() if k != "activity"}}
    (state_dir() / "last.json").write_text(json.dumps(last, indent=1), encoding="utf-8")
    return facts, found


# ---- actions -----------------------------------------------------------------------------------------------------
def _ram_used():
    import psutil
    v = psutil.virtual_memory()
    return v.total - v.available


def unload_lms(a, facts, run=None):
    import subprocess
    run = run or subprocess.run
    exe = (facts.get("lms") or {}).get("exe")
    if not exe:
        return False, "no lms CLI"
    r = run([exe, "unload", a["id"]], capture_output=True, text=True, timeout=120)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[:200]


def unload_ollama(a, facts, cfg, opener=None):
    import urllib.request
    from .probe import loopback
    req = urllib.request.Request(loopback(cfg["ollama_url"]) + "/api/generate", method="POST",
                                 data=json.dumps({"model": a["name"], "keep_alive": 0}).encode(),
                                 headers={"Content-Type": "application/json"})
    with (opener or urllib.request.urlopen)(req, timeout=30) as r:
        return 200 <= r.status < 300, f"HTTP {r.status}"


def kill(a):
    """Stop a registered process and its children, only if pid AND creation time still match."""
    import psutil
    from .probe import ct
    try:
        p = psutil.Process(a["pid"])
        if ct(p.create_time()) != a["ctime"]:
            return False, "creation time differs: the PID was reused, not ours, left alone"
        procs = p.children(recursive=True) + [p]
        for x in procs:
            try:
                x.terminate()
            except psutil.Error:
                pass
        _gone, alive = psutil.wait_procs(procs, timeout=5)
        for x in alive:
            try:
                x.kill()
            except psutil.Error:
                pass
        return True, f"stopped {len(procs)} process(es)" + (f", {len(alive)} killed after 5 s" if alive else "")
    except psutil.NoSuchProcess:
        return True, "already gone"
    except psutil.Error as e:
        return False, str(e)[:200]


def allowed(a, cfg):
    if a["kind"] in ("unload_lms", "unload_ollama"):
        return cfg["act_unload_idle_models"]
    if a["kind"] == "kill":
        return cfg["act_kill_orphans"]
    return False


def log_action(rec):
    with open(state_dir() / "actions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    try:
        from ..autopilot import room
        obj = f"pid={rec['action'].get('pid')}" if rec["action"]["kind"] == "kill" else \
            f"model={rec['action'].get('id') or rec['action'].get('name')}"
        room.append(room.line_for("fieldkit-hog", "", "stop", obj,
                                  f"hog {rec['action']['kind']} {'OK' if rec['ok'] else 'FAILED'}: {rec['why']} | RAM "
                                  f"{rec['ram_before'] / 2**30:.1f} -> {rec['ram_after'] / 2**30:.1f} GB | {rec['result']}"))
    except Exception:
        pass


def act(cfg=None, facts=None, found=None, dry=False, settle=3.0, do=None):
    cfg = cfg or config()
    if facts is None or found is None:
        facts, found = check(cfg, sample=True)
    done = []
    for f in found:
        a = f.get("action")
        if not a or not allowed(a, cfg):
            continue
        if dry:
            done.append({"action": a, "why": f["line"], "ok": None, "result": "dry run"})
            continue
        before = _ram_used()
        try:
            if do:
                ok, res = do(a)
            elif a["kind"] == "unload_lms":
                ok, res = unload_lms(a, facts)
            elif a["kind"] == "unload_ollama":
                ok, res = unload_ollama(a, facts, cfg)
            else:
                ok, res = kill(a)
        except Exception as e:
            ok, res = False, str(e)[:200]
        time.sleep(settle)
        rec = {"t": time.time(), "at": rules.stamp(), "action": a, "why": f["line"], "numbers": f["numbers"],
               "ok": ok, "result": res, "ram_before": before, "ram_after": _ram_used()}
        log_action(rec)
        done.append(rec)
    return done


def preflight_check(ctx=None, cfg=None, facts=None, ram_fn=None, do=None):
    """The hog's check in the ONE preflight (fieldkit/core/preflight.py STEPS) before every guarded run: one pass
    without the temperature (the run guard samples it), the safe actions (idle models unloaded, dead sessions'
    orphans stopped), then the free-RAM floor. -> (status, one line); "stop" only when free RAM is still under
    preflight_min_free_pct AFTER reclaiming. Raising is left to the preflight, which turns it into a WARN."""
    from . import probe, registry
    cfg = cfg or config()
    if facts is None:
        reg = registry.load()
        facts = probe.gather(cfg, keep={(e["pid"], e["ctime"]) for e in reg}, with_temp=False)
    facts, found = check(cfg, facts=facts, sample=True)
    done = act(cfg, facts, found, settle=1.0, do=do)
    used, total = (ram_fn or _ram_now)()
    free_pct = (total - used) * 100.0 / total
    freed = sum(max(0, d["ram_before"] - d["ram_after"]) for d in done if d.get("ok"))
    line = f"hog: RAM {used / 2**30:.1f}/{total / 2**30:.1f} GB ({100 - free_pct:.0f}%)"
    if done:
        line += f", {len(done)} action(s), reclaimed {freed / 2**30:.1f} GB"
    if free_pct < cfg["preflight_min_free_pct"]:
        top = ", ".join(rules._top(facts.get("procs", []), 3))
        return "stop", (f"{line}: {free_pct:.0f}% free is under the floor of {cfg['preflight_min_free_pct']}% "
                        f"after reclaiming what is safe; top: {top}")
    rest = [f for f in found if f["check"] not in ("ram", "commit") and not f.get("action")]
    if rules.worst(rest) or any(f["severity"] != "ok" for f in found if f["check"] in ("ram", "commit")):
        return "warn", line + "; " + rules.summary(found)[len("hog: "):]
    return "ok", line


# ---- the prompt hook's one line ----------------------------------------------------------------------------------
def hook_line(now=None, ram_fn=None):
    """-> one line when something is wrong, else "". Reads the last watch's result (written <= 15 min ago) and the
    live RAM; measures nothing slow (it runs on every prompt)."""
    try:
        now = now or time.time()
        cfg = config()
        parts = []
        try:
            last = json.loads((state_dir() / "last.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = None
        if last and now - last.get("t", 0) <= cfg["hook_fresh_min"] * 60 and last.get("severity", 0) > 0:
            parts.append(last["summary"])
        used, total = (ram_fn or _ram_now)()
        pct = used * 100.0 / total
        if pct >= cfg["ram_warn_pct"] and not parts:
            parts.append(f"hog: RAM {used / 2**30:.1f}/{total / 2**30:.1f} GB ({pct:.0f}%) now; fieldkit hog check")
        return (" | ".join(parts))[:400]
    except Exception:
        return ""


def _ram_now():
    import psutil
    v = psutil.virtual_memory()
    return v.total - v.available, v.total


# ---- the CLI -----------------------------------------------------------------------------------------------------
def _dur(s):
    s = str(s).strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return int(float(s[:-1]) * mult[s[-1]]) if s[-1] in mult else int(s)


def main(argv=None, prog="fieldkit hog"):
    ap = argparse.ArgumentParser(prog=prog, description="\n\n".join(__doc__.split("\n\n")[:2]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check")
    c.add_argument("--json", action="store_true")
    a = sub.add_parser("act")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--json", action="store_true")
    sub.add_parser("watch")
    sub.add_parser("preflight")
    r = sub.add_parser("register")
    r.add_argument("pid", type=int)
    r.add_argument("--cap", default=None, help="e.g. 90m, 3h (default: orphan_cap_h)")
    r.add_argument("--why", default="")
    lg = sub.add_parser("log")
    lg.add_argument("n", nargs="?", type=int, default=20)
    sub.add_parser("doctor")
    i = sub.add_parser("install")
    i.add_argument("--apply", action="store_true")
    i.add_argument("--remove", action="store_true")
    ns = ap.parse_args(argv)
    cmd = ns.cmd or "check"

    if cmd == "check":
        facts, found = check()
        if getattr(ns, "json", False):
            print(json.dumps({"summary": rules.summary(found), "severity": rules.worst(found), "findings": found,
                              "ram": facts["ram"], "commit": facts["commit"], "temp": facts["temp"],
                              "lms": {k: v for k, v in facts["lms"].items() if k != "activity"},
                              "ollama": facts["ollama"], "disks": facts["disks"],
                              "top": rules._top(facts["procs"], config()["top_n"])}, indent=1))
        else:
            print(rules.summary(found))
        return rules.worst(found)
    if cmd in ("act", "watch"):
        cfg = config()
        facts, found = check(cfg, sample=True)
        done = act(cfg, facts, found, dry=getattr(ns, "dry_run", False))
        if getattr(ns, "json", False):
            print(json.dumps({"summary": rules.summary(found), "actions": done}, indent=1))
        else:
            print(rules.summary(found))
            for d in done:
                print(f"  {d['action']['kind']}: {d['result']} ({'OK' if d['ok'] else 'dry run' if d['ok'] is None else 'FAILED'})")
        return rules.worst(found)
    if cmd == "preflight":
        from ..core import preflight
        return preflight.RANK[preflight.run("fieldkit hog preflight", steps=preflight.STEPS)["status"]]
    if cmd == "register":
        import psutil
        from . import registry
        try:
            p = psutil.Process(ns.pid)
        except psutil.Error as e:
            print(f"hog register: {e}")
            return 3
        sess = registry.session_process()
        from .probe import ct
        e = registry.entry(p, rules.orphan_class(p.name(), " ".join(p.cmdline()), config()["orphan_classes"]) or "registered",
                           os.environ.get("CLAUDE_SESSION_ID"),
                           (sess.pid, ct(sess.create_time())) if sess else None,
                           _dur(ns.cap) if ns.cap else None, "register", ns.why)
        added = registry.add([e])
        print(f"hog: registered {e['name']} {e['pid']}" + (" (already registered)" if not added else "")
              + f", cap {(e['cap_s'] or config()['orphan_cap_h'] * 3600) / 3600:.1f} h")
        return 0
    if cmd == "log":
        p = state_dir() / "actions.jsonl"
        for line in (p.read_text(encoding="utf-8").splitlines()[-ns.n:] if p.is_file() else []):
            d = json.loads(line)
            print(f"{d['at']}  {d['action']['kind']:<13} {'OK ' if d['ok'] else 'BAD'}  {d['why'][:110]}  "
                  f"RAM {d['ram_before'] / 2**30:.1f}->{d['ram_after'] / 2**30:.1f} GB")
        return 0
    from . import install
    if cmd == "doctor":
        ok, lines = install.doctor()
        print("\n".join(lines))
        return 0 if ok else 1
    if cmd == "install":
        return install.main(apply=ns.apply, remove=ns.remove)
    return 3


if __name__ == "__main__":
    sys.exit(main())
