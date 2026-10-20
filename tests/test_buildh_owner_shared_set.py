# Version: 1.0.0 · updated 26-10-16-18-10
"""A port whose harness root is the SHARED patch set inside the owner's repo (Gorilla.firefox/gorilla-patchset):
the Windows build script is found one folder up, and the patch set (policy + decision register) is still the shared
one, never the owner repo's old per-machine policy (2026-10-16, the 158 port)."""
from pathlib import Path

from fieldkit.buildh import ownercheck


def _layout(tmp_path):
    owner = tmp_path / "Gorilla.firefox"
    (owner / "harness").mkdir(parents=True)
    (owner / "harness" / "gorilla_build.py").write_text("# the owner's Windows build script\n")
    (owner / "config").mkdir()
    (owner / "config" / "patch_policy.json").write_text('{"groups": {}, "patchset_root": "gorilla-patchset/patches"}')
    shared = owner / "gorilla-patchset"
    (shared / "config").mkdir(parents=True)
    (shared / "config" / "patch_policy.json").write_text('{"groups": {"23.REBASE.158": {}}, "patchset_root": "patches"}')
    (shared / "decisions").mkdir()
    (shared / "decisions" / "PRODUCT-DECISIONS.yaml").write_text("decisions: []\n")
    return owner, shared


def test_the_build_script_is_found_one_folder_up_and_the_patch_set_stays_shared(tmp_path):
    owner, shared = _layout(tmp_path)
    t = {"meta": {"harness_root": str(shared)}}
    assert ownercheck._owner_root(t) == owner
    assert Path(ownercheck.patchset_owner(t)) == shared, "went back to the owner repo's old policy"


def test_the_owner_repo_as_harness_root_still_works(tmp_path):
    owner, _ = _layout(tmp_path)
    t = {"meta": {"harness_root": str(owner)}}
    assert ownercheck._owner_root(t) == owner
    assert Path(ownercheck.patchset_owner(t)) == owner
