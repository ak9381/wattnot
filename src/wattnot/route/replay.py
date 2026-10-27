"""Replay a recorded cloud run through the router. Costs nothing: no API call is made.

Input: a cloud receipt (`wattnot.cloud-receipt/1`) from `wattnot cloud demo` or a recorded
live run. For every request class it compares:

  baseline     always `xhigh` (the API default) in the home region
  effort only  the router picks among the efforts that were tried, home region only
  effort+region  the same, plus the SIMULATED region what-if

Labels:
  USD       as in the source receipt (MEASURED only if that run was live)
  joules    ESTIMATE, a range (FLOP model, not a meter)
  grams CO2 ESTIMATE from an AVERAGE grid intensity, not a marginal one (SIMULATED when the
            intensity comes from the static fixture). Never a carbon claim.
  region    SIMULATED, because hosted backends do not let us choose one.
The report is not a signed receipt. It pins the source receipt by sha256.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wattnot.cloud import receipt as _cloud
from wattnot.route.router import CLOUD_SCHEMA, choose, class_candidates, expand_regions

HOME_REGION = "us-east"
KEYS = ("usd", "j_mid", "gco2_mid")
NOTES = [
    "Region is a what-if: Baseten and Meta's APIs do not let us choose one.",
    "Latency and data-residency differences between regions are not modelled.",
    "Carbon uses average grid intensity, not marginal, and is only a tie-breaker in the router.",
    "Energy is a FLOP-model range, not a measurement. None of this is a carbon claim.",
]


def _grams(wh: float, gco2_per_kwh: float | None) -> float | None:
    return None if gco2_per_kwh is None else wh / 1000 * gco2_per_kwh


def _totals(rows: list[dict]) -> dict:
    def s(k):
        return round(sum(r[k] for r in rows), 6)

    grams = [r["gco2_mid"] for r in rows]
    return {"usd": s("usd"), "wh_low": s("wh_low"), "wh_high": s("wh_high"),
            "j_mid": round(sum(r["j_mid"] for r in rows), 3),
            "gco2_mid": None if any(g is None for g in grams) else round(sum(grams), 4)}


def _row(cls: str, n: int, c, grid) -> dict:
    g = grid.reading(c.region)
    gco2 = None if g is None else g.gco2_per_kwh
    wh_mid = c.wh_mid
    grams = _grams(wh_mid * n, gco2)
    return {"class": cls, "requests": n, "candidate": c.id, "effort": c.effort,
            "region": c.region, "usd": round(c.usd_per_req * n, 8),
            "wh_low": round(c.wh_low * n, 6), "wh_high": round(c.wh_high * n, 6),
            "j_mid": round(wh_mid * 3600 * n, 3),
            "gco2_mid": None if grams is None else round(grams, 6), "gco2_per_kwh": gco2,
            "pass_rate": c.pass_rate}


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return round(100 * (b - a) / a, 2)


def replay(cloud: Mapping[str, Any], grid, *, home_region: str = HOME_REGION,
           regions: list[str] | None = None, tie_pct: float = 3.0) -> dict:
    if cloud.get("schema") != CLOUD_SCHEMA:
        raise ValueError(f"not a cloud receipt: schema is {cloud.get('schema')!r}")
    ok, want, got = _cloud.verify_hash(cloud)
    if not ok:
        raise ValueError("cloud receipt hash does not match its content; refusing to replay it")
    if grid.reading(home_region) is None:
        raise ValueError(f"home region {home_region!r} has no grid reading")
    regions = regions or grid.region_names()
    unknown = [r for r in regions if grid.reading(r) is None]
    if unknown:
        raise ValueError(f"no grid reading for region(s): {', '.join(unknown)}")
    base_rows, eff_rows, full_rows, classes = [], [], [], []
    for cls in cloud["classes"]:
        n = cls["items"] * cls["repeats"]
        cands = class_candidates(cloud, cls)
        ref = next(c for c in cands if c.is_reference)
        home = expand_regions(cands, [home_region])
        d_eff = choose(home, grid, tie_pct=tie_pct)
        d_full = choose(expand_regions(cands, regions), grid, tie_pct=tie_pct)
        if d_eff.chosen is None or d_full.chosen is None:
            raise ValueError(f"class {cls['class']}: nothing passed the gate")
        b = _row(cls["class"], n, expand_regions([ref], [home_region])[0], grid)
        e = _row(cls["class"], n, d_eff.chosen, grid)
        f = _row(cls["class"], n, d_full.chosen, grid)
        base_rows.append(b)
        eff_rows.append(e)
        full_rows.append(f)
        classes.append({"class": cls["class"], "requests": n, "baseline": b,
                        "effort_only": {**e, "reason": d_eff.reason},
                        "effort_and_region": {**f, "reason": d_full.reason,
                                              "tie_broken_by_grid": d_full.tie_broken_by_grid},
                        "excluded": [r for r in d_full.ranked if r["status"] == "excluded"
                                     and "@" + home_region in r["id"]]})
    tb, te, tf = _totals(base_rows), _totals(eff_rows), _totals(full_rows)
    gl = grid.reading(home_region)
    return {
        "format": "wattnot.route-replay/1",
        "label": "SIMULATED" if cloud["label"] == "SIMULATED" else "ESTIMATE",
        "labels": {"usd": cloud["labels"]["usd"], "joules": "ESTIMATE",
                   "gco2": gl.label if gl.label == "SIMULATED" else "ESTIMATE",
                   "region": "SIMULATED"},
        "source_receipt": {"schema": CLOUD_SCHEMA, "receipt_id": cloud["receipt_id"],
                           "sha256": cloud["integrity"]["sha256"], "mode": cloud["mode"],
                           "label": cloud["label"]},
        "backend": f"{cloud['provider']}/{cloud['model'].split('/')[-1]}",
        "home_region": home_region, "regions": regions,
        "grid": {"label": gl.label, "source": gl.source, "as_of": gl.as_of,
                 "kind": "average, not marginal"},
        "tie_pct": tie_pct,
        "requests": sum(c["requests"] for c in classes),
        "classes": classes,
        "totals": {"baseline": tb, "effort_only": te, "effort_and_region": tf,
                   "change_pct": {
                       "effort_only": {k: _delta(tb[k], te[k]) for k in KEYS},
                       "effort_and_region": {k: _delta(tb[k], tf[k]) for k in KEYS}}},
        "notes": NOTES,
    }


def format_report(rep: dict) -> str:
    lab = rep["labels"]
    t = rep["totals"]
    lines = [
        f"route replay [{rep['label']}] {rep['requests']} recorded requests, no API call made",
        f"source {rep['source_receipt']['receipt_id']} ({rep['source_receipt']['label']}), "
        f"home region {rep['home_region']}, grid {rep['grid']['label']} ({rep['grid']['as_of']})",
        "",
        f"{'class':<10}{'baseline':<16}{'effort only':<16}{'effort+region':<28}",
    ]
    for c in rep["classes"]:
        e, f = c["effort_only"], c["effort_and_region"]
        b = c["baseline"]
        base = f"{b['effort']} @{b['region'] or '-'}"
        both = f"{f['effort']} @{f['region'] or '-'}"
        lines.append(f"{c['class']:<10}{base:<16}{e['effort']:<16}{both:<28}")
    lines.append("")
    for key, name in (("baseline", "always xhigh"), ("effort_only", "router, effort only"),
                      ("effort_and_region", "router + region what-if")):
        r = t[key]
        g = "n/a" if r["gco2_mid"] is None else f"{r['gco2_mid']:.2f} g"
        lines.append(f"{name:<26} USD {r['usd']:.4f} [{lab['usd']}]   "
                     f"{r['j_mid']:.0f} J [ESTIMATE]   CO2 {g} [{lab['gco2']}]")
    ch = t["change_pct"]
    lines.append("")
    eo, er = ch["effort_only"], ch["effort_and_region"]
    lines.append(f"effort only:  USD {eo['usd']}%, joules {eo['j_mid']}%")
    lines.append(f"+ region:     USD {er['usd']}%, joules {er['j_mid']}%, CO2 {er['gco2_mid']}%")
    lines += ["", *[f"note: {n}" for n in rep["notes"]]]
    return "\n".join(lines)
