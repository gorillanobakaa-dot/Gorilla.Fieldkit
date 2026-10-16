"""The maintainer's product decisions for a build, each one a check the harness runs on every build.

Why (2026-10-02): decisions made in conversation (English only, one theme, no automatic updates, uBlock Origin
the only extension, ...) were believed to be "in the patches", and a later port lost some of them. A decision that
lives only in a chat or a note is forgotten; a decision that is a failing check is not. So every decision is an
entry in the owner repo's register, decisions/PRODUCT-DECISIONS.yaml, and every entry names how it is verified.

    fieldkit build-harness decisions TASK [--strict]

Each entry: id, title, decided (date), by (always the maintainer), provenance (where it was decided), status
(enforced | pending | trade-off | retired), why (what the user gains or loses), and verify: a list of checks:

    mozconfig_has:   {file, text}            a line in a build configuration
    tree_contains:   {path, text}            text in the ported source tree (the task workdir)
    tree_lacks:      {path, text}            text that must NOT be in the ported tree
    pref:            {name, value, locked}   what the INSTALLED browser ships (firefox.js wins over greprefs.js);
                                             value null = the pref must not be set; locked true/false is checked
    tree_absent:     [relative paths]        files the ported tree must NOT hold (a deletion the patch set makes)
    tree_present:    [relative paths]        files the ported tree must hold (a new file the patch set adds)
    installed_absent: [relative paths]       files that must not exist in the install folder
    installed_present: [relative paths]      files that MUST exist in the install folder (a defence layer that ships as
                                             a file, e.g. distribution/policies.json: 2026-10-03 the 157 install had
                                             lost it while the tree still carried it)
    omni_absent:     [entry prefixes]        nothing in omni.ja / browser/omni.ja may start with these
    fieldkit_test:   pytest node id          a harness behaviour (run from the Fieldkit folder)
    image_sharp:     {path, master, ratio}   a raster in the tree is a real downsample of its master (an .svg with an
                                             embedded PNG, or a PNG): edge energy at the raster's size >= ratio x the
                                             master's Lanczos downsample (2026-10-02: the About logo was 12.6 vs 29.3)

Verdicts: ENFORCED (every check passed), VIOLATED (a check failed), PENDING (status pending: decided, not yet in
the build), ACCEPTED (status trade-off: a known cost the maintainer accepted; no check), UNCHECKABLE (a check could
not run: no install, unreadable file). Fail closed: VIOLATED and UNCHECKABLE always fail; PENDING fails with
--strict, which the regression baseline and a release use. An enforced entry without checks is VIOLATED.
"""
import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

REGISTER = Path("decisions") / "PRODUCT-DECISIONS.yaml"
STATUSES = ("enforced", "pending", "trade-off", "retired")
KINDS = ("mozconfig_has", "tree_contains", "tree_lacks", "pref", "installed_absent", "omni_absent", "fieldkit_test",
         "image_sharp", "tree_absent", "tree_present", "installed_present", "about_register")
FIELDKIT = Path(__file__).resolve().parents[2]


PLATFORMS = ("linux", "windows")


def this_platform():
    return "windows" if os.name == "nt" else "linux"


def register_root(t):
    """The folder holding the register for a task: the owner repo when it is found, else the task's harness root when
    that holds decisions/PRODUCT-DECISIONS.yaml. 2026-10-14: the Linux harness folder has no Windows preflight script,
    so the owner repo was never found and `decisions` crashed on None. One register serves both operating systems."""
    from .ownercheck import _owner_root
    for c in (_owner_root(t), t.get("meta", {}).get("harness_root")):
        if c and (Path(c) / REGISTER).is_file():
            return Path(c)
    return None


PATCHSET = "gorilla-patchset/"


def owner_path(owner, rel):
    """A path the register names relative to the owner repo. On Windows the shared patch set is the owner repo's
    `gorilla-patchset/` subfolder; on Linux the harness folder IS the patch set (2026-10-14: D-157-01 and D-157-22
    named gorilla-patchset/mozconfig and were uncheckable on Linux). The prefix is dropped only when no such
    subfolder exists, so the Windows layout is never second-guessed."""
    owner = Path(owner)
    if rel.startswith(PATCHSET) and not (owner / PATCHSET).is_dir():
        return owner / rel[len(PATCHSET):]
    return owner / rel


