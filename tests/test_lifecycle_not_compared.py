"""A snapshot part that could not be read must make a lifecycle NOT CLEAN, never clean by silence."""
from fieldkit.build import lifecycle
from fieldkit.core import snapshot
from tests.test_lifecycle import CLEAN_UNINSTALL, INSTALL, _spec


def test_uncompared_part_is_not_clean(monkeypatch, tmp_path):
    real = snapshot.diff

    def diff(a, b):
        d = real(a, b)
        d["services"] = {"added": [], "removed": [], "changed": {}, "not_compared": "powershell timed out"}
        return d

    monkeypatch.setattr(snapshot, "diff", diff)
    r = lifecycle.run(_spec(tmp_path, INSTALL, CLEAN_UNINSTALL), approve=True, state_dir=tmp_path.parent / "stnc")
    assert not r["clean"]
    assert any("NOT COMPARED" in line for line in lifecycle.lines(r))
