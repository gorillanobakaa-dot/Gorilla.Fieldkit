"""Techniques: the idea behind a fix is checked in the tree, so it survives code that moved (T-157-31, the blank
new tab of build 19)."""
import subprocess

from fieldkit.buildh import techniques as tq

NL = chr(10)


def _tree(tmp_path, files):
    w = tmp_path / "w"
    for rel, text in files.items():
        p = w / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline=NL)
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    return w


T = [{"id": "T-X", "title": "t", "decision": "D-1", "problem": "p", "concept": "c", "apply": ["a"], "verify": ["v"],
      "signals": [
          {"name": "lock", "kind": "must_have", "path": "a/lock.mjs", "text": "PHYSICAL LOCK (D-1)"},
          {"name": "gone", "kind": "must_lack", "path": "a/page.mjs", "text": "feature.ready()"},
          {"name": "waiters", "kind": "watch", "pathspec": ["a"], "regex": r"Feature\.ready\(\)", "guard_lines": 3, "allow": {}},
      ]}]

LOCKED_FN = NL.join([
    "  async install({", "    url,", "  }) {",
    "    // GORILLA UNLEASHED - PHYSICAL LOCK (D-1): never", "    if (true) {", "      return;", "    }",
    "    if (url) {", "      x();", "    }", "    await Feature.ready();", "  },", ""])


def test_a_tree_that_keeps_the_technique_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(tq, "TECHNIQUES", T)
    w = _tree(tmp_path, {
        "a/lock.mjs": "// GORILLA UNLEASHED - PHYSICAL LOCK (D-1)" + NL,
        "a/page.mjs": "// T-X: the page no longer awaits Feature.ready()" + NL + "build();" + NL,   # a comment is not a hit
        "a/inst.mjs": LOCKED_FN,                                                              # dead code below a lock
        "a/near.mjs": "// GORILLA TECHNIQUE T-X: why" + NL + "await Feature.ready();" + NL,
    })
    r = tq.scan(w)[0]
    assert r["ok"], tq.lines([r])
    states = sorted(h["state"] for s in r["signals"] for h in s.get("hits", []))
    assert states == ["guarded", "guarded"]


def test_a_new_waiter_a_lost_lock_or_returned_code_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(tq, "TECHNIQUES", T)
    w = _tree(tmp_path, {
        "a/lock.mjs": "nothing here" + NL,
        "a/page.mjs": "await feature.ready();" + NL,
        "a/new.mjs": "if (x) {" + NL + "  await Feature.ready();" + NL + "}" + NL,              # an if is not a function
    })
    r = tq.scan(w)[0]
    bad = {s["name"] for s in r["signals"] if not s["ok"]}
    assert not r["ok"] and bad == {"lock", "gone", "waiters"}
    rows = tq.gate_rows(w)
    assert rows and rows[0][1] is False


def test_the_real_registry_is_complete():
    for t in tq.TECHNIQUES:
        assert t["problem"] and t["concept"] and t["apply"] and t["verify"] and t["decision"].startswith("D-")
        assert any(s["kind"] == "watch" for s in t["signals"]), t["id"]
