"""Tool registry (probe safety) and the command line."""
import json
import subprocess
import sys
from pathlib import Path

from fieldkit.desk import registry

ROOT = Path(__file__).resolve().parents[1]


def _cli(*args):
    r = subprocess.run([sys.executable, "-m", "fieldkit", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8")
    return r.returncode, r.stdout, r.stderr


def test_registry_loads_with_unique_ids():
    tools = registry.load()
    assert len([t for t in tools if t["path"].startswith(str(registry.settings.ROOT))]) >= 20   # the public cards alone
    assert all(t.get("path") or t.get("repo") for t in tools)


def test_probe_safety_rules(tmp_path):
    safe = tmp_path / "safe.py"
    safe.write_text('"""doc"""\nimport argparse\n\ndef main():\n    argparse.ArgumentParser().parse_args()\n\n'
                    'if __name__ == "__main__":\n    main()\n')
    assert registry.probe_safety(safe)[0] is True
    runs_on_load = tmp_path / "organize_like.py"         # the organize.py incident
    runs_on_load.write_text("import shutil\nshutil.move('a', 'b')\nprint('done')\n")
    ok, why = registry.probe_safety(runs_on_load)
    assert ok is False and why.startswith("RUNS ON LOAD")
    lib = tmp_path / "lib.py"
    lib.write_text("import os\n\ndef helper():\n    return 1\n")
    assert registry.probe_safety(lib) == (False, "library module: import it, do not run it")
    boilerplate = tmp_path / "boiler.py"                 # common start-up code is not "work"
    boilerplate.write_text(
        "import os, sys\nHERE = os.path.dirname(__file__)\nif HERE not in sys.path:\n    sys.path.insert(0, HERE)\n"
        "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
        "try:\n    sys.stdout.reconfigure(encoding='utf-8')\nexcept Exception:\n    pass\n"
        "import argparse\n\ndef main():\n    argparse.ArgumentParser().parse_args()\n\n"
        "if __name__ == '__main__':\n    main()\n")
    assert registry.probe_safety(boilerplate) == (True, "main guard + argparse, no module-level work")
    loop = tmp_path / "loop.py"
    loop.write_text("import argparse\nfor f in []:\n    pass\nif __name__ == '__main__':\n    pass\n")
    assert registry.probe_safety(loop)[0] is False
    bom = tmp_path / "bom.py"
    bom.write_bytes(b"\xef\xbb\xbf" + safe.read_bytes())
    assert registry.probe_safety(bom)[0] is True        # a BOM is not a syntax error



def test_cli_host_and_tools_json():
    rc, out, _ = _cli("host", "--json")
    assert rc == 0 and "system" in json.loads(out)
    rc, out, _ = _cli("tools", "list", "--json")
    assert rc == 0 and any(t["id"] == "office" for t in json.loads(out))


def test_cli_pipeline_list_and_plan():
    rc, out, _ = _cli("pipeline", "list", "--json")
    names = {p["name"] for p in json.loads(out)}
    assert {"debian-kernel", "firefox-windows"} <= names
    rc, out, err = _cli("pipeline", "plan", "debian-kernel", "--json", "--var", "workdir=/tmp/kb", "project=/tmp/proj")
    assert rc == 0, err
    plan = json.loads(out)
    assert plan["vars"]["src"] == "/tmp/kb/linux-7.2.9"


def test_cli_triage_exit_codes(tmp_path):
    known = tmp_path / "k.log"
    known.write_text(" 0:01.21 E Cannot find the target C compiler\n")
    assert _cli("triage", str(known), "--json")[0] == 0
    novel = tmp_path / "n.log"
    novel.write_text("x.c:1:1: error: something never seen before\n")
    assert _cli("triage", str(novel), "--json")[0] == 3


def test_cli_office_round_trip(tmp_path):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"type": "xlsx", "sheets": [{"name": "A", "rows": [["x"], [1]]}]}))
    out = tmp_path / "o.xlsx"
    assert _cli("office", "create", str(spec), str(out), "--json")[0] == 0
    rc, text, _ = _cli("office", "read", str(out))
    assert rc == 0 and "## A" in text
    assert _cli("office", "check", str(out), "--json")[0] == 0


def test_cli_privacy_exit_code(tmp_path):
    (tmp_path / "ok.txt").write_text("nothing")
    assert _cli("privacy", "scan", str(tmp_path), "--json")[0] == 0
    (tmp_path / "bad.txt").write_text("key " + "sk-ant-" + "x" * 30)
    rc, out, _ = _cli("privacy", "scan", str(tmp_path), "--json")
    assert rc == 3 and "anthropic-key" in out


def test_cli_kernel_localversion():
    rc, out, _ = _cli("kernel", "localversion", "--base", "7.1.2", "--tags", "unleashed", "gorilla", "eapd", "--json")
    assert rc == 0 and json.loads(out)["length"] <= 64


def test_cli_bad_usage():
    assert _cli("pipeline", "plan", "no-such-pipeline")[0] != 0


def test_install_skills_is_idempotent_and_scoped(tmp_path, monkeypatch):
    import importlib
    sys.path.insert(0, str(ROOT))
    inst = importlib.import_module("install_skills")
    (tmp_path / ".claude" / "skills" / "someone-elses-skill").mkdir(parents=True)
    monkeypatch.setattr(inst, "TARGETS", [tmp_path / ".claude" / "skills", tmp_path / ".agents" / "skills"])
    inst.main([])
    names = sorted(p.name for p in (tmp_path / ".claude" / "skills").iterdir())
    assert "someone-elses-skill" in names and "fieldkit-office" in names
    assert not (tmp_path / ".agents").exists()           # parent absent: not created
    inst.main(["--remove"])
    assert sorted(p.name for p in (tmp_path / ".claude" / "skills").iterdir()) == ["someone-elses-skill"]


def test_every_skill_has_frontmatter_and_names_real_commands():
    import re
    import argparse
    from fieldkit.cli import build_parser
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    cli_cmds = set(sub.choices)                 # the real commands, not a hand-kept list
    for md in (ROOT / "skills").glob("*/SKILL.md"):
        text = md.read_text(encoding="utf-8")
        assert text.startswith("---\nname: ") and "\ndescription: " in text, md
        used = set(re.findall(r"`fieldkit (\w+)", text)) | set(re.findall(r"^fieldkit (\w+)", text, re.M))
        assert used and used <= cli_cmds, (md, used - cli_cmds)
