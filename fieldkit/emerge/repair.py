"""fieldkit emerge repair RECIPE --files F ...: the emergency repair (owner, 2026-11-17: "small fix = one step, never
full emerge"). A frontend fix of a few files (JS, CSS, FTL, HTML, JSON, pictures) goes straight into the installed
build's omni archive, checked on the pages it is for, and exported as the next patch of the port-fix series. ONE
deterministic step: no stages, no compile, no test suite, no model. Driven only by the recipe's `repair:` block:

    repair:
      source: "{harness}/src"                   the git tree the files are edited in
      patches: "{harness}/gorilla-patchset/patches"
      series: "21.PORT.FIXES"                    the patch goes to <patches>/<series>.<major>/NNN-<name>.patch
      program: firefox.exe                       the program in the install folder (reopened if it was running)
      packages: [omni.ja, browser/omni.ja]       the archives a file may land in, relative to the install folder
      extensions: [.mjs, .js, .css, ...]         what counts as a frontend file
      max_files: 10                              more than this is not a small fix
      check: {pages: [about:newtab, about:home], dprs: [1]}

What it does, in order: refuse anything that is not small (an extension not listed, too many files, a file the
build preprocesses (#ifdef / #include ...), a file with no single archive entry) with "run the normal emerge";
stop the product's processes (by PID, only programs inside the install folder); copy each archive it changes to
<archive>.before-repair-<stamp> beside it (never deleted); rewrite the archive with the new bytes, every other entry
and every compression method kept; touch .purgecaches (the profile stays); run the runtime visual check on a
throwaway copy of the install for the pages named; on any FAIL or UNVERIFIABLE in RT-CONTENT, RT-LOAD or RT-RUN, or
a page not measured, put the archives back and exit 3; else write the patch and print the push. The product is
reopened when it was running.
"""
import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

GATING = ("RT-CONTENT", "RT-LOAD", "RT-RUN")
PREPROCESSED = re.compile(r"^#(filter|include|includesubst|ifdef|ifndef|if |elif|expand|literal|define)\b", re.M)


class Refused(Exception):
    """Not a small fix: the normal emerge is the way."""


def spec_of(recipe):
    """-> the recipe's `repair:` block, its paths filled in. Refused when the recipe declares none."""
    raw = (getattr(recipe, "spec", None) or {}).get("repair")
    if not raw:
        raise Refused(f"recipe {recipe.name} declares no repair: block: run the normal emerge")
    if raw.get("not_applicable"):
        raise Refused(f"recipe {recipe.name}: repair not applicable ({raw['not_applicable']}): run the normal emerge")
    sp = recipe._resolve(dict(raw))
    for k in ("source", "patches", "series", "program", "packages", "extensions", "max_files", "check"):
        if k not in sp:
            raise Refused(f"recipe {recipe.name}: repair: has no {k!r}")
    return sp


def members(install_dir, packages):
    """-> {archive relative path: [member names]} for every listed archive that exists."""
    out = {}
    for rel in packages:
        p = Path(install_dir) / rel
        if p.is_file():
            with zipfile.ZipFile(p) as z:
                out[rel] = z.namelist()
    return out


