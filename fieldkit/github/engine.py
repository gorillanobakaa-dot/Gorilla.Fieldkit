"""fieldkit github engine: read a repository, compare it with a profile, find known mistakes, apply with consent, undo.

No model anywhere. Every GitHub call goes through `api()` (the one function tests replace); every other program
(git, gh login, privacy scan) goes through `_exec()`. The harness never handles a token or password: `gh auth login`
is typed by the person. Text, menu paths and risks live in profiles.yaml / glossary.yaml / doctor.yaml.
"""
import copy
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SAMPLE_BRANCH = "feat/example"            # a non-default branch name, to ask "does this rule reach feature branches?"
RULESET_KEYS = ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")
SECRET_FILES = (".env", "id_rsa", "id_ed25519", ".pem", ".key", ".pfx", ".p12", "credentials", ".npmrc", ".netrc")


class GhError(Exception):
    """GitHub (or gh) refused or failed; `.text` is gh's own message, shown to the person unchanged."""

    def __init__(self, text, status=None):
        super().__init__(text)
        self.text = text
        self.status = status


def _load(name):
    return yaml.safe_load((HERE / name).read_text(encoding="utf-8"))


def catalogue():
    return _load("profiles.yaml")


def glossary():
    return _load("glossary.yaml")["terms"]


def doctor_data():
    return _load("doctor.yaml")


def _exec(argv, stdin=None, timeout=120):
    """-> (returncode, stdout, stderr). A missing program is returncode 127."""
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[0]}: timed out"
    return p.returncode, p.stdout or "", p.stderr or ""


def api(method, path, body=None):
    """The one GitHub call. -> parsed JSON ({} for an empty answer); raises GhError with gh's message."""
    argv = ["gh", "api", "-X", method, path]
    if body is not None:
        argv += ["--input", "-"]
    rc, out, err = _exec(argv, stdin=json.dumps(body) if body is not None else None)
    if rc != 0:
        msg = (err or out).strip()
        m = re.search(r"HTTP (\d{3})", msg)
        raise GhError(msg, int(m.group(1)) if m else None)
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except ValueError:
        return {"raw": out}


# ---------------------------------------------------------------- repository and prerequisites

def parse_remote(url):
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", (url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def resolve_repo(arg=None):
    if arg:
        return arg
    rc, out, _ = _exec(["git", "remote", "get-url", "origin"])
    return parse_remote(out) if rc == 0 else None


def doctor(repo=None):
    """-> list of {id, ok, text, fix}: git, gh, login, remote, admin. Later steps are skipped when an earlier one fails."""
    data = doctor_data()["steps"]
    plat = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    res = []

    def add(sid, ok):
        s = data[sid]
        fix = "" if ok else (s["fix"].get(plat) or s["fix"].get("any", ""))
        res.append({"id": sid, "ok": ok, "text": s["ok"] if ok else s["missing"], "fix": fix})
        return ok

    if not add("git", shutil.which("git") is not None):
        return res
    if not add("gh", shutil.which("gh") is not None):
        return res
    if not add("login", _exec(["gh", "auth", "status"])[0] == 0):
        return res
    repo = resolve_repo(repo)
    if not add("remote", bool(repo)):
        return res
    try:
        admin = bool((api("GET", f"repos/{repo}").get("permissions") or {}).get("admin"))
    except GhError:
        admin = False
    add("admin", admin)
    return res


# ---------------------------------------------------------------- reading a repository

def _try(method, path, default=None):
    try:
        return api(method, path)
    except GhError:
        return default


