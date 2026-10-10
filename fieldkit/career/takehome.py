"""UK take-home pay from a gross annual salary (tax.yaml: the year and region are printed with every answer)."""
from pathlib import Path

import yaml

HERE = Path(__file__).parent


def take_home(gross, pension_pct=0.0, T=None):
    T = T or yaml.safe_load((HERE / "tax.yaml").read_text(encoding="utf-8"))
    gross = float(gross)
    pension = gross * float(pension_pct) / 100          # net pay arrangement: before income tax, not before NI
    taxable_pay = gross - pension
    pa = T["personal_allowance"]
    if taxable_pay > T["allowance_taper_from"]:
        pa = max(0.0, pa - (taxable_pay - T["allowance_taper_from"]) / 2)
    left, tax, lower = max(0.0, taxable_pay - pa), 0.0, 0.0
    for band in T["income_tax"]:
        upto = band["upto"] if band["upto"] is not None else float("inf")
        part = max(0.0, min(left, upto) - lower)
        tax += part * band["rate"]
        lower = upto
    N = T["national_insurance"]
    ni = max(0.0, min(gross, N["upper_earnings_limit"]) - N["primary_threshold"]) * N["main_rate"] + \
        max(0.0, gross - N["upper_earnings_limit"]) * N["upper_rate"]
    net = gross - pension - tax - ni
    return {"gross": round(gross, 2), "pension": round(pension, 2), "income_tax": round(tax, 2),
            "national_insurance": round(ni, 2), "take_home_year": round(net, 2),
            "take_home_month": round(net / 12, 2), "take_home_week": round(net / 52, 2),
            "tax_year": T["year"], "region": T["region"]}
