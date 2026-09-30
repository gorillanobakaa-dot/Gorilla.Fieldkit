"""harvest: reads scripts without running them; ranks them for plain-words queries."""
from fieldkit import harvest


def _mk(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_index_reads_each_language_without_running(tmp_path):
    _mk(tmp_path, "srcA/icons.py", '"""Render crisp icon frames from one master.\n\nEvery size separately."""\n'
        'import argparse, PIL\nimport winreg\n\ndef render():\n    pass\n\n'
        'if __name__ == "__main__":\n    p = argparse.ArgumentParser()\n    p.add_argument("--size")\n')
    _mk(tmp_path, "srcA/boom.py", 'raise SystemExit("this must never run")\n')
    _mk(tmp_path, "srcB/fix.ps1", "<#\n.SYNOPSIS\n  Restart the hotspot safely.\n#>\nparam([string]$Ssid)\n"
        "function Restart-Hotspot { Stop-Service x }\n")
    _mk(tmp_path, "srcB/build.sh", "#!/usr/bin/env bash\n# Build the kernel as .deb packages.\nmake bindeb-pkg\n")
    _mk(tmp_path, "srcB/tests/test_x.py", "def test_x():\n    pass\n")
    idx = {e["id"]: e for e in harvest.build(tmp_path)}
    py = idx["srcA/icons.py"]
    assert py["title"] == "Render crisp icon frames from one master." and py["options"] == ["--size"]
    assert py["functions"] == ["render"] and py["needs"] == ["PIL"] and "registry" in py["touches"]
    assert py["probe"] == "safe" and idx["srcA/boom.py"]["probe"] in ("runs-on-load", "no-cli", "library")
    ps = idx["srcB/fix.ps1"]
    assert ps["title"] == "Restart the hotspot safely." and ps["options"] == ["Ssid"]
    assert ps["functions"] == ["Restart-Hotspot"] and "services" in ps["touches"]
    assert idx["srcB/build.sh"]["title"] == "Build the kernel as .deb packages."
    assert idx["srcB/tests/test_x.py"]["is_test"] is True


def test_find_ranks_by_meaning_words_and_skips_tests(tmp_path):
    _mk(tmp_path, "a/icons.py", '"""Render crisp icon frames."""\n')
    _mk(tmp_path, "a/hotspot.ps1", "# Restart the wifi hotspot.\n")
    _mk(tmp_path, "a/test_icons.py", '"""Test crisp icon frames."""\n')
    idx = harvest.build(tmp_path)
    hits = harvest.find(idx, "crisp icons")
    assert [h["id"] for h in hits] == ["a/icons.py"]
    assert harvest.find(idx, "nothing matches zzqx") == []


def test_markdown_is_one_line_per_script(tmp_path):
    _mk(tmp_path, "a/x.py", '"""Does X."""\n')
    md = harvest.to_markdown(harvest.build(tmp_path))
    assert "- `a/x.py` - Does X. [python, 2 lines]" in md
