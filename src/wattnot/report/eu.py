"""EU data-centre reporting export (fields from Delegated Regulation (EU) 2024/1364).

Only what software can fill from receipts. Every field carries value, unit,
label (MEASURED / ESTIMATE / SIMULATED / NOT_AVAILABLE) and source. Fields that need
facility data (PUE, renewable share, waste heat reuse, ...) are NOT_AVAILABLE
with the reason. No offset or "neutral" wording anywhere.
"""

from __future__ import annotations

import csv
import html
import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

LABELS = ("MEASURED", "ESTIMATE", "SIMULATED", "NOT_AVAILABLE")
BANNED = ("neutral", "offset", "carbon-free", "carbon free", "net zero", "net-zero")
TEMPLATE = Path(__file__).parent / "templates" / "eu.html"
FACILITY = "needs facility data (operator meters); a GPU-level tool cannot see it"
COLUMNS = ("receipt_id", "field", "value", "unit", "label", "source")


def _f(name, value, unit, label, source) -> dict:
    assert label in LABELS
    return {"field": name, "value": value, "unit": unit, "label": label, "source": source}


def _na(name, unit, why=FACILITY) -> dict:
    return _f(name, None, unit, "NOT_AVAILABLE", why)


def fields_for(receipt: Mapping, *, grid_gco2_per_kwh: float | None = None,
               grid_source: str | None = None) -> list[dict]:
    m = receipt["measurement"]
    lab = m["label"]
    rid = receipt["receipt_id"]
    n = receipt["subject"]["workload"]["n_requests"]
    cand, base = receipt["candidate"], receipt["baseline"]
    wh = cand["wh_per_req"]["mean"]
    window_kwh = sum(cand["wh_per_req"]["samples"]) * n / 1000
    res = receipt["result"]
    src = f"receipt {rid}, meter {m['meter']}"
    water = receipt["estimates"]["water_ml_per_req"]
    out = [
        _f("it_energy_measured_window", round(window_kwh, 6), "kWh", lab,
           f"{src}: sum over {cand['reps']} reps x {n} requests"),
        _f("energy_per_request", wh, "Wh/request", lab, src),
        _f("energy_per_request_baseline", base["wh_per_req"]["mean"], "Wh/request", lab, src),
        _f("energy_saving_billed", res["billed_saving_pct"], "%",
           lab if res["billable"] or lab == "SIMULATED"
           else "ESTIMATE", f"{src}: lower bound of 95% bootstrap CI, Welch p="
           f"{res['welch']['p_value']:.3g}" + ("" if res["billable"] else " (not significant)")),
        (_f("idle_power", m["idle_w"], "W", lab, src) if m.get("idle_w") is not None
         else _na("idle_power", "W", "idle not measured in this run")),
        _f("water_per_request", water["value"], "mL/request", "ESTIMATE",
           f"{water['method']}; {water['source']}"),
    ]
    if grid_gco2_per_kwh is not None and grid_source:
        out.append(_f("sci_per_request", round(wh / 1000 * grid_gco2_per_kwh, 6),
                      "gCO2e/request", "ESTIMATE",
                      f"SCI (Green Software Foundation) operational part only, E x I; "
                      f"I={grid_gco2_per_kwh} g/kWh from {grid_source}; embodied M not included"))
    else:
        out.append(_na("sci_per_request", "gCO2e/request",
                       "needs a grid carbon-intensity figure with a source (--grid-gco2)"))
    out += [_na("pue", "ratio"), _na("renewable_energy_share", "%"),
            _na("waste_heat_reused", "kWh"), _na("total_water_input", "m3"),
            _na("rated_it_power", "kW")]
    filled = sum(f["label"] != "NOT_AVAILABLE" for f in out)
    out.append(_f("heat_water_readiness", round(filled / (len(out) + 1), 3), "share of fields",
                  "ESTIMATE", "share of this export's fields that are filled (MEASURED or "
                  "ESTIMATE); a readiness indicator, not a regulatory metric"))
    for f in out:
        f["receipt_id"] = rid
    return out


def to_csv(rows: Sequence[Mapping]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r[k] is None else r[k]) for k in COLUMNS})
    return buf.getvalue()


def to_html(rows: Sequence[Mapping], *, title: str = "EU 2024/1364 field export") -> str:
    body = "\n".join(
        "<tr class=\"{cls}\">{cells}</tr>".format(
            cls=r["label"].lower(),
            cells="".join(f"<td>{html.escape('' if r[k] is None else str(r[k]))}</td>"
                          for k in COLUMNS)) for r in rows)
    head = "".join(f"<th>{c}</th>" for c in COLUMNS)
    return TEMPLATE.read_text().replace("{{title}}", html.escape(title)) \
        .replace("{{head}}", head).replace("{{rows}}", body)


def check_wording(text: str) -> list[str]:
    low = text.lower()
    return [w for w in BANNED if w in low]


def export(receipt_paths: Sequence[str | Path], out: str | Path, **kw) -> tuple[Path, Path]:
    rows: list[dict] = []
    for p in receipt_paths:
        rows += fields_for(json.loads(Path(p).read_text()), **kw)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    csv_text, html_text = to_csv(rows), to_html(rows)
    bad = check_wording(csv_text + html_text)
    if bad:
        raise ValueError(f"banned wording in report: {bad}")
    c, h = out.with_suffix(".csv"), out.with_suffix(".html")
    c.write_text(csv_text, encoding="utf-8")
    h.write_text(html_text, encoding="utf-8")
    return c, h
