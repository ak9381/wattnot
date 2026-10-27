"""Meter audit: check our receipt totals against Baseten's own usage data.

Baseten's usage endpoint (`GET https://api.baseten.co/v1/model_apis/usage`)
returns input, cached input, uncached input and output tokens and a request count
in 1m / 1h / 1d buckets, and can filter by API key prefix. We sum the buckets that
cover the run and compare each total with the receipt. A gap over 2% is flagged.

What is still unverified until a real key is used:

* whether the hackathon key may call the management endpoint (it may be scoped to
  inference only; a 401 or 403 is reported as `unavailable`, not as a mismatch),
* how long the meter lags behind the calls (re-run the audit a few minutes later),
* whether other traffic on the same key prefix lands in the same window.

Reasoning tokens are part of `output_tokens` on the meter, so they are compared as
output, not separately.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

from wattnot.cloud import MGMT_URL, MODEL
from wattnot.cloud.transport import CloudError, key_prefix, read_env, read_key, scrub

METRICS_KEY_ENV = "BASETEN_METRICS_KEY"
TOLERANCE = 0.02
USAGE_PATH = "/v1/model_apis/usage"
FIELDS = (("requests", "request_count"), ("input_tokens", "input_tokens"),
          ("cached_input_tokens", "cached_input_tokens"),
          ("uncached_input_tokens", "uncached_input_tokens"), ("output_tokens", "output_tokens"))


def totals_from_calls(rows: Sequence[Mapping]) -> dict:
    ok = [r for r in rows if r.get("status") == 200]
    prompt = sum(r["prompt_tokens"] for r in ok)
    cached = sum(r["cached_tokens"] for r in ok)
    return {"requests": len(ok), "input_tokens": prompt, "cached_input_tokens": cached,
            "uncached_input_tokens": prompt - cached,
            "output_tokens": sum(r["completion_tokens"] for r in ok)}


def sum_meter(items: Sequence[Mapping], *, model: str | None = None) -> dict:
    """Add up `results` rows across buckets. Rows for other models are ignored."""

    out = dict.fromkeys(("request_count", "input_tokens", "cached_input_tokens",
                         "uncached_input_tokens", "output_tokens"), 0)
    for bucket in items:
        for row in bucket.get("results") or []:
            if model and row.get("model") and not _same_model(row["model"], model):
                continue
            for k in out:
                out[k] += int(row.get(k) or 0)
    return out


def _same_model(a: str, b: str) -> bool:
    return a == b or a.endswith(b) or b.endswith(a)


def reconcile(ours: Mapping, meter: Mapping, *, tolerance: float = TOLERANCE) -> dict:
    rows, worst = [], 0.0
    for name, mkey in FIELDS:
        a, b = int(ours[name if name != "requests" else "requests"]), int(meter[mkey])
        gap = abs(a - b) / max(a, b) if max(a, b) else 0.0
        worst = max(worst, gap)
        rows.append({"field": name, "receipt": a, "meter": b, "gap_pct": round(100 * gap, 3),
                     "ok": gap <= tolerance})
    ok = all(r["ok"] for r in rows)
    return {"status": "match" if ok else "mismatch", "tolerance_pct": tolerance * 100,
            "worst_gap_pct": round(100 * worst, 3), "rows": rows,
            "label": "MEASURED"}


def unavailable(why: str) -> dict:
    return {"status": "unavailable", "reason": why, "tolerance_pct": TOLERANCE * 100,
            "rows": [], "label": "NOT_AVAILABLE"}


def window_for(receipt: Mapping, pad_min: int = 2) -> tuple[str, str]:
    w = receipt.get("window") or {}
    if not w.get("start") or not w.get("end"):
        raise ValueError("the receipt has no call window; audit needs a live run")
    start = datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(w["end"].replace("Z", "+00:00")) + timedelta(minutes=pad_min)
    start = start.astimezone(UTC).replace(second=0, microsecond=0)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.astimezone(UTC).strftime(fmt)


def _why(e: urllib.error.HTTPError, key: str) -> str:
    try:
        doc = json.loads(e.read())
        return scrub(str(doc.get("code") or doc.get("message") or "")[:60], key)
    except (ValueError, OSError, AttributeError):
        return "no detail"


def fetch_usage(start: str, end: str, *, env: Mapping[str, str] | None = None,
                base: str = MGMT_URL, timeout_s: float = 60,
                opener: Callable | None = None) -> list[dict]:
    """Live call to the usage endpoint, 1-minute buckets, filtered by our key prefix.

    Raises CloudError with a safe message on 401/403/network errors.
    """

    key = read_key(env)
    # The inference key is usually not allowed to read usage. A metrics-scoped key (type
    # WORKSPACE_EXPORT_METRICS) can be passed in BASETEN_METRICS_KEY; the usage rows are still
    # filtered to the inference key's prefix.
    auth = read_env(METRICS_KEY_ENV, env) or key
    params: list[tuple[str, str]] = [("start_time", start), ("end_time", end),
                                     ("bucket_width", "1m"), ("limit", "1440"),
                                     ("group_by", "model")]
    pre = key_prefix(key)
    if pre:
        params.append(("api_keys", pre))
    url = f"{base.rstrip('/')}{USAGE_PATH}?{urllib.parse.urlencode(params)}"
    items: list[dict] = []
    cursor = None
    while True:
        u = url + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        req = urllib.request.Request(u, headers={"Authorization": f"Bearer {auth}"})
        try:
            with (opener or urllib.request.urlopen)(req, timeout=timeout_s) as resp:  # noqa: S310
                doc = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise CloudError(f"usage endpoint answered HTTP {e.code}"
                             + (f" ({_why(e, auth)}); this key is probably inference-only. Set "
                                f"{METRICS_KEY_ENV} to a metrics-scoped key to run the audit"
                                if e.code in (401, 403) else "")) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise CloudError(scrub(f"usage endpoint failed: {type(e).__name__}", key)) from None
        items += doc.get("items") or []
        pg = doc.get("pagination") or {}
        if not pg.get("has_more") or not pg.get("cursor"):
            return items
        cursor = pg["cursor"]


def audit(receipt: Mapping, call_rows: Sequence[Mapping], meter_items: Sequence[Mapping] | None,
          *, tolerance: float = TOLERANCE, why_unavailable: str | None = None) -> dict:
    if meter_items is None:
        return unavailable(why_unavailable or "no meter data")
    ours = totals_from_calls(call_rows)
    res = reconcile(ours, sum_meter(meter_items, model=receipt.get("model", MODEL)),
                    tolerance=tolerance)
    res["model"] = receipt.get("model", MODEL)
    return res
