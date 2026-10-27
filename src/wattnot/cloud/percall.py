"""Per-call audit: reconcile a receipt with the raw usage blocks Baseten returned.

The workspace meter cannot be read when the key lives in Baseten's managed hackathon account,
so this mode checks the receipt against the replies themselves, which a recording keeps
(usage block, response id, model, finish reason, rate-limit headers). It also recomputes every
call's cost from Baseten's published list prices (`GET /v1/model_apis`, read with a free
metrics key) and compares the sums with the receipt.

What this is, and is not:

* It is a reconciliation of the receipt against Baseten's per-response usage.
* It is NOT the billing meter. The receipt was built from these same replies, so a match shows
  that our parsing, per-class sums and USD arithmetic are right and that the replies are
  self-consistent (reasoning inside completion, cached inside prompt, total = prompt + completion,
  unique response ids, the right model). It cannot show that Baseten billed the same amount.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from wattnot.cloud import MGMT_URL, MODEL
from wattnot.cloud.audit import METRICS_KEY_ENV, TOLERANCE
from wattnot.cloud.pricing import Rates, cost_usd
from wattnot.cloud.transport import FIXTURE_FORMAT, CloudError, read_env, scrub

TITLE = "Per-call usage audit"
SCOPE = ("reconciled against Baseten's per-response usage, not the billing meter")
CAVEAT = ("The receipt was built from these same responses, so this checks our parsing, sums "
          "and price arithmetic and the responses' internal consistency. It does not show what "
          "Baseten billed.")
USD_EPS = 1e-7  # receipts round USD to 8 places


# ---- list prices ----------------------------------------------------------------------------

def _same_family(model: str, name: str) -> bool:
    return model == name or model.startswith(name) or name.startswith(model)


def fetch_list_prices(model: str = MODEL, *, env: Mapping[str, str] | None = None,
                      base: str = MGMT_URL, timeout_s: float = 60,
                      opener: Callable | None = None) -> dict | None:
    """Published input and output price per 1M tokens, or None if no key is set.

    Raises CloudError (never containing a key) if the endpoint refuses or has no such model.
    """

    key = read_env(METRICS_KEY_ENV, env)
    if not key:
        return None
    req = urllib.request.Request(f"{base.rstrip('/')}/v1/model_apis",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout_s) as resp:  # noqa: S310
            doc = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise CloudError(f"model list answered HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise CloudError(scrub(f"model list failed: {type(e).__name__}", key)) from None
    return prices_from_catalog(doc, model)


def prices_from_catalog(doc: Mapping, model: str = MODEL) -> dict:
    for m in doc.get("items") or []:
        if _same_family(model, str(m.get("name", ""))):
            try:
                return {"model_listed_as": m["name"],
                        "input_per_m": float(m["cost_per_million_input_tokens"]),
                        "output_per_m": float(m["cost_per_million_output_tokens"]),
                        "cached_per_m": None,  # the catalog does not list a cached rate
                        "source": "GET /v1/model_apis (Baseten list price)"}
            except (KeyError, TypeError, ValueError):
                break
    raise CloudError(f"no usable list price for {model} in the model catalog")


# ---- recordings -----------------------------------------------------------------------------

def _tags_ok(rows: Sequence[Mapping], doc: Mapping) -> bool:
    rep = doc.get("replies") or {}
    for r in rows:
        x = rep.get(r["tag"])
        if x is None:
            return False
        u = (x.get("body") or {}).get("usage") or {}
        if int(u.get("prompt_tokens") or 0) != r["prompt_tokens"] and r["status"] == 200:
            return False
    return True


def find_recording(rows: Sequence[Mapping], candidates: Sequence[str | Path]) -> tuple[Path, dict]:
    for c in candidates:
        p = Path(c)
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if doc.get("format") == FIXTURE_FORMAT and _tags_ok(rows, doc):
            return p, doc
    raise CloudError("no recording matches this receipt's calls (pass --recording FILE)")


# ---- the audit ------------------------------------------------------------------------------

def _gap(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b)) if max(abs(a), abs(b)) else 0.0


def _raw(body: Mapping) -> dict:
    u = body.get("usage") or {}
    ptd, ctd = u.get("prompt_tokens_details") or {}, u.get("completion_tokens_details") or {}
    msg = ((body.get("choices") or [{}])[0].get("message")) or {}
    content = msg.get("content") or ""
    return {"prompt": int(u.get("prompt_tokens") or 0),
            "cached": int(ptd.get("cached_tokens") or 0),
            "completion": int(u.get("completion_tokens") or 0),
            "reasoning": ctd.get("reasoning_tokens"), "total": u.get("total_tokens"),
            "id": body.get("id"), "model": body.get("model"),
            "finish": ((body.get("choices") or [{}])[0]).get("finish_reason"),
            "sha": hashlib.sha256(content.encode()).hexdigest()[:16] if content else None}


def audit_per_call(receipt: Mapping, rows: Sequence[Mapping], recording: Mapping, *,
                   prices: Mapping | None = None, recording_name: str = "",
                   tolerance: float = TOLERANCE, simulated: bool = False) -> dict:
    rates_doc = receipt.get("rates") or {}
    cached_rate = rates_doc.get("cached_per_m", Rates().cached_per_m)
    if prices:
        rates = Rates(prices["input_per_m"], cached_rate, prices["output_per_m"])
        price_note = (f"input and output priced at Baseten's list price from {prices['source']}; "
                      f"the cached rate (${cached_rate}/1M) is not in the catalog, so it is the "
                      "receipt's own and unconfirmed")
        price_src = "list"
    else:
        rates = Rates(rates_doc.get("input_per_m", Rates().input_per_m), cached_rate,
                      rates_doc.get("output_per_m", Rates().output_per_m))
        price_note = "list prices were not fetched; costs recomputed at the receipt's own rates"
        price_src = "receipt"
    model = receipt.get("model", MODEL)
    replies = recording.get("replies") or {}

    checks: list[dict] = []  # {"check", "passed", "total"}
    bad: list[dict] = []

    def tally(name: str, ok: bool, tag: str, detail: str = "") -> None:
        c = next((x for x in checks if x["check"] == name), None)
        if c is None:
            c = {"check": name, "passed": 0, "total": 0}
            checks.append(c)
        c["total"] += 1
        c["passed"] += bool(ok)
        if not ok and len(bad) < 20:
            bad.append({"check": name, "call": tag, "detail": detail})

    ids: dict[str, int] = {}
    raw_by_tag: dict[str, dict] = {}
    raw_usd: dict[str, float] = {}
    for r in rows:
        tag = r["tag"]
        rep = replies.get(tag)
        if rep is None:
            tally("call is in the recording", False, tag)
            continue
        tally("call is in the recording", True, tag)
        body = rep.get("body") or {}
        tally("status matches", rep.get("status") == r["status"], tag)
        if r["status"] != 200:
            continue
        x = _raw(body)
        raw_by_tag[tag] = x
        raw_usd[tag] = cost_usd(rates, x["prompt"], x["cached"], x["completion"])
        tally("prompt tokens match", x["prompt"] == r["prompt_tokens"], tag)
        tally("cached tokens match", x["cached"] == r["cached_tokens"], tag)
        tally("completion tokens match", x["completion"] == r["completion_tokens"], tag)
        if x["reasoning"] is not None:
            tally("reasoning tokens match", x["reasoning"] == r["reasoning_tokens"], tag)
        tally("finish reason matches", x["finish"] == r["finish_reason"], tag)
        tally("answer hash matches", x["sha"] == r.get("answer_sha256"), tag)
        tally("rate-limit headers match", (rep.get("headers") or {}) == (r.get("rate_limit") or {}),
              tag)
        tally("cost recomputed from list prices matches", abs(raw_usd[tag] - r["usd"]) <= USD_EPS,
              tag, f"receipt {r['usd']} vs recomputed {raw_usd[tag]:.8f}")
        # internal consistency of Baseten's own usage block
        tally("response id present and unique", bool(x["id"]) and ids.get(x["id"], 0) == 0, tag)
        ids[x["id"] or ""] = ids.get(x["id"] or "", 0) + 1
        tally("response names our model", x["model"] == model, tag, str(x["model"]))
        tally("cached tokens do not exceed prompt tokens", x["cached"] <= x["prompt"], tag)
        if x["reasoning"] is not None:
            tally("reasoning tokens are inside completion tokens",
                  x["reasoning"] <= x["completion"], tag)
        if x["total"] is not None:
            tally("total tokens = prompt + completion", x["total"] == x["prompt"] + x["completion"],
                  tag)

    ok_rows = [r for r in rows if r["status"] == 200 and r["tag"] in raw_by_tag]
    totals = []

    def row(field: str, ours: float, raw: float, usd: bool = False) -> None:
        g = _gap(ours, raw)
        totals.append({"field": field, "receipt": round(ours, 8) if usd else int(ours),
                       "meter": round(raw, 8) if usd else int(raw),
                       "gap_pct": round(100 * g, 3), "ok": g <= (USD_EPS if usd else tolerance)})

    row("requests", len(ok_rows), len(raw_by_tag))
    row("input_tokens", sum(r["prompt_tokens"] for r in ok_rows),
        sum(x["prompt"] for x in raw_by_tag.values()))
    row("cached_input_tokens", sum(r["cached_tokens"] for r in ok_rows),
        sum(x["cached"] for x in raw_by_tag.values()))
    row("output_tokens", sum(r["completion_tokens"] for r in ok_rows),
        sum(x["completion"] for x in raw_by_tag.values()))
    row("reasoning_tokens", sum(r["reasoning_tokens"] or 0 for r in ok_rows),
        sum(int(x["reasoning"] or 0) for x in raw_by_tag.values()))
    row("usd_all_calls", sum(r["usd"] for r in ok_rows), sum(raw_usd.values()), usd=True)

    # the receipt's own headline numbers, rebuilt from the raw replies
    t = receipt.get("totals") or {}

    def class_usd(cls: str, effort: str) -> float:
        return sum(raw_usd[r["tag"]] for r in ok_rows
                   if r["class"] == cls and r["effort"] == effort)

    base = sum(class_usd(c["class"], "xhigh") for c in receipt.get("classes") or [])
    if "would_have_cost_xhigh_usd" in t:
        row("xhigh_baseline_usd", t["would_have_cost_xhigh_usd"], base, usd=True)
    if "experiment_usd" in t:
        row("experiment_usd", t["experiment_usd"], sum(raw_usd.values()), usd=True)
    for c in receipt.get("classes") or []:
        meas = c.get("measured") or {}
        eff = c["chosen_effort"]
        sel = [r for r in ok_rows if r["class"] == c["class"] and r["effort"] == eff]
        if sel and "prompt_tokens" in meas:
            row(f"class {c['class']} chosen-level completion tokens", meas["completion_tokens"],
                sum(raw_by_tag[r["tag"]]["completion"] for r in sel))

    n_pass = sum(c["passed"] for c in checks)
    n_all = sum(c["total"] for c in checks)
    match_pct = round(100 * n_pass / n_all, 3) if n_all else 0.0
    ok = n_pass == n_all and all(x["ok"] for x in totals)
    return {"status": "match" if ok else "mismatch", "source": "per-call",
            "title": TITLE, "scope": SCOPE, "caveat": CAVEAT,
            "label": "SIMULATED" if simulated else "MEASURED",
            "tolerance_pct": tolerance * 100,
            "worst_gap_pct": max((x["gap_pct"] for x in totals), default=0.0),
            "match_pct": match_pct, "checks_passed": n_pass, "checks_total": n_all,
            "calls": len(rows), "calls_checked": len(raw_by_tag),
            "checks": checks, "rows": totals, "failures": bad,
            "price_source": price_src, "price_note": price_note,
            "list_prices": dict(prices) if prices else None,
            "rates_used": rates.to_dict(), "recording": recording_name, "model": model}
