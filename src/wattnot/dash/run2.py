"""The headline RUN2 numbers for the v2 dashboard's before/after block.

Source file (read first when present):
    data/h100-run2/vm/steps/cap350.json  (Llama 3.1 8B, 350 W cap vs 700 W stock)

That file holds per-pair repeats. The loader below averages the repeats the
same way the file's own ``mean`` fields do. When the file is missing (for
example in a packaged copy without the raw data), the ``FALLBACK`` constant
below carries the same means, rounded for display:

* energy per request: -27.1% (MEASURED)
* p99 latency: +7.7% (MEASURED)
* answer quality: 93.7% stock vs 93.7% capped, unchanged (MEASURED)

Cost and water are NOT in the file. They are ESTIMATEs the dashboard derives
from measured joules with the bundle's price and water factors.
"""

from __future__ import annotations

import json
from pathlib import Path

SOURCE = "data/h100-run2/vm/steps/cap350.json"

FALLBACK = {
    "model": "Llama 3.1 8B Instruct",
    "cap_w": 350,
    "stock_w": 700,
    "source": SOURCE,
    "from_file": False,
    "j_per_req_stock": 15.959,
    "j_per_req_cap": 11.635,
    "saved_pct": -27.1,
    "p99_s_stock": 3.605,
    "p99_s_cap": 3.882,
    "p99_change_pct": 7.7,
    "quality_stock_pct": 93.7,
    "quality_cap_pct": 93.7,
    "label": "MEASURED",
}


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def load(root: Path | str | None = None) -> dict:
    """Headline RUN2 numbers. Reads SOURCE under ``root`` when it exists."""
    if root is not None:
        p = Path(root) / SOURCE
        if p.is_file():
            try:
                d = json.loads(p.read_text())
            except ValueError:
                return dict(FALLBACK)
            stock = _mean(d.get("j_per_req_stock") or [])
            cap = _mean(d.get("j_per_req_cap") or [])
            p99 = d.get("p99_s") or {}
            q = d.get("quality") or {}
            if stock and cap:
                return {
                    "model": "Llama 3.1 8B Instruct",
                    "cap_w": d.get("cap_w", 350),
                    "stock_w": 700,
                    "source": SOURCE,
                    "from_file": True,
                    "j_per_req_stock": round(stock, 3),
                    "j_per_req_cap": round(cap, 3),
                    "saved_pct": round((cap / stock - 1) * 100, 1),
                    "p99_s_stock": round(float(p99["stock"]), 3) if "stock" in p99 else None,
                    "p99_s_cap": round(float(p99["cap"]), 3) if "cap" in p99 else None,
                    "p99_change_pct": round(d["p99_change_pct"]["mean"], 1),
                    "quality_stock_pct": round(float(q["stock_pct"]), 1) if "stock_pct" in q else None,
                    "quality_cap_pct": round(float(q["cap_pct"]), 1) if "cap_pct" in q else None,
                    "label": "MEASURED",
                }
    return dict(FALLBACK)
