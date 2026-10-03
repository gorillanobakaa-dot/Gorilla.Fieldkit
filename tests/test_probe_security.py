"""probe_safety: only definitions, imports, docstrings, the main guard and constant assignments are harmless;
argparse is detected from the syntax tree (an import AND a parse_args call), never from the text.
Nothing here is ever run: probe_safety only parses."""
import pytest

from fieldkit.desk import registry

MAIN = "\ndef main():\n    import argparse\n    argparse.ArgumentParser().parse_args()\n\nif __name__ == '__main__':\n    main()\n"


def _probe(tmp_path, body, tail=MAIN):
    f = tmp_path / "tool.py"
    f.write_text("import argparse, os, sys, re, atexit\nfrom pathlib import Path\n" + body + tail, encoding="utf-8")
    return registry.probe_safety(f)


@pytest.mark.parametrize("body", [
    "x = do_work()\n",                                    # the hole: any assignment used to pass
    "x, y = 1, do_work()\n",
    "atexit.register(do_work)\n",                         # passed the old suffix whitelist
    "evil.reconfigure()\n",                               # ditto: matched '.reconfigure'
    "sys.stdout.reconfigure(encoding=pick())\n",          # set-up call, but its argument calls something
    "@app.route('/')\ndef index():\n    pass\n",         # decorator runs at import
    "def f(x=compute()):\n    pass\n",                    # default evaluated at import
    "class A:\n    x = compute()\n",                      # class bodies run at import
    "class A(make_base()):\n    pass\n",
    "if os.path.exists('x'):\n    pass\n",               # the if-test itself calls
    "try:\n    import yaml\nexcept ImportError:\n    yaml = install('yaml')\n",
    "for f in []:\n    pass\n",
    "with open('x', 'w') as fh:\n    pass\n",
    "os.environ['X'] = compute()\n",
    "Path('a').replace('b')\n",
    "P = Path('a').replace('b')\n",                       # Path.replace renames on disk: never pure
    "SOURCES = _discover_sources()\n",
    "print('hello')\n",
])
def test_module_level_work_is_unsafe(tmp_path, body):
    safe, why = _probe(tmp_path, body)
    assert safe is False and why.startswith("RUNS ON LOAD"), why


@pytest.mark.parametrize("body", [
    '"""docstring"""\n',
    "X = 1\nY = (1, 'a', None)\nZ = {'a': [1, 2]}\nW = -X + 2 * len\n",
    "NAME: str = 'x'\nFLAGS = X if True else None\n",
    "RX = re.compile(r'[a-z]+')\nHERE = Path(__file__).resolve().parent\nOUT = HERE / 'out'\n",
    "HERE = os.path.dirname(os.path.abspath(__file__))\nif HERE not in sys.path:\n    sys.path.insert(0, HERE)\n",
    "WORDS = set('a b c'.split())\nPAIRS = {'(': ')'}\nCLOSERS = {v: k for k, v in PAIRS.items()}\n",
    "TABLE = {}\nTABLE['a'] = (1, lambda v: v == '771')\n",
    "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n",
    "try:\n    sys.stdout.reconfigure(encoding='utf-8')\nexcept Exception:\n    pass\n",
    "from dataclasses import dataclass, field\n@dataclass(frozen=True)\nclass A:\n    x: int = 0\n"
    "    y: list = field(default_factory=list)\n\n    @property\n    def z(self):\n        return 1\n",
    "def f(a: int = 1, *, b: str = 'x') -> int:\n    return do_work()\n",   # the body is not run on load
])
def test_set_up_and_definitions_are_harmless(tmp_path, body):
    assert _probe(tmp_path, body) == (True, "main guard + argparse, no module-level work")


def test_argparse_in_text_only_is_not_argparse(tmp_path):
    """The old text search counted a docstring that merely mentions argparse."""
    f = tmp_path / "t.py"
    f.write_text('"""Example:\n    import argparse\n    ap.parse_args()\n"""\nimport sys\n\ndef main():\n'
                 '    print(sys.argv)\n\nif __name__ == "__main__":\n    main()\n', encoding="utf-8")
    assert registry.probe_safety(f) == (False, "no argparse: --help is not understood")


def test_argparse_imported_but_never_parsed_is_not_safe(tmp_path):
    f = tmp_path / "t.py"
    f.write_text("import argparse\n\ndef main():\n    argparse.ArgumentParser()\n    do_everything()\n\n"
                 "if __name__ == '__main__':\n    main()\n", encoding="utf-8")
    assert registry.probe_safety(f) == (False, "no argparse: --help is not understood")


def test_only_the_exact_main_guard_counts(tmp_path):
    f = tmp_path / "t.py"
    f.write_text("import argparse, sys\nif '__main__' in sys.argv:\n    argparse.ArgumentParser().parse_args()\n",
                 encoding="utf-8")
    assert registry.probe_safety(f)[0] is False
    g = tmp_path / "g.py"                                  # the guard's else runs on import
    g.write_text("import argparse\nif __name__ == '__main__':\n    argparse.ArgumentParser().parse_args()\n"
                 "else:\n    do_work()\n", encoding="utf-8")
    safe, why = registry.probe_safety(g)
    assert safe is False and why.startswith("RUNS ON LOAD")
    h = tmp_path / "h.py"                                  # reversed comparison is still the guard
    h.write_text("import argparse\nif '__main__' == __name__:\n    argparse.ArgumentParser().parse_args()\n",
                 encoding="utf-8")
    assert registry.probe_safety(h)[0] is True
