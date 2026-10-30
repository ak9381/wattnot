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
FRAMING = "supports EU Energy Efficiency Directive data-center reporting"
CSRD = "CSRD roadmap"
PROOF_SPEC = "wattnot.proof-receipt/1"
DISCLAIMER = "Not a compliance claim; facility-level metrics require operator metering."


def _f(name, value, unit, label, source) -> dict:
    assert label in LABELS
    return {"field": name, "value": value, "unit": unit, "label": label, "source": source}


def _na(name, unit, why=FACILITY) -> dict:
    return _f(name, None, unit, "NOT_AVAILABLE", why)


def fields_for(receipt: Mapping, *, grid_gco2_per_kwh: float | None = None,
               grid_source: str | None = None) -> list[dict]:
    if receipt.get("spec") == PROOF_SPEC:
        return proof_fields(receipt, grid_gco2_per_kwh=grid_gco2_per_kwh, grid_source=grid_source)
    m = receipt["measurement"]
    lab = m["label"]
    rid = receipt["receipt_id"]
    n = receipt["subject"]["workload"]["n_requests"]
    cand, base = receipt["candidate"], receipt["baseline"]
    wh = cand["wh_per_req"]["mean"]
    # Cloud-adapted receipts carry an ESTIMATE range rather than per-rep samples; fall back to
    # the mean x reps so the EU export works for both measured (GPU) and cloud receipts.
    samples = cand["wh_per_req"].get("samples")
    window_kwh = (sum(samples) if samples else wh * cand["reps"]) * n / 1000
    res = receipt["result"]
    src = f"receipt {rid}, meter {m['meter']}"
    out = [
        (_f("it_energy_measured_window", round(window_kwh, 6), "kWh", lab,
            f"{src}: sum over {cand['reps']} reps x {n} requests") if samples else
         _f("it_energy_measured_window", round(window_kwh, 6), "kWh", "ESTIMATE",
            f"{src}: mean x {cand['reps']} reps x {n} requests (no per-rep samples)")),
        _f("energy_per_request", wh, "Wh/request", lab, src),
        _f("energy_per_request_baseline", base["wh_per_req"]["mean"], "Wh/request", lab, src),
        _f("energy_saving_billed", res["billed_saving_pct"], "%",
           lab if res["billable"] or lab == "SIMULATED"
           else "ESTIMATE", f"{src}: lower bound of 95% bootstrap CI, Welch p="
           f"{res['welch']['p_value']:.3g}" + ("" if res["billable"] else " (not significant)")),
        (_f("idle_power", m["idle_w"], "W", lab, src) if m.get("idle_w") is not None
         else _na("idle_power", "W", "idle not measured in this run")),
    ]
    joules_cand = (receipt.get("joules") or {}).get("candidate") or {}
    j_per_good = joules_cand.get("j_per_good_answer")
    j_label = lab
    j_src = f"{src}: candidate Wh/req x 3600 / pass_rate"
    if j_per_good is None:
        quality = cand.get("quality") or {}
        pass_rate = quality.get("fixed_pass_rate")
        judge_score = quality.get("judge_score")
        if pass_rate and pass_rate > 0:
            j_per_good = round((wh * 3600) / pass_rate, 6)
            j_src = f"{src}: candidate Wh/req x 3600 / fixed pass rate {pass_rate}"
        elif judge_score and judge_score > 0:
            j_per_good = round((wh * 3600) / judge_score, 6)
            j_src = f"{src}: candidate Wh/req x 3600 / judge agreement score {judge_score}"
        else:
            # No pass rate at all: the per-correct figure is an assumption, not measured.
            j_per_good = round(wh * 3600, 6)
            j_label = "ESTIMATE"
            j_src = f"{src}: candidate Wh/req x 3600; pass rate unavailable, assumed 1.0"
    out.append(_f("joules_per_correct_answer", j_per_good, "J/correct", j_label, j_src))
    return _finish(out, wh, rid, grid_gco2_per_kwh, grid_source)


def verify_proof_hash(receipt: Mapping) -> bool:
    """The proof receipt's SHA-256 is over its canonical JSON with an empty integrity block."""
    from wattnot.receipt.canon import canonical_sha256
    body = json.loads(json.dumps(dict(receipt)))
    want = (body.get("integrity") or {}).get("sha256")
    body["integrity"] = {"sha256": None, "signer": None, "rekor": None}
    return bool(want) and canonical_sha256(body) == want


