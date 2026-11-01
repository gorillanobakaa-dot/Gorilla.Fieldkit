"""fieldkit ship gh-release vX --add FILE...: files added to a published release through the release_tag mandate,
SHA256SUMS regenerated and re-uploaded, the page's table row added (2026-11-01: v0.1.158 shipped without the Arch
package and gh-release could only create releases)."""
import hashlib
import json
import subprocess

import pytest

from fieldkit.method import ship

PAGE = ("intro\n\n| File | Bytes | SHA-256 |\n|---|---|---|\n"
        f"| `a.deb` | 1,000 | `{'a' * 64}` |\n| `b.exe` | 2,000 | `{'b' * 64}` |\n\nCheck a download.\n")


def test_merge_sums_adds_a_line_and_replaces_one_of_the_same_name():
    text = f"{'a' * 64}  a.deb\n{'b' * 64}  b.exe\n"
    out = ship.merge_sums(text, {"c.pkg": "c" * 64, "a.deb": "d" * 64})
    assert out == f"{'d' * 64}  a.deb\n{'b' * 64}  b.exe\n{'c' * 64}  c.pkg\n"


def test_add_rows_puts_the_row_after_the_last_row_of_the_table():
    out = ship.add_rows(PAGE, [("c.pkg.tar.zst", 23475734, "c" * 64)])
    assert f"| `b.exe` | 2,000 | `{'b' * 64}` |\n| `c.pkg.tar.zst` | 23,475,734 | `{'c' * 64}` |\n\nCheck" in out
    assert ship.add_rows(out, [("c.pkg.tar.zst", 1, "e" * 64)]).count("c.pkg.tar.zst") == 1


def test_add_rows_refuses_a_page_without_the_table():
    with pytest.raises(ValueError):
        ship.add_rows("no table here", [("x", 1, "c" * 64)])


class Gh:
    def __init__(self, assets=("a.deb", "b.exe", "SHA256SUMS-v1.txt")):
        self.calls, self.assets, self.notes = [], list(assets), None

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = ""
        if cmd[1:3] == ["release", "view"]:
            out = json.dumps({"body": PAGE, "assets": [{"name": n} for n in self.assets]})
        elif cmd[1:3] == ["release", "download"]:
            d = cmd[cmd.index("-D") + 1]
            open(f"{d}/SHA256SUMS-v1.txt", "w").write(f"{'a' * 64}  a.deb\n{'b' * 64}  b.exe\n")
        elif cmd[1:3] == ["release", "upload"]:
            self.sums = open([c for c in cmd if c.endswith("SHA256SUMS-v1.txt")][0]).read()
        elif cmd[1:3] == ["release", "edit"]:
            self.notes = open(cmd[cmd.index("--notes-file") + 1], encoding="utf-8").read()
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    from fieldkit.core import sync
    monkeypatch.setattr(sync, "_slug", lambda repo: "o/r")
    monkeypatch.setattr(ship, "_mandate_log", lambda *a, **k: None)
    p = tmp_path / "c.pkg.tar.zst"
    p.write_bytes(b"arch package")
    (tmp_path / "w").mkdir()
    return p


def test_add_uploads_the_file_and_regenerated_sums_and_adds_the_page_row(pkg):
    gh = Gh()
    r = ship.gh_release_add(pkg.parent, "v1", [pkg], mandate=lambda a, **k: ("allow", "ok", "m.yaml"), run=gh,
                            workdir=pkg.parent / "w")
    sha = hashlib.sha256(b"arch package").hexdigest()
    assert r["ok"] and r["files"] == {"c.pkg.tar.zst": sha}
    upload = [c for c in gh.calls if c[1:3] == ["release", "upload"]][0]
    assert str(pkg) in upload and "--clobber" in upload and upload[-2:] == ["-R", "o/r"]
    assert gh.sums.endswith(f"{sha}  c.pkg.tar.zst\n") and gh.sums.startswith(f"{'a' * 64}  a.deb\n")
    assert f"| `c.pkg.tar.zst` | 12 | `{sha}` |" in gh.notes
    assert not any(c[1:3] == ["release", "create"] for c in gh.calls)


def test_add_changes_nothing_without_the_mandate(pkg):
    gh = Gh()
    r = ship.gh_release_add(pkg.parent, "v1", [pkg], mandate=lambda a, **k: ("ask", "CI not green", None), run=gh)
    assert not r["ok"] and r["state"] == "not-mandated" and gh.calls == []


def test_add_changes_nothing_when_the_release_has_no_sums_file(pkg):
    gh = Gh(assets=("a.deb",))
    r = ship.gh_release_add(pkg.parent, "v1", [pkg], mandate=lambda a, **k: ("allow", "ok", None), run=gh,
                            workdir=pkg.parent / "w")
    assert not r["ok"] and r["state"] == "no-sums"
    assert not any(c[1:3] in (["release", "upload"], ["release", "edit"]) for c in gh.calls)


@pytest.mark.parametrize("files", [[], ["missing.bin"], ["SHA256SUMS-v1.txt"]])
def test_add_refuses_bad_files_before_asking_anyone(tmp_path, files):
    (tmp_path / "SHA256SUMS-v1.txt").write_text("")
    asked = []
    r = ship.gh_release_add(tmp_path, "v1", [tmp_path / f for f in files],
                            mandate=lambda a, **k: asked.append(a) or ("allow", "ok", None), run=Gh())
    assert not r["ok"] and r["state"] == "bad-input" and asked == []