def plan(sp, files, install_dir, maps=None, source_root=None):
    """-> [(source file, archive rel, member)]. Refused for anything that is not a small frontend fix."""
    maps = dict(maps or {})
    files = list(files)
    if not files:
        raise Refused("no --files given")
    if len(files) > int(sp["max_files"]):
        raise Refused(f"{len(files)} files is more than {sp['max_files']}: not a small fix, run the normal emerge")
    exts = {e.lower() for e in sp["extensions"]}
    root = Path(source_root or sp["source"])
    have = members(install_dir, sp["packages"])
    if not have:
        raise Refused(f"none of {sp['packages']} is in {install_dir}: not an installed build")
    out = []
    for f in files:
        p = Path(f) if Path(f).is_absolute() else root / f
        if p.suffix.lower() not in exts:
            raise Refused(f"{f}: {p.suffix or 'no extension'} is not a frontend file: run the normal emerge")
        if not p.is_file():
            raise Refused(f"{f}: not found under {root}")
        if PREPROCESSED.search(p.read_text(encoding="utf-8", errors="replace")):
            raise Refused(f"{f}: the build preprocesses it (#ifdef/#include ...): run the normal emerge")
        if f in maps:
            jar, _, mem = maps[f].partition(":")
            if jar not in have or mem not in have[jar]:
                raise Refused(f"--map {f}={maps[f]}: no such entry in the install")
            out.append((p, jar, mem))
            continue
        hits = [(jar, m) for jar, names in have.items() for m in names if m.rsplit("/", 1)[-1] == p.name]
        if len(hits) != 1:
            why = "no entry" if not hits else f"{len(hits)} entries ({', '.join(f'{j}:{m}' for j, m in hits[:4])})"
            raise Refused(f"{f}: {why} named {p.name} in {list(have)}: pass --map {f}=ARCHIVE:MEMBER")
        out.append((p, hits[0][0], hits[0][1]))
    return out


def rewrite(archive, new_bytes):
    """Write `archive` again with {member: bytes} replaced; every other entry, its order and compression kept."""
    archive = Path(archive)
    tmp = archive.with_name(archive.name + ".repair-tmp")
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(tmp, "w") as dst:
        for info in src.infolist():
            data = new_bytes.get(info.filename)
            dst.writestr(info, data if data is not None else src.read(info.filename),
                         compress_type=info.compress_type)
    os.replace(tmp, archive)


def verdict(res, pages):
    """-> [problem] from a runtime.run result: a gating item that did not pass, or a page with no item at all."""
    bad = [f"{i['rule']} {i['item']}: {i['verdict']} {i['evidence']}" for i in res.get("items") or []
           if i["rule"] in GATING and i["verdict"] in ("FAIL", "UNVERIFIABLE") and not i.get("accepted")]
    seen = " ".join(i["item"] for i in res.get("items") or [])
    bad += [f"{p}: not measured" for p in pages if p not in seen]
    return bad


def next_patch(patches, series, version, name):
    major = str(version).split(".")[0]
    d = Path(patches) / f"{series}.{major}"
    nums = [int(m.group(1)) for m in (re.match(r"^(\d+)-", q.name) for q in d.glob("*.patch")) if m] if d.is_dir() else []
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "repair"
    return d / f"{(max(nums) + 1) if nums else 1:03d}-{slug}.patch", major


