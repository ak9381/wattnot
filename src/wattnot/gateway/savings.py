"""One savings record per gateway request, and a running total for the live counter.

Actual = every upstream call the gateway made for this request (step-ups included).
Baseline = the same request sent once to the baseline tier with the full (untrimmed) context and the
caller's own output budget: what the app paid before WattNot. Baseline tokens are an ESTIMATE:
prompt = the context saver's tokens-before (or the answer call's prompt tokens); completion = the
answer's completion tokens scaled by the baseline/actual reasoning ratio is not knowable, so we use the
answer's own completion tokens when it came from the baseline tier and the configured
``baseline_completion_factor`` otherwise (default 1.0, i.e. no credit for shorter thinking).
Energy: MEASURED only when ``energy.source: nvml`` and a meter fills ``joules``; otherwise
tokens x J/token from the H100 runs, labelled ESTIMATE.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path


def _price(model: str):
    from wattnot.meta.ledger import price_for
    return price_for(model or "")[0]


def _usd(model: str, pin: int, pout: int) -> float:
    p = _price(model)
    return pin / 1e6 * p.input_per_m + pout / 1e6 * p.output_per_m


def _jpt(cfg: dict, model: str) -> float:
    t = cfg["energy"]["j_per_token"]
    return float(t.get(model, t.get("default", 0.0)))


def _est_tokens(body: dict) -> int:
    return max(1, len(json.dumps(body.get("messages") or [])) // 4)


def record_for(body: dict, calls: list, status: int, base_tier: dict, cfg: dict, run: str | None = None,
               ratio: float | None = None) -> dict:
    act_in = sum(int(c["usage"].get("prompt_tokens") or 0) for c in calls)
    act_out = sum(int(c["usage"].get("completion_tokens") or 0) for c in calls)
    act_usd = sum(_usd(c["model"], int(c["usage"].get("prompt_tokens") or 0),
                       int(c["usage"].get("completion_tokens") or 0)) for c in calls)
    act_j = sum(_jpt(cfg, c["model"]) * (int(c["usage"].get("prompt_tokens") or 0)
                                         + int(c["usage"].get("completion_tokens") or 0)) for c in calls)
    bmodel = base_tier["model"]
    full_prompt = _est_tokens(body)
    last = calls[-1]["usage"] if calls else {}
    # baseline prompt: the untrimmed request (chars/4); never below what was actually sent
    b_in = max(full_prompt, int(last.get("prompt_tokens") or 0))
    # completion: same as the answer when the baseline tier answered; otherwise scaled by the
    # measured baseline/small completion ratio from shadow samples (1.0 until there are any)
    answered_base = bool(calls) and calls[-1].get("model_tier") == base_tier["name"]
    factor = 1.0 if answered_base or ratio is None else float(ratio)
    b_out = int(round(int(last.get("completion_tokens") or 0) * factor)) if calls else 0
    if not calls:   # guard stop: the call that would have gone out, with no answer tokens counted
        b_out = 0
    b_usd = _usd(bmodel, b_in, b_out)
    b_j = _jpt(cfg, bmodel) * (b_in + b_out)
    # per-request joules are tokens x J/token; the node agent's NVML samples are reported separately
    label = "ESTIMATE"
    return {
        "ts": round(time.time(), 3), "run": (run or "")[:32], "status": status,
        "upstream_calls": len(calls), "stepped_up": max(0, len(calls) - 1),
        "guard_stopped": not calls and status == 200,
        "images_shrunk": sum(c.get("images_shrunk", 0) for c in calls),
        "actual": {"prompt_tokens": act_in, "completion_tokens": act_out, "usd": round(act_usd, 7),
                   "joules": round(act_j, 2), "models": [c["model"] for c in calls]},
        "baseline": {"model": bmodel, "prompt_tokens": b_in, "completion_tokens": b_out,
                     "usd": round(b_usd, 7), "joules": round(b_j, 2), "label": "ESTIMATE",
                     "completion_ratio": round(factor, 3), "j_per_token": _jpt(cfg, bmodel)},
        "labels": {"tokens": "MEASURED (provider usage)", "usd": "ESTIMATE (list price)",
                   "joules": label},
    }


class SavingsLog:
    """Append-only JSONL plus an in-memory total. Thread-safe."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, rec: dict) -> dict:
        """Add a hash-chained receipt id (``gw_<sha12>``, over the record and the previous id)."""
        import hashlib
        with self._lock:
            prev = getattr(self, "_prev", None)
            if prev is None:
                old = self.records()
                prev = old[-1].get("receipt_id", "") if old else ""
            rec = {**rec, "prev_receipt_id": prev}
            rid = "gw_" + hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()[:12]
            rec["receipt_id"] = rid
            self._prev = rid
            with self.path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def summary(self, last: int = 20) -> dict:
        return summarize(self.records(), last=last)


def summarize(recs: list[dict], last: int = 20) -> dict:
    def tot(side: str, k: str) -> float:
        return sum(float(r[side][k]) for r in recs)

    a_tok = tot("actual", "prompt_tokens") + tot("actual", "completion_tokens")
    b_tok = tot("baseline", "prompt_tokens") + tot("baseline", "completion_tokens")
    a_usd, b_usd = tot("actual", "usd"), tot("baseline", "usd")
    a_j, b_j = tot("actual", "joules"), tot("baseline", "joules")

    def pct(a: float, b: float):
        return round(100 * (b - a) / b, 2) if b else None

    labels = {r["labels"]["joules"] for r in recs}
    return {
        "spec": "wattnot.gateway-savings/1", "requests": len(recs),
        "errors": sum(1 for r in recs if r["status"] >= 400),
        "stepped_up": sum(r["stepped_up"] for r in recs),
        "guard_stopped": sum(1 for r in recs if r["guard_stopped"]),
        "images_shrunk": sum(r.get("images_shrunk", 0) for r in recs),
        "tokens": {"actual": int(a_tok), "baseline": int(b_tok), "saved_pct": pct(a_tok, b_tok),
                   "label": "actual MEASURED, baseline ESTIMATE"},
        "usd": {"actual": round(a_usd, 5), "baseline": round(b_usd, 5), "saved_pct": pct(a_usd, b_usd),
                "label": "ESTIMATE (list price)"},
        "energy_wh": {"actual": round(a_j / 3600, 4), "baseline": round(b_j / 3600, 4),
                      "saved_pct": pct(a_j, b_j),
                      "label": "MEASURED" if labels == {"MEASURED"} else "ESTIMATE"},
        "last": recs[-last:],
    }
