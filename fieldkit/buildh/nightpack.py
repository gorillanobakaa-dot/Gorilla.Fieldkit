# Version: 1.0.0 · updated 26-10-19
"""The night pack: every owner-only step the night may need, approved ONCE before bed, run by Windows when it is due.

    fieldkit build-harness night-pack TASK prepare [release=TAG] [rejudge=RUN] [dep=REF] [rerun=RUN]
    fieldkit build-harness night-pack TASK arm [hours=12]      the owner, at their own terminal: shows it all, one yes
    fieldkit build-harness night-pack TASK run                  Windows' scheduled task, every 10 minutes
    fieldkit build-harness night-pack TASK status | disarm

Owner, 2026-10-19, after a night at the keyboard: "you prepare in advance all the preapproved commands you want me to
preapprove. I preapprove it and then go to sleep ... an emergency release for moments when the developer is super
exhausted or DEADLINES". What can be pre-approved, each bound to conditions checked when it runs:

  rejudge   a leak-gate re-judge of RUN (same recorded evidence, judge versions already committed), only when no policy
            GAINS an item: it may only turn FAIL into PASS (the judge passes a destination only for an allowed host)
  release   the release-publish plan for TAG (state/publish/TAG.plan.json, prepared and proven), only when the leak
            gate is a release PASS on the installed build and the plan's assets are unchanged; the old release is
            marked superseded only after GitHub's digests match (release-publish send does that order itself)

Never in a pack: allowlist entries or dispositions, the decision register, harness or hook changes, anything about
telemetry. Those wait for the owner in the morning report.

Safety: arm needs the owner's terminal (task.owner_terminal) and stores the pack's SHA-256; run refuses a pack that
changed since it was armed, an AI agent's process tree, or an expired arming, runs each action at most once, disarms at
the first failure (the scheduled task is removed), and writes and speaks the morning report.
"""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from . import task

DIR = task.STATE.parent / "nightpack"
TASK_NAME = "Gorilla night pack"


def _p(task_id, name):
    return DIR / f"{task_id}.{name}.json"


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def prepare(task_id, release=None, rejudge=None, dep=None, rerun=None, gate=True, say=print):
    """-> the pack (list of actions in order), written to state/nightpack/TASK.pack.json."""
    actions = []
    if rejudge:
        actions.append({"kind": "rejudge", "run": rejudge, "dep": dep, "rerun": rerun,
                        "condition": "no policy gains an item; the re-judged FINAL_RESULT is PASS"})
    if release:
        plan = DIR.parent / "publish" / f"{release}.plan.json"
        if not plan.is_file():
            raise task.Refused(f"no release-publish plan for {release}: run release-publish prepare first")
        actions.append({"kind": "release", "tag": release, "plan_sha256": _sha(plan), "gate": bool(gate),
                        "condition": "leak gate release PASS on the installed build; plan assets unchanged; "
                                     "the old release superseded only after GitHub's digests match"})
    if not actions:
        raise task.Refused("an empty night pack: give release=TAG and/or rejudge=RUN")
    pack = {"task": task_id, "prepared": time.strftime("%Y-%m-%dT%H:%M:%S"), "actions": actions,
            "never": "allowlist, dispositions, decision register, harness and hooks, telemetry: these wait for the owner"}
    DIR.mkdir(parents=True, exist_ok=True)
    _p(task_id, "pack").write_text(json.dumps(pack, indent=1), encoding="utf-8")
    for i, a in enumerate(actions, 1):
        say(f"  {i}. {a['kind']} {a.get('tag') or a.get('run')}: {a['condition']}")
    say(f"NIGHT PACK PREPARED: {_p(task_id, 'pack')} - the owner arms it (night-pack {task_id} arm)")
    return pack


