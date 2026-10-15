# Version: 1.0.0 · updated 26-10-15-02-20
"""api-drift: the 2026-10-15 FindInReadable failure, found in ported files and rewritten from the table."""
import subprocess

from fieldkit.build import apidrift, firefox_preflight

NEW_API = ("inline bool CaseInsensitiveFindInReadable(const nsACString& aPattern,\n"
           "                                          const nsACString& aSource) {}\n")
OLD_CALL = ('  if (x && (FindInReadable("av01"_ns, aType.OriginalString(),\n'
            '                      nsCaseInsensitiveCStringComparator) ||\n'
            '       FindInReadable("vp8"_ns, aType.OriginalString(),\n'
            '                      nsCaseInsensitiveCStringComparator))) {}\n')


def _git(w, *a):
    subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)


def _tree(tmp_path, header=NEW_API):
    w = tmp_path / "src"
    (w / "xpcom" / "string").mkdir(parents=True)
    (w / "dom").mkdir()
    (w / "xpcom" / "string" / "nsReadableUtils.h").write_text(header)
    (w / "dom" / "DecoderTraits.cpp").write_text("// pristine\n")
    _git(w, "init", "-q")
    _git(w, "add", "-A")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "pristine")
    (w / "dom" / "DecoderTraits.cpp").write_text(OLD_CALL)        # the port's change
    return w


def test_table_loads_and_names_the_158_change():
    ids = [r["id"] for r in apidrift.rules()]
    assert "findinreadable-comparator" in ids


def test_scan_finds_and_apply_rewrites_and_checkpoints(tmp_path):
    w = _tree(tmp_path)
    r = apidrift.scan(w)
    assert not r["ok"] and r["found"][0]["count"] == 2 and r["found"][0]["lines"] == [1, 3]
    apidrift.apply(w, r["found"])
    text = (w / "dom" / "DecoderTraits.cpp").read_text()
    assert 'CaseInsensitiveFindInReadable("av01"_ns, aType.OriginalString())' in text
    assert "Comparator" not in text
    assert apidrift.scan(w)["ok"]
    msg = subprocess.run(["git", "-C", str(w), "log", "-1", "--format=%B"], capture_output=True, text=True).stdout
    assert "findinreadable-comparator" in msg


def test_a_release_without_the_new_api_is_never_rewritten(tmp_path):
    w = _tree(tmp_path, header="// an older release: only the comparator overload\n")
    r = apidrift.scan(w)
    assert r["ok"] and r["rules"] == 0


def test_preflight_row_blocks_until_fixed(tmp_path):
    w = _tree(tmp_path)

    class Ctx:
        src = w
    ok, detail = firefox_preflight._c_api_drift(Ctx)
    assert not ok and "findinreadable-comparator x2" in detail
    apidrift.apply(w, apidrift.scan(w)["found"])
    assert firefox_preflight._c_api_drift(Ctx)[0]
    assert any(c.cid == "api-drift" for c in firefox_preflight.CHECKS)
