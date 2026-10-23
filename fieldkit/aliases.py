# Version: 1.0.0 · updated 26-10-23-02-23
"""fk: short, plain-English names for long Fieldkit commands. It always prints the full command before running it.

    fk                          every short name, what it does, and the full command
    fk NAME [values] [more]     print "runs: fieldkit ...", then run it; its exit code is fk's
    fk NAME --show              print the full command only
    fk --json                   the list as JSON (Gorilla OpenCode reads it to offer the same names as /commands)

Owner, 2026-10-23: "comenzile sunt astronomic de lungi. dureaza mai mult sa le tastezi de mana daca vrei sa lucrezi
manual decat sa faci tu lucrul singur ... alias-uri ca in linux si comenzile sa fie scurte si in limbaj uman. comanda
intreaga va fi afisata ca sa stie user-ul ce face". Names in English: the projects are for everyone.

The names are data: fieldkit/desk/aliases.yaml, plus local/aliases.yaml (git-ignored) for a person's own names and
their defaults, e.g. which Mac. A {placeholder} is filled by the values typed after the name, in order; {name=x} has
a default; a default in local/aliases.yaml covers the rest. A missing value is asked for in words, never guessed.
"""
import difflib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "fieldkit" / "desk" / "aliases.yaml"
LOCAL = ROOT / "local" / "aliases.yaml"
SLOT = re.compile(r"\{(\w+)(?:=([^}]*))?\}")


def load(public=PUBLIC, local=LOCAL):
    """-> (aliases, defaults). A local alias of the same name wins."""
    pub = yaml.safe_load(Path(public).read_text(encoding="utf-8")) or {}
    loc = (yaml.safe_load(Path(local).read_text(encoding="utf-8")) or {}) if Path(local).is_file() else {}
    aliases = dict(pub.get("aliases") or {})
    aliases.update(loc.get("aliases") or {})
    return aliases, dict(loc.get("defaults") or {})


def expand(entry, values, defaults):
    """-> (argv after 'fieldkit', missing slot names). Values fill the slots in order; the rest go on the end."""
    values = list(values)
    missing = []
    def fill(m):
        name, default = m.group(1), m.group(2)
        if values:
            return shlex.quote(values.pop(0))
        if default is not None:
            return default
        if name in defaults:
            return shlex.quote(str(defaults[name]))
        missing.append(name)
        return m.group(0)
    line = SLOT.sub(fill, entry["run"])
    return shlex.split(line) + values, missing


def listing(aliases):
    w = max((len(n) for n in aliases), default=4)
    out = ["SHORT NAMES (fk NAME runs the command; fk NAME --show only prints it)"]
    for n, e in aliases.items():
        out.append(f"  {n:<{w}}  {e.get('does', '')}")
        out.append(f"  {'':<{w}}    fieldkit {e['run']}")
    return out


def main(argv=None, run=subprocess.run):
    argv = list(sys.argv[1:] if argv is None else argv)
    aliases, defaults = load()
    if not argv or argv[0] in ("-h", "--help", "list"):
        if argv[:1] in (["-h"], ["--help"]):
            print(__doc__.split("\n\n")[1] + "\n")          # the usage lines: NAME, --show, --json
        print("\n".join(listing(aliases)))
        return 0
    if argv[0] == "--json":
        print(json.dumps({n: {"run": "fieldkit " + e["run"], "does": e.get("does", "")} for n, e in aliases.items()},
                         indent=1))
        return 0
    name, rest = argv[0], argv[1:]
    show = "--show" in rest
    rest = [r for r in rest if r != "--show"]
    if name not in aliases:
        near = difflib.get_close_matches(name, list(aliases), n=3, cutoff=0.5)
        print(f"fk: no short name '{name}'." + (f" Did you mean: {', '.join(near)}?" if near else "") +
              " (fk alone lists them all)")
        return 2
    words, missing = expand(aliases[name], rest, defaults)
    full = "fieldkit " + shlex.join(words)
    if missing:
        print(f"fk {name} needs: {', '.join(missing)}.  Full command: fieldkit {aliases[name]['run']}")
        print(f"  type it after the name (fk {name} {' '.join('<' + m + '>' for m in missing)}), "
              f"or set it once in {LOCAL} under defaults:")
        return 2
    print(f"runs: {full}", flush=True)
    if show:
        return 0
    return run([sys.executable, "-m", "fieldkit", *words]).returncode


if __name__ == "__main__":
    sys.exit(main())