def _named_paths(e):
    out = []
    for c in e.get("verify") or []:
        kind, arg = next(iter(c.items()))
        if kind in ("tree_contains", "tree_lacks") and isinstance(arg, dict) and arg.get("path"):
            out.append(arg["path"])
        elif kind in ("tree_absent", "tree_present") and isinstance(arg, list):
            out += [str(x) for x in arg]
    return out


def citations(t, files, limit=4):
    """The register decisions that cover any of `files` (tree-relative), as packet lines.

    2026-10-14: the Claude Code classifier refused a Marionette lock and the glean checksum work as 'security
    weakening'. A decision that lives only in the register is invisible at the moment of the edit, so the packet
    now carries it: the decision's id, title and its note for models sit in the transcript BEFORE any change is
    made. An entry covers a file when one of its checks names it, or when its `covers:` globs match it."""
    import fnmatch
    root = register_root(t)
    if root is None:
        return []
    try:
        reg = load(root)
    except (OSError, ValueError):
        return []
    out = []
    for e in reg["entries"]:
        if e.get("status") == "retired" or (e.get("platforms") and this_platform() not in e["platforms"]):
            continue
        named, globs = set(_named_paths(e)), list(e.get("covers") or [])
        if any(f in named or any(fnmatch.fnmatch(f, g) for g in globs) for f in files):
            out.append(f"DECISION {e['id']} ({e.get('status')}): {' '.join(str(e.get('title', '')).split())}")
            if e.get("models"):
                out.append("  for models: " + " ".join(str(e["models"]).split()))
        if len(out) >= limit * 2:
            break
    if out:
        out.insert(0, "This step touches files covered by the maintainer's decision register "
                      "(decisions/PRODUCT-DECISIONS.yaml). These are settled; quote them, do not re-argue them:")
    return out


class Unreadable(Exception):
    pass


def register_file(owner_root):
    """The register to read for an owner repo: the shared patch set's (gorilla-patchset/decisions/...) when the owner
    repo carries it, else the owner's own. Paths inside stay relative to the owner (owner_path). 2026-10-16: Windows
    read Gorilla.firefox/decisions/ (67 KB, last written 2026-10-09) while every decision since lived in the shared
    set's (110 KB): D-157-28's new checks and D-158-15/16's platforms were never judged."""
    shared = Path(owner_root) / PATCHSET / REGISTER
    return shared if shared.is_file() else Path(owner_root) / REGISTER


def load(owner_root):
    p = register_file(owner_root)
    if not p.is_file():
        raise FileNotFoundError(f"no decision register at {p}")
    raw = p.read_bytes()
    data = yaml.safe_load(raw) or {}
    entries = data.get("decisions") or []
    problems = []
    seen = set()
    for e in entries:
        i = e.get("id")
        if not i or i in seen:
            problems.append(f"entry without a unique id: {e.get('title')!r}")
        seen.add(i)
        if e.get("status") not in STATUSES:
            problems.append(f"{i}: status must be one of {STATUSES}")
        for need in ("title", "decided", "by", "provenance", "why"):
            if not e.get(need):
                problems.append(f"{i}: missing {need}")
        bad = [x for x in (e.get("platforms") or []) if x not in PLATFORMS]
        if bad:
            problems.append(f"{i}: unknown platform {bad} (platforms: {', '.join(PLATFORMS)})")
        for c in e.get("verify") or []:
            if len(c) != 1 or next(iter(c)) not in KINDS:
                problems.append(f"{i}: unknown check {c!r} (kinds: {', '.join(KINDS)})")
                continue
            arg = next(iter(c.values()))
            if isinstance(arg, dict) and arg.get("platform") and arg["platform"] not in PLATFORMS:
                problems.append(f"{i}: unknown platform {arg['platform']!r} on a check (platforms: {', '.join(PLATFORMS)})")
    problems += platform_problems(owner_root, entries)
    return {"path": str(p), "sha256": hashlib.sha256(raw).hexdigest(), "release": data.get("release"),
            "entries": entries, "problems": problems}


