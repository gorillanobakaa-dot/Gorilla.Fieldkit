"""self-test: build a fixture repository of a kind, seed ONE omission per surface, and require the check to name it.
The clean fixture (nothing omitted) must pass. Exit 0 only when every omission was caught."""
import json
import tempfile
from pathlib import Path

import yaml

from . import core

# the capability each seeded omission is aimed at, per kind
PRIMARY = {"opencode": {"helper": "alphahelper", "command": "alphacmd"},
           "fieldkit": {"tool": "alpha", "package": "zeta"}}
SHA = "0" * 64


class State:
    def __init__(self):
        self.files, self.cards, self.manifest, self.block, self.done = {}, {}, [], [], set()

    def add(self, rel, line):
        self.files[rel] = self.files.get(rel, "") + line + "\n"

    def write(self, root):
        for rel, text in self.files.items():
            p = Path(root) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")

        def put(rel, text):
            p = Path(root) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        if self.cards:
            put("fieldkit/desk/tools.yaml", yaml.safe_dump({"tools": list(self.cards.values())}, sort_keys=False))
        if self.manifest:
            put("internal/arsenal/manifest.json", json.dumps({"series": [{"entries": self.manifest}]}))
        if self.block:
            put("ReleaseNotes/GITHUB-RELEASE-NOTES-v0.1.1.md",
                "# page\n<!-- helpers:begin -->\n" + "\n".join(f"| {n} |" for n in self.block)
                + "\n<!-- helpers:end -->\n")


def _fill(s, name):
    return s.replace("{name}", name).replace("{command}", name).replace("{sha}", SHA)


def satisfy(st, cfg, ctype, rule, name):
    t = rule["type"]
    if t == "word_in":
        st.add(rule["create"], _fill(rule["sample"], name))
    elif t == "exists":
        st.files[_fill(rule["path"], name)] = f"# {name}\n"
    elif t == "manifest":
        st.manifest.append({"id": name, "detect": {"binaries": [name]}})
    elif t == "systems":
        for system, prefix in cfg["systems"].items():
            if system != "windows":
                st.add("internal/toolbox/SHA256SUMS", f"{SHA}  {prefix}amd64/{name}")
    elif t == "block":
        st.block.append(name)
    elif t == "kind_has":
        if rule["id"] not in st.done:
            st.done.add(rule["id"])
            st.add(rule["files"][0], _fill(rule["sample"], name))
    elif t == "card_field":
        st.cards[name][rule["field"]] = ["python", "-m", "fieldkit", name] if rule["field"] == "entry" else f"{name} {rule['field']}"
    elif t == "card_path":
        st.cards[name]["path"] = f"${{FIELDKIT}}/fieldkit/{name}"
        st.files[f"fieldkit/{name}/__init__.py"] = ""
    elif t == "card_test":
        st.cards[name]["test"] = ["python", "-m", "pytest", "-q", f"${{FIELDKIT}}/tests/test_{name}.py"]
        st.files[f"tests/test_{name}.py"] = ""
    elif t == "card_platforms":
        st.cards[name]["platforms"] = list(cfg.get("required_systems", ["windows", "linux"]))
    else:
        raise ValueError(f"self-test cannot satisfy rule type {t!r}")


def build(root, kind, omit=None):
    """Write the fixture. omit = (capability type, rule id) leaves exactly that surface out."""
    rules = core.load_rules()
    cfg = rules[kind]
    st = State()
    st.add(cfg["repo_marker"], "# fixture")          # how the kind is recognized, whatever else is omitted
    for ctype, cap in cfg["capabilities"].items():
        name = PRIMARY[kind][ctype]
        # the capability must be discoverable whatever is omitted
        if ctype == "helper":
            st.files[f"internal/toolbox/bin/windows-amd64/{name}.exe.gz"] = ""
        elif ctype == "command":
            st.add("internal/commands/registry.go", f'\tName: "{name}",')
        elif ctype == "tool":
            st.cards[name] = {"id": name}
        elif ctype == "package":
            st.files[f"fieldkit/{name}/__init__.py"] = ""
        for rule in cap["surfaces"]:
            if omit == (ctype, rule["id"]) or (omit and omit[0] == ctype and omit[1] in rule.get("needs", [])):
                continue
            if rule["type"] == "package_card":
                st.cards[name] = {"id": name, "entry": ["python", "-m", "fieldkit", name]}
                for r2 in cfg["capabilities"]["tool"]["surfaces"]:
                    if omit != ("tool", r2["id"]):
                        satisfy(st, cfg, "tool", r2, name)
            else:
                satisfy(st, cfg, ctype, rule, name)
    st.write(root)
    return cfg


def run(kind):
    """-> (ok, [lines])"""
    rules = core.load_rules()
    if kind not in rules:
        return False, [f"unknown kind {kind!r}"]
    out, ok = [], True
    with tempfile.TemporaryDirectory(prefix=f"intg-selftest-{kind}-") as tmp:
        clean = Path(tmp) / "clean"
        clean.mkdir()
        build(clean, kind)
        r = core.run_check(clean)
        good = r["ok"] and r["kind"] == kind
        ok &= good
        out.append(f"{'ok    ' if good else 'FAIL  '} clean fixture passes" + ("" if good else f": {r['gaps'][:3]}"))
        n = 0
        for ctype, cap in rules[kind]["capabilities"].items():
            for rule in cap["surfaces"]:
                n += 1
                d = Path(tmp) / f"omit{n}"
                d.mkdir()
                build(d, kind, (ctype, rule["id"]))
                r = core.run_check(d)
                want = "*" if rule.get("scope") == "kind" else PRIMARY[kind][ctype]
                hit = [g for g in r["gaps"] if g["surface"] == rule["id"] and g["capability"] == want]
                ok &= bool(hit)
                out.append(f"{'caught' if hit else 'MISSED'} {kind}:{ctype}:{rule['id']}"
                           + (f" -> {hit[0]['fix']}" if hit else " (the check did not name the seeded omission)"))
    return ok, out