def _scheduler_register(task_id, hours, runner=subprocess.run):
    """One scheduled task under the owner's account, every 10 minutes until the expiry (no elevation)."""
    py = sys.executable
    end = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + hours * 3600))
    ps = (f"$a = New-ScheduledTaskAction -Execute '{py}' -Argument '-m fieldkit build-harness night-pack {task_id} run' "
          f"-WorkingDirectory '{task.STATE.parents[1]}'; "
          f"$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10) "
          f"-RepetitionDuration (New-TimeSpan -Hours {hours}); "
          f"$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -WakeToRun "
          f"-ExecutionTimeLimit (New-TimeSpan -Hours 2) -DeleteExpiredTaskAfter (New-TimeSpan -Hours 1); "
          f"$t.EndBoundary = '{end}'; "
          f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t -Settings $s -Force | Out-Null; 'registered'")
    r = runner(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, errors="replace")
    return r.returncode == 0 and "registered" in (r.stdout or ""), (r.stdout + r.stderr).strip()[-300:]


def _scheduler_remove(runner=subprocess.run):
    runner(["powershell", "-NoProfile", "-Command",
            f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false -ErrorAction SilentlyContinue"],
           capture_output=True, text=True, errors="replace")


def arm(task_id, hours=12, say=print, confirm=None, owner=None, register=None):
    owner = task.owner_terminal if owner is None else owner
    if not owner():
        say("REFUSED: arming the night pack is the owner's, at their own terminal: " + task.agent_shell())
        return 3
    pack = _load(_p(task_id, "pack"))
    if not pack:
        say(f"REFUSED: no prepared night pack for {task_id}")
        return 3
    say(f"NIGHT PACK for {task_id}, prepared {pack['prepared']}, valid {hours} h from now:")
    for i, a in enumerate(pack["actions"], 1):
        say(f"  {i}. {a['kind']} {a.get('tag') or a.get('run')}: {a['condition']}")
        if a["kind"] == "release":
            plan = _load(DIR.parent / "publish" / f"{a['tag']}.plan.json", {})
            say(f"       title: {plan.get('title')} | repo {plan.get('gh_repo')} | push {plan.get('pushes')}")
            for n, x in (plan.get("assets") or {}).items():
                say(f"       asset {n} sha256 {x['sha256']}")
            if plan.get("supersede"):
                say(f"       then: {plan['supersede']} -> {plan.get('supersede_title')}")
    say(f"  never: {pack['never']}")
    if confirm is None:
        confirm = input("Pre-approve all of this for the night? Type yes: ").strip().lower() == "yes"
    if not confirm:
        say("not armed")
        return 1
    ok, out = (register or _scheduler_register)(task_id, hours)
    if not ok:
        say(f"NOT ARMED: Windows did not take the scheduled task: {out}")
        return 3
    armed = {"task": task_id, "pack_sha256": _sha(_p(task_id, "pack")), "armed": time.time(),
             "expires": time.time() + hours * 3600, "done": []}
    _p(task_id, "armed").write_text(json.dumps(armed, indent=1), encoding="utf-8")
    say(f"ARMED until {time.strftime('%H:%M', time.localtime(armed['expires']))}: Windows checks every 10 minutes. "
        "Good night.")
    return 0


def _report(task_id, lines, speak=None):
    DIR.mkdir(parents=True, exist_ok=True)
    rp = DIR / f"{task_id}.morning.md"
    with open(rp, "a", encoding="utf-8") as f:
        f.write("\n".join(f"- {time.strftime('%H:%M')} {l}" for l in lines) + "\n")
    try:
        from .herald import say as _herald
        (speak or _herald)(lines[-1])
    except Exception:
        pass


def run(task_id, say=print, rejudge=None, gate_ok=None, send=None, agent=None, speak=None, remove=None):
    """One scheduled pass: each pending action whose conditions hold runs once; the first failure disarms."""
    agent = task.agent_shell if agent is None else agent
    remove = remove or _scheduler_remove
    why = agent()
    if why and "agent" in why:
        say("REFUSED: the night pack runs only from Windows' scheduler, never from an AI agent: " + why)
        return 3
    armed = _load(_p(task_id, "armed"))
    if not armed:
        say("not armed")
        return 0
    if time.time() > armed["expires"]:
        remove()
        _p(task_id, "armed").unlink()
        _report(task_id, ["The night pack expired; nothing more ran."], speak)
        return 0
    if _sha(_p(task_id, "pack")) != armed["pack_sha256"]:
        remove()
        _report(task_id, ["STOPPED: the night pack changed after it was armed. Nothing ran. Look at it in the morning."], speak)
        return 3
    pack = _load(_p(task_id, "pack"))
    for i, a in enumerate(pack["actions"]):
        if i in armed["done"]:
            continue
        if a["kind"] == "rejudge":
            res = (rejudge or _rejudge)(task_id, a)
            if res is None:
                return 0                                              # not due yet: the next pass looks again
            ok, line = res
        elif a["kind"] == "release":
            if a.get("gate", True) and not (gate_ok or _gate_ok)(task_id):
                return 0                                              # the gate is not a release PASS yet: wait
            plan = DIR.parent / "publish" / f"{a['tag']}.plan.json"
            if _sha(plan) != a["plan_sha256"]:
                ok, line = False, f"the release plan for {a['tag']} changed after arming"
            else:
                from . import releasepublish as rp
                rc = (send or rp.send)(a["tag"], say=say, confirm=True, owner=lambda: True)
                ok, line = rc == 0, (f"{a['tag']} is live and verified" if rc == 0 else f"{a['tag']} stopped (rc {rc})")
        else:
            ok, line = False, f"unknown action {a['kind']}"
        armed["done"].append(i)
        _p(task_id, "armed").write_text(json.dumps(armed, indent=1), encoding="utf-8")
        if not ok:
            remove()
            _report(task_id, [f"STOPPED at {a['kind']}: {line}. Nothing after it ran."], speak)
            return 3
        _report(task_id, [f"done: {a['kind']}: {line}"], speak)
    remove()
    _p(task_id, "armed").unlink()
    _report(task_id, ["The night pack is complete. Everything in it ran and was verified."], speak)
    return 0


def _gate_ok(task_id):
    from . import buildrun, install as inst
    lg = _load(Path(buildrun._owner_root(task.load(task_id))) / "state" / "leakgate_result.json", {})
    ini = Path(inst.find_install() or "") / "application.ini"
    bid = next((l.split("=", 1)[1].strip() for l in ini.read_text(encoding="utf-8").splitlines()
                if l.startswith("BuildID=")), None) if ini.is_file() else None
    return lg.get("release_run") is True and lg.get("FINAL_RESULT") == "PASS" and str(lg.get("BUILD")) == str(bid)


def _rejudge(task_id, a):
    """-> None (not due: the run is still going), or (ok, line). Refuses a re-judge that ADDS an item anywhere."""
    from ..leakgate import gate as lg, status
    from . import buildrun
    if status.gate_running():
        return None
    root = task.STATE / "leakgate" / task_id
    t = task.load(task_id)
    new, changes = lg.rejudge(root / a["run"], Path(buildrun._owner_root(t)), why="night pack (owner pre-approved)",
                              dependency_baseline=(t["workdir"], a["dep"]) if a.get("dep") else None,
                              rerun=root / a["rerun"] if a.get("rerun") else None)
    added = {p: c["added"] for p, c in changes.items() if c.get("added")}
    if added:
        return False, f"the re-judge ADDED items ({list(added)}): not pre-approvable, the owner decides"
    return new["FINAL_RESULT"] == "PASS", f"re-judged {a['run']}: FINAL_RESULT {new['FINAL_RESULT']}"


def status(task_id):
    return {"pack": _load(_p(task_id, "pack")), "armed": _load(_p(task_id, "armed"))}


def disarm(task_id, say=print):
    _scheduler_remove()
    a = _p(task_id, "armed")
    if a.is_file():
        a.unlink()
    say("disarmed")
    return 0