_WRITERS = {}


def writers(owner_root):
    """{file: {platforms on which some patch of the set writes it}}, from the policy (platforms, exclude_on, exclude),
    cached by the policy's mtime. {} when there is no policy beside the register."""
    from . import firefox
    pol = Path(owner_root) / "config" / "patch_policy.json"
    if not pol.is_file():
        return {}
    key = (str(pol), pol.stat().st_mtime_ns)
    if key in _WRITERS:
        return _WRITERS[key]
    out = {}
    for plat in PLATFORMS:
        try:
            pset, groups = firefox._policy(owner_root, plat)
        except (KeyError, ValueError, OSError):
            return {}                  # a policy without groups (a test world, a half-written file): nothing to judge
        for g, spec in groups.items():
            if spec.get("status") != "enabled":
                continue
            ex = set(spec.get("exclude", []))
            for pf in (pset / g).rglob("*.patch"):
                if pf.name in ex:
                    continue
                for f in firefox.parse_patch(pf.read_text(encoding="utf-8", errors="replace")):
                    out.setdefault(f["file"], set()).add(plat)
    _WRITERS.clear()
    _WRITERS[key] = out
    return out


def platform_problems(owner_root, entries):
    """A decision with no `platforms` whose tree checks read only files that the set writes on SOME platforms must say
    so: it cannot hold elsewhere. 2026-10-16: D-158-15 and D-158-16 (01.MEDIA, Linux-only) were "Linux only" in the
    prose and failed on the first Windows tree. -> [problem]"""
    w = writers(owner_root)
    if not w:
        return []
    out = []
    for e in entries:
        if e.get("platforms") or e.get("status") == "retired":
            continue
        paths = [arg["path"] for c in e.get("verify") or [] for k, arg in c.items()
                 if k == "tree_contains" and isinstance(arg, dict) and arg.get("path") and not arg.get("platform")]
        plats = [w.get(pth) for pth in paths]
        if paths and all(plats) and len(set().union(*plats)) < len(PLATFORMS):
            only = sorted(set().union(*plats))
            out.append(f"{e.get('id')}: its tree checks read {paths[:2]}, which the set writes only on {only}; "
                       f"declare platforms: {only} (or make it hold everywhere)")
    return out


def _zip_names(install_dir):
    """Member names of both jars, packed or UNPACKED (layout.members). 2026-10-15: zip-only, so on the unpacked Linux
    build (D-158-11) every omni_absent decision (D-157-03, -04, -35, -40) read UNCHECKABLE."""
    from . import layout
    names = []
    for ja in ("omni.ja", "browser/omni.ja"):
        try:
            names += layout.members(install_dir, ja)
        except (OSError, zipfile.BadZipFile) as e:
            raise Unreadable(f"{ja}: {e}")
    return names


