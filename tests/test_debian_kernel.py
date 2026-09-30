"""debian-kernel project tools: the 64-char name guard, the audited config injector, and the README claim audit."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.core import settings

REPO = Path(settings.expand("${FIELDKIT}")) / "toolbox" / "debian-kernel"
pytestmark = pytest.mark.skipif(not (REPO / "kernel_config_injector.py").is_file(), reason="debian-kernel not gathered")


def run(*args, cwd=None):
    r = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120, cwd=cwd)
    return r.returncode, r.stdout + r.stderr


# -- kernel_build_namer -----------------------------------------------------------

def test_localversion_fits_debian_limit_and_is_clean():
    code, out = run(REPO / "kernel_build_namer.py", "localversion", "--base", "7.1.2",
                    "--tags", "Unleashed", "gorilla", "7.1.2", "EAPD_fix", "bbr")
    lv = [l for l in out.splitlines() if l.startswith("-unleashed")][-1]      # stderr carries a note
    assert code == 0 and lv.startswith("-unleashed.gorilla-")
    assert lv == lv.lower() and "7.1.2" not in lv and "_" not in lv
    assert len("7.1.2") + len(lv) <= 64
    assert re.search(r"-\d{2}\.\d{2}\.\d{2}--\d{2}\.\d{2}$", lv)


def test_localversion_drops_feature_tags_instead_of_overflowing():
    tags = ["unleashed", "gorilla"] + [f"feature{i}" for i in range(12)]
    code, out = run(REPO / "kernel_build_namer.py", "localversion", "--base", "7.1.2", "--tags", *tags)
    lv = [l for l in out.splitlines() if l.startswith("-unleashed")][-1]
    assert len("7.1.2") + len(lv) <= 64 and "feature0" in lv and "feature11" not in lv


# -- kernel_config_injector -------------------------------------------------------

def test_injector_applies_every_flag_once_and_is_idempotent(tmp_path):
    cfg = tmp_path / ".config"
    cfg.write_text("CONFIG_LOCALVERSION=\"\"\nCONFIG_NO_HZ_FULL=y\nCONFIG_SOMETHING_ELSE=m\n")
    code, out = run(REPO / "kernel_config_injector.py", tmp_path)
    assert code == 0 and "phantom MIVYBRIDGE present : no (clean)" in out, out
    text = cfg.read_text()
    assert "CONFIG_SOMETHING_ELSE=m" in text                       # unrelated lines kept
    assert "CONFIG_NO_HZ_FULL=y" not in text                       # the audited contamination is gone
    flags = [l.split("=")[0] for l in text.splitlines() if l.startswith("CONFIG_")]
    assert len(flags) == len(set(flags))                           # nothing duplicated
    code, out = run(REPO / "kernel_config_injector.py", tmp_path)
    assert "lines modified            : 0" in out and "flags appended (new)      : 0" in out
    assert cfg.read_text() == text


def test_injector_refuses_an_unconfigured_tree(tmp_path):
    code, out = run(REPO / "kernel_config_injector.py", tmp_path)
    assert code != 0 and "no .config" in out


# -- audit_readme_claims ----------------------------------------------------------

def test_every_readme_claim_holds_in_the_shipped_source(tmp_path):
    text = (REPO / "audit_readme_claims.py").read_text(encoding="utf-8")
    text = re.sub(r"^P = pathlib\.Path\(.*\)$", lambda m: "P = pathlib.Path(%r)" % str(REPO), text, count=1, flags=re.M)
    (tmp_path / "audit.py").write_text(text, encoding="utf-8")
    code, out = run(tmp_path / "audit.py")
    m = re.search(r"(\d+)/(\d+) claims verified", out)
    assert m and m.group(1) == m.group(2), out[-1500:]
