"""fieldkit mutate reads a test's output as UTF-8 whatever the locale (2026-10-26, Gorilla.Opencode job 01).

text=True decoded with the locale code page (cp1252 on the Windows laptop): a failing Go test printed UTF-8, a byte
cp1252 leaves undefined (0x81, 0x9D) killed the reader thread, r.stdout was None and `r.stdout + r.stderr` raised
TypeError, aborting the whole plan. PYTHONUTF8=1 hid it. The locale is forced to cp1252 here so the test holds on a
UTF-8 CI runner and under PYTHONUTF8=1 too.
"""
import subprocess
import sys

import pytest

from fieldkit.method import mutate as mu

TEXT = "ș ț — ✓ Ł"          # Ł is C5 81 in UTF-8: 0x81 is undefined in cp1252


def _child(code):
    return [sys.executable, "-I", "-c",
            "import sys; sys.stdout.buffer.write(" + repr(TEXT.encode("utf-8")) + "); sys.stdout.flush(); "
            "sys.exit(" + str(code) + ")"]


@pytest.fixture
def cp1252(monkeypatch):
    """Decode as the Windows laptop's locale would, also where the runner's locale is UTF-8."""
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp1252", raising=False)
    import locale
    monkeypatch.setattr(locale, "getencoding", lambda: "cp1252", raising=False)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a, **k: "cp1252")


def test_mutate_utf8_run_test_failing_child(cp1252):
    ok, out = mu.run_test(_child(1))
    assert ok is False
    assert TEXT in out


def test_mutate_utf8_run_test_passing_child(cp1252):
    ok, out = mu.run_test(_child(0))
    assert ok is True
    assert TEXT in out


def test_mutate_utf8_invalid_bytes_replaced(cp1252):
    ok, out = mu.run_test([sys.executable, "-I", "-c",
                           "import sys; sys.stderr.buffer.write(b'x\\x81\\x9d\\xffy'); sys.exit(1)"])
    assert ok is False
    assert out.startswith("x") and out.endswith("y")


def test_mutate_utf8_caught_not_crash(cp1252, tmp_path):
    src = tmp_path / "rule.txt"
    src.write_text("keep\n", encoding="utf-8")
    test = [sys.executable, "-I", "-c",
            "import sys; t = open(sys.argv[1], encoding='utf-8').read(); "
            "sys.stdout.buffer.write(" + repr(TEXT.encode("utf-8")) + "); sys.exit(0 if t == 'keep\\n' else 1)",
            str(src)]
    r = mu.mutate_once(src, "keep", "broken", test)
    assert r["verdict"] == "caught"
    assert src.read_text(encoding="utf-8") == "keep\n"
