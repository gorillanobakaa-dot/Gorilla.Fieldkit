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
    installed_absent: [relative paths]       files that must not exist in the install folder
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
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

REGISTER = Path("decisions") / "PRODUCT-DECISIONS.yaml"
STATUSES = ("enforced", "pending", "trade-off", "retired")
KINDS = ("mozconfig_has", "tree_contains", "tree_lacks", "pref", "installed_absent", "omni_absent", "fieldkit_test",
         "image_sharp")
FIELDKIT = Path(__file__).resolve().parents[2]


class Unreadable(Exception):
    pass


def load(owner_root):
    p = Path(owner_root) / REGISTER
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
        for c in e.get("verify") or []:
            if len(c) != 1 or next(iter(c)) not in KINDS:
                problems.append(f"{i}: unknown check {c!r} (kinds: {', '.join(KINDS)})")
    return {"path": str(p), "sha256": hashlib.sha256(raw).hexdigest(), "release": data.get("release"),
            "entries": entries, "problems": problems}


def _zip_names(install_dir):
    names = []
    for ja in ("omni.ja", "browser/omni.ja"):
        try:
            with zipfile.ZipFile(Path(install_dir) / ja) as z:
                names += z.namelist()
        except OSError as e:
            raise Unreadable(f"{ja}: {e}")
    return names


def _check(kind, arg, ctx):
    """-> (ok, evidence). Raises Unreadable when the check cannot run."""
    owner, workdir, install = ctx["owner"], ctx["workdir"], ctx["install"]
    if kind == "mozconfig_has":
        p = Path(owner) / arg["file"]
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
        if not install:
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
        return _image_sharp(Path(workdir), arg)
    if kind == "fieldkit_test":
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", arg], cwd=FIELDKIT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        tail = (r.stdout.strip().splitlines() or ["no output"])[-1]
        return r.returncode == 0, f"{arg}: {tail}"
    raise Unreadable(f"unknown check {kind}")


def _energy(im):
    from PIL import ImageFilter, ImageStat
    return ImageStat.Stat(im.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]


def _image_sharp(workdir, arg):
    import base64
    import io
    import re
    from PIL import Image
    raster, master = workdir / arg["path"], workdir / arg["master"]
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


def check(owner_root, workdir=None, install_dir=None, strict=False):
    reg = load(owner_root)
    ctx = {"owner": owner_root, "workdir": workdir, "install": install_dir}
    rows = []
    for e in reg["entries"]:
        st = e.get("status")
        if st == "retired":
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
            try:
                ok, what = _check(kind, arg, ctx)
            except (Unreadable, OSError, subprocess.TimeoutExpired) as x:
                verdict = "UNCHECKABLE" if verdict == "ENFORCED" else verdict
                ev.append(f"{kind}: cannot check: {x}")
                continue
            ev.append(("ok   " if ok else "FAIL ") + f"{kind}: {what}")
            if not ok:
                verdict = "VIOLATED"
        rows.append({"id": e["id"], "title": e["title"], "verdict": verdict, "evidence": ev})
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
    n = {v: sum(r["verdict"] == v for r in res["rows"]) for v in ("ENFORCED", "PENDING", "ACCEPTED", "VIOLATED", "UNCHECKABLE")}
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
