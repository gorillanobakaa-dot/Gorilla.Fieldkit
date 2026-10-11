"""fieldkit hdmi: the HDMI Harness for an agent (see run.py for why it needs a launcher)."""
import argparse
import sys

from . import run as runmod


def main(argv=None, prog="fieldkit hdmi"):
    ap = argparse.ArgumentParser(prog=prog, description=runmod.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=[*runmod.READ_ONLY, *runmod.ELEVATED])
    ap.add_argument("arg", nargs="?", help="profile-list: the profile; tv-wifi-report: the network name")
    ap.add_argument("--area", choices=["laptop", "tv"], help="diag: only the laptop or only the TV checks")
    ap.add_argument("--only", help="test-quick: comma-separated test ids, e.g. H01,H07")
    ap.add_argument("--path", help="the harness folder (default: toolbox/hdmi-harness, or FIELDKIT_HDMI)")
    a = ap.parse_args(argv)
    if sys.platform != "win32":
        print("REFUSED: the HDMI Harness drives Windows display drivers (Windows 10/11 only)")
        return 2
    extra = ([a.arg] if a.arg else []) + (["--area", a.area] if a.area else []) + (["--only", a.only] if a.only else [])
    try:
        code, text = runmod.run(a.command, extra, a.path)
    except (ValueError, FileNotFoundError) as e:
        print(f"REFUSED: {e}")
        return 2
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
