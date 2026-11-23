# Version: 1.0.0 · updated 26-10-16-12-20
"""fieldkit size: finds the program, says plainly when it is missing, passes arguments and exit codes through, and
(when the real program is built here) measures a known tree exactly and never follows a junction or symlink."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.desk import size
from fieldkit.desk import size_install


def _fake(tmp_path, body_rc=0):
    """A stand-in gorilla-size: answers --version, echoes its arguments, exits with body_rc."""
    script = tmp_path / "fake_gs.py"
    script.write_text("import sys\nargs = sys.argv[1:]\n"
                      "if args == ['--version']:\n    print('gorilla-size 9.9.9'); sys.exit(0)\n"
                      f"print('ARGS', ' '.join(args)); sys.exit({body_rc})\n", encoding="utf-8")
    if os.name == "nt":
        exe = tmp_path / "gorilla-size.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp_path / "gorilla-size"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    return exe


def test_integrate_dispatches_without_building(tmp_path, monkeypatch):
    monkeypatch.setattr(size, "source_dir", lambda: tmp_path)
    seen = []
    monkeypatch.setattr(size_install, "main", lambda args, default: seen.append((args, default)) or 0)
    assert size.main(["integrate", "--from-dir", "release"]) == 0
    assert seen == [(["--from-dir", "release"], tmp_path / "target" / "release")]


def test_integrate_runs_only_safe_binary_probes_and_installs_both(tmp_path, monkeypatch):
    source = tmp_path / "release"
    source.mkdir()
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("LOCALAPPDATA", str(home))
    windows = sys.platform == "win32"
    suffix = ".exe" if windows else ""
    names = ("gorilla-size", "gorilla-trash")
    for name in names:
        (source / (name + suffix)).write_text(name, encoding="utf-8")
    probes = []
    registry = []

    def probe(args, **kwargs):
        probes.append(args)
        return subprocess.CompletedProcess(args, 0, Path(args[0]).stem + " description", "")

    from fieldkit.desk import install_preflight
    monkeypatch.setattr(install_preflight, "run_preflight", lambda **kw: {"status": "ok"})
    monkeypatch.setattr(size_install.subprocess, "run", probe)
    monkeypatch.setattr(size_install, "install_windows", lambda *args: registry.append(args))
    assert size_install.main([], source) == 0
    assert size_install.main([], source) == 0
    bindir = home / "Fieldkit" / "bin" if windows else home / ".local" / "bin"
    for name in names:
        assert (bindir / (name + suffix)).read_text(encoding="utf-8") == name
    expected = [[str(source / (name + suffix)), flag] for name, flag in zip(names, ("--version", "--help"))]
    assert probes == expected * 2
    assert registry == ([(bindir, bindir / "gorilla-trash.exe")] * 2 if windows else [])


def test_missing_program_is_said_with_the_fix(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FIELDKIT_GORILLA_SIZE_SRC", str(tmp_path / "nosrc"))
    monkeypatch.delenv("FIELDKIT_GORILLA_SIZE", raising=False)
    monkeypatch.setattr(size, "bin_dir", lambda: tmp_path / "nobin")
    monkeypatch.setattr(size.shutil, "which", lambda n: None)
    assert size.locate() is None
    assert size.main(["."]) == 2
    assert "doctor --install" in capsys.readouterr().err
    lines = []
    assert size.doctor(out=lines.append) == 1
    assert lines[0].startswith("MISSING") and any("fieldkit gather --only gorilla-size" in x for x in lines)


def test_doctor_and_passthrough_use_the_program_found(tmp_path, monkeypatch):
    exe = _fake(tmp_path, body_rc=3)
    monkeypatch.setenv("FIELDKIT_GORILLA_SIZE", str(exe))
    assert size.locate() == exe
    lines = []
    assert size.doctor(out=lines.append) == 0 and "gorilla-size 9.9.9" in lines[0]
    assert size.main(["find", "--min-size", "2G", "."]) == 3     # the program's own exit code


def test_a_program_that_does_not_answer_is_broken_not_ok(tmp_path, monkeypatch):
    bad = tmp_path / ("bad.cmd" if os.name == "nt" else "bad")
    bad.write_text("@exit /b 1\r\n" if os.name == "nt" else "#!/bin/sh\nexit 1\n", encoding="utf-8")
    if os.name != "nt":
        bad.chmod(0o755)
    monkeypatch.setenv("FIELDKIT_GORILLA_SIZE", str(bad))
    monkeypatch.setenv("FIELDKIT_GORILLA_SIZE_SRC", str(tmp_path / "nosrc"))
    lines = []
    assert size.doctor(out=lines.append) == 1 and lines[0].startswith("BROKEN")


def test_a_binary_built_from_an_older_source_is_stale_not_ok(tmp_path, monkeypatch):
    # 2026-10-16: the first doctor called the old binary OK after the gathered source had moved on
    bindir, src = tmp_path / "bin", tmp_path / "src"
    bindir.mkdir()
    src.mkdir()
    fake = _fake(tmp_path)
    exe = bindir / size.EXE
    if os.name == "nt":
        # a .cmd cannot be renamed to .exe and still run; point locate() at the fake but keep the stamp logic
        monkeypatch.setattr(size, "EXE", fake.name)
        exe = bindir / fake.name
    exe.write_bytes(fake.read_bytes())
    if os.name != "nt":
        exe.chmod(0o755)
    monkeypatch.delenv("FIELDKIT_GORILLA_SIZE", raising=False)
    monkeypatch.setenv("FIELDKIT_GORILLA_SIZE_SRC", str(src))
    monkeypatch.setattr(size, "bin_dir", lambda: bindir)
    (src / "PROVENANCE.json").write_text('{"commit": "new123"}', encoding="utf-8")
    (bindir / "gorilla-size.source").write_text("old999", encoding="utf-8")
    lines = []
    assert size.doctor(out=lines.append) == 1
    assert lines[0].startswith("STALE") and "old999" in lines[0] and "new123" in lines[0]
    (bindir / "gorilla-size.source").write_text("new123", encoding="utf-8")
    lines = []
    assert size.doctor(out=lines.append) == 0 and lines[0].startswith("OK")


def test_a_local_checkout_without_provenance_is_judged_by_its_git_head(tmp_path, monkeypatch):
    # 2026-10-18: FIELDKIT_GORILLA_SIZE_SRC named a local checkout (no PROVENANCE.json); doctor called 0.2 OK
    bindir, src = tmp_path / "bin", tmp_path / "src"
    bindir.mkdir()
    src.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t")
    git = lambda *a: subprocess.run(["git", "-C", str(src), *a], capture_output=True, text=True, env=env, check=True)
    git("init", "-q")
    (src / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    git("add", "Cargo.toml")
    git("commit", "-q", "-m", "one")
    head = git("rev-parse", "--short=7", "HEAD").stdout.strip()
    fake = _fake(tmp_path)
    exe = bindir / size.EXE
    if os.name == "nt":
        monkeypatch.setattr(size, "EXE", fake.name)
        exe = bindir / fake.name
    exe.write_bytes(fake.read_bytes())
    if os.name != "nt":
        exe.chmod(0o755)
    monkeypatch.delenv("FIELDKIT_GORILLA_SIZE", raising=False)
    monkeypatch.setenv("FIELDKIT_GORILLA_SIZE_SRC", str(src))
    monkeypatch.setattr(size, "bin_dir", lambda: bindir)
    assert size.source_commit() == head
    (bindir / "gorilla-size.source").write_text("9021533", encoding="utf-8")
    lines = []
    assert size.doctor(out=lines.append) == 1 and lines[0].startswith("STALE") and head in lines[0]
    (bindir / "gorilla-size.source").write_text(head, encoding="utf-8")
    lines = []
    assert size.doctor(out=lines.append) == 0 and lines[0].startswith("OK")
    (src / "Cargo.toml").write_text("[package]\nname = 'changed'\n", encoding="utf-8")   # edited, not committed
    assert size.source_commit() == head + "-dirty"
    assert size.doctor(out=[].append) == 1


REAL = size.locate()
real = pytest.mark.skipif(REAL is None or size.version(REAL)[0] is None,
                          reason="gorilla-size is not built on this machine (fieldkit size doctor --install)")


@real
def test_the_real_program_measures_exactly_and_lists_links(tmp_path):
    (tmp_path / "big").mkdir()
    (tmp_path / "big" / "a").write_bytes(b"x" * 3000)
    (tmp_path / "b").write_bytes(b"x" * 1000)
    link = tmp_path / "src"
    if os.name == "nt":
        made = subprocess.run(["cmd", "/C", "mklink", "/J", str(link), str(tmp_path / "big")],
                              capture_output=True).returncode == 0
    else:
        link.symlink_to(tmp_path / "big")
        made = True
    assert made
    r = subprocess.run([str(REAL), str(tmp_path), "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    t = json.loads(r.stdout)["roots"][0]["totals"]
    assert t["bytes"] == 4000 and t["files"] == 2 and t["links_not_followed"] == 1