def read_state(repo):
    """Everything the checks need, as plain JSON-able data. Raises GhError when the repository itself cannot be read."""
    info = api("GET", f"repos/{repo}")
    default = info.get("default_branch") or "main"
    st = {"repo": repo, "default": default, "info": info, "notes": []}
    listed = _try("GET", f"repos/{repo}/rulesets")
    if listed is None:
        st["notes"].append("rulesets could not be read (no admin rights, or this plan has no rulesets)")
    st["rulesets"] = [r for r in (_try("GET", f"repos/{repo}/rulesets/{x['id']}", x) for x in (listed or []))]
    br = _try("GET", f"repos/{repo}/branches?per_page=100")
    st["branches"] = [b["name"] for b in br] if isinstance(br, list) else []
    closed = _try("GET", f"repos/{repo}/pulls?state=closed&per_page=10")
    closed = closed if isinstance(closed, list) else []
    st["merged_heads"] = [p["head"]["ref"] for p in closed
                          if p.get("merged_at") and (p.get("head", {}).get("repo") or {}).get("full_name") == repo]
    shas = [default] + [p["head"]["sha"] for p in closed[:5] if p.get("head", {}).get("sha")]
    ran, ok = set(), False
    for s in shas:
        r = _try("GET", f"repos/{repo}/commits/{s}/check-runs?per_page=100")
        if isinstance(r, dict) and "check_runs" in r:
            ok = True
            ran |= {c["name"] for c in r["check_runs"]}
    st["ran"] = sorted(ran)
    st["ran_readable"] = ok
    cl = _try("GET", f"repos/{repo}/branches/{default}/protection/required_status_checks")
    st["classic_checks"] = list((cl or {}).get("contexts") or []) if isinstance(cl, dict) else []
    st["dependabot_alerts"] = _try("GET", f"repos/{repo}/vulnerability-alerts") is not None
    acts = _try("GET", f"repos/{repo}/actions/permissions")
    st["actions_enabled"] = acts.get("enabled") if isinstance(acts, dict) else None
    st["files"] = {"license_file": _try("GET", f"repos/{repo}/contents/LICENSE") is not None,
                   "security_policy": _try("GET", f"repos/{repo}/contents/SECURITY.md") is not None,
                   "code_owners_file": any(_try("GET", f"repos/{repo}/contents/{p}") is not None
                                           for p in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"))}
    return st


def _match(patterns, branch, default):
    for p in patterns:
        if p == "~ALL" or (p == "~DEFAULT_BRANCH" and branch == default):
            return True
        if p not in ("~DEFAULT_BRANCH",) and fnmatch.fnmatchcase(f"refs/heads/{branch}", p):
            return True
    return False


def covers(rs, branch, default):
    """True when this ruleset is active, aimed at branches, and its include/exclude reach `branch`."""
    if rs.get("target") != "branch" or rs.get("enforcement") != "active":
        return False
    ref = (rs.get("conditions") or {}).get("ref_name") or {}
    return _match(ref.get("include") or [], branch, default) and not _match(ref.get("exclude") or [], branch, default)


def _rules(st, branch=None):
    d = st["default"]
    branch = branch or d
    return [x for r in st["rulesets"] if covers(r, branch, d) for x in r.get("rules") or []]


def values(st):
    """Setting id -> the value the repository has now (effective over every active ruleset reaching the default branch)."""
    info, d = st["info"], st["default"]
    rules = _rules(st)

    def of(t):
        return [x.get("parameters") or {} for x in rules if x.get("type") == t]
    pr, rsc = of("pull_request"), of("required_status_checks")
    sa = info.get("security_and_analysis") or {}
    checks = sorted({c["context"] for p in rsc for c in p.get("required_status_checks") or []}
                    | set(st.get("classic_checks") or []))
    v = {
        "ruleset_targets_main": any(covers(r, d, d) for r in st["rulesets"]),
        "block_deletion": any(x.get("type") == "deletion" for x in rules),
        "block_force_push": any(x.get("type") == "non_fast_forward" for x in rules),
        "linear_history": any(x.get("type") == "required_linear_history" for x in rules),
        "freeze": any(x.get("type") == "update" for x in rules),
        "pr_required": bool(pr),
        "approvals": max([p.get("required_approving_review_count", 0) for p in pr] or [0]),
        "dismiss_stale": any(p.get("dismiss_stale_reviews_on_push") for p in pr),
        "resolve_conversations": any(p.get("required_review_thread_resolution") for p in pr),
        "last_push_approval": any(p.get("require_last_push_approval") for p in pr),
        "code_owner_review": any(p.get("require_code_owner_review") for p in pr),
        "required_checks": checks,
        "checks_up_to_date": any(p.get("strict_required_status_checks_policy") for p in rsc),
        "auto_merge": bool(info.get("allow_auto_merge")),
        "delete_branch_on_merge": bool(info.get("delete_branch_on_merge")),
        "secret_scanning": (sa.get("secret_scanning") or {}).get("status") == "enabled",
        "push_protection": (sa.get("secret_scanning_push_protection") or {}).get("status") == "enabled",
        "dependabot_alerts": bool(st.get("dependabot_alerts")),
        "actions_enabled": st.get("actions_enabled"),
        "private": bool(info.get("private")),
    }
    v.update(st.get("files") or {})
    return v


def same(a, b):
    if isinstance(a, list) or isinstance(b, list):
        return set(a or []) == set(b or [])
    return a == b


def fmt(v):
    if v is True:
        return "on"
    if v is False:
        return "off"
    if v is None:
        return "unknown"
    if isinstance(v, list):
        return ", ".join(v) if v else "none"
    return str(v)


# ---------------------------------------------------------------- plan

def resolve_wanted(profile, st, checks=None):
    """The profile's settings with 'ran-on-main' replaced by the check names that really ran, or by `checks`."""
    want = dict(profile.get("settings") or {})
    if want.get("required_checks") == "ran-on-main":
        want["required_checks"] = sorted(checks) if checks else list(st.get("ran") or [])
    elif checks and "required_checks" in want:
        want["required_checks"] = sorted(checks)
    return want


def blockers(st, want, checks=None):
    """Setting id -> why it must not be applied now (the mistakes this tool exists to stop)."""
    cur, bad = values(st), {}
    ran = set(st.get("ran") or [])
    if "required_checks" in want:
        names = want["required_checks"]
        unknown = [n for n in names if n not in ran] if st.get("ran_readable") else []
        if not names:
            bad["required_checks"] = ("no check has run on this repository yet, so there is no real name to require "
                                      "(a name that never runs would block every pull request forever)")
        elif unknown:
            bad["required_checks"] = (f"{', '.join(unknown)} never ran on this repository. Checks that did: "
                                      f"{', '.join(sorted(ran)) or 'none'}")
    checks_after = cur["required_checks"] if "required_checks" in bad or "required_checks" not in want \
        else want["required_checks"]
    if want.get("checks_up_to_date") and not checks_after:
        bad["checks_up_to_date"] = "there is no required check to keep up to date"
    approvals_after = want.get("approvals", cur["approvals"]) if "approvals" not in bad else cur["approvals"]
    if want.get("auto_merge") and not checks_after and not approvals_after:
        bad["auto_merge"] = ("no required checks and no approvals: auto-merge would merge a pull request at once, "
                             "before CI runs")
    if want.get("actions_enabled") is False and checks_after:
        bad["actions_enabled"] = "required checks exist and cannot run with Actions off: every merge would be blocked"
    return bad


def make_plan(profile, st, checks=None):
    """-> {changes: [{id, current, wanted, blocked}], notes: []}. Only settings that differ are listed."""
    cur = values(st)
    want = resolve_wanted(profile, st, checks)
    bad = blockers(st, want, checks)
    changes = [{"id": sid, "current": cur.get(sid), "wanted": w, "blocked": bad.get(sid)}
               for sid in catalogue()["settings"] if sid in want for w in [want[sid]] if not same(cur.get(sid), w) or sid in bad]
    return {"changes": changes, "notes": list(st.get("notes") or [])}


# ---------------------------------------------------------------- the known mistakes (check)

def _ruleset_label(r):
    return f"ruleset '{r.get('name')}'"


def find_mistakes(st):
    """-> list of {id, text, fix}: the settings mistakes this tool knows, each in plain words with the exact fix."""
    out, d, info = [], st["default"], st["info"]
    cat = catalogue()["settings"]
    cur = values(st)
    path = cat["ruleset_targets_main"]["where"].split(" (")[0]
    branch_rs = [r for r in st["rulesets"] if r.get("target") == "branch" and r.get("enforcement") == "active"]
    if not st["rulesets"] and not st.get("classic_checks"):
        out.append({"id": "no-protection", "text": "Nothing protects main: this repository has no ruleset. "
                    f"Rulesets are under {path} (not under Settings > Security).",
                    "fix": "fieldkit github apply full-speed-ahead   (or long-horizon)"})
    for r in branch_rs:                                                           # mistake 2
        ref = (r.get("conditions") or {}).get("ref_name") or {}
        hit = [b for b in st["branches"] if _match(ref.get("include") or [], b, d)
               and not _match(ref.get("exclude") or [], b, d)]
        if not hit:
            out.append({"id": "ruleset-no-target", "text": f"{_ruleset_label(r)} is active but applies to no branch "
                        f"(include list: {ref.get('include') or 'empty'}). It protects nothing.",
                        "fix": f"{path} > open it > Target branches > add 'Default branch'; or: fieldkit github apply PROFILE"})
    ran = set(st.get("ran") or [])
    if cur["required_checks"] and st.get("ran_readable"):                         # mistake 3
        bad = [n for n in cur["required_checks"] if n not in ran]
        if bad:
            out.append({"id": "check-name-unknown", "text": f"Required check(s) {', '.join(bad)} never ran on this "
                        f"repository. Checks that did: {', '.join(sorted(ran)) or 'none'}. Every pull request would "
                        "wait forever.",
                        "fix": f"{path} > open the ruleset > Require status checks > remove the wrong name, add a real one"})
    if cur["last_push_approval"]:                                                 # mistake 4
        out.append({"id": "unattributed-approval", "text": "'Require approval of the most recent reviewable push' is "
                    "on. Commits made by programs, such as fieldkit@localhost (no GitHub user), then wait for an "
                    "approval nobody can give.",
                    "fix": f"{path} > open the ruleset > Require a pull request > untick that box"})
    if cur["auto_merge"] and not cur["required_checks"] and not cur["approvals"]:  # mistake 5
        out.append({"id": "automerge-no-checks", "text": "Auto-merge is allowed but no check or approval is required: "
                    "a pull request would merge at once, before CI runs.",
                    "fix": "turn off Settings > General > Pull Requests > Allow auto-merge, or require checks first: "
                           "fieldkit github apply long-horizon"})
    if cur["delete_branch_on_merge"]:                                             # mistake 6
        blockers_ = [r for r in branch_rs if covers(r, SAMPLE_BRANCH, d)
                     and any(x.get("type") == "deletion" for x in r.get("rules") or [])]
        for r in blockers_:
            out.append({"id": "branches-not-deleted", "text": f"'Delete branch after merge' is on, but "
                        f"{_ruleset_label(r)} forbids deleting every branch it covers, feature branches included, "
                        "so merged branches stay.",
                        "fix": f"{path} > open it > Target branches: keep only the default branch (main)"})
        stale = [b for b in st.get("merged_heads") or [] if b in st["branches"] and b != d]
        if stale and not blockers_:
            out.append({"id": "branches-not-deleted", "text": f"Merged branches still exist: {', '.join(stale[:5])}. "
                        "Usual causes: the setting was turned on after they merged, or they were merged by something "
                        "other than a pull request.", "fix": "delete them on the Branches page, or: git push origin --delete NAME"})
    return out


def closest_profile(st):
    cat = catalogue()["profiles"]
    best = None
    for pid, p in cat.items():
        if p.get("mode") or not p["settings"]:
            continue
        n = len(make_plan(p, st)["changes"])
        if best is None or n < best[0]:
            best = (n, pid)
    return best[1]


def check(st, profile_id=None):
    """-> {profile, findings: [{id,text,fix}]}. Findings: known mistakes first, then profile differences."""
    cat = catalogue()
    prof = cat["profiles"].get(profile_id) if profile_id else None
    if profile_id and (prof is None or prof.get("mode") == "audit"):
        pid = closest_profile(st)
    else:
        pid = profile_id
    findings = find_mistakes(st)
    if pid:
        for c in make_plan(cat["profiles"][pid], st)["changes"]:
            s = cat["settings"][c["id"]]
            why = f" Blocked: {c['blocked']}." if c["blocked"] else ""
            findings.append({"id": f"differs:{c['id']}", "text": f"{s['title']}: is {fmt(c['current'])}, profile "
                             f"'{pid}' wants {fmt(c['wanted'])}.{why}",
                             "fix": f"{s['where']}  or: fieldkit github apply {pid}"})
    return {"profile": pid, "findings": findings}


# ---------------------------------------------------------------- setting values

def _managed(st):
    rs = st["rulesets"]
    named = [r for r in rs if r.get("name") == catalogue()["ruleset_name"]]
    if named:
        return named[0]
    d = st["default"]
    cov = [r for r in rs if covers(r, d, d)]
    if len(cov) == 1:
        return cov[0]
    dead = [r for r in rs if r.get("target") == "branch" and r.get("enforcement") == "active" and not cov]
    return dead[0] if len(dead) == 1 else None


def _body(rs):
    return {k: copy.deepcopy(rs[k]) for k in RULESET_KEYS if k in rs}


def _skeleton():
    return {"name": catalogue()["ruleset_name"], "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}, "rules": [], "bypass_actors": []}


PR_DEFAULTS = {"required_approving_review_count": 0, "dismiss_stale_reviews_on_push": False,
               "require_code_owner_review": False, "require_last_push_approval": False,
               "required_review_thread_resolution": False}
RSC_DEFAULTS = {"strict_required_status_checks_policy": False, "required_status_checks": []}
SIMPLE_RULES = {"block_deletion": "deletion", "block_force_push": "non_fast_forward",
                "linear_history": "required_linear_history", "freeze": "update"}
PR_PARAMS = {"approvals": "required_approving_review_count", "dismiss_stale": "dismiss_stale_reviews_on_push",
             "resolve_conversations": "required_review_thread_resolution",
             "last_push_approval": "require_last_push_approval", "code_owner_review": "require_code_owner_review"}


def _rule(body, t, defaults=None):
    for x in body["rules"]:
        if x["type"] == t:
            return x
    if defaults is None:
        return None
    x = {"type": t, "parameters": copy.deepcopy(defaults)}
    body["rules"].append(x)
    return x


def _drop(body, t):
    body["rules"] = [x for x in body["rules"] if x["type"] != t]


def _edit_ruleset(sid, value, body):
    if sid in SIMPLE_RULES:
        t = SIMPLE_RULES[sid]
        _drop(body, t)
        if value:
            body["rules"].append({"type": t})
    elif sid == "pr_required":
        _drop(body, "pull_request")
        if value:
            _rule(body, "pull_request", PR_DEFAULTS)
    elif sid in PR_PARAMS:
        _rule(body, "pull_request", PR_DEFAULTS)["parameters"][PR_PARAMS[sid]] = value
    elif sid == "required_checks":
        old = {c["context"]: c for x in body["rules"] if x["type"] == "required_status_checks"
               for c in x["parameters"].get("required_status_checks", [])}
        strict = (_rule(body, "required_status_checks") or {"parameters": {}})["parameters"] \
            .get("strict_required_status_checks_policy", False)
        _drop(body, "required_status_checks")
        if value:
            body["rules"].append({"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": strict,
                "required_status_checks": [old.get(n, {"context": n}) for n in value]}})
    elif sid == "checks_up_to_date":
        r = _rule(body, "required_status_checks", RSC_DEFAULTS)
        r["parameters"]["strict_required_status_checks_policy"] = bool(value)
    else:
        raise KeyError(sid)


def set_value(st, sid, value):
    """Change one setting on GitHub. `st` is a fresh read. Raises GhError when GitHub refuses."""
    repo, kind = st["repo"], catalogue()["settings"][sid]["kind"]
    if kind == "manual":
        raise GhError("this one is a file you add yourself")
    if kind == "ruleset":
        rs = _managed(st)
        body = _body(rs) if rs else _skeleton()
        if sid == "ruleset_targets_main":
            body["conditions"] = {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}
            body["target"], body["enforcement"] = "branch", "active" if value else "disabled"
        else:
            _edit_ruleset(sid, value, body)
        if rs:
            api("PUT", f"repos/{repo}/rulesets/{rs['id']}", body)
        else:
            api("POST", f"repos/{repo}/rulesets", body)
        return
    status = {True: "enabled", False: "disabled"}
    if sid in ("auto_merge", "delete_branch_on_merge", "private"):
        field = {"auto_merge": "allow_auto_merge"}.get(sid, sid)
        api("PATCH", f"repos/{repo}", {field: bool(value)})
    elif sid == "secret_scanning":
        api("PATCH", f"repos/{repo}", {"security_and_analysis": {"secret_scanning": {"status": status[bool(value)]}}})
    elif sid == "push_protection":
        api("PATCH", f"repos/{repo}", {"security_and_analysis": {
            "secret_scanning_push_protection": {"status": status[bool(value)]}}})
    elif sid == "dependabot_alerts":
        api("PUT" if value else "DELETE", f"repos/{repo}/vulnerability-alerts")
    elif sid == "actions_enabled":
        api("PUT", f"repos/{repo}/actions/permissions", {"enabled": bool(value)})
    else:
        raise KeyError(sid)


def privacy_scan(path="."):
    """-> (clean, text). A public switch is allowed only when this is clean."""
    rc, out, err = _exec([sys.executable, "-m", "fieldkit", "privacy", "scan", path, "--git"], timeout=600)
    return rc == 0, (out + err).strip()[-1500:]


# ---------------------------------------------------------------- glossary

class Gloss:
    """Adds the two-sentence meaning of a word the first time it appears in a text."""

    def __init__(self):
        self.terms, self.seen = glossary(), set()

    def add(self, text):
        notes = []
        for term, meaning in self.terms.items():
            if term in self.seen:
                continue
            flags = 0 if len(term) <= 3 else re.I
            if re.search(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", text, flags):
                self.seen.add(term)
                notes.append(f"    [{term}] {meaning}")
        return text + ("\n" + "\n".join(notes) if notes else "")


# ---------------------------------------------------------------- snapshot, apply, undo

def snapshot_dir():
    from ..core.settings import state_root
    return Path(state_root()) / "state" / "github"


def write_snapshot(st, profile_id, folder=None):
    folder = Path(folder or snapshot_dir())
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{st['repo'].replace('/', '_')}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    n = 1
    while path.exists():
        path = path.with_name(f"{path.stem.split('+')[0]}+{n}.json")
        n += 1
    path.write_text(json.dumps({"repo": st["repo"], "profile": profile_id, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "state": st}, indent=1), encoding="utf-8")
    return path


def latest_snapshot(repo, folder=None):
    folder = Path(folder or snapshot_dir())
    found = sorted(folder.glob(f"{repo.replace('/', '_')}-*.json"), key=lambda p: p.stat().st_mtime) \
        if folder.is_dir() else []
    return found[-1] if found else None


def _explain_block(sid, repo, g, cur=None, want=None, show_change=True):
    s = catalogue()["settings"][sid]
    lines = [f"{s['title']}", f"  Where to find it: {s['where']}", f"  Page: {s['url'].format(repo=repo)}",
             f"  What it does: {s['what']}", f"  Why you want it: {s['why']}", f"  What changes for you: {s['impact']}",
             f"  Risk if wrong: {s['risk']}"]
    if show_change:
        lines.insert(1, f"  Now: {fmt(cur)}   ->   Wanted: {fmt(want)}")
    return g.add("\n".join(lines))


def apply_profile(profile_id, repo, ask=input, out=print, owner=None, checks=None, folder=None):
    """Change the repository to the profile, one setting at a time, each asked first. -> exit code."""
    if owner is None:
        from ..buildh import task
        owner = task.owner_terminal
    if not owner():
        out("REFUSED: apply changes GitHub settings and only a person at a terminal may run it "
            "(an AI agent's shell is not one). Open a terminal yourself, or double-click the launcher.")
        return 1
    cat = catalogue()
    prof = cat["profiles"][profile_id]
    st = read_state(repo)
    if not (st["info"].get("permissions") or {}).get("admin"):
        out(f"REFUSED: you are not an admin of {repo}; GitHub would refuse every change. {doctor_data()['steps']['admin']['fix']['any']}")
        return 1
    plan = make_plan(prof, st, checks)
    todo = [c for c in plan["changes"]]
    if not todo:
        out(f"{repo} already matches '{prof['title']}'. Nothing to change.")
        return 0
    g = Gloss()
    snap = write_snapshot(st, profile_id, folder)
    out(g.add(f"Before anything changes I saved the current settings to:\n  {snap}\nYou can put them back with: "
              f"fieldkit github undo \"{snap}\"   (or: fieldkit github undo latest)"))
    done, skipped, failed, blocked, manual = [], [], [], [], []
    stop = False
    for c in todo:
        sid = c["id"]
        s = cat["settings"][sid]
        out("\n" + _explain_block(sid, repo, g, c["current"], c["wanted"]))
        if s["kind"] == "manual":
            manual.append(sid)
            out(f"  This one is a file you add yourself: {s['set']}. I will not do it for you.")
            continue
        if c["blocked"]:
            blocked.append(sid)
            out(f"  NOT APPLIED: {c['blocked']}.")
            continue
        if sid == "private" and c["wanted"] is False:
            clean, text = privacy_scan(".")
            if not clean:
                blocked.append(sid)
                out(f"  NOT APPLIED: `fieldkit privacy scan` found something. Making the repository public cannot be "
                    f"taken back.\n{text}")
                continue
            out("  `fieldkit privacy scan` found nothing.")
        while True:
            a = ask("  Apply this? [y] yes  [s] skip this one  [n] stop here  [w] why: ").strip().lower()
            if a in ("y", "s", "n"):
                break
            out(f"  {s['why']}\n  How to undo: {s['undo']}" if a == "w" else "  Please type y, s, n or w.")
        if a == "n":
            stop = True
            break
        if a == "s":
            skipped.append(sid)
            continue
        try:
            set_value(read_state(repo), sid, c["wanted"])
            after = values(read_state(repo)).get(sid)
        except GhError as e:
            failed.append(sid)
            out(f"  FAILED: GitHub said: {e.text}")
            continue
        if same(after, c["wanted"]):
            done.append(sid)
            out(f"  DONE and verified: read back {fmt(after)}.")
        else:
            failed.append(sid)
            out(f"  NOT VERIFIED: I set it, but GitHub now reports {fmt(after)}, not {fmt(c['wanted'])}.")
    out(f"\nSummary for {repo}, profile '{prof['title']}':")
    out(f"  changed and verified: {', '.join(done) or 'none'}")
    for label, lst in (("skipped by you", skipped), ("not applied (unsafe now)", blocked),
                       ("you add these files yourself", manual), ("failed", failed)):
        if lst:
            out(f"  {label}: {', '.join(lst)}")
    if stop:
        out("  You stopped early; the rest was not touched.")
    if done or failed:
        out(f"  Put it all back: fieldkit github undo \"{snap}\"")
    return 3 if failed else 0


def undo(snapshot, ask=input, out=print, owner=None):
    """Put the repository back to a snapshot, asked once, verified. Never deletes on GitHub: a ruleset made since is disabled."""
    if owner is None:
        from ..buildh import task
        owner = task.owner_terminal
    if not owner():
        out("REFUSED: undo changes GitHub settings and only a person at a terminal may run it.")
        return 1
    data = json.loads(Path(snapshot).read_text(encoding="utf-8"))
    old, repo = data["state"], data["repo"]
    now = read_state(repo)
    want, cur = values(old), values(now)
    cat = catalogue()["settings"]
    diffs = [sid for sid in cat if cat[sid]["kind"] != "manual" and sid in want and not same(cur.get(sid), want[sid])]
    old_ids = {r["id"] for r in old["rulesets"]}
    fresh = [r for r in now["rulesets"] if r["id"] not in old_ids and r.get("enforcement") != "disabled"]
    if not diffs and not fresh:
        out(f"{repo} already matches the snapshot. Nothing to undo.")
        return 0
    g = Gloss()
    out(g.add(f"Snapshot of {data['time']} (profile {data['profile']}). Putting back:"))
    for sid in diffs:
        out(f"  {cat[sid]['title']}: {fmt(cur.get(sid))} -> {fmt(want[sid])}")
    for r in fresh:
        out(f"  {_ruleset_label(r)} was made after the snapshot: it will be switched to disabled (never deleted).")
    if ask("Put these back? [y/N]: ").strip().lower() != "y":
        out("Nothing changed.")
        return 0
    errors = []
    try:
        for r in old["rulesets"]:                       # rulesets first, whole, exactly as saved
            api("PUT", f"repos/{repo}/rulesets/{r['id']}", _body(r))
        for r in fresh:
            body = _body(r)
            body["enforcement"] = "disabled"
            api("PUT", f"repos/{repo}/rulesets/{r['id']}", body)
        for sid in diffs:
            if cat[sid]["kind"] == "repo":
                set_value(read_state(repo), sid, want[sid])
    except GhError as e:
        errors.append(e.text)
    after = values(read_state(repo))
    bad = [sid for sid in diffs if not same(after.get(sid), want[sid])]
    if errors or bad:
        out(f"NOT FULLY RESTORED. GitHub said: {'; '.join(errors) or '-'}; still different: {', '.join(bad) or '-'}")
        return 3
    out("Restored and verified: every setting reads back as in the snapshot.")
    return 0


# ---------------------------------------------------------------- first repository

def first_repository(ask=input, out=print, cwd=".", name=None):
    """Make a private repository from a local folder, step by step, each step asked. -> exit code."""
    cwd = str(Path(cwd).resolve())
    fr = doctor_data()["first_repo"]
    name = name or Path(cwd).name

    def yes(q):
        return ask(f"  {q} [y/N]: ").strip().lower() == "y"

    def git(*a):
        return _exec(["git", "-C", cwd, *a])
    out(Gloss().add("First Repository: a PRIVATE repository from this folder:\n  " + cwd))
    gi = Path(cwd) / ".gitignore"
    have = gi.read_text(encoding="utf-8", errors="replace").splitlines() if gi.exists() else []
    missing = [p for p in fr["gitignore"] if p not in have]
    if missing:
        out("A .gitignore keeps secrets and build output out of GitHub. It is missing these lines:\n    "
            + "\n    ".join(missing))
        if yes("Add them to .gitignore?"):
            gi.write_text("\n".join(have + missing) + "\n", encoding="utf-8")
        else:
            out("Stopped: without it a password file could be uploaded.")
            return 0
    if git("rev-parse", "--git-dir")[0] != 0:
        if not yes("This folder is not a git repository yet. Run `git init`?"):
            return 0
        if git("init", "-b", "main")[0] != 0:
            out("git init failed.")
            return 3
    rc, listing, _ = git("ls-files", "--cached", "--others", "--exclude-standard")
    files = [f for f in listing.splitlines() if f]
    risky = [f for f in files if any(f.lower().endswith(x) or f.lower().split("/")[-1] == x for x in SECRET_FILES)]
    out(f"These {len(files)} files would be uploaded:\n    " + "\n    ".join(files[:40]) + ("\n    ..." if len(files) > 40 else ""))
    if risky:
        out("STOPPED: these look like secrets and would be uploaded: " + ", ".join(risky)
            + "\nAdd them to .gitignore (or move them out) and run this again.")
        return 3
    if git("rev-parse", "HEAD")[0] != 0:
        if not yes("Save these files as your first commit?"):
            return 0
        git("add", "-A")
        rc, o, e = git("commit", "-m", "First commit")
        if rc != 0:
            out(f"git commit failed: {(e or o).strip()}")
            return 3
    if not yes(f"Create the PRIVATE GitHub repository '{name}' and upload (push) the files now?"):
        return 0
    rc, o, e = _exec(["gh", "repo", "create", name, "--private", "--source", cwd, "--remote", "origin", "--push"])
    if rc != 0:
        out(f"GitHub refused: {(e or o).strip()}")
        return 3
    out("Created and pushed. It is private. Next, pick how strict to be:\n"
        "  fieldkit github apply full-speed-ahead   (solo, no friction)\n"
        "  fieldkit github apply long-horizon       (pull requests and CI required)")
    return 0
