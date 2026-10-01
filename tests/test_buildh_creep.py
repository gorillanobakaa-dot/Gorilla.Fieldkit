"""Live run 16 (2026-10-02): the build found, one stop at a time, upstream 157 code reaching into components the
fork removes (speech recognition, the ml directory). The verifier now lists such references up front: lines in the
new tree naming an excised symbol that the old pristine version of the file did not have.
"""
import subprocess

from fieldkit.buildh import firefox


def test_excised_symbols_come_from_gorilla_comments_and_deleted_files(tmp_path):
    hr = tmp_path / "h"
    (hr / "config").mkdir(parents=True)
    (hr / "patchset/16.D").mkdir(parents=True)
    (hr / "config/patch_policy.json").write_text('{"patchset_root": "patchset", "groups": {"16.D": {"status": "enabled"}}}', encoding="utf-8")
    (hr / "patchset/16.D/a.patch").write_text("--- a/x\n+++ b/x\n@@ -1 +1 @@\n+    # GORILLA excised: \"ml\",\n+# GORILLA excised: backends/llama (llama.cpp)\n", encoding="utf-8")
    (hr / "patchset/16.D/DELETED_FILES.manifest.txt").write_text("dom/media/webspeech/recognition/PSpeechRecognition.ipdl\nbrowser/x/moz.build\n", encoding="utf-8")
    syms = firefox.excised_symbols(hr)
    assert "PSpeechRecognition" in syms and "backends" not in syms and "ml" not in syms       # too short / not a symbol


def test_creep_lists_new_mentions_only(tmp_path):
    w, old = tmp_path / "w", tmp_path / "old"
    for root, text in ((w, "include protocol PA;\ninclude protocol PSpeechRecognition;\n"), (old, "include protocol PA;\n")):
        (root / "dom/ipc").mkdir(parents=True)
        (root / "dom/ipc/PContent.ipdl").write_text(text, encoding="utf-8")
    (w / "dom/ipc/Old.cpp").write_text("PSpeechRecognition here too\n", encoding="utf-8")
    (old / "dom/ipc/Old.cpp").write_text("PSpeechRecognition here too\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], check=True)
    out = firefox.excision_creep(w, old, ["PSpeechRecognition"])
    assert out == [("dom/ipc/PContent.ipdl", 1, "2: include protocol PSpeechRecognition;")]
    assert firefox.excision_creep(w, old, []) == []
