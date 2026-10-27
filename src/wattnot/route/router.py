"""The router: pick the backend, effort and region for a request class.

One small pure function, `choose`. The order of the decision is fixed:

1. Gate first. A candidate that failed the quality gate or the p99 limit is out, however
   cheap it is. Carbon is never a reason to pass a failed gate.
2. Fewest joules per good answer: `Wh per request x 3600 / pass rate`. Energy for hosted
   models is an ESTIMATE range, so the middle of the range ranks them (the same method
   applies to every candidate, so the order does not depend on where in the range it falls).
3. Within `tie_pct` of the best on joules, the cleaner grid wins. Then the cheaper USD.
   Then the id, so the answer is the same every run.

Region is a what-if. Hosted APIs do not let us pick a region, so `expand_regions` copies each
candidate into several regions and the result is labelled SIMULATED. Joules do not change with
region (only carbon does). Latency and data-residency differences are not modelled.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from wattnot.cloud import energy as _energy

TIE_PCT = 3.0


@dataclass(frozen=True)
class Candidate:
    id: str
    backend: str  # for example "baseten/muse-spark-open"
    effort: str
    usd_per_req: float
    wh_low: float  # per request; low == high for a metered backend
    wh_high: float
    pass_rate: float  # 0..1 on the quality gate
    gate_passed: bool
    gate_note: str = ""
    region: str | None = None
    is_reference: bool = False
    usd_label: str = "SIMULATED"  # MEASURED when the source run was live
    energy_label: str = "ESTIMATE"  # MEASURED for a metered GPU box

    @property
    def wh_mid(self) -> float:
        return (self.wh_low + self.wh_high) / 2

    @property
    def j_per_good_answer(self) -> float | None:
        if self.pass_rate <= 0:
            return None
        return self.wh_mid * 3600 / self.pass_rate

    def to_dict(self) -> dict:
        d = asdict(self)
        j = self.j_per_good_answer
        d["j_per_good_answer"] = None if j is None else round(j, 4)
        d["usd_per_req"] = round(self.usd_per_req, 9)
        d["wh_low"], d["wh_high"] = round(self.wh_low, 9), round(self.wh_high, 9)
        return d


@dataclass(frozen=True)
class Decision:
    chosen: Candidate | None
    reason: str
    ranked: tuple[dict, ...]  # every candidate with what happened to it
    grid: dict | None  # the reading used to break the tie, if one was used
    tie_broken_by_grid: bool

    def to_dict(self) -> dict:
        return {"chosen": None if self.chosen is None else self.chosen.to_dict(),
                "reason": self.reason, "ranked": list(self.ranked), "grid": self.grid,
                "tie_broken_by_grid": self.tie_broken_by_grid}


def _gco2(grid, region: str | None) -> float | None:
    r = grid.reading(region) if grid is not None else None
    return None if r is None else r.gco2_per_kwh


def choose(cands: Iterable[Candidate], grid=None, *, tie_pct: float = TIE_PCT) -> Decision:
    cands = list(cands)
    if tie_pct < 0:
        raise ValueError("tie_pct must be >= 0")
    ranked: list[dict] = []
    eligible: list[Candidate] = []
    for c in cands:
        if not c.gate_passed:
            ranked.append({"id": c.id, "status": "excluded",
                           "why": c.gate_note or "failed the gate"})
        elif c.j_per_good_answer is None:
            ranked.append({"id": c.id, "status": "excluded",
                           "why": "no good answers (pass rate 0)"})
        else:
            eligible.append(c)
    if not eligible:
        return Decision(None, "no candidate passed the gate; nothing to route to",
                        tuple(ranked), None, False)
    best_j = min(c.j_per_good_answer for c in eligible)
    band = [c for c in eligible if c.j_per_good_answer <= best_j * (1 + tie_pct / 100)]

    def key(c: Candidate):
        g = _gco2(grid, c.region)
        return (float("inf") if g is None else g, c.usd_per_req, c.id)

    band.sort(key=key)
    chosen = band[0]
    # "grid broke a tie" means more than one candidate was in the band and carbon differed
    by_grid = len(band) > 1 and len({_gco2(grid, c.region) for c in band}) > 1
    for c in sorted(eligible, key=lambda x: (x.j_per_good_answer, x.id)):
        ranked.append({"id": c.id, "status": "chosen" if c is chosen else (
            "tied" if c in band else "passed"),
            "j_per_good_answer": round(c.j_per_good_answer, 4), "usd_per_req": c.usd_per_req,
            "region": c.region, "gco2_per_kwh": _gco2(grid, c.region)})
    reading = grid.reading(chosen.region).to_dict() if by_grid and grid is not None else None
    return Decision(chosen, _reason(chosen, band, eligible, best_j, by_grid, grid, tie_pct),
                    tuple(ranked), reading, by_grid)


def _reason(chosen, band, eligible, best_j, by_grid, grid, tie_pct) -> str:
    where = f" in {chosen.region}" if chosen.region else ""
    head = (f"{chosen.effort}{where}: passes the gate, {chosen.j_per_good_answer:.1f} J per good "
            f"answer")
    if len(eligible) == 1:
        return head + " (the only candidate that passed)"
    if not by_grid:
        nxt = sorted((c for c in eligible if c is not chosen),
                     key=lambda c: c.j_per_good_answer)[0]
        return head + (f", lowest of {len(eligible)} that passed; next is "
                       f"{nxt.j_per_good_answer:.1f} J")
    others = [c for c in band if c is not chosen]
    gs = [g for g in (_gco2(grid, c.region) for c in band) if g is not None]
    cmp = f" ({min(gs):g} gCO2/kWh vs up to {max(gs):g})" if gs else ""
    return head + (f"; {len(others)} other option(s) are within {tie_pct:g}% on joules, so the "
                   f"cleaner grid decided{cmp}")


def expand_regions(cands: Iterable[Candidate], regions: Iterable[str]) -> list[Candidate]:
    """Copy each candidate into every region. Used for the SIMULATED region what-if."""

    out = []
    for c in cands:
        for r in regions:
            out.append(replace(c, id=f"{c.id}@{r}", region=r))
    return out


# ---- candidates from a recorded cloud receipt ------------------------------------

CLOUD_SCHEMA = "wattnot.cloud-receipt/1"


def class_candidates(cloud: Mapping[str, Any], cls: Mapping[str, Any]) -> list[Candidate]:
    """Every effort the tuner actually tried for one class, plus the xhigh reference.

    Skipped efforts have no data, so they are not candidates. Prompt and cached tokens are
    taken from the class totals, because the prompts are the same at every effort.
    """

    n = cls["items"] * cls["repeats"]
    live = cloud.get("label") == "MEASURED" or cloud.get("labels", {}).get("usd") == "MEASURED"
    usd_label = "MEASURED" if live else str(cloud["labels"]["usd"])
    m, x = cls["measured"], cls["would_have_cost_xhigh"]
    prompt, cached = m["prompt_tokens"] / n, m["cached_tokens"] / n
    backend = f"{cloud['provider']}/{cloud['model'].split('/')[-1]}"
    out: list[Candidate] = []

    def energy(completion_per_req: float) -> tuple[float, float]:
        return _energy.wh_range(prompt, cached, completion_per_req)

    for t in cls["tried"]:
        if t.get("skipped"):
            continue
        lo, hi = energy(t["completion_tokens"] / n)
        out.append(Candidate(
            id=f"{cls['class']}:{t['effort']}", backend=backend, effort=t["effort"],
            usd_per_req=t["usd"] / n, wh_low=lo, wh_high=hi,
            pass_rate=t.get("quality_pct", 0.0) / 100, gate_passed=bool(t["passed"]),
            gate_note="" if t["passed"] else "failed: " + ", ".join(
                t.get("failed_gates") or ["gate"]),
            usd_label=usd_label))
    lo, hi = _energy.wh_range(x["prompt_tokens"] / n, x["cached_tokens"] / n,
                              x["completion_tokens"] / n)
    ref_pass = 1.0
    for t in cls["tried"]:
        if not t.get("skipped") and "reference_pct" in t:
            ref_pass = t["reference_pct"] / 100
            break
    out.append(Candidate(
        id=f"{cls['class']}:xhigh", backend=backend, effort=cloud.get("reference_effort", "xhigh"),
        usd_per_req=x["usd"] / n, wh_low=lo, wh_high=hi, pass_rate=ref_pass, gate_passed=True,
        gate_note="", is_reference=True, usd_label=usd_label))
    return out
