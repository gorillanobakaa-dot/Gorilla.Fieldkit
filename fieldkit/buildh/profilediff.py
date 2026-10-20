# Version: 1.0.0 · updated 26-10-18
"""The same probe on a fresh profile and on a COPY of a real one: what only the real profile shows.

Born 2026-10-18. The installed 158 offered Perplexity, Bing, DuckDuckGo, Wikipedia and eBay on the owner's profile
(a copy of the 157 one) and only Google on a fresh profile: the build was right, the profile carried the engines. A
bug that is there on "my" browser and not on a test one is this class; the answer is never a guess from the
screenshot. Run the probe that measures the thing on both and read the difference. Linux and Windows: probe.run
starts the installed browser the same way on both (an autoconfig sandbox on a copy of the install; the real
profile is copied first, never opened).

    fieldkit build-harness profile-diff TASK probe=search-engines profile=<real profile dir> [install=<dir>]

A line only on the real copy is what the profile brings (prefs, search settings, stored data); the fix belongs in
the build (lock the setting, refuse the data, as D-158-13's identifier lock did) or in profile-fresh, never in the
profile by hand.
"""
from pathlib import Path


def _lines(install_dir, probe_name, profile, run, wait):
    from . import probe
    r = (run or probe.run)(install_dir, probe_name, wait=wait, timeout=240, say=lambda m: None,
                           **({"profile": profile} if profile else {}))
    return [l.strip() for l in r.get("lines", []) if l.strip()], bool(r.get("done"))


def diff(install_dir, probe_name, profile, run=None, wait=10):
    """-> {ok, done, fresh_only, real_only, same}. ok: both runs finished and the two outputs are the same."""
    if not Path(profile).is_dir():
        raise FileNotFoundError(f"no profile folder {profile}")
    fresh, d1 = _lines(install_dir, probe_name, None, run, wait)
    real, d2 = _lines(install_dir, probe_name, str(profile), run, wait)
    fs, rs = set(fresh), set(real)
    return {"ok": d1 and d2 and fs == rs, "done": d1 and d2,
            "fresh_only": [l for l in fresh if l not in rs], "real_only": [l for l in real if l not in fs],
            "same": len(fs & rs)}


def lines(r, probe_name, profile):
    if not r["done"]:
        return [f"PROFILE-DIFF {probe_name}: UNKNOWN - a probe run did not finish (nothing is proven)"]
    out = [f"PROFILE-DIFF {probe_name}: {'SAME' if r['ok'] else 'DIFFERS'} - {r['same']} line(s) on both; "
           f"real profile = a copy of {Path(profile).name}"]
    out += [f"  only on the real profile: {l}" for l in r["real_only"][:40]]
    out += [f"  only on a fresh profile : {l}" for l in r["fresh_only"][:40]]
    return out