def proof_fields(receipt: Mapping, *, grid_gco2_per_kwh: float | None = None,
                 grid_source: str | None = None) -> list[dict]:
    """Adapter for the signed receipt the live power-cap sweep writes
    (`wattnot.proof-receipt/1`, from `wattnot.proof.orchestrate`).

    Energy comes from per-block NVML counter deltas (J/request per block, stock and cap
    interleaved). The noise test is Welch's t on those per-block J/request samples plus a
    bootstrap CI of the saving: the counter is read per block, not per request, so n is the
    number of blocks, and the source says so. A MOCK receipt is reported as SIMULATED.
    """
    from wattnot.gates.noise import noise_gate
    if not verify_proof_hash(receipt):
        raise ValueError(f"receipt {receipt.get('receipt_id')}: SHA-256 does not match; "
                         "not exporting an altered receipt")
    rid = receipt["receipt_id"]
    lab = "SIMULATED" if receipt.get("label") in ("MOCK", "SIMULATED") else receipt["label"]
    if lab not in LABELS:
        raise ValueError(f"receipt {rid}: unknown label {receipt.get('label')!r}")
    steps = receipt.get("steps") or []
    best_w = receipt.get("best_cap_w")
    best = next((x for x in steps if x.get("cap_w") == best_w), None)
    hw = receipt.get("hardware") or {}
    src = (f"receipt {rid} (sha256 {receipt['integrity']['sha256'][:12]}), "
           f"{(receipt.get('method') or {}).get('energy', 'NVML energy counter')}")

    def per_block(x):  # requests per block in one arm
        q = x.get("quality") or {}
        reps = x.get("repeats") or len(x.get("j_per_req_stock") or []) or 1
        return (q.get("checked") or 0) / reps

    joules = sum((sum(x.get("j_per_req_stock") or []) + sum(x.get("j_per_req_cap") or []))
                 * per_block(x) for x in steps)
    n_blocks = sum(len(x.get("j_per_req_stock") or []) + len(x.get("j_per_req_cap") or [])
                   for x in steps)
    out = [_f("it_energy_measured_window", round(joules / 3.6e6, 6), "kWh", lab,
              f"{src}: sum over {n_blocks} sweep blocks ({len(steps)} caps); GPU only")]
    ref = best or (steps[0] if steps else None)
    if ref is None:
        raise ValueError(f"receipt {rid}: no sweep steps to report")
    stock_j, cap_j = ref["j_per_req_stock"], ref["j_per_req_cap"]
    wh_stock = sum(stock_j) / len(stock_j) / 3600
    wh = (sum(cap_j) / len(cap_j) / 3600) if best else wh_stock
    which = f"kept cap {best_w} W" if best else "stock (no cap was kept)"
    out += [_f("energy_per_request", float(f"{wh:.6g}"), "Wh/request", lab, f"{src}: {which}"),
            _f("energy_per_request_baseline", float(f"{wh_stock:.6g}"), "Wh/request", lab,
               f"{src}: stock {hw.get('default_cap_w')} W, interleaved blocks")]
    if best and len(stock_j) >= 2 and len(cap_j) >= 2:
        g = noise_gate(stock_j, cap_j).detail
        billable = g["grade"] == "billable"
        out.append(_f("energy_saving_billed", g["billed_saving_pct"], "%",
                      lab if billable or lab == "SIMULATED" else "ESTIMATE",
                      f"{src}: lower bound of 95% bootstrap CI, Welch p={g['p_value']:.3g} on "
                      f"per-block J/request (n={len(stock_j)}+{len(cap_j)} blocks, not per "
                      "request)" + ("" if billable else " (not significant)")))
    else:
        out.append(_na("energy_saving_billed", "%",
                       "no cap was kept, or under 2 blocks per arm: nothing to bill"))
    out.append(_na("idle_power", "W", "idle not measured in the sweep"))
    q = ref.get("quality") or {}
    rate = (q.get("cap_pct") if best else q.get("stock_pct"))
    if rate == 0:
        out.append(_na("joules_per_correct_answer", "J/correct",
                       f"graded pass rate was 0% ({q.get('checked')} answers checked): no correct "
                       "answers, J/correct is unbounded"))
    elif rate:
        out.append(_f("joules_per_correct_answer", round(wh * 3600 / (rate / 100), 6),
                      "J/correct", lab, f"{src}: J/request / graded pass rate {rate:g}% "
                      f"({q.get('checked')} answers checked)"))
    else:
        out.append(_na("joules_per_correct_answer", "J/correct",
                       "no graded pass rate in the receipt"))
    if best and best.get("mean_w"):
        out.append(_f("mean_gpu_power_at_kept_cap", round(best["mean_w"]["cap"], 1), "W", lab,
                      f"{src}: counter energy / block time"))
    return _finish(out, wh, rid, grid_gco2_per_kwh, grid_source)


