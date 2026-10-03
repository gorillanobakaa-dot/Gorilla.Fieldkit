"""Draft cards never guess read-only for a script whose code writes, deletes or starts processes.
Effects are read from the syntax tree; no script is run."""
import pytest

from fieldkit.desk import cards

HEAD = "import os, shutil, subprocess\nfrom pathlib import Path\n"


def _draft(tmp_path, body, probe="safe", touches=()):
    f = tmp_path / "tool.py"
    f.write_text(HEAD + body, encoding="utf-8")
    e = {"id": "src/tool.py", "title": "t", "language": "python", "touches": list(touches), "options": [],
         "portable": True, "probe": probe}
    return cards.draft_card(dict(e, path=str(f)))


@pytest.mark.parametrize("body, effect", [
    ("def main(p):\n    open(p, 'w').write('x')\n", "writes-files"),
    ("def main(p):\n    with open(p, mode='a') as f:\n        pass\n", "writes-files"),
    ("def main(p):\n    with open(p, 'r+b') as f:\n        pass\n", "writes-files"),
    ("def main(p, m):\n    open(p, m)\n", "writes-files"),                         # unknown mode: assume write
    ("def main(p):\n    Path(p).open('x')\n", "writes-files"),
    ("def main(p):\n    Path(p).write_text('x')\n", "writes-files"),
    ("def main(p):\n    Path(p).write_bytes(b'x')\n", "writes-files"),
    ("from shutil import copy as cp\ndef main(a, b):\n    cp(a, b)\n", "writes-files"),
    ("def main(a, b):\n    shutil.move(a, b)\n", "writes-files"),
    ("def main(a, b):\n    os.rename(a, b)\n", "writes-files"),
    ("import json\ndef main(d, f):\n    json.dump(d, f)\n", "writes-files"),
    ("def main(p):\n    shutil.rmtree(p)\n", "deletes-files"),
    ("from os import remove\ndef main(p):\n    remove(p)\n", "deletes-files"),
    ("def main(p):\n    os.unlink(p)\n", "deletes-files"),
    ("def main(p):\n    Path(p).unlink()\n", "deletes-files"),
    ("def main():\n    subprocess.run(['x'])\n", "processes"),
    ("import subprocess as sp\ndef main():\n    sp.Popen(['x'])\n", "processes"),
    ("def main():\n    os.system('x')\n", "processes"),
])
def test_code_that_changes_things_is_never_read_only(tmp_path, body, effect):
    c = _draft(tmp_path, body)
    assert effect in c["effects"] and c["safety"] != "read-only", c


def test_reading_only_is_still_read_only(tmp_path):
    body = ("import json\ndef main(p):\n    a = open(p).read()\n    b = open(p, 'rb').read()\n"
            "    c = Path(p).read_text()\n    d = json.load(open(p, mode='r'))\n    return [a, b, c, d].copy()\n")
    c = _draft(tmp_path, body)
    assert c["safety"] == "read-only" and not ({"writes-files", "deletes-files", "processes"} & set(c["effects"]))


def test_writes_without_any_harvest_touch_used_to_pass_as_read_only(tmp_path):
    """harvest's text patterns do not see Path.write_text; the AST does."""
    c = _draft(tmp_path, "def main(p):\n    Path(p).write_text('x')\n", touches=())
    assert c["safety"] == "unknown" and "writes-files" in c["effects"]


def test_unparsable_python_is_unknown_not_read_only(tmp_path):
    c = _draft(tmp_path, "def (:\n", probe="no-cli")
    assert c["safety"] == "unknown"


def test_unused_safety_tuple_is_gone():
    assert not hasattr(cards, "SAFETY")
