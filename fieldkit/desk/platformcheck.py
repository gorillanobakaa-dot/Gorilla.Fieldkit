# Version: 1.0.0 · updated 26-10-16-22-30
"""Every tool card's `platforms` is a promise, held to the tests that CI runs on Windows AND Linux.

Owner, 2026-10-16: "make sure that the tools we have made in the harness work in linux as well and when linux
creates another tool that one will work on windows as well". CI runs the whole suite on windows-latest and
ubuntu-latest for every push (.github/workflows/tests.yml), so a tool whose test runs on both is proven on both.
What nothing checked:
  - a card that promises a system while its whole test file is skipped there (the promise is untested);
  - a NEW card for one system only, with no reason: a tool born on one machine that the other never gets.
Rules, fail closed:
  1. platforms is a non-empty subset of KNOWN;
  2. a card naming windows or linux but not both says why in `platforms_why`, or is in the frozen backlog
     (BASELINE: the cards that were single-system on 2026-10-16; the list only shrinks);
  3. a card promising windows/linux has no test file skipped as a whole on that system (module-level pytestmark).

    python -m fieldkit.desk.platformcheck [--json]      the port backlog and every problem
"""
import json
import re
import sys
from pathlib import Path

import yaml

KNOWN = {"windows", "linux", "darwin", "freebsd"}
BUILT = ("windows", "linux")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BASELINE = HERE / "single_platform_baseline.txt"
LOCAL_BASELINE = ROOT / "local" / "single_platform_baseline.txt"

_SKIP_WHOLE = {
    "windows": re.compile(r"^pytestmark\s*=.*(linux_only|skipif\([^)]*(is_windows|os\.name\s*==\s*.nt.|"
                          r"sys\.platform\s*==\s*.win32.|startswith\(.win))", re.M),
    "linux": re.compile(r"^pytestmark\s*=.*(windows_only|skipif\([^)]*(is_linux|os\.name\s*!=\s*.nt.|"
                        r"sys\.platform\s*!=\s*.win32.|startswith\(.linux))", re.M),
}


def cards(files=None):
    files = files or [HERE / "tools.yaml", ROOT / "local" / "tools.yaml"]
    out = []
    for f in files:
        if Path(f).is_file():
            for c in (yaml.safe_load(Path(f).read_text(encoding="utf-8")) or {}).get("tools") or []:
                out.append({**c, "_file": str(f)})
    return out


def baseline(files=None):
    ids = set()
    for f in files or (BASELINE, LOCAL_BASELINE):
        if Path(f).is_file():
            ids |= {l.split("#")[0].strip() for l in Path(f).read_text(encoding="utf-8").splitlines()} - {""}
    return ids


def single(card):
    """The one built system a card is limited to, or None."""
    p = set(card.get("platforms") or [])
    have = [x for x in BUILT if x in p]
    return have[0] if len(have) == 1 else None


def _test_files(card):
    t = card.get("test") or []
    return [Path(str(a).replace("${FIELDKIT}", str(ROOT))) for a in t if str(a).endswith(".py")]


def skipped_whole(test_file):
    """The systems a test file is skipped on as a whole -> set."""
    try:
        text = Path(test_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {p for p, rx in _SKIP_WHOLE.items() if rx.search(text)}


def check(all_cards=None, frozen=None):
    """-> {"problems": [...], "backlog": [ids single-system with no reason], "dual": n}"""
    all_cards = cards() if all_cards is None else all_cards
    frozen = baseline() if frozen is None else frozen
    problems, backlog = [], []
    for c in all_cards:
        cid, plats = c.get("id"), set(c.get("platforms") or [])
        if not plats or not plats <= KNOWN:
            problems.append(f"{cid}: platforms {sorted(plats)} (allowed: {sorted(KNOWN)})")
            continue
        one = single(c)
        if one and not c.get("platforms_why"):
            if cid in frozen:
                backlog.append(cid)
            else:
                problems.append(f"{cid}: {one} only, with no platforms_why - port it to the other system, or say why "
                                f"it cannot run there")
        for tf in _test_files(c):
            bad = skipped_whole(tf) & plats & set(BUILT)
            if bad:
                problems.append(f"{cid}: promises {sorted(bad)}, but its test {tf.name} is skipped there as a whole")
    return {"problems": problems, "backlog": sorted(backlog),
            "dual": sum(1 for c in all_cards if not single(c))}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--help" in argv or "-h" in argv:
        # 2026-10-19: it ran the check on --help and exited 1 on a card's platform problem, so `cards check` reported
        # "--help fails" instead of naming the card that has no platforms_why
        print("usage: python -m fieldkit.desk.platformcheck [--json]\n"
              "Every card's platforms: a single-system card needs platforms_why (a reason, or a port planned).\n"
              "Exit 1 names each card with a problem.")
        return 0
    r = check()
    if "--json" in argv:
        print(json.dumps(r, indent=1))
    else:
        print(f"PLATFORMS: {len(r['problems'])} problem(s), {len(r['backlog'])} single-system card(s) waiting for a "
              f"port or a reason, {r['dual']} on both")
        for p in r["problems"]:
            print("  PROBLEM " + p)
        for b in r["backlog"]:
            print("  backlog " + b)
    return 1 if r["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
