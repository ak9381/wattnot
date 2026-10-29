"""The cloud receipt: one JSON file per tuning run, hash-chained to the one before.

What is in it, and how each part is labelled:

* tokens (prompt, cached, completion, reasoning), USD and latency: MEASURED, taken
  from the API's `usage` block and our own clock. USD is tokens times the published
  rate; the rates and where they came from are stored in the receipt.
* energy: ESTIMATE, a range, never a measurement (we cannot see Baseten's GPUs).
* for each class, what the same requests cost at `xhigh`: MEASURED too, because the
  reference run is a real run, not a model of one.
* offline replays of a synthetic fixture are SIMULATED everywhere, like the fake
  backend elsewhere in WattNot.

The receipt holds numbers, ids and hashes only: no prompts, no answers, no
reasoning text. The same linter that guards the GPU receipts checks it.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from wattnot.cloud import BASE_URL, EFFORTS, KEY_ENV, MODEL, PROVIDER, REFERENCE, energy
from wattnot.cloud.pricing import Rates
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.lint import lint

SCHEMA = "wattnot.cloud-receipt/1"
LABEL_WORDS = ("MEASURED", "ESTIMATE", "ADVICE", "SIMULATED")
BANNED = ("neutral", "offset", "carbon-free", "carbon free", "net zero", "net-zero")


def build_receipt(result: Mapping, *, mode: str, rates: Rates, workload: Mapping,
                  settings: Mapping, calls_sha256: str, now: float | None = None,
                  prev_sha256: str | None = None, window: Mapping | None = None,
                  api_key_prefix_sha: str | None = None,
                  host: Mapping | None = None) -> dict:
    """mode: live | replay-recorded | replay-synthetic."""

    simulated = mode == "replay-synthetic"
    label = "SIMULATED" if simulated else "MEASURED"
    now = time.time() if now is None else now
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime(now))
    classes = []
    for c in result["classes"]:
        m, b = c["measured"], c["baseline_xhigh"]
        classes.append({
            "class": c["class"], "items": c["items"], "repeats": c["repeats"],
            "chosen_effort": c["chosen_effort"], "kept": c["kept"],
            "outcome": (f"kept {c['chosen_effort']}" if c["kept"]
                        else "stayed on xhigh: no cheaper level passed the gates"),
            "tried": c["tried"], "escape_retry_rate": c["escape_retry_rate"],
            "measured": {
                "label": label, "prompt_tokens": m["prompt_tokens"],
                "cached_tokens": m["cached_tokens"], "completion_tokens": m["completion_tokens"],
                "reasoning_tokens": m["reasoning_tokens"],
                "reasoning_source": m["reasoning_source"], "usd": m["usd_with_escape"],
                "usd_before_escape": m["usd"],
                "latency_p50_s": m["latency_p50_s"], "latency_p99_s": m["latency_p99_s"],
                "cached_share": c["cached_share"], "truncated": m["truncated"]},
            "would_have_cost_xhigh": {
                "label": label, "prompt_tokens": b["prompt_tokens"],
                "cached_tokens": b["cached_tokens"], "completion_tokens": b["completion_tokens"],
                "reasoning_tokens": b["reasoning_tokens"], "usd": b["usd"],
                "latency_p50_s": b["latency_p50_s"], "latency_p99_s": b["latency_p99_s"]},
            "saving": {"label": label, "usd": c["saving_usd"], "pct": c["saving_pct"],
                       "completion_tokens": b["completion_tokens"] - m["completion_tokens"]},
            "stopped": c["stopped"],
        })
    tuned = {"requests": 0, "prompt": 0, "cached": 0, "completion": 0}
    base = dict(tuned)
    tuned_usd = base_usd = 0.0
    for c in classes:
        t, b = c["measured"], c["would_have_cost_xhigh"]
        tuned_usd += t["usd"]
        base_usd += b["usd"]
        for k, tk, bk in (("prompt", "prompt_tokens", "prompt_tokens"),
                          ("cached", "cached_tokens", "cached_tokens"),
                          ("completion", "completion_tokens", "completion_tokens")):
            tuned[k] += t[tk]
            base[k] += b[bk]
    n_req = sum(c["items"] * c["repeats"] for c in classes) or 1
    reps = max(1, int(settings.get("repeats", 1)))  # one workload run = n_req / reps requests
    prompt_total = tuned["prompt"]
    saving_usd = base_usd - tuned_usd
    est = energy.estimate(tuned, base)
    est["label"] = "ESTIMATE"
    exp_usd = result["experiment_usd"]
    r = {
        "schema": SCHEMA,
        "receipt_id": f"cloud_{stamp}_{hashlib.sha256(calls_sha256.encode()).hexdigest()[:6]}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "provider": (host or {}).get("provider", PROVIDER),
        "model": (host or {}).get("model", MODEL),
        "endpoint": (host or {}).get("endpoint", BASE_URL), "mode": mode,
        "label": label,
        "labels": {"tokens": label, "usd": label, "latency": label, "energy": "ESTIMATE",
                   "rates": "ESTIMATE" if not rates.confirmed else label},
        "key_env": (host or {}).get("key_env") or KEY_ENV, "api_key_prefix_sha256": api_key_prefix_sha,
        "reference_effort": REFERENCE, "efforts_available": list(EFFORTS),
        "rates": rates.to_dict(), "workload": dict(workload), "settings": dict(settings),
        "planner": {"proposer": result["proposal"]["proposer"],
                    "note": result["proposal"]["note"],
                    "orders": result["proposal"]["orders"],
                    "tokens": result["proposal"]["tokens"],
                    "usd": result["proposal"].get("usd", 0.0)},
        "classes": classes,
        "totals": {
            "label": label, "requests": n_req, "tuned_usd": round(tuned_usd, 8),
            "would_have_cost_xhigh_usd": round(base_usd, 8), "saving_usd": round(saving_usd, 8),
            "saving_pct": round(100 * saving_usd / base_usd, 2) if base_usd else 0.0,
            "tuned_usd_per_request": round(tuned_usd / n_req, 8),
            "xhigh_usd_per_request": round(base_usd / n_req, 8),
            "completion_tokens_tuned": tuned["completion"],
            "completion_tokens_xhigh": base["completion"],
            "cached_share": round(tuned["cached"] / prompt_total, 4) if prompt_total else 0.0,
            "experiment_usd": exp_usd,
            "experiment_note": ("what this whole tuning run cost, including every level tried; "
                                "it pays for itself after the number of workload runs below"),
            "payback_runs": (round(exp_usd / (saving_usd / reps), 2)
                             if saving_usd > 0 else None),
        },
        "energy": est,
        "cache": {"label": label, "affinity_header_sent": bool(settings.get("affinity")),
                  "volatile_values_in_stable_prefix": result["volatile_in_prefix"]},
        "window": dict(window) if window else None,
        "meter_audit": None,  # filled by `wattnot cloud audit`
        "calls": {"file": "calls.jsonl", "sha256": calls_sha256, "n": len(result["calls"])},
        "unverified": unverified_notes(result, rates, mode),
        "stopped": result["stopped"],
    }
    r["integrity"] = {"canonicalization": "JCS RFC 8785", "sha256": canonical_sha256(r),
                      "prev_sha256": prev_sha256, "signer": None, "rekor": None}
    problems = check(r)
    if problems:
        raise ValueError("cloud receipt failed checks: " + "; ".join(problems))
    return r


def unverified_notes(result: Mapping, rates: Rates, mode: str) -> list[str]:
    """Things a reader should not assume. Short and specific."""

    calls = result["calls"]
    notes = []
    if mode == "replay-synthetic":
        notes.append("synthetic fixture: no real API call was made, every number is SIMULATED")
    if not rates.confirmed:
        notes.append("USD rates are the published ones and not yet checked against an invoice")
    api = sum(c.reasoning_source == "api" for c in calls)
    est = sum(c.reasoning_source == "estimated_from_text" for c in calls)
    if mode != "replay-synthetic":
        if api == 0:
            notes.append("usage.completion_tokens_details.reasoning_tokens was "
                         + ("empty on every call; reasoning tokens are estimated from the text "
                            "length (ESTIMATE)" if est else "absent; reasoning share is unknown"))
        elif api < len(calls):
            notes.append(f"reasoning_tokens was filled on {api} of {len(calls)} calls")
    if calls and not any(c.fields.get("cached_tokens") for c in calls):
        notes.append("usage.prompt_tokens_details.cached_tokens was not returned; "
                     "cached share is 0 by default")
    return notes


def check(r: Mapping) -> list[str]:
    """Lint (no text, no long strings), label words, and banned wording."""

    problems = lint(r)
    text = json.dumps(r).lower()
    problems += [f"banned wording: {w}" for w in BANNED if w in text]
    for key in ("tokens", "usd", "latency", "energy"):
        if r["labels"][key] not in LABEL_WORDS:
            problems.append(f"bad label for {key}")
    if r["label"] == "SIMULATED" and "MEASURED" in json.dumps(r):
        problems.append("a SIMULATED receipt must not say MEASURED")
    return problems


def write(receipt: Mapping, calls: Sequence[Mapping], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{receipt['receipt_id']}.json"
    p.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    (out / f"{receipt['receipt_id']}.calls.jsonl").write_text(
        "".join(json.dumps(c, sort_keys=True) + "\n" for c in calls), encoding="utf-8")
    return p


def calls_digest(calls: Sequence[Mapping]) -> str:
    h = hashlib.sha256()
    for c in calls:
        h.update((json.dumps(c, sort_keys=True) + "\n").encode())
    return h.hexdigest()


def last_in_chain(out_dir: str | Path) -> str | None:
    d = Path(out_dir)
    files = sorted(p for p in d.glob("cloud_*.json")
                   if not p.name.endswith((".calls.jsonl", ".sigstore.json")))
    return json.loads(files[-1].read_text())["integrity"]["sha256"] if files else None


def verify_hash(receipt: Mapping) -> tuple[bool, str, str]:
    want = (receipt.get("integrity") or {}).get("sha256", "")
    got = canonical_sha256(receipt)
    return want == got, want, got


def reseal(receipt: dict) -> dict:
    """Recompute the hash after a field (like meter_audit) was added."""

    receipt["integrity"]["sha256"] = canonical_sha256(receipt)
    return receipt