def export_patch(sp, files, recipe_name, version, name, why, base="HEAD", run=subprocess.run):
    """Write the source diff of `files` against `base` as the next patch of the series -> path (None: no diff)."""
    rels = [str(Path(f)) for f in files]
    diff = run(["git", "-C", str(sp["source"]), "diff", base, "--", *rels], capture_output=True, text=True,
               encoding="utf-8").stdout
    if not diff.strip():
        return None
    path, major = next_patch(sp["patches"], sp["series"], version, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (f"# Gorilla {major} port fix\n# {why}\n# Files: {', '.join(r.replace(os.sep, '/') for r in rels)}\n"
            f"# Exported-by: fieldkit emerge repair {recipe_name}\n\n")
    path.write_text(head + diff, encoding="utf-8", newline="\n")
    return path


def repair(recipe, files, install_dir=None, maps=None, name="repair", why=None, base="HEAD", dry_run=False,
           say=print, check=None, stop=None, procs=None, reopen=None, stamp=None):
    """-> {"ok", "why", "patch", "backups", "problems"}. check/stop/procs/reopen: seams for tests."""
    from ..buildh import install as inst, probe
    sp = spec_of(recipe)
    if install_dir is None:
        install_dir = inst.find_install()
        if install_dir is None:
            return {"ok": False, "why": inst.no_target()["why"], "problems": []}
    install_dir = Path(install_dir)
    steps = plan(sp, files, install_dir, maps)
    for p, jar, mem in steps:
        say(f"  {p.name} -> {jar}:{mem}")
    if dry_run:
        return {"ok": True, "why": "dry run: nothing changed", "plan": [(str(p), j, m) for p, j, m in steps],
                "problems": []}
    procs = procs or probe.processes_in
    stop = stop or probe.stop_tree
    running = procs(install_dir)
    for pid in running:
        stop(pid)
    say(f"  stopped {len(running)} process(es) of {install_dir}" if running else "  the product was not running")
    stamp = stamp or _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    by_jar = {}
    for p, jar, mem in steps:
        by_jar.setdefault(jar, {})[mem] = p.read_bytes()
    backups = {}
    for jar, new in by_jar.items():
        a = install_dir / jar
        b = a.with_name(f"{a.name}.before-repair-{stamp}")
        shutil.copy2(a, b)
        backups[jar] = b
        rewrite(a, new)
        say(f"  {jar}: {len(new)} entr{'y' if len(new) == 1 else 'ies'} replaced (backup {b.name})")
    (install_dir / ".purgecaches").touch()
    pages = list(sp["check"].get("pages") or [])
    if check is None:
        from ..visual import runtime
        res = runtime.run(install_dir, only=pages, dprs=tuple(sp["check"].get("dprs") or (1,)), say=say)
    else:
        res = check(install_dir, pages)
    problems = verdict(res, pages)
    out = {"backups": {k: str(v) for k, v in backups.items()}, "problems": problems, "patch": None}
    if problems:
        for jar, b in backups.items():
            shutil.copy2(b, install_dir / jar)
        (install_dir / ".purgecaches").touch()
        out.update(ok=False, why=f"the check failed on {len(problems)} item(s): archives put back as they were")
    else:
        patch = export_patch(sp, [str(p.relative_to(Path(sp["source"]))) if p.is_relative_to(Path(sp["source"]))
                                  else str(p) for p, _, _ in steps],
                             recipe.name, recipe.vars.get("version", "0"), name, why or name, base=base)
        out.update(ok=True, why="installed and checked", patch=str(patch) if patch else None)
    if running:
        (reopen or _reopen)(install_dir / sp["program"])
        say("  reopened the product (it was running)")
    return out


def _reopen(program):
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0   # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([str(program)], creationflags=flags, start_new_session=sys.platform != "win32",
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(recipe, files, install_dir=None, maps=(), name="repair", why=None, base="HEAD", dry_run=False,
         as_json=False):
    import json
    try:
        mp = {}
        for m in maps or ():
            k, sep, v = m.partition("=")
            if not sep or ":" not in v:
                print(f"--map needs SRC=ARCHIVE:MEMBER, got {m!r}")
                return 2
            mp[k] = v
        res = repair(recipe, files, install_dir=install_dir, maps=mp, name=name, why=why, base=base,
                     dry_run=dry_run)
    except Refused as e:
        print(f"REFUSED: {e}")
        print("NEXT: fieldkit emerge run " + recipe.name)
        return 2
    if as_json:
        print(json.dumps(res, indent=1, default=str))
    for p in res.get("problems") or []:
        print(f"  FAIL {p}")
    print(f"REPAIR {'OK' if res['ok'] else 'FAILED'}: {res['why']}")
    if res.get("patch"):
        print(f"  patch written: {res['patch']}")
        print(f"NEXT: commit it in {Path(res['patch']).parents[2]} and fieldkit ship push --auto from there")
    elif res["ok"] and not dry_run:
        print("NEXT: no source diff against the base to export: pass --base REV (the commit before the fix)")
    elif not res["ok"]:
        print("NEXT: the fix does not work on the installed build: correct it and run the repair again")
    return 0 if res["ok"] else 3
