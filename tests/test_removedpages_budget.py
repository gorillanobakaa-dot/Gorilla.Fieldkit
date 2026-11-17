"""The removed-pages probe spends several seconds per address, so its time budget grows with the list (2026-11-16, Firefox
160: 33 addresses outran the fixed 300 s and build-verify reported 'None of 33 reported')."""
from fieldkit.buildh import probe as pb
from fieldkit.buildh import removedpages as rp


def _run_with(monkeypatch, tmp_path, n_pages, timeout):
    seen = {}
    reg = tmp_path / "ABOUT-PAGES.yaml"
    reg.write_text("pages:\n" + "".join(f"  - {{name: p{i}, verdict: remove}}\n" for i in range(n_pages)), encoding="utf-8")

    def fake_run(install_dir, probe, wait=10, timeout=300, say=print, **kw):
        seen["timeout"] = timeout
        return {"lines": []}
    monkeypatch.setattr(pb, "run", fake_run)
    rp.rows("C:/nowhere", reg, files=[], timeout=timeout)
    return seen["timeout"]


def test_the_budget_grows_with_the_number_of_addresses(monkeypatch, tmp_path):
    assert _run_with(monkeypatch, tmp_path, 40, 300) == 90 + 10 * 40          # 490: more than the fixed 300


def test_a_longer_budget_asked_for_is_kept(monkeypatch, tmp_path):
    assert _run_with(monkeypatch, tmp_path, 3, 900) == 900                    # never shortened