def _finish(out: list[dict], wh: float, rid: str, grid_gco2_per_kwh, grid_source) -> list[dict]:
    """Shared tail: water and SCI views, facility fields, readiness, receipt id on each row."""
    # Water is a generated view, recomputed from the receipt's measured energy with the
    # current owner-standardised factors (headline on-site cooling, plus an upper bound
    # that includes power-plant water). The signed receipt is not modified.
    from wattnot.receipt.build import WUE_L_PER_KWH, WUE_UPPER_L_PER_KWH
    water_headline = round(wh * WUE_L_PER_KWH, 6)
    water_upper = round(wh * WUE_UPPER_L_PER_KWH, 6)
    water_method = "Wh/request x WUE (L/kWh)"
    out.insert(min(len(out), 5), _f("water_per_request", water_headline, "mL/request", "ESTIMATE",
               f"{water_method}; 0.36 L/kWh on-site cooling (LBNL 2024), ESTIMATE; not measured"))
    out.insert(min(len(out), 6), _f("water_per_request_upper", water_upper, "mL/request",
               "ESTIMATE", f"{water_method}; up to 1.8 L/kWh including power-plant water, "
               "upper ESTIMATE; not measured"))
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
    w.writerow({"receipt_id": "", "field": "disclaimer", "value": DISCLAIMER,
                "unit": "", "label": "", "source": f"{FRAMING}; {CSRD}"})
    return buf.getvalue()


def to_html(rows: Sequence[Mapping], *, title: str = "EU 2024/1364 field export") -> str:
    body = "\n".join(
        "<tr class=\"{cls}\">{cells}</tr>".format(
            cls=r["label"].lower(),
            cells="".join(f"<td>{html.escape('' if r[k] is None else str(r[k]))}</td>"
                          for k in COLUMNS)) for r in rows)
    head = "".join(f"<th>{c}</th>" for c in COLUMNS)
    return TEMPLATE.read_text(encoding="utf-8").replace("{{title}}", html.escape(title)) \
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


def generate_report(receipt_or_path: Mapping | str | Path, out_dir: str | Path | None = None,
                    *, format: str = "html", **kw) -> Any:
    """Generate EU efficiency report in one click from a signed receipt."""
    if isinstance(receipt_or_path, (str, Path)):
        receipt = json.loads(Path(receipt_or_path).read_text(encoding="utf-8"))
    else:
        receipt = dict(receipt_or_path)

    # Verify receipt integrity hash
    if "integrity" in receipt and receipt.get("spec") != PROOF_SPEC:
        from wattnot.receipt.build import verify_hash

        ok, _, _ = verify_hash(receipt)
        if not ok:
            raise ValueError(f"receipt {receipt.get('receipt_id')} integrity hash verification failed")

    rows = fields_for(receipt, **kw)
    if out_dir is not None:
        out = Path(out_dir) / f"eu_report_{receipt.get('receipt_id', 'latest')}"
        if isinstance(receipt_or_path, (str, Path)):
            return export([receipt_or_path], out, **kw)
        p = Path(out_dir) / f"{receipt.get('receipt_id', 'rcpt')}.json"
        p.write_text(json.dumps(receipt), encoding="utf-8")
        return export([p], out, **kw)

    if format == "csv":
        return to_csv(rows)
    if format == "html":
        return to_html(rows)
    if format != "json":
        raise ValueError(f"unknown report format {format!r}: want html, csv, or json")
    return {
        "receipt_id": receipt.get("receipt_id"),
        "fields": rows,
        "framing": FRAMING,
        "csrd": CSRD,
        "disclaimer": DISCLAIMER,
    }
