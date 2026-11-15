"""pins self-test: a small clean fixture tree, then each break kind seeded into a temp copy; check must exit 3."""
import contextlib
import io
import shutil
import tempfile
from pathlib import Path

from . import cli

HEX = {"a": "a" * 64, "b": "b" * 64, "c": "c" * 64}

CLEAN = {
    "third_party/PINS.md": (
        "# Third-party pins\n\n| tool | upstream | version | sha256 |\n|---|---|---|---|\n"
        f"| oxipng | https://github.com/oxipng/oxipng | 10.2.1 | oxipng-10.2.1-x86_64-unknown-linux-musl.tar.gz {HEX['a']} |\n"),
    "internal/toolbox/SHA256SUMS": f"# pins\n{HEX['b']}  linux-amd64/oxipng\n{HEX['c']}  windows-amd64/oxipng.exe\n",
    "scripts/build-toolbox.ps1": "# embeds the pinned helpers\n$pins = Get-Content internal/toolbox/SHA256SUMS\n",
    "scripts/build-toolbox.sh": "#!/usr/bin/env bash\n# embeds the pinned helpers\nwhile read -r sum f; do :; done < internal/toolbox/SHA256SUMS\n",
    "scripts/fetch-toolbox-extras.sh": (
        "#!/usr/bin/env bash\nOXI=https://github.com/oxipng/oxipng/releases/download/v10.2.1\n"
        "declare -A url=(\n  [oxipng-10.2.1-x86_64-unknown-linux-musl.tar.gz]=$OXI/oxipng-10.2.1-x86_64-unknown-linux-musl.tar.gz)\n"
        f"declare -A want=(\n  [oxipng-10.2.1-x86_64-unknown-linux-musl.tar.gz]={HEX['a']})\n"),
}

# kind -> (file, line appended). Each line is a source that must be refused.
BREAKS = {
    "latest-release": ("scripts/fetch-toolbox-extras.sh",
                       'curl -fsSL "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz" -o /tmp/f.tar.xz'),
    "releases-latest": ("scripts/fetch-toolbox-extras.sh", "curl -fsSL https://github.com/o/r/releases/latest/download/tool-1.2.3.zip -o /tmp/t.zip"),
    "nightly": ("scripts/build-toolbox.sh", "curl -fsSL https://example.org/nightly/tool-1.2.3.tar.gz -o /tmp/t.tgz"),
    "rolling": ("scripts/build-toolbox.ps1", "Invoke-WebRequest https://example.org/rolling/tool-1.2.3.zip -OutFile t.zip"),
    "autobuild": ("scripts/fetch-toolbox-extras.sh", "curl -fsSL https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-18-13-01/ffmpeg-n9.0.2-linux64-gpl-9.0.zip -o /tmp/f.zip"),
    "master-archive": ("scripts/build-toolbox.sh", "curl -fsSL https://github.com/o/r/archive/master.tar.gz -o /tmp/m.tgz"),
    "main-archive": ("scripts/build-toolbox.ps1", "Invoke-WebRequest https://github.com/o/r/archive/main.zip -OutFile m.zip"),
    "refs-heads": ("scripts/fetch-toolbox-extras.sh", "curl -fsSL https://github.com/o/r/archive/refs/heads/dev.zip -o /tmp/d.zip"),
    "head-archive": ("scripts/build-toolbox.sh", "curl -fsSL https://example.org/o/r/archive/HEAD.tar.gz -o /tmp/h.tgz"),
    "unversioned": ("scripts/fetch-toolbox-extras.sh", "curl -fsSL https://example.org/downloads/ffmpeg.zip -o /tmp/u.zip"),
    "unpinned": ("scripts/fetch-toolbox-extras.sh", "curl -fsSL https://example.org/dl/tool-2.0.1.tar.gz -o /tmp/p.tgz"),
    "pins-md-rolling": ("third_party/PINS.md", "| bad | https://example.org/tool-latest.zip | 1.0 | x |"),
    "malformed-sums": ("internal/toolbox/SHA256SUMS", "deadbeef  linux-amd64/tool"),
}


def build(root, kind=None):
    root = Path(root)
    for rel, text in CLEAN.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    if kind:
        rel, line = BREAKS[kind]
        with open(root / rel, "a", encoding="utf-8", newline="\n") as h:
            h.write(line + "\n")


def _exit(root):
    with contextlib.redirect_stdout(io.StringIO()):
        return cli.main(["check", "--repo", str(root), "--json"])


def run():
    ok, lines = True, []
    tmp = Path(tempfile.mkdtemp(prefix="pins-selftest-"))
    try:
        for kind in [None, *BREAKS]:
            d = tmp / (kind or "clean")
            build(d, kind)
            rc = _exit(d)
            want = 0 if kind is None else 3
            good = rc == want
            ok &= good
            lines.append(f"{'ok' if good else 'MISSED'} {kind or 'clean tree'}: check exit {rc}, wanted {want}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok, lines
