"""ps-compat-check must not crash on non-UTF-8 subprocess output (0x81 byte), and a crash must exit nonzero."""
import subprocess
import sys

from fieldkit.repair import ps_compat_check as pc


def test_0x81_generator_output_does_not_crash(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "gen.json").write_text("x", encoding="utf-8")
    gen = [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'a\x81b')"]
    monkeypatch.setattr(pc, "GENERATORS", {"gen.json": gen})
    pc.check_stale_generated(tmp_path)  # no UnicodeDecodeError


def test_crash_exits_nonzero(monkeypatch):
    def boom(argv=None):
        raise UnicodeDecodeError("charmap", b"\x81", 0, 1, "undefined")
    monkeypatch.setattr(pc, "_main", boom)
    assert pc.main([]) == 1
