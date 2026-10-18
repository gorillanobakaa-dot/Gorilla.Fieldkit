"""profile-diff (2026-10-18): what only the real profile shows, from the same probe on a fresh profile and a copy."""
from fieldkit.buildh import profilediff


def fake(by_profile):
    return lambda install, probe, profile=None, **kw: {"done": True, "lines": by_profile[profile]}


def test_the_lines_only_the_real_profile_brings_are_named(tmp_path):
    real = tmp_path / "abc.default"
    real.mkdir()
    run = fake({None: ["SEARCH|engine|google|Google|1"],
                str(real): ["SEARCH|engine|google|Google|1", "SEARCH|engine|perplexity|Perplexity|0"]})
    r = profilediff.diff("inst", "search-engines", real, run=run)
    assert not r["ok"] and r["real_only"] == ["SEARCH|engine|perplexity|Perplexity|0"] and r["fresh_only"] == []
    assert "DIFFERS" in profilediff.lines(r, "search-engines", real)[0]


def test_an_unfinished_run_proves_nothing(tmp_path):
    real = tmp_path / "p"
    real.mkdir()
    run = lambda install, probe, profile=None, **kw: {"done": profile is None, "lines": ["x"]}
    r = profilediff.diff("inst", "search-engines", real, run=run)
    assert not r["ok"] and "UNKNOWN" in profilediff.lines(r, "search-engines", real)[0]