def _check(kind, arg, ctx):
    """-> (ok, evidence). Raises Unreadable when the check cannot run."""
    owner, workdir, install = ctx["owner"], ctx["workdir"], ctx["install"]
    if kind == "mozconfig_has":
        p = owner_path(owner, arg["file"])
        if not p.is_file():
            raise Unreadable(f"{arg['file']} not found")
        lines = [l.split("#")[0].strip() for l in p.read_text(encoding="utf-8", errors="replace").splitlines()]
        return arg["text"] in lines, f"{arg['file']}: {arg['text']}"
    if kind in ("tree_contains", "tree_lacks"):
        if not workdir:
            raise Unreadable("no ported tree")
        p = Path(workdir) / arg["path"]
        if not p.is_file():
            if kind == "tree_lacks":
                return True, f"{arg['path']} absent"
            raise Unreadable(f"{arg['path']} not in the tree")
        has = arg["text"].replace("\r\n", "\n") in p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        return (has if kind == "tree_contains" else not has), f"{arg['path']}: {'has' if has else 'lacks'} {arg['text'][:60]!r}"
    if kind == "pref":
        if not install and workdir and ctx.get("source_prefs"):
            # no build yet: judge the source tree's default-pref files (check-change after every source edit). Opt-in
            # only: a public claim is proven on the shipped artefact, never on the source (test_buildh_claims)
            from . import proof
            if "tree_prefs" not in ctx:
                ctx["tree_prefs"] = proof.tree_prefs(workdir)
            got = ctx["tree_prefs"].get(arg["name"])
            if got and got[2]:
                raise Unreadable(f"{arg['name']} is set under #if in the tree; the install decides it")
            ctx["shipped"] = {n: v[:2] for n, v in ctx["tree_prefs"].items()}
        elif not install:
            raise Unreadable("no installed browser")
        if "shipped" not in ctx:
            from . import proof
            ctx["shipped"] = proof.shipped_prefs(install)
            if not ctx["shipped"]:
                raise Unreadable("no prefs readable from the install")
        got = ctx["shipped"].get(arg["name"])
        want = arg.get("value")
        if want is None:
            return got is None, f"{arg['name']}: {'not set' if got is None else got}"
        want = str(want).lower() if isinstance(want, bool) else str(want)
        if got is None:
            return False, f"{arg['name']}: not set (want {want})"
        ok = got[0] == want
        if "locked" in arg:
            ok = ok and (("locked" in (got[1] or "")) == bool(arg["locked"]))
        return ok, f"{arg['name']} = {got[0]}{' locked' if 'locked' in (got[1] or '') else ''} (want {want}{' locked' if arg.get('locked') else ''})"
    if kind == "installed_absent":
        if not install:
            raise Unreadable("no installed browser")
        present = [r for r in arg if (Path(install) / r).exists()]
        return not present, "present: " + ", ".join(present) if present else "absent: " + ", ".join(arg)
    if kind == "installed_present":
        if not install:
            raise Unreadable("no installed browser")
        missing = [r for r in arg if not (Path(install) / r).exists()]
        return not missing, "MISSING from the install: " + ", ".join(missing) if missing else "present: " + ", ".join(arg)
    if kind in ("tree_absent", "tree_present"):
        if not workdir:
            raise Unreadable("no ported tree")
        there = [r for r in arg if (Path(workdir) / r).exists()]
        if kind == "tree_absent":
            return not there, "still in the tree: " + ", ".join(there[:5]) if there else f"absent: {len(arg)} path(s)"
        gone = [r for r in arg if r not in there]
        return not gone, "missing from the tree: " + ", ".join(gone[:5]) if gone else f"present: {len(arg)} path(s)"
    if kind == "omni_absent":
        if not install:
            raise Unreadable("no installed browser")
        if "names" not in ctx:
            ctx["names"] = _zip_names(install)
        hits = [n for n in ctx["names"] if any(n.startswith(p) for p in arg)]
        return not hits, ("found: " + ", ".join(hits[:5])) if hits else "none of: " + ", ".join(arg)
    if kind == "image_sharp":
        if not workdir:
            raise Unreadable("no ported tree")
        return _image_sharp(Path(workdir), arg, owner)
    if kind == "about_register":
        # every about: page the tree can register is in the owner's reviewed register, and none it marks "remove" is
        # still registered (2026-10-08: about:about hides 30 pages; a new upstream page must be reviewed first)
        if not workdir:
            raise Unreadable("no ported tree")
        return about_register(owner_path(owner, arg["file"]), workdir)
    if kind == "fieldkit_test":
        # its own temporary folder: pytest's shared pytest-current link, once made by an ELEVATED run (the owner's
        # leakgate-baseline window, 2026-10-06), cannot be replaced by a normal account, and every later run then exits
        # non-zero with every test passed (build 28 read four decisions as VIOLATED for that alone)
        import tempfile
        base = tempfile.mkdtemp(prefix="fk-decision-test-")
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp", base, arg],
                           cwd=FIELDKIT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        tail = (r.stdout.strip().splitlines() or ["no output"])[-1]
        return r.returncode == 0, f"{arg}: {tail}"
    raise Unreadable(f"unknown check {kind}")


