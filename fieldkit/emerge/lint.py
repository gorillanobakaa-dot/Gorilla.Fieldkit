# Version: 1.0.0 · updated 26-11-11-18-10
"""fieldkit emerge lint: a recipe is general - any version, any supported system; never pinned to one (P9).

Owner, 2026-11-11: "când se face o rețetă de build și de compilare, nu numai pentru Firefox, dar pentru orice proiect,
să fie generală și să nu fie dependentă de versiune ... Nu vreau să fie dependentă de o singură versiune de sistem
operativ, nu ... de o singură versiune de program."

Each finding names the recipe, the place and the fix:
  PIN       a recipe or pipeline var holds a product version ("158.0", "v0.6.0", "7.2.9"): it comes from
            `latest:` (recipe.resolve_latest: fieldkit.local.json, the newest release folder, a version file) or
            `--var`, never from the recipe text. A pipeline's pin passes only when every recipe on that pipeline
            resolves the var with `latest:`
  NAME      a recipe named after one version ("kernel-7.0.9-unleashed") that is not marked `archive: WHY` (an
            archived release folder that is checked, never built)
            A toolchain floor (go 1.24 for macOS 10.13) is not a product version: `toolchain_pins: {VAR: WHY}`
  ONE-OS    a stage that runs on one system and says `offload: false` without `platforms_why`: the other system
            can never build it (a stage with no offload gets one by itself: recipe.auto_offload)
  PATH      a machine path written in a recipe (/home/NAME, C:\\Users\\NAME): ${DOCUMENTS}, ${HOME}, ${LOCAL:key}
Exit 0 clean, 3 findings. CI runs it on every recipe (tests/test_emerge_lint.py).
"""
import re
from pathlib import Path

from ..core import settings
from .recipe import HERE, PIPES, VERSION_RE, available

RECIPES = HERE / "recipes"
NAME_VERSION = re.compile(r"\d+\.\d+")
MACHINE_PATH = re.compile(r"(/home/[a-z_][\w-]*|/Users/[\w-]+|[A-Za-z]:\\\\?Users\\\\?[\w-]+)")


def _stages(spec):
    st = spec.get("stages") or []
    return [(sid, dict(x or {})) for sid, x in st.items()] if isinstance(st, dict) else \
        [(x.get("id"), dict(x)) for x in st]


def lint_spec(name, spec, pipeline_spec=None, pipeline_resolved=()):
    """-> [(kind, where, message)] for one recipe (its spec and, when it has one, its pipeline's)."""
    out = []
    latest = set((spec.get("latest") or {}).keys())
    tpins = {k for k, why in (spec.get("toolchain_pins") or {}).items() if str(why or "").strip()}
    for k, v in (spec.get("vars") or {}).items():
        if isinstance(v, str) and VERSION_RE.match(v.strip()) and k not in tpins:
            out.append(("PIN", f"{name} vars.{k}", f"{v!r} is one version: move it to `latest:` (newest the harness "
                        "knows) or pass --var"))
    if pipeline_spec:
        for k, v in (pipeline_spec.get("vars") or {}).items():
            if isinstance(v, str) and VERSION_RE.match(v.strip()) and k not in latest | tpins \
                    and k not in pipeline_resolved:
                out.append(("PIN", f"pipeline {pipeline_spec.get('name')} vars.{k}",
                            f"{v!r} is one version and recipe {name} does not resolve {k} with `latest:`"))
    if NAME_VERSION.search(str(spec.get("name", name))) and not spec.get("archive"):
        out.append(("NAME", name, "named after one version: make it general (version from `latest:`) or mark "
                    "`archive: WHY` if it only checks an archived release"))
    for sid, s in _stages(spec) + (_stages(pipeline_spec) if pipeline_spec else []):
        plats = set(s.get("platforms") or [])
        single = plats and (plats <= {"windows"} or plats <= {"linux", "debian"})
        if single and s.get("offload") is False and not s.get("platforms_why"):
            out.append(("ONE-OS", f"{name} stage {sid}", f"runs on {sorted(plats)} only with `offload: false` and "
                        "no platforms_why: the other system can never build it"))
    return out


def lint_file(path):
    import yaml
    text = Path(path).read_text(encoding="utf-8")
    spec = yaml.safe_load(text) or {}
    name = spec.get("name", Path(path).stem)
    out = []
    for m in MACHINE_PATH.finditer(text):
        out.append(("PATH", f"{name}", f"machine path {m.group(0)!r}: use ${{DOCUMENTS}}, ${{HOME}} or ${{LOCAL:key}}"))
    pspec = None
    if spec.get("pipeline"):
        pf = PIPES / f"{spec['pipeline']}.yaml"
        if pf.is_file():
            pspec = settings.read_file(pf)
    return out + lint_spec(name, spec, pspec)


def lint_all(names=None):
    names = names or available()
    found = []
    for n in names:
        found += lint_file(RECIPES / f"{n}.yaml")
    return found


def main(names=None, as_json=False):
    import json
    found = lint_all(names)
    if as_json:
        print(json.dumps({"ok": not found, "findings": [{"kind": k, "where": w, "why": m} for k, w, m in found]}))
    else:
        for k, w, m in found:
            print(f"{k:<7} {w}: {m}")
        n = len(names or available())
        print(f"LINT {'CLEAN' if not found else 'FINDINGS'}: {n} recipe(s), {len(found)} finding(s)")
        print("NEXT: " + ("fieldkit emerge plan RECIPE" if not found else
                          "fix each line (a version goes to `latest:`), then fieldkit emerge lint"))
    return 0 if not found else 3
