"""Energy, carbon and water in one signed `sci` block, in the GSF SCI format.

Software Carbon Intensity (Green Software Foundation, ISO/IEC 21031:2024):

    SCI = (E x I + M) per R

* R, the functional unit: one correct answer (an answer that passed the quality gate).
* E, energy per R in kWh: the receipt's own energy (NVML, so MEASURED on a GPU run) divided by
  the graded pass rate. The block carries it per request and per correct answer, per arm.
* I, grid carbon intensity in gCO2e/kWh: a cited, configurable figure for the region the GPU
  ran in. Default: India, CEA CO2 Baseline Database v20.0, FY 2023-24 weighted average,
  0.727 tCO2/MWh (CO2 only). Always ESTIMATE, with its source and year.
* M, embodied emissions: NOT_AVAILABLE unless a cited figure is given (then ESTIMATE).
* Water: the same Wh x WUE (L/kWh) rule as the receipt's `estimates`, with its source.

The block sits inside the hash and the signature. The verifier re-derives every number from the
receipt's own energy and pass rate plus the stored I and WUE, and fails the receipt on a
mismatch. Nothing here claims "neutral" or "offset": it reports an intensity, not a balance.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

SCI_SPEC = "GSF SCI 1.0 (ISO/IEC 21031:2024): SCI = (E x I + M) per R"
TOL = 1e-8   # relative

GRID = {
    "IN": {"gco2e_per_kwh": 727.0, "label": "ESTIMATE", "region": "IN (India national grid)",
           "year": "FY 2023-24",
           "source": "CEA CO2 Baseline Database for the Indian Power Sector v20.0 (Dec 2024), "
                     "weighted average emission factor 0.727 tCO2/MWh; CO2 only (CH4, N2O not "
                     "included)",
           "url": "https://cea.nic.in/cdm-co2-baseline-database/?lang=en"},
}
DEFAULT_REGION = "IN"   # the E2E Networks H100 VM is in Delhi

WUE = {"l_per_kwh": 0.36, "label": "ESTIMATE",
       "source": "0.36 L/kWh on-site cooling (LBNL 2024 US Data Center Energy Usage Report)"}
WUE_UPPER = {"l_per_kwh": 1.8, "label": "ESTIMATE",
             "source": "1.8 L/kWh including power-plant (electricity) water, upper bound"}

M_NOT_AVAILABLE = {"status": "NOT_AVAILABLE", "gco2e_per_correct": None,
                   "why": "no cited embodied-emissions figure for this GPU and server; SCI "
                          "shown is operational only (E x I)"}


def grid_from_env(env: Mapping[str, str] | None = None) -> dict:
    """I for this run. `WATTNOT_GRID_REGION` picks a built-in row; `WATTNOT_GRID_GCO2E_PER_KWH`
    with `WATTNOT_GRID_SOURCE` (and optional `WATTNOT_GRID_YEAR`) overrides it. A number
    without a source is refused: I must always be cited."""

    env = os.environ if env is None else env
    v, src = env.get("WATTNOT_GRID_GCO2E_PER_KWH"), env.get("WATTNOT_GRID_SOURCE")
    if v:
        if not src:
            raise ValueError("WATTNOT_GRID_GCO2E_PER_KWH needs WATTNOT_GRID_SOURCE (cite it)")
        return {"gco2e_per_kwh": float(v), "label": "ESTIMATE", "source": src,
                "year": env.get("WATTNOT_GRID_YEAR", "not given"),
                "region": env.get("WATTNOT_GRID_REGION", "custom")}
    region = env.get("WATTNOT_GRID_REGION", DEFAULT_REGION)
    if region not in GRID:
        raise ValueError(f"no built-in grid factor for {region!r}; set WATTNOT_GRID_GCO2E_PER_KWH "
                         "and WATTNOT_GRID_SOURCE")
    return dict(GRID[region])


def _r(x: float | None) -> float | None:
    return None if x is None else float(f"{x:.10g}")   # 10 significant figures


def arms_energy(receipt: Mapping) -> dict[str, dict]:
    """(Wh per request, pass rate 0-1, label) per arm, from the receipt's own numbers.
    Classic receipts: `baseline` / `candidate`. Proof receipts: stock vs the best passing cap
    (or the first step if none passed)."""

    if "baseline" in receipt and "candidate" in receipt:
        lab = (receipt.get("measurement") or {}).get("label", "ESTIMATE")
        out = {}
        for k in ("baseline", "candidate"):
            a = receipt[k]
            out[k] = {"wh_per_request": float(a["wh_per_req"]["mean"]),
                      "pass_rate": (a.get("quality") or {}).get("fixed_pass_rate"), "label": lab}
        return out
    steps = receipt.get("steps") or []
    if not steps:
        raise KeyError("no arms and no steps")
    best = receipt.get("best_cap_w")
    ref = next((x for x in steps if x.get("cap_w") == best), steps[0])
    q = ref.get("quality") or {}
    lab = ref.get("label") or receipt.get("label", "ESTIMATE")

    def mean(xs):
        xs = [float(x) for x in xs or []]
        if not xs:
            raise KeyError("empty energy samples")
        return sum(xs) / len(xs)

    def rate(pct):
        return None if pct is None else float(pct) / 100

    return {"baseline": {"wh_per_request": mean(ref["j_per_req_stock"]) / 3600,
                         "pass_rate": rate(q.get("stock_pct")), "label": lab},
            "candidate": {"wh_per_request": mean(ref["j_per_req_cap"]) / 3600,
                          "pass_rate": rate(q.get("cap_pct")), "label": lab,
                          "cap_w": ref.get("cap_w")}}


def _arm(e: Mapping, i: float, wue: float, wue_hi: float, m: float | None) -> dict:
    kwh = e["wh_per_request"] / 1000
    pr = e["pass_rate"]
    kwh_c = kwh / pr if pr else None
    g_req = kwh * i
    g_c = None if kwh_c is None else kwh_c * i
    out = {"E_kwh_per_request": _r(kwh), "E_kwh_per_correct": _r(kwh_c), "pass_rate": pr,
           "E_label": e["label"],
           "gco2e_per_request": _r(g_req), "gco2e_per_correct": _r(g_c),
           "sci_gco2e_per_R": _r(None if g_c is None else g_c + (m or 0.0)),
           "water_ml_per_request": _r(e["wh_per_request"] * wue),
           "water_ml_per_correct": _r(None if kwh_c is None else kwh_c * 1000 * wue),
           "water_ml_per_request_upper": _r(e["wh_per_request"] * wue_hi)}
    if "cap_w" in e:
        out["cap_w"] = e["cap_w"]
    return out


def sci_block(receipt: Mapping, *, grid: Mapping | None = None, embodied: Mapping | None = None,
              wue: Mapping = WUE, wue_upper: Mapping = WUE_UPPER) -> dict[str, Any]:
    I = dict(grid or grid_from_env())   # noqa: E741
    if embodied and embodied.get("gco2e_per_correct") is not None:
        if not embodied.get("source"):
            raise ValueError("an embodied (M) figure needs a cited source")
        M = {"gco2e_per_correct": float(embodied["gco2e_per_correct"]), "label": "ESTIMATE",
             "source": embodied["source"]}
    else:
        M = dict(M_NOT_AVAILABLE)
    return _assemble(receipt, I, M, dict(wue), dict(wue_upper))


def _assemble(receipt, I, M, W, WU) -> dict:  # noqa: E741
    i, m = float(I["gco2e_per_kwh"]), M.get("gco2e_per_correct")
    arms = {k: _arm(e, i, W["l_per_kwh"], WU["l_per_kwh"], m)
            for k, e in arms_energy(receipt).items()}
    b, c = arms["baseline"]["sci_gco2e_per_R"], arms["candidate"]["sci_gco2e_per_R"]
    return {"spec": SCI_SPEC, "R": "one correct answer (passed the quality gate)",
            "label": "ESTIMATE",
            "why_label": "E is the receipt's measured energy; I and water are cited factors, "
                         "so every carbon and water number is ESTIMATE",
            "scope": "operational (E x I)" + ("" if M.get("status") == "NOT_AVAILABLE"
                                               else " + embodied (M)"),
            "I": I, "M": M, "water": {"wue": W, "wue_upper": WU},
            "arms": arms,
            "change_pct_sci": None if not b or c is None else round(100 * (c - b) / b, 4)}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= TOL * abs(b) + 1e-15
    return a == b


def check_sci(receipt: Mapping) -> tuple[bool, dict]:
    """Re-derive E x I, M and water from the receipt and the stored I/WUE. Absent block = ok."""

    s = receipt.get("sci")
    if s is None:
        return True, {"present": False}
    try:
        derived = _assemble(receipt, dict(s["I"]), dict(s["M"]), dict(s["water"]["wue"]),
                            dict(s["water"]["wue_upper"]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
        return False, {"present": True, "error": f"cannot re-derive sci: {e}"[:200]}
    if not s.get("I", {}).get("source"):
        return False, {"present": True, "error": "grid intensity I has no source"}
    ok = _same(s, derived)
    d: dict = {"present": True, "derived": derived}
    if not ok:
        d["error"] = "sci block (E x I, water) does not match the receipt's own numbers"
    return ok, d


def summary_line(s: Mapping) -> str:
    c = s["arms"]["candidate"]
    g = c.get("gco2e_per_correct")
    w = c.get("water_ml_per_correct")
    return (f"[ESTIMATE] SCI {('%.4g' % g) if g is not None else '–'} gCO2e per correct answer "
            f"(E {('%.4g' % c['E_kwh_per_correct']) if c['E_kwh_per_correct'] is not None else '–'} kWh "
            f"[{c['E_label']}] x I {s['I']['gco2e_per_kwh']:g} g/kWh, {s['I'].get('region', '')} "
            f"{s['I'].get('year', '')}; M {s['M'].get('status', 'ESTIMATE')}); water "
            f"{('%.4g' % w) if w is not None else '–'} mL per correct at "
            f"{s['water']['wue']['l_per_kwh']:g} L/kWh")
