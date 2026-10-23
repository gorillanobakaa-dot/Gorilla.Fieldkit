# Version: 1.0.0 · updated 26-10-23-21-52
"""fieldkit release-cycle: one release, the whole cycle, each stage DONE only by its artefact (owner, 2026-10-23: a
release took 80 minutes once and days now). Real git in a temp folder, real build and screenshot commands, the real
release-page compose; GitHub is a stand-in that remembers what was published."""
import functools
import json
import subprocess
import sys
import zlib
import struct

import pytest

from fieldkit.method import releasecycle as rc
from fieldkit.method import shiprelease as sr

V = "v0.2.0"


def _g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=tester", *a], check=True,
                          capture_output=True, text=True).stdout.strip()


def _png(path, side=120):
    """A real PNG, big enough to pass the size floor (noise does not compress)."""
    import os
    raw = b"".join(b"\x00" + os.urandom(side * 3) for _ in range(side))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class GitHub:
    def __init__(self):
        self.releases, self.calls = {}, []

    def __call__(self, args, run=None):
        self.calls.append(args)
        if args[:2] == ["release", "view"]:
            r = self.releases.get(args[2])
            return (0, json.dumps(r), "") if r else (1, "", "release not found")
        if args[:2] == ["release", "create"]:
            notes = args[args.index("--notes-file") + 1]
            files = args[args.index("--notes-file") + 2:]     # the assets; not "starts with /" (C:\ on Windows)
            body = open(notes, encoding="utf-8").read()
            self.releases[args[2]] = {"assets": [{"name": __import__("os").path.basename(f)} for f in files],
                                      "body": body}
            return 0, "", ""
        if args[:2] == ["release", "upload"]:
            self.releases[args[2]]["assets"] += [{"name": __import__("os").path.basename(a)} for a in args[5:]]
            return 0, "", ""
        return 1, "", f"unexpected gh {args}"


