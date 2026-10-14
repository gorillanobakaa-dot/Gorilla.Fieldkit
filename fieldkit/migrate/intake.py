"""S1 upstream intake: what the new Firefox release brings that the patch set has never seen.

The patch set cuts what it knows. A new release adds prefs, endpoints, modules, actors, about: pages, background
tasks and AI/ML features the patches cannot know about, and each one may call home. So before anything is applied,
pristine release N-1 and pristine release N are compared (both read from git, never from a working tree) and every
new thing becomes an INTAKE item:

    pref            a pref name new in all.js, firefox.js or StaticPrefList.yaml
    host            a host new in a pref value, or named by a new network-capable module
    module          a new .sys.mjs anywhere, or a new source file in a component the leak gate audits
                    (leakgate.audit.COMPONENTS); network use is detected with the leak gate's own patterns
    actor           a new JSWindowActor / JSProcessActor name (ActorManagerParent, DesktopActors)
    about           a new about: page (nsAboutRedirector.cpp, AboutRedirector.cpp)
    backgroundtask  a new BackgroundTask_*.sys.mjs
    AI/ML           any of the above whose name or path names an AI/ML feature

Dispositions (MIGRATION-PLAN.md S1: cut at source, accepted with a decision, or not network-capable):
    auto            deterministic, recorded with its rule: a pref with no URL value whose name names no network or
                    AI feature; a module with no network API (leak-gate patterns) and no AI path; a host already
                    decided in leakgate/dispositions.json or allow.json
    a brief         every other item, clustered (one brief per category and component), answered by the maintainer
                    with `build-harness decide` (fieldkit/briefs); the gate reads the decision from the journal
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

from ..buildh import proof
from ..leakgate import audit as la
from . import ledger

AI = re.compile(r"(^|[/._-])(ai|ml|genai|llm|llama|chatbot|aichat|inference|embeddings?|smart\w*|aiwindow|onnx|"
                r"transformers?|semantic)([/._-]|$)", re.I)
NETWORKY = re.compile(r"url|uri\b|endpoint|server|host|telemetry|upload|report|ping|remote|update|sync|fetch|download|"
                      r"merino|experiment|nimbus|normandy|glean|sponsor|suggest|weather|geo|region|push|captive|"
                      r"connectivity|doh|trr|ohttp|relay|vpn|proxy|ipprotection|beacon|attribution", re.I)
NOT_SHIPPED = ("mobile/", "testing/", "third_party/", "python/", "taskcluster/", "tools/", "build/", "obj-", "docs/",
               "remote/test", "js/src/tests", "toolkit/crashreporter/tools")
ACTOR_FILES = ("toolkit/modules/ActorManagerParent.sys.mjs", "browser/components/DesktopActors.sys.mjs",
               "browser/components/BrowserGlue.sys.mjs")
ABOUT_FILES = ("docshell/base/nsAboutRedirector.cpp", "browser/components/about/AboutRedirector.cpp")
ACTOR = re.compile(r"^  ([A-Z]\w+): \{", re.M)
ABOUT = re.compile(r"\{\s*\"([a-z0-9-]+)\",\s*\"")
STATIC = re.compile(r"^- name:\s*(\S+)\s*$")
BGTASK = re.compile(r"BackgroundTask_\w+\.sys\.mjs$")

# D-158-03 (owner, 2026-10-15): closed by default. A new upstream thing in one of these classes is cut at the source
# without asking; only what fits no class becomes a question (a brief). "We are trying to close all the doors and
# secure the browser ... Everything that is not compatible with our philosophy we should not even have to ask."
DENY = [
    ("AI / ML", r"(^|[/._-])(ai|ml|genai|llm|llama|chatbot|aichat|inference|embeddings?|smart\w*|aiwindow|onnx|"
                                r"transformers?|semantic|pageassist|linkpreview|shortcut-?chat)([/._-]|$)"),
    ("chat bots and assistants", r"(chat|assistant|copilot|sidekick)"),
    ("weather and content widgets", r"(weather|stocks|widget|sponsored|pocket|discoverystream|topstories|newsfeed|trending)"),
    ("helpers and suggestions", r"(suggest|recommend|cfr|asrouter|onboarding|feature-?callout|spotlight|messaging|tips?\b)"),
    ("experiments and remote control of features", r"(nimbus|experiment|normandy|studies|shield|rollout|trainhop|remote-?settings)"),
    ("remote control of the browser", r"(marionette|remote-?agent|webdriver|bidi|cdp\b)"),
    ("telemetry and reporting", r"(telemetry|glean|fog\b|ping|crash-?report|usage-?report|attribution|metrics)"),
    ("translations and other languages", r"(translat|langpack|language-?pack|locale-?switch|intl\.locale)"),
    ("themes and colorways", r"(colorway|theme-?(store|picker|gallery)|lightweight-?theme)"),
]
_DENY_RX = [(c, re.compile(rx, re.I)) for c, rx in DENY]


def deny_class(*texts):
    """The D-158-03 class a new upstream item falls in, or None (then it is a question for the owner)."""
    hay = " ".join(t for t in texts if t)
    return next((c for c, rx in _DENY_RX if rx.search(hay)), None)
LABEL = {"pref": "pref", "host": "host", "module": "module", "actor": "actor", "about": "about",
         "backgroundtask": "backgroundtask"}


class Rev:
    """One commit of a repository, read-only: file list and blobs (git ls-tree, git cat-file --batch)."""

    def __init__(self, repo, commit):
        self.repo, self.commit = str(repo), commit
        self._files, self._cat, self._cache = None, None, {}

    def files(self):
        if self._files is None:
            r = subprocess.run(["git", "-C", self.repo, "ls-tree", "-r", "--name-only", self.commit], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", env={**__import__("os").environ, "GIT_OPTIONAL_LOCKS": "0"})
            if r.returncode != 0:
                raise RuntimeError(f"git ls-tree {self.commit[:12]} in {self.repo}: {r.stderr.strip()[:200]}")
            self._files = set(r.stdout.splitlines())
        return self._files

    def read(self, rel):
        if rel in self._cache:
            return self._cache[rel]
        if self._cat is None:
            self._cat = subprocess.Popen(["git", "-C", self.repo, "cat-file", "--batch"], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE)
        self._cat.stdin.write(f"{self.commit}:{rel}\n".encode("utf-8"))
        self._cat.stdin.flush()
        head = self._cat.stdout.readline().decode("utf-8", "replace").split()
        text = None
        if len(head) == 3 and head[1] == "blob":
            data = self._cat.stdout.read(int(head[2]))
            self._cat.stdout.read(1)
            text = data.decode("utf-8", "replace")
        self._cache[rel] = text
        return text

    def close(self):
        if self._cat:
            try:
                self._cat.stdin.close()
                self._cat.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._cat = None


def _iid(category, name):
    return f"INTAKE-{category}-" + hashlib.sha256(f"{category}|{name}".encode("utf-8")).hexdigest()[:8]


def _slug(s, n=48):
    return re.sub(r"[^A-Za-z0-9]+", "-", str(s)).strip("-")[:n] or "all"


def _domain(host):
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _static_prefs(text):
    out, lines = {}, (text or "").splitlines()
    for i, l in enumerate(lines):
        m = STATIC.match(l)
        if m:
            val = next((x.split(":", 1)[1].strip() for x in lines[i + 1:i + 6] if x.strip().startswith("value:")), None)
            out[m.group(1)] = val
    return out


def _prefs(rev):
    out = {}
    for src in ledger.PREF_FILES:
        for name, (val, flag, _) in proof.pref_lines(rev.read(src) or "").items():
            out[name] = (val, src)
    for name, val in _static_prefs(rev.read(ledger.STATIC_PREFS)).items():
        out.setdefault(name, (val or "", ledger.STATIC_PREFS))
    return out


def _hosts(text):
    return {h.lower() for h in ledger.HOST.findall(text or "")}


def _shipped(rel):
    return not rel.startswith(NOT_SHIPPED) and not la.SKIP.search("/" + rel)


def compute(old, new, dispositions=None, allow=None, say=None):
    """old, new: Rev. -> {"old", "new", "items": [...], "clusters": [...], "counts": {...}}. Read-only."""
    dispositions = dispositions or {}
    items = []

    def add(category, name, where, detail, network, ai, auto=None, hosts=()):
        items.append({"id": _iid(category, name), "category": category, "name": name, "where": where,
                      "detail": str(detail)[:300], "network": bool(network), "ai": bool(ai), "auto": auto,
                      "hosts": sorted(hosts)[:10], "default_cut": deny_class(name, where)})
    say and say(f"  intake: listing files of {old.commit[:12]} and {new.commit[:12]} ...")
    of, nf = old.files(), new.files()
    op, np_ = _prefs(old), _prefs(new)
    old_hosts = set()
    for v, _ in op.values():
        old_hosts |= _hosts(v)
    for name in sorted(set(np_) - set(op)):
        val, src = np_[name]
        ai = bool(AI.search(name))
        url = "://" in (val or "")
        net = url or bool(NETWORKY.search(name))
        auto = None if (net or ai) else "no URL value, and the name names no network or AI feature: not network-capable"
        add("pref", name, src, f"{name} = {val}", net, ai, auto, _hosts(val))
    new_pref_hosts = {}
    for name, (val, src) in np_.items():
        for h in _hosts(val) - old_hosts:
            new_pref_hosts.setdefault(h, f"{src}: {name} = {val}")
    shipped_new = sorted(f for f in nf - of if _shipped(f))
    module_hosts = {}
    comp = tuple(p for ps in la.COMPONENTS.values() for p in ps)
    for rel in shipped_new:
        is_mod = rel.endswith(".sys.mjs") or (rel.startswith(comp) and rel.endswith(la.SOURCE_EXT))
        if not is_mod and not BGTASK.search(rel):
            continue
        text = new.read(rel) or ""
        apis = la.network_apis(text)
        ai = bool(AI.search(rel))
        hs = {h for h in _hosts(text) if not h.endswith((".example", "example.com", "localhost"))}
        if BGTASK.search(rel):
            add("backgroundtask", rel, rel, f"network APIs: {', '.join(apis) or 'none'}", bool(apis), ai, None, hs)
            continue
        auto = None if (apis or ai) else "no network API (leak-gate audit patterns) and no AI/ML path: not network-capable"
        if auto is None and ((dispositions.get(rel) or {}).get("approval")):
            auto = "already decided in the leak gate (an approved source disposition)"
        add("module", rel, rel, f"network APIs: {', '.join(apis) or 'none'}", bool(apis), ai, auto, hs)
        if apis:
            for h in hs - old_hosts:
                module_hosts.setdefault(h, rel)
    for h, where in sorted({**module_hosts, **new_pref_hosts}.items()):
        decided = la.host_decided(h, dispositions, allow) == "approved"
        auto = "already decided in the leak gate (an approved host disposition or allowlist entry)" if decided else None
        add("host", h, where.split(":", 1)[0], where, True, bool(AI.search(h)), auto)
    for rel in ACTOR_FILES:
        o, n = set(ACTOR.findall(old.read(rel) or "")), set(ACTOR.findall(new.read(rel) or ""))
        for name in sorted(n - o):
            add("actor", name, rel, f"actor {name} registered in {rel}", True, bool(AI.search(name)))
    for rel in ABOUT_FILES:
        o, n = set(ABOUT.findall(old.read(rel) or "")), set(ABOUT.findall(new.read(rel) or ""))
        for name in sorted(n - o):
            add("about", f"about:{name}", rel, f"about:{name} mapped in {rel}", True, bool(AI.search(name)))
    seen, uniq = set(), []
    for it in items:
        if it["id"] not in seen:
            seen.add(it["id"])
            uniq.append(it)
    return {"old": old.commit, "new": new.commit, "items": uniq, "clusters": cluster(uniq), "counts": counts(uniq)}


def component(it):
    c = it["category"]
    if c == "pref":
        return ".".join(it["name"].split(".")[:2])
    if c == "host":
        return _domain(it["name"])
    if c in ("module",):
        return "/".join(it["name"].split("/")[:3])
    return "all"


def cluster(items):
    out = {}
    for it in items:
        if it["auto"]:
            continue
        label = "AI-ML" if it["ai"] else LABEL[it["category"]]
        key = f"INTAKE-{label}-{_slug(component(it))}"
        c = out.setdefault(key, {"id": key, "brief": "B-" + key, "category": label, "component": component(it), "items": []})
        c["items"].append(it["id"])
    return sorted(out.values(), key=lambda c: (c["category"] != "AI-ML", c["category"], c["id"]))


def counts(items):
    out = {}
    for it in items:
        k = out.setdefault(it["category"], {"new": 0, "auto": 0, "needs_disposition": 0})
        k["new"] += 1
        k["auto" if it["auto"] else "needs_disposition"] += 1
    return out


def decided_items(task_id):
    """-> {intake item id: (brief id, option, words)} from the task journal's `decide` events on B-INTAKE- briefs (the
    brief snapshot saved at the decision names the items it covered). A `hold` answer disposes nothing."""
    from ..buildh import task
    p = task.STATE / task_id / "journal.jsonl"
    out = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") != "decide" or not str(e.get("brief", "")).startswith("B-INTAKE-") or e.get("option") == "hold":
            continue
        try:
            b = json.loads(Path(e.get("snapshot", "")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for iid in (b.get("target") or {}).get("intake") or []:
            out[iid] = (e["brief"], e.get("option"), e.get("words"))
    return out


def default_cuts(data):
    """-> [item] the D-158-03 classes cut without asking: port work (cut at the source), never a question."""
    return [i for i in data.get("items") or [] if i.get("default_cut") and not i.get("auto")]


def open_clusters(data, decided):
    """The clusters with at least one item that has neither an automatic nor a recorded disposition. An item in a
    D-158-03 class is disposed (cut) by that decision and is never a question."""
    cut = {i["id"] for i in default_cuts(data)}
    out = []
    for c in data.get("clusters") or []:
        left = [i for i in c["items"] if i not in decided and i not in cut]
        if left:
            out.append({**c, "open": left})
    return out
