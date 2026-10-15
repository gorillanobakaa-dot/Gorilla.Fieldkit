# Version: 1.0.0 · updated 26-10-15-10-40
"""must-keep: the 2026-10-03 regeneration that silently dropped the GPU unlock (D-158-06), refused before a build."""
import json
import subprocess

from fieldkit.build import firefox_preflight as fp

POLICY = {"must_keep": {"files": [
    {"file": "widget/gtk/GfxInfo.cpp", "platform": "linux", "decision": "D-158-06", "why": "gtk layer"},
    {"file": "widget/GfxInfoBase.cpp", "platform": "all", "decision": "D-158-06", "why": "shared layer"},
    {"file": "widget/windows/Only.cpp", "platform": "windows", "decision": "x", "why": "not this platform"}]},
    "patchset_root": "patches", "groups": {"02.GPU": {"status": "enabled"}}}
# what a patch WRITES decides, not its name (2026-10-16: exported patches are named after their reason)
NAMES = {"widget_gtk_GfxInfo.cpp.patch": "widget/gtk/GfxInfo.cpp", "widget_GfxInfoBase.cpp.patch": "widget/GfxInfoBase.cpp"}


class Ctx:
    def __init__(self, root, src):
        self.root, self.src = root, src


def _setup(tmp_path, changed, patches):
    root, src = tmp_path / "harness", tmp_path / "src"
    (root / "config").mkdir(parents=True)
    (root / "config" / "patch_policy.json").write_text(json.dumps(POLICY))
    (root / "patches" / "02.GPU").mkdir(parents=True)
    for p in patches:
        f = NAMES[p]
        (root / "patches" / "02.GPU" / p).write_text(f"--- a/{f}\n+++ b/{f}\n@@ -1 +1,2 @@\n upstream\n+// GORILLA\n")
    (src / "widget" / "gtk").mkdir(parents=True)
    for f in ("widget/gtk/GfxInfo.cpp", "widget/GfxInfoBase.cpp"):
        (src / f).write_text("upstream\n")
    g = lambda *a: subprocess.run(["git", "-C", str(src), *a], check=True, capture_output=True)
    g("init", "-q")
    g("add", "-A")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "pristine")
    for f in changed:
        (src / f).write_text("upstream\n// GORILLA OVERRIDE\n")
    return Ctx(root, src)


def test_the_158_state_after_the_regeneration_is_refused(tmp_path):
    c = _setup(tmp_path, ["widget/GfxInfoBase.cpp"], ["widget_GfxInfoBase.cpp.patch"])
    ok, detail = fp._c_must_keep_tree(c)
    assert not ok and detail == "the tree does not carry: widget/gtk/GfxInfo.cpp (D-158-06)"
    ok, detail = fp._c_must_keep_set(c)
    assert not ok and "widget/gtk/GfxInfo.cpp" in detail and "Only.cpp" not in detail   # windows-only row skipped


def test_complete_tree_and_set_pass(tmp_path):
    c = _setup(tmp_path, ["widget/gtk/GfxInfo.cpp", "widget/GfxInfoBase.cpp"],
               ["widget_gtk_GfxInfo.cpp.patch", "widget_GfxInfoBase.cpp.patch"])
    assert fp._c_must_keep_tree(c)[0] and fp._c_must_keep_set(c)[0]


def test_rows_are_registered_with_the_right_weight():
    rows = {c.cid: c for c in fp.CHECKS}
    assert rows["must-keep-tree"].severity == fp.BLOCKER and rows["must-keep-set"].severity == fp.WARN


def test_a_patch_named_after_its_reason_counts_by_what_it_writes(tmp_path):
    c = _setup(tmp_path, ["widget/gtk/GfxInfo.cpp", "widget/GfxInfoBase.cpp"], ["widget_GfxInfoBase.cpp.patch"])
    f = "widget/gtk/GfxInfo.cpp"
    (c.root / "patches" / "02.GPU" / "010-gpu-blocklist-short-circuit.patch").write_text(
        f"--- a/{f}\n+++ b/{f}\n@@ -1 +1,2 @@\n upstream\n+// GORILLA\n")
    assert fp._c_must_keep_set(c)[0]