@pytest.fixture
def project(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "app"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    _g(repo, "checkout", "-q", "-b", "main")
    (repo / "main.txt").write_text("app\n")
    (repo / "notes").mkdir()
    _g(repo, "add", "-A")
    _g(repo, "commit", "-qm", "app")
    _g(repo, "push", "-q", "origin", "main")
    shot = tmp_path / "shoot.py"
    shot.write_text("import sys, test_release_cycle as t, pathlib; t._png(pathlib.Path(sys.argv[1]))\n")
    rel = tmp_path / "releases" / "app.yaml"
    rel.parent.mkdir()
    rel.write_text(f"""project: app
repo: {repo}
out: "{{repo}}/dist/{{version}}"
build:
  - [{sys.executable}, -c, "import pathlib; d = pathlib.Path('dist/{{version}}'); d.mkdir(parents=True, exist_ok=True); (d / 'app-{{v}}-linux').write_text('bin'); (d / 'app-{{v}}.exe').write_text('exe')"]
assets: ["app-{{v}}-linux", "app-{{v}}.exe"]
gallery: docs/gallery/{{version}}
screenshots:
  - name: start.png
    proves: the app starting, its banner whole
    cmd: [{sys.executable}, {shot}, "docs/gallery/{{version}}/start.png"]
notes: {{opening: notes/opening.md, layman: notes/layman.md, developer: notes/developer.md}}
""")
    monkeypatch.setattr(rc, "release_file", lambda p: rel)
    monkeypatch.setattr(rc, "_slug", lambda repo: "o/app")
    monkeypatch.setattr(sr, "repo_slug", lambda repo: "o/app")
    monkeypatch.setenv("PYTHONPATH", str(__import__("pathlib").Path(__file__).parent) + __import__("os").pathsep +
                       __import__("os").environ.get("PYTHONPATH", ""))      # ";" on Windows (windows-latest CI, 2026-10-23)
    gh = GitHub()
    monkeypatch.setattr(rc, "_gh", gh)
    monkeypatch.setattr(sr, "audit", functools.partial(sr.audit, gh=lambda args, timeout=60: ("[]", "")))
    return repo, gh, tmp_path


def _next(pl):
    from fieldkit.core import next as nxt
    return nxt.decide(pl)


def test_the_whole_cycle_runs_stage_by_stage_and_ends_with_nothing_left(project):
    repo, gh, tmp = project
    pl = rc.pipeline("app", V, state_root=tmp / "state")
    assert "release-cycle app run --var version=v0.2.0 --only audit-start" in _next(pl)["command"]
    rep = pl.run()
    stopped = rep["stopped_at"] if "stopped_at" in rep else None
    assert not rep["ok"] and stopped in ("on-base", "notes")
    d = _next(pl)
    assert d["kind"] == "BLOCKED" and d["stage"] == "notes" and "write the notes first" in d["why"]
    assert (repo / "dist" / V / "SHA256SUMS-v0.2.0.txt").is_file() and (repo / "docs/gallery" / V / "start.png").is_file()
    opening = ("## What this is\nA small app.\n\n## Should you download it\nYes, if you use it.\n\n"
               "## Why it matters\nIt starts faster.\n")
    for k, text in (("opening", opening),
                    ("layman", "## In plain words\nWhat changed, in full words.\n"),
                    ("developer", "## For developers\nThe commits and the proof.\n")):
        (repo / "notes" / f"{k}.md").write_text(text)
    rep = pl.run()
    assert not rep["ok"] and _next(pl)["stage"] == "on-base"           # notes and shots are not on main yet
    _g(repo, "add", "-A", "notes", "docs")
    _g(repo, "commit", "-qm", "notes and screenshots")
    _g(repo, "push", "-q", "origin", "main")
    rep = pl.run()
    assert rep["ok"], [(s["id"], s.get("status"), s.get("result"), s.get("verify")) for s in rep["stages"]]
    page = gh.releases[V]
    assert {a["name"] for a in page["assets"]} == {"app-0.2.0-linux", "app-0.2.0.exe", "SHA256SUMS-v0.2.0.txt"}
    assert "raw.githubusercontent.com/o/app/v0.2.0/docs/gallery/v0.2.0/start.png" in page["body"]
    assert _next(pl)["kind"] == "DONE"


def test_a_build_that_skips_one_asset_stops_the_cycle(project):
    # the Arch package shipped through v0.1.111 and then silently stopped for four releases (brain lesson)
    repo, gh, tmp = project
    rel = rc.release_file("app")
    rel.write_text(rel.read_text().replace("(d / 'app-{v}.exe').write_text('exe')", "None"))
    pl = rc.pipeline("app", V, state_root=tmp / "state")
    pl.run()
    d = _next(pl)
    assert d["kind"] == "BLOCKED" and d["stage"] == "build"
    assert gh.releases == {}


def test_an_asset_left_from_an_earlier_build_is_not_this_release(project):
    # gorilla-opencode.exe carries no version in its name and shares Compiled.Builds with every release
    import os
    repo, gh, tmp = project
    rel = rc.release_file("app")
    rel.write_text(rel.read_text().replace("(d / 'app-{v}.exe').write_text('exe')", "None"))
    (repo / "dist" / V).mkdir(parents=True)
    old = repo / "dist" / V / "app-0.2.0.exe"
    old.write_text("an old exe")
    os.utime(old, (1_700_000_000, 1_700_000_000))
    pl = rc.pipeline("app", V, state_root=tmp / "state")
    pl.run()
    assert _next(pl)["stage"] == "build"
    report = json.loads((tmp / "state/release-cycle/app" / V / "last-report.json").read_text())
    build = next(x for x in report["stages"] if x["id"] == "build")
    assert any("older than this build" in v and "app-0.2.0.exe" in v for v in build["verify"])


def test_a_screenshot_that_is_not_a_real_png_is_not_a_screenshot(project):
    repo, gh, tmp = project
    (repo / "docs/gallery" / V).mkdir(parents=True)
    (repo / "docs/gallery" / V / "start.png").write_text("not a picture")
    pl = rc.pipeline("app", V, state_root=tmp / "state")
    pl.run()
    assert _next(pl)["stage"] == "screenshots"


def test_an_open_pull_request_stops_the_cycle_before_anything_is_built(project, monkeypatch):
    repo, gh, tmp = project
    prs = json.dumps([{"number": 7, "title": "x", "headRefName": "feat/x", "statusCheckRollup": []}])
    monkeypatch.setattr(sr, "audit", functools.partial(sr.audit.func, gh=lambda args, timeout=60: (
        prs if "open" in args else "[]", "")))
    pl = rc.pipeline("app", V, state_root=tmp / "state")
    pl.run()
    d = _next(pl)
    assert d["stage"] == "audit-start" and "#7" in d["why"]
    assert not (repo / "dist").exists()


def test_an_existing_release_only_gets_what_it_lacks(project):
    repo, gh, tmp = project
    gh.releases[V] = {"assets": [{"name": "app-0.2.0-linux"}], "body": "old"}
    (repo / "dist" / V).mkdir(parents=True)
    for n in ("app-0.2.0-linux", "app-0.2.0.exe", "SHA256SUMS-v0.2.0.txt"):
        (repo / "dist" / V / n).write_text("x")
    pl = rc.pipeline("app", V, state_root=tmp / "state")
    from fieldkit.core.pipeline import Context
    rc.publish(Context(pl))
    assert [a for a in gh.calls if a[:2] == ["release", "upload"]][0][5:] == [
        str(repo / "dist" / V / "app-0.2.0.exe"), str(repo / "dist" / V / "SHA256SUMS-v0.2.0.txt")]
    assert not any("--clobber" in a for a in gh.calls)


def test_the_release_file_is_refused_when_it_is_incomplete(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text("project: x\nrepo: /r\n")
    with pytest.raises(ValueError):
        rc.load(f, V)
    with pytest.raises(ValueError):
        rc.load(f, "0.2")
    with pytest.raises(FileNotFoundError):
        rc.load(tmp_path / "none.yaml", V)
