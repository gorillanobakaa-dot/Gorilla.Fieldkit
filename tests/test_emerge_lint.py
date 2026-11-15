"""RECIPES ARE GENERAL (P9, owner 2026-11-11): emerge lint, `latest:` and the automatic offload of one-system stages."""
from fieldkit.emerge import lint
from fieldkit.emerge.recipe import Recipe, auto_offload, resolve_latest

PY = "python3"


def test_every_shipped_recipe_is_general():
    """CI: a recipe with a pinned product version, a version name or a stage that can never be offloaded fails."""
    found = lint.lint_all()
    assert not found, "\n".join(f"{k} {w}: {m}" for k, w, m in found)


def test_a_pinned_product_version_is_a_finding():
    found = lint.lint_spec("x", {"name": "x", "vars": {"version": "158.0", "src": "${DOCUMENTS}/x"}})
    assert [k for k, _w, _m in found] == ["PIN"]
    assert lint.lint_spec("x", {"name": "x", "vars": {"tag": "v0.6.0"}})[0][0] == "PIN"


def test_a_pipeline_pin_passes_only_when_the_recipe_resolves_it():
    pipe = {"name": "p", "vars": {"version": "7.2.9"}}
    assert lint.lint_spec("k", {"name": "k"}, pipe)[0][0] == "PIN"
    assert lint.lint_spec("k", {"name": "k", "latest": {"version": {"local": "kernel.version"}}}, pipe) == []


def test_a_toolchain_floor_needs_its_reason():
    pipe = {"name": "p", "vars": {"mac_go": "1.24"}}
    assert lint.lint_spec("g", {"name": "g", "toolchain_pins": {"mac_go": ""}}, pipe)[0][0] == "PIN"
    assert lint.lint_spec("g", {"name": "g", "toolchain_pins": {"mac_go": "last Go for 10.13"}}, pipe) == []


def test_a_version_named_recipe_needs_archive():
    assert lint.lint_spec("kernel-7.0.9", {"name": "kernel-7.0.9"})[0][0] == "NAME"
    assert lint.lint_spec("kernel-7.0.9", {"name": "kernel-7.0.9", "archive": "checked, never built"}) == []


def test_a_one_os_stage_that_refuses_offload_needs_a_reason():
    st = {"name": "x", "stages": [{"id": "s", "platforms": ["windows"], "offload": False}]}
    assert lint.lint_spec("x", st)[0][0] == "ONE-OS"
    st["stages"][0]["platforms_why"] = "the registry of this PC"
    assert lint.lint_spec("x", st) == []


def test_a_machine_path_is_a_finding(tmp_path):
    f = tmp_path / "r.yaml"
    home = "/" + "home/someone/src"          # built, so the privacy scan does not read it as a real home
    f.write_text('name: r\nvars: {src: "%s"}\nstages: []\n' % home, encoding="utf-8")
    assert [k for k, _w, _m in lint.lint_file(f)] == ["PATH"]


# -- latest: the newest the harness knows --------------------------------------------------------------------------
def test_latest_takes_the_version_newest_folder(tmp_path):
    for n in ("v0.9.2", "v0.10.0", "v0.9.10"):
        (tmp_path / n).mkdir()
    val, how = resolve_latest({"newest": "{src}/v*"}, {"src": str(tmp_path)})
    assert val == "v0.10.0" and "newest of 3" in how


def test_latest_tries_each_resolver_in_order(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nversion = "0.12.1"\n', encoding="utf-8")
    spec = [{"local": "x.version"}, {"file": "{src}/Cargo.toml", "regex": '^version = "([^"]+)"'}]
    assert resolve_latest(spec, {"src": str(tmp_path)}, local={})[0] == "0.12.1"
    assert resolve_latest(spec, {"src": str(tmp_path)}, local={"x": {"version": "9"}})[0] == "9"


def test_latest_not_found_says_what_it_tried(tmp_path):
    val, why = resolve_latest({"newest": "{src}/nothing-*"}, {"src": str(tmp_path)})
    assert val is None and "nothing matches" in why


def test_a_recipe_takes_its_version_from_latest_and_var_wins(tmp_path):
    (tmp_path / "rel").mkdir()
    for n in ("1.2", "1.10"):
        (tmp_path / "rel" / n).mkdir()
    spec = {"name": "demo", "vars": {"src": str(tmp_path)}, "latest": {"version": {"newest": "{src}/rel/*"}},
            "stages": [{"id": "a", "run": {"cmd": [PY, "-c", "pass"]}, "uses": [], "needs": []}]}
    r = Recipe(spec, state_root=tmp_path / "st", strict=False)
    assert r.vars["version"] == "1.10"
    r2 = Recipe(spec, state_root=tmp_path / "st", strict=False, overrides={"version": "1.2"})
    assert r2.vars["version"] == "1.2" and r2.latest["version"] == "--var"


# -- a stage for the other system goes over the cable by itself --------------------------------------------------------
def test_a_one_system_stage_gets_an_offload():
    o = auto_offload({"id": "build", "platforms": ["windows"], "timeout": 600}, "demo", "demo", "/r", {"v": "1"})
    assert o["device"] == "windows" and o["auto"]
    assert o["argv"] == ["emerge", "run", "demo", "--only", "build", "--local", "--no-fix", "--var", "v=1"]
    assert auto_offload({"id": "d", "platforms": ["debian"]}, "k", "k", "/r")["device"] == "linux"


def test_no_offload_for_a_stage_that_runs_anywhere_or_refuses_it():
    assert auto_offload({"id": "a"}, "r", "p", "/t") is None
    assert auto_offload({"id": "a", "platforms": ["windows", "linux"]}, "r", "p", "/t") is None
    assert auto_offload({"id": "a", "platforms": ["windows"], "offload": False}, "r", "p", "/t") is None
    assert auto_offload({"id": "a", "platforms": ["nowhere"]}, "r", "p", "/t") is None


def test_a_recipe_plans_the_other_systems_stage_as_an_offload(tmp_path):
    other = "linux" if __import__("sys").platform == "win32" else "windows"
    spec = {"name": "demo", "stages": [{"id": "a", "platforms": [other], "run": {"cmd": [PY, "-c", "pass"]},
                                        "uses": [], "needs": []}]}
    r = Recipe(spec, state_root=tmp_path / "st", strict=False)
    assert r.by_id["a"]["offload"]["device"] == other