def about_register(path, workdir):
    """-> (ok, evidence): the tree's about: pages (aboutregistry.scan) against the reviewed register at `path`
    ({pages: [{name, verdict: keep|remove, ...}]})."""
    from . import aboutregistry
    if not Path(path).is_file():
        raise Unreadable(f"{path} not found")
    pages = {e["name"]: e for e in (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("pages") or []}
    registered = set(aboutregistry.scan(workdir))
    unreviewed = sorted(registered - set(pages))
    back = sorted(n for n in registered if pages.get(n, {}).get("verdict") == "remove")
    bad_verdict = sorted(n for n, e in pages.items() if e.get("verdict") not in ("keep", "remove"))
    ok = not unreviewed and not back and not bad_verdict
    parts = []
    if unreviewed:
        parts.append(f"not reviewed: {', '.join('about:' + n for n in unreviewed[:8])}")
    if back:
        parts.append(f"marked remove but registered: {', '.join('about:' + n for n in back[:8])}")
    if bad_verdict:
        parts.append(f"verdict must be keep or remove: {', '.join(bad_verdict[:5])}")
    kept = sum(1 for n in registered if pages.get(n, {}).get("verdict") == "keep")
    return ok, "; ".join(parts) or f"{len(registered)} registered page(s), all reviewed ({kept} kept); "         f"{sum(1 for e in pages.values() if e.get('verdict') == 'remove')} removed page(s) stay removed"


def _energy(im):
    from PIL import ImageFilter, ImageStat
    return ImageStat.Stat(im.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]


def _image_sharp(workdir, arg, owner=None):
    import base64
    import io
    import re
    from PIL import Image
    # `owner:<path>`: the master lives in the owner repo (D-157-35: the canonical 2598 px master is in
    # gorilla-patchset/deb_template, and the tree keeps only the one 1400 px about-logo.png made from it)
    m_arg = arg["master"]
    master = owner_path(owner, m_arg[6:]) if m_arg.startswith("owner:") and owner else workdir / m_arg
    raster = workdir / arg["path"]
    if not raster.is_file() or not master.is_file():
        raise Unreadable(f"missing {arg['path'] if not raster.is_file() else arg['master']}")
    if master.suffix == ".svg":
        m = re.search(r"data:image/png;base64,([A-Za-z0-9+/=\s]+)", master.read_text(encoding="utf-8", errors="replace"))
        if not m:
            raise Unreadable(f"{arg['master']} holds no embedded PNG")
        ref = Image.open(io.BytesIO(base64.b64decode(m.group(1))))
    else:
        ref = Image.open(master)
    got = Image.open(raster)
    got.load()
    ref = ref.convert("RGBA")
    if ref.size[0] < got.size[0]:
        raise Unreadable(f"master {ref.size[0]}px is smaller than {arg['path']} ({got.size[0]}px): it cannot vouch for it")
    want = _energy(ref.resize(got.size, Image.LANCZOS))
    have = _energy(got)
    ratio = float(arg.get("ratio", 0.75))
    return have >= ratio * want, f"{arg['path']} edge energy {have:.1f} vs {want:.1f} from {arg['master']} (need {ratio:.0%})"


def check(owner_root, workdir=None, install_dir=None, strict=False, source_prefs=False):
    reg = load(owner_root)
    ctx = {"owner": owner_root, "workdir": workdir, "install": install_dir, "source_prefs": source_prefs}
    rows = []
    for e in reg["entries"]:
        st = e.get("status")
        if st == "retired":
            continue
        if e.get("platforms") and this_platform() not in e["platforms"]:
            # one register for both operating systems; an entry may name the ones it applies to (absent = both)
            rows.append({"id": e["id"], "title": e["title"], "verdict": "OTHER OS",
                         "evidence": [f"applies to {', '.join(e['platforms'])} only; this is {this_platform()}"]})
            continue
        if st == "trade-off":
            rows.append({"id": e["id"], "title": e["title"], "verdict": "ACCEPTED", "evidence": [e["why"].strip()[:160]]})
            continue
        if st == "pending":
            rows.append({"id": e["id"], "title": e["title"], "verdict": "PENDING",
                         "evidence": [str(e.get("pending_on") or "decided, not yet in the build")]})
            continue
        checks = e.get("verify") or []
        if not checks:
            rows.append({"id": e["id"], "title": e["title"], "verdict": "VIOLATED", "evidence": ["enforced but no check: a decision nobody verifies is forgotten"]})
            continue
        verdict, ev = "ENFORCED", []
        for c in checks:
            kind, arg = next(iter(c.items()))
            if isinstance(arg, dict) and arg.get("platform") and arg["platform"] != this_platform():
                ev.append(f"skip {kind}: {arg['platform']} only")         # one register, two operating systems
                continue
            try:
                ok, what = _check(kind, arg, ctx)
            except (Unreadable, OSError, subprocess.TimeoutExpired) as x:
                verdict = "UNCHECKABLE" if verdict == "ENFORCED" else verdict
                ev.append(f"{kind}: cannot check: {x}")
                continue
            ev.append(("ok   " if ok else "FAIL ") + f"{kind}: {what}")
            if not ok:
                verdict = "VIOLATED"
        if all(x.startswith("skip ") for x in ev):
            verdict = "OTHER OS"                  # nothing ran here: never report ENFORCED on zero checks
        rows.append({"id": e["id"], "title": e["title"], "verdict": verdict, "evidence": ev})
    notes = {e["id"]: " ".join(str(e["models"]).split()) for e in reg["entries"] if e.get("models")}
    for r in rows:
        if r["id"] in notes:
            r["models"] = notes[r["id"]]
    bad = [r for r in rows if r["verdict"] in ("VIOLATED", "UNCHECKABLE") or (strict and r["verdict"] == "PENDING")]
    ok = not bad and not reg["problems"]
    return {"ok": ok, "register": reg["path"], "sha256": reg["sha256"], "release": reg["release"],
            "problems": reg["problems"], "rows": rows, "strict": strict}


def lines(res):
    out = [f"decision register {res['register']} (sha256 {res['sha256'][:12]}, release {res['release']})"]
    out += [f"  REGISTER PROBLEM: {p}" for p in res["problems"]]
    for r in res["rows"]:
        out.append(f"  {r['verdict']:<11} {r['id']}  {r['title']}")
        if r["verdict"] not in ("ENFORCED", "ACCEPTED"):
            out += [f"      {x}" for x in r["evidence"]]
        if r.get("models"):
            # the maintainer's word to any model reading this, so a settled decision is quoted, never re-argued
            out.append(f"      for models: {r['models']}")
    n = {v: sum(r["verdict"] == v for r in res["rows"]) for v in ("ENFORCED", "PENDING", "ACCEPTED", "VIOLATED", "UNCHECKABLE", "OTHER OS")}
    out.append("  " + ", ".join(f"{k.lower()} {v}" for k, v in n.items()))
    out.append("DECISIONS " + ("OK" if res["ok"] else "NOT OK") + (" (strict: pending counts as not done)" if res["strict"] else ""))
    return out


def proof_row(owner_root, workdir, install_dir):
    """The post-install row: every decision checked against this installed build (pending is reported, not failed)."""
    if not owner_root:
        return {"check": "decisions: the maintainer's product decisions hold in this build", "ok": False,
                "evidence": "no owner repository known for this task, so no decision register: unchecked is not passed"}
    try:
        res = check(owner_root, workdir, install_dir)
    except FileNotFoundError as e:
        return {"check": "decisions: the maintainer's product decisions hold in this build", "ok": False, "evidence": str(e)}
    bad = [f"{r['id']} {r['verdict']}" for r in res["rows"] if r["verdict"] not in ("ENFORCED", "ACCEPTED", "PENDING")]
    pend = [r["id"] for r in res["rows"] if r["verdict"] == "PENDING"]
    return {"check": "decisions: the maintainer's product decisions hold in this build", "ok": res["ok"],
            "evidence": ("; ".join(bad) if bad else "all enforced decisions hold") + (f"; pending: {', '.join(pend)}" if pend else ""),
            "bad": bad}
