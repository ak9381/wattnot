"""Who is behind a WattNot run, and what each one cost.

Pure functions over saved logs: the Meta usage table, the Baseten usage table
and a small JSON file with the rented-GPU runs. Nothing here calls a network,
invents a number or reads a secret. `build` returns the `wattnot.partners/1`
snapshot the dashboard serves at `/api/dash/partners`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

FORMAT = "wattnot.partners/1"
IDS = ("meta", "baseten", "e2e", "nvidia", "huggingface", "sigstore",
       "fireworks", "modal")

_COST_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)")
_SEP_RE = re.compile(r"^:?-+:?$")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_sep(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_RE.match(c) for c in cells)


def _num(cell: str):
    """A table number with `~`, commas or `n/a`. None when there is no number."""
    t = cell.replace(",", "").lstrip("~").strip()
    if t in ("", "n/a", "na", "-", "n\\a"):
        return None
    try:
        return int(t)
    except ValueError:
        try:
            return float(t)
        except ValueError:
            return None


def _cost(cell: str) -> tuple[float | None, bool]:
    """(USD, is_estimate). None when the cell names no price."""
    m = _COST_RE.search(cell.replace(",", ""))
    if not m:
        return None, False
    return float(m.group(1)), "estimate" in cell.lower()


def _parse(text: str) -> dict:
    """One usage table -> {"calls", "usd", "rows", "label"}.

    The header row is the `|` line naming `Calls` and `Cost`; separator,
    blank and prose rows (no calls and no cost) are skipped. Any row priced
    as an estimate makes the totals an estimate.
    """
    text = text or ""
    header: list[str] | None = None
    calls_i = cost_i = -1
    calls = 0
    usd = 0.0
    rows = 0
    estimate = False
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = _cells(line)
        if _is_sep(cells):
            continue
        low = [c.lower() for c in cells]
        if header is None and "calls" in low and "cost" in low:
            header = low
            calls_i, cost_i = low.index("calls"), low.index("cost")
            continue
        if header is None:
            continue
        padded = cells + [""] * max(0, max(calls_i, cost_i) + 1 - len(cells))
        n, (price, est) = _num(padded[calls_i]), _cost(padded[cost_i])
        if n is None and price is None:
            continue  # a prose row or a blank row, not a usage row
        rows += 1
        if isinstance(n, (int, float)):
            calls += n
        if price is not None:
            usd += price
        estimate = estimate or est
    return {"calls": int(calls), "usd": round(usd, 4), "rows": rows,
            "label": "ESTIMATE" if estimate else "MEASURED"}


def parse_meta_log(text: str) -> dict:
    """The Meta usage table (Time, Via, Task, Calls, ..., Cost, Ledger total)."""
    return _parse(text)


def parse_baseten_log(text: str) -> dict:
    """The Baseten usage table (Time, Task, Model, Calls, ..., Cost)."""
    return _parse(text)


def e2e_cost(runs: list[dict] | None) -> dict:
    """Rented-GPU runs -> {"total_inr", "label"}.

    A run without `cost_inr` is priced from its own hours, hourly rate and
    GST; that is arithmetic on given numbers, not a new number. ESTIMATE
    unless every run says MEASURED.
    """
    total = 0.0
    for r in runs or []:
        c = r.get("cost_inr")
        if c is None:
            c = (float(r.get("hours") or 0) * float(r.get("rate_inr_per_h") or 0)
                 * (1 + float(r.get("gst_pct") or 0) / 100))
        total += float(c)
    label = "MEASURED" if runs and all(r.get("label") == "MEASURED" for r in runs) \
        else "ESTIMATE"
    return {"total_inr": round(total, 2), "label": label}


def _cost_block(value, unit, label) -> dict:
    return {"value": value, "unit": unit, "label": label}


def _used(name: str, what: str, source: str, calls, cost: dict,
          note: str | None = None) -> dict:
    entry = {"name": name, "what": what, "status": "used", "calls": calls,
             "cost": cost, "source": source}
    if note is not None:
        entry["note"] = note
    return entry


def build(meta_text: str | None = None, baseten_text: str | None = None,
          e2e_runs: list[dict] | None = None, now=None) -> dict:
    """The partners snapshot. A partner with no input keeps status "used"
    with null numbers and the note "ledger not provided": nothing is invented.
    """
    if meta_text:
        parsed = parse_meta_log(meta_text)
        meta = _used("Meta", "Muse Spark API (planner), Muse Code, "
                     "SAM 3.1 room scan, open-weight models",
                     "meta-usage/LOG.md", parsed["calls"],
                     _cost_block(parsed["usd"], "USD", parsed["label"]))
    else:
        meta = _used("Meta", "Muse Spark API (planner), Muse Code, "
                     "SAM 3.1 room scan, open-weight models",
                     "meta-usage/LOG.md", None, _cost_block(None, None, None),
                     note="ledger not provided")
    if baseten_text:
        parsed = parse_baseten_log(baseten_text)
        baseten = _used("Baseten", "Muse Spark Open planner and hosted-model runs",
                        "meta-usage/BASETEN-LOG.md", parsed["calls"],
                        _cost_block(parsed["usd"], "USD", parsed["label"]))
    else:
        baseten = _used("Baseten", "Muse Spark Open planner and hosted-model runs",
                        "meta-usage/BASETEN-LOG.md", None,
                        _cost_block(None, None, None), note="ledger not provided")
    if e2e_runs:
        priced = e2e_cost(e2e_runs)
        e2e = _used("E2E Networks", "the rented H100", "e2e-runs.json", None,
                    _cost_block(priced["total_inr"], "INR", priced["label"]))
    else:
        e2e = _used("E2E Networks", "the rented H100", "e2e-runs.json", None,
                    _cost_block(None, None, None), note="ledger not provided")
    free = lambda name, what, source: {  # noqa: E731
        "name": name, "what": what, "status": "free", "calls": None,
        "cost": _cost_block(None, None, None), "source": source}
    idle = lambda name: {  # noqa: E731
        "name": name, "what": "not used yet", "status": "not_used", "calls": None,
        "cost": _cost_block(None, None, None), "source": None}
    partners = [
        {"id": "meta", **meta},
        {"id": "baseten", **baseten},
        {"id": "e2e", **e2e},
        {"id": "nvidia", **free("NVIDIA", "GPU power counter (NVML)",
                                "read from the card, not billed")},
        {"id": "huggingface", **free("Hugging Face", "model weights",
                                     "weights carry no per-call bill")},
        {"id": "sigstore", **free("Sigstore", "receipt signing, offline key, "
                                         "no public log yet",
                                  "offline signature bundle")},
        {"id": "fireworks", **idle("Fireworks")},
        {"id": "modal", **idle("Modal")},
    ]
    ts = now if isinstance(now, str) else (now or datetime.now(UTC)).isoformat(
        timespec="seconds")
    return {"format": FORMAT, "generated_at": ts, "partners": partners}


__all__ = ["build", "e2e_cost", "parse_baseten_log", "parse_meta_log"]
