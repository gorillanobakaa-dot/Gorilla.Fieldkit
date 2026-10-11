"""Who made this computer, and are the maker's hardware-button services here? (read-only)

On Lenovo business laptops the hardware buttons (microphone mute, airplane mode, brightness, phone keys) run
through Lenovo's own background services. The framework learned this by breaking them: its profiles no longer
disable them, and 06-services/Disable-LenovoServices.ps1 is the only, opt-in, way to. This check reads the maker and
lists those services, and fails if any profile still names one. For other makers there is no verified list yet:
it says so instead of guessing which of their services carry the buttons.
"""
import json
import re
from pathlib import Path

from . import fit as fitmod


def lenovo_services(repo_modules):
    """The OEM services the framework itself protects, read from its opt-in script (one source of truth)."""
    ps1 = Path(repo_modules) / "06-services" / "Disable-LenovoServices.ps1"
    if not ps1.is_file():
        return []
    return re.findall(r"Service\s*=\s*'([^']+)'", ps1.read_text(encoding="utf-8", errors="replace"))


def check(repo_modules, maker=None, present=None):
    """maker and present(service) -> bool|None are probes; None means ask Windows."""
    if maker is None:
        maker = (fitmod._ps("(Get-CimInstance Win32_ComputerSystem).Manufacturer") or "").strip()
    protected = lenovo_services(repo_modules)
    data = json.loads((Path(repo_modules) / "06-services" / "profiles.json").read_text(encoding="utf-8-sig"))
    in_profiles = sorted({s["service"] for p in data["profiles"].values() for s in p["services"]} & set(protected))
    if present is None:
        def present(name):
            out = fitmod._ps(f"[bool](Get-Service -Name '{name}' -ErrorAction SilentlyContinue)")
            return None if out is None else out.strip().lower() == "true"
    here = [s for s in protected if present(s)]
    lenovo = "lenovo" in maker.lower()
    rep = {"maker": maker or "unknown", "protected": protected, "present": here, "in_profiles": in_profiles,
           "verified_list": lenovo or not maker}
    if in_profiles:
        rep["finding"] = (f"profiles.json disables {', '.join(in_profiles)}: the hardware buttons of a Lenovo "
                          "laptop depend on them")
    elif not lenovo and maker:
        rep["note"] = (f"no verified list of {maker}'s hardware-button services: before applying a services profile "
                       "here, check every 'OEM' entry it names against what that maker's hotkeys need")
    return rep
