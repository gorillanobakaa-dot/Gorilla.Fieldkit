"""The exam project: a small, fixed, generated code tree. Same bytes every time.

About 40 files: a Python app with a function defined in a folder named `build`
(the pfind 2.1.0 blind spot), references to it in many files, a decoy name that
does not exist, a 1,500-line failed Debian build log, a CRLF file holding a code
block, and a manifest naming three files that are missing.
"""
import random
from pathlib import Path

DEFINED_IN = "src/build/kernel_tools.py"
MISSING_FROM_MANIFEST = ["assets/icons/gorilla-256.png", "docs/INSTALL.md", "src/net/hotspot.py"]
SNIPPET_FILE = "src/win/profile_loader.py"
SNIPPET = ("def load_profile(path):\n"
           "    with open(path, encoding=\"utf-8\") as f:\n"
           "        data = json.load(f)\n"
           "    return migrate(data)")


def _kernel_tools():
    lines = ['"""Kernel config helpers."""', "import re", "", "",
             "def slug(s):", "    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')", "", ""]
    lines += [f"# helper {i}: keeps the fragment tidy" for i in range(1, 30)]
    lines += ["", "", "def apply_fragment(config_path, flags):",
              '    """Set every flag in a .config. Idempotent."""',
              "    text = open(config_path).read()",
              "    for k, v in flags.items():",
              "        text = re.sub(rf'^{k}=.*$', f'{k}={v}', text, flags=re.M)",
              "    open(config_path, 'w').write(text)", ""]
    return "\n".join(lines) + "\n"


def definition_line():
    return _kernel_tools().splitlines().index("def apply_fragment(config_path, flags):") + 1


def _build_log(rng):
    noise = ["  CC      drivers/net/wireless/ath/ath9k/{}.o", "  LD [M]  sound/pci/hda/{}.ko",
             "warning: {} declared but not used [-Wunused-variable]", "  AR      lib/{}.a",
             "  CHK     include/generated/{}.h", "note: 'error' in comment of {}.c is not an error"]
    words = ["hw", "init", "regd", "mlme", "tcp", "gpio", "btcoex", "alc269", "thinkpad_acpi", "reg"]
    out = [f"dpkg-buildpackage: info: source package linux-upstream", "dpkg-buildpackage: info: source version 7.1.2"]
    for i in range(1480):
        out.append(rng.choice(noise).format(rng.choice(words) + str(i % 97)))
        if i == 700:
            out.append("warning: the compiler differs from the one used to build the kernel")
        if i == 1100:
            out.append("error: reference to undefined label in comment (harmless, ignored)")
    out += ["dpkg-checkbuilddeps: error: Unmet build dependencies: libdw-dev:native",
            "dpkg-buildpackage: warning: build dependencies/conflicts unsatisfied; aborting",
            "make[2]: *** [scripts/Makefile.package:121: bindeb-pkg] Error 3",
            "make[1]: *** [Makefile:1789: bindeb-pkg] Error 2"]
    return "\n".join(out) + "\n"


def build(root):
    """Write the exam tree under root (which must be empty or absent)."""
    rng = random.Random(20260929)
    root = Path(root)
    files = {
        DEFINED_IN: _kernel_tools(),
        "src/build/__init__.py": "",
        "src/__init__.py": "",
        "src/cli.py": "from src.build.kernel_tools import apply_fragment\n\n\ndef main():\n"
                      "    apply_fragment('.config', {'CONFIG_HZ': '1000'})\n",
        "src/pipeline.py": "from src.build import kernel_tools\n\n\ndef stage_config(ctx):\n"
                           "    kernel_tools.apply_fragment(ctx.config, ctx.flags)  # apply_fragment is idempotent\n",
        "tests/test_kernel_tools.py": "from src.build.kernel_tools import apply_fragment\n\n\n"
                                      "def test_apply_fragment(tmp_path):\n    p = tmp_path / '.config'\n"
                                      "    p.write_text('CONFIG_HZ=250\\n')\n    apply_fragment(p, {'CONFIG_HZ': '1000'})\n",
        "tests/test_pipeline.py": "def test_stage_config_calls_apply_fragment():\n    assert True\n",
        "docs/DESIGN.md": "# Design\n\n`apply_fragment` sets flags. See also `rebalance_caches`, planned for 3.0.\n",
        "docs/NOTES.md": "Cache work: rebalance the caches nightly (not written yet).\n",
        "src/cache.py": "CACHE = {}\n\n\ndef clear_cache():\n    CACHE.clear()\n\n\ndef cache_size():\n"
                        "    return len(CACHE)\n",
        "logs/build-7.1.2.log": _build_log(rng),
        "MANIFEST.txt": "\n".join(sorted([DEFINED_IN, "src/cli.py", "src/pipeline.py", "src/cache.py",
                                           "docs/DESIGN.md", "docs/NOTES.md", "README.md",
                                           "tests/test_pipeline.py", "src/win/profile_loader.py"]
                                          + MISSING_FROM_MANIFEST)) + "\n",
        "README.md": "# gorilla-app\n\nA small app used by fieldkit exam.\n",
    }
    for i in range(1, 16):
        files[f"src/plugins/plugin_{i:02d}.py"] = (f'"""Plugin {i}."""\n\n\ndef run_{i}(ctx):\n'
                                                   f"    return ctx.get('value_{i}', {i})\n")
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    loader = ("import json\n\n\ndef migrate(d):\n    return d\n\n\n" + SNIPPET + "\n\n\n"
              "def save_profile(path, data):\n    with open(path, 'w') as f:\n        json.dump(data, f)\n")
    (root / SNIPPET_FILE).parent.mkdir(parents=True, exist_ok=True)
    (root / SNIPPET_FILE).write_bytes(loader.replace("\n", "\r\n").encode("utf-8"))   # a Windows file
    return root
