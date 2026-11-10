"""P26: the per-project harness sections.

    fieldkit capture project NAME      firefox | kernel | opencode: the section a small model jumps into (a GOTO)

The section is built from data (projects.yaml, the emerge recipes, fieldkit/desk/tools.yaml, the capture ledger): the
recipes' stages in order, each one sentence + one command + one check; the project's tool cards; the tools the capture
loop adopted for it; the ledger lines still open for it. Nothing is run here.
"""
import json
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def load():
    return yaml.safe_load((HERE / "projects.yaml").read_text(encoding="utf-8")) or {}


def prefixed(project, cid):
    """The id a captured candidate gets: the project's prefix, once."""
    pre = (load().get(project) or {}).get("prefix")
    return cid if not pre or cid.startswith(pre) else pre + cid


def project_of_id(cid):
    return next((n for n, d in load().items() if str(cid).startswith(d.get("prefix", "\0"))), None)


def _recipe_steps(name, root):
    p = Path(root) / "fieldkit" / "emerge" / "recipes" / f"{name}.yaml"
    if not p.is_file():
        return []
    stages = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("stages") or {}
    out = []
    for i, (st, d) in enumerate(stages.items(), 1):
        needs = (d or {}).get("needs") or []
        out.append({"n": i, "stage": st, "sentence": f"Stage {st}" + (f" (after {', '.join(needs)})" if needs else "") + " of the " + name + " recipe.",
                    "command": f"fieldkit emerge run {name}",
                    "check": f"fieldkit emerge status {name} shows {st} done or skipped (not failed)"})
    return out


def section(name, root=None, tools=None, ledger=None):
    """-> the project's section as a dict; raises KeyError for an unknown project."""
    cfg = load()[name]
    root = Path(root or HERE.parents[1])
    if tools is None:
        tools = [t["id"] for t in yaml.safe_load((root / "fieldkit" / "desk" / "tools.yaml").read_text(encoding="utf-8"))["tools"]]
    pre, fam = cfg["prefix"], set(cfg.get("families") or [])
    ids = sorted(t for t in tools if t.startswith(pre) or t in fam)
    adopted = []
    ad = root / "fieldkit" / "capture" / "adopted"
    if ad.is_dir():
        adopted = sorted(p.stem for p in ad.glob("*.sh") if p.stem.startswith(pre))
    if ledger is None:
        from . import store as st
        lp = st.capture_dir() / "agent-ledger.jsonl"
        ledger = [json.loads(x) for x in lp.read_text(encoding="utf-8").splitlines() if x.strip()] if lp.is_file() else []
    open_lines = [x for x in ledger if x.get("project") == name]
    return {"project": name, "title": cfg["title"], "entry": cfg["entry"],
            "workflows": [{"recipe": r, "steps": _recipe_steps(r, root)} for r in cfg["recipes"]],
            "tools": ids, "adopted": adopted, "open": open_lines}


def lines(sec):
    out = [f"{sec['title']} harness section ({sec['entry']}): start at workflow 1, do each step, run its check, then the next."]
    for i, w in enumerate(sec["workflows"], 1):
        out.append(f"WORKFLOW {i}: {w['recipe']}  (fieldkit emerge plan {w['recipe']} first)")
        for s in w["steps"]:
            out.append(f"  {s['n']}. {s['sentence']} | command: {s['command']} | check: {s['check']}")
    out.append(f"TOOLS ({len(sec['tools'])}): " + (", ".join(sec["tools"][:12]) + (" ..." if len(sec["tools"]) > 12 else "") if sec["tools"] else "none yet")
               + "   (fieldkit cards show ID)")
    out.append("ADOPTED BY CAPTURE: " + (", ".join(sec["adopted"]) or "none yet"))
    for x in sec["open"][-5:]:
        out.append(f"OPEN: {x.get('reason', '')[:160]}")
    return out


def cmd(a):
    try:
        sec = section(a.name)
    except KeyError:
        print(f"no project {a.name}; one of: {', '.join(load())}")
        return 2
    ls = lines(sec)
    if a.json:
        print(json.dumps({**sec, "next": "run workflow 1 step by step"}, ensure_ascii=False, indent=1))
    else:
        print("\n".join(ls + ["NEXT: do workflow 1 from its first step"]))
    return 0
