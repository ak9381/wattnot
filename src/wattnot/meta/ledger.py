"""Persistent spend ledger and the guard in front of every Meta API call.

The ledger is one JSON file outside the repo (default ~/.wattnot/meta_spend.json,
override with WATTNOT_META_LEDGER). Every call is written *before* it is sent as a
`pending` entry at its worst-case cost, then settled with the real token usage.
So a crash or a timeout never leaves spend unrecorded, and two processes cannot
both squeeze under the cap (the check and the write happen under one file lock).

    refuse a call when   recorded total + worst-case estimate  >  CAP_USD
    warn at              recorded total + worst-case estimate  >  SOFT_USD

Prices are the published pay-as-you-go rates (https://dev.meta.ai/docs/pricing-rate-limits,
read 2026-10-27). A model that is not in the table is priced at the highest published
rate (Muse Wonder), and the entry says `price_assumed`.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import uuid
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

CAP_USD = 20.0
SOFT_USD = 15.0
LEDGER_ENV = "WATTNOT_META_LEDGER"
PRICE_SOURCE = "https://dev.meta.ai/docs/pricing-rate-limits (Standard mode, read 2026-10-27)"
DEFAULT_MAX_OUTPUT = 2000  # applied to a request that sets no output cap
CHARS_PER_TOKEN = 3.0  # worst case: real text is about 4 chars per token


@dataclass(frozen=True)
class Price:
    input_per_m: float
    cached_per_m: float
    output_per_m: float


PRICES: dict[str, Price] = {
    "muse-spark-1.3": Price(1.25, 0.15, 4.25),
    "muse-spark-1.2": Price(1.25, 0.15, 4.25),
    "muse-spark-1.1": Price(1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": Price(0.10, 0.002, 0.20),
    "muse-spark-1.2-contributor": Price(0.10, 0.002, 0.20),
    "muse-wonder-1.0": Price(4.00, 0.20, 20.00),
}
ASSUMED = Price(4.00, 0.20, 20.00)  # unknown model: the highest published rate
# Models billed per image, not per token: USD per image (SAM 3.1: $2.50 per 1,000 images).
PER_IMAGE_USD: dict[str, float] = {"sam-3.1": 0.0025}


class BudgetExceeded(RuntimeError):
    """The call would take total spend over the hard cap."""


def price_for(model: str) -> tuple[Price, bool]:
    """(price, assumed). A dated or suffixed id falls back to the base model's price."""

    if model in PRICES:
        return PRICES[model], False
    base = max((m for m in PRICES if model.startswith(m + "-") and "contributor" not in m
                and "contributor" not in model), key=len, default=None)
    if base:
        return PRICES[base], False
    return ASSUMED, True


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int,
             cached_tokens: int = 0) -> float:
    """Uncached input + cached input + output. Reasoning tokens are inside completion."""

    p, _ = price_for(model)
    cached = max(0, min(int(cached_tokens), int(prompt_tokens)))
    usd = ((int(prompt_tokens) - cached) * p.input_per_m + cached * p.cached_per_m
           + int(completion_tokens) * p.output_per_m) / 1_000_000
    return round(usd, 8)


def output_cap(body: Mapping) -> int:
    for k in ("max_completion_tokens", "max_tokens"):
        if isinstance(body.get(k), int) and body[k] > 0:
            return body[k]
    return DEFAULT_MAX_OUTPUT


def estimate(body: Mapping) -> tuple[int, int, float]:
    """Worst case for one call: (input tokens, output tokens, USD).

    Input is the request size at 3 chars per token with no cache discount; output is the
    full output cap (reasoning tokens count against it), so the real cost is never higher.
    """

    n_in = math.ceil(len(json.dumps(body, separators=(",", ":"))) / CHARS_PER_TOKEN)
    n_out = output_cap(body)
    return n_in, n_out, cost_usd(str(body.get("model", "")), n_in, n_out)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SpendLedger:
    def __init__(self, path: str | Path | None = None, *, cap_usd: float = CAP_USD,
                 soft_usd: float = SOFT_USD) -> None:
        if soft_usd > cap_usd:
            raise ValueError("soft limit above the hard cap")
        self.path = Path(path or os.environ.get(LEDGER_ENV)
                         or Path.home() / ".wattnot" / "meta_spend.json").expanduser()
        self.cap_usd = float(cap_usd)
        self.soft_usd = float(soft_usd)

    # ---- file handling -------------------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self) -> Iterator[dict]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            doc = self._read()
            yield doc
            tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(doc, indent=1) + "\n")
            os.replace(tmp, self.path)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "cap_usd": self.cap_usd, "soft_usd": self.soft_usd,
                    "price_source": PRICE_SOURCE, "entries": []}
        doc = json.loads(self.path.read_text())
        if not isinstance(doc.get("entries"), list):
            raise ValueError(f"{self.path}: not a spend ledger")
        return doc

    def entries(self) -> list[dict]:
        return self._read()["entries"] if self.path.exists() else []

    # ---- totals --------------------------------------------------------------------------

    def total(self) -> float:
        return round(sum(e["usd"] for e in self.entries()), 6)

    def remaining(self) -> float:
        return round(max(0.0, self.cap_usd - self.total()), 6)

    def summary(self) -> dict:
        es = self.entries()
        per: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0,
                                                    "usd": 0.0})
        for e in es:
            p = per[e["purpose"]]
            p["calls"] += 1
            p["input"] += e["input_tokens"]
            p["output"] += e["output_tokens"]
            p["usd"] = round(p["usd"] + e["usd"], 6)
        total = round(sum(e["usd"] for e in es), 6)
        return {"cap_usd": self.cap_usd, "soft_usd": self.soft_usd, "total_usd": total,
                "remaining_usd": round(max(0.0, self.cap_usd - total), 6),
                "calls": len(es),
                "input_tokens": sum(e["input_tokens"] for e in es),
                "output_tokens": sum(e["output_tokens"] for e in es),
                "pending": sum(e["status"] == "pending" for e in es),
                "errors": sum(e["status"] == "error" for e in es),
                "warning": total >= self.soft_usd,
                "per_purpose": dict(sorted(per.items(), key=lambda kv: -kv[1]["usd"]))}

    # ---- the guard -----------------------------------------------------------------------

    def reserve(self, body: Mapping, purpose: str) -> tuple[str, bool]:
        """Write a pending entry at worst-case cost. Raises BudgetExceeded over the cap.

        Returns (entry id, over the soft limit).
        """

        n_in, n_out, usd = estimate(body)
        model = str(body.get("model", ""))
        _, assumed = price_for(model)
        with self._locked() as doc:
            total = sum(e["usd"] for e in doc["entries"])
            if total + usd > self.cap_usd:
                raise BudgetExceeded(
                    f"refused: ${total:.4f} spent + ${usd:.4f} worst case for this call "
                    f"> ${self.cap_usd:.2f} cap")
            eid = uuid.uuid4().hex[:12]
            doc["entries"].append({
                "id": eid, "ts": _now(), "model": model, "purpose": purpose,
                "status": "pending", "input_tokens": n_in, "output_tokens": n_out,
                "cached_tokens": 0, "usd": usd, "estimated": True, "price_assumed": assumed})
            return eid, total + usd > self.soft_usd

    def settle(self, eid: str, usage: Mapping | None, *, model: str | None = None,
               status: str = "ok", http_status: int | None = None, latency_s: float | None = None,
               reasoning_tokens: int | None = None, note: str | None = None) -> dict:
        """Replace a pending entry's estimate with the real usage.

        usage None + status error: the request was rejected, nothing was billed ($0).
        usage None + status unknown: outcome not known (timeout), keep the worst case.
        """

        with self._locked() as doc:
            e = next((x for x in doc["entries"] if x["id"] == eid), None)
            if e is None:
                raise KeyError(eid)
            if usage:
                u = usage.get("prompt_tokens_details") or {}
                e.update(input_tokens=int(usage.get("prompt_tokens", 0)),
                         output_tokens=int(usage.get("completion_tokens", 0)),
                         cached_tokens=int(u.get("cached_tokens", 0) or 0), estimated=False)
                e["usd"] = cost_usd(model or e["model"], e["input_tokens"], e["output_tokens"],
                                    e["cached_tokens"])
                rt = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                reasoning_tokens = reasoning_tokens if reasoning_tokens is not None else rt
            elif status == "error":
                e.update(input_tokens=0, output_tokens=0, usd=0.0, estimated=False)
            e["status"] = status
            if http_status is not None:
                e["http_status"] = http_status
            if latency_s is not None:
                e["latency_s"] = round(latency_s, 3)
            if reasoning_tokens is not None:
                e["reasoning_tokens"] = int(reasoning_tokens)
            if note:
                e["note"] = note[:200]
            return dict(e)

    def charge_image(self, model: str, purpose: str, *, images: int = 1,
                     note: str = "") -> str:
        """Record a per-image call (SAM) before it is sent. Raises BudgetExceeded over the cap.

        Returns the entry id. A per-image call has no token usage to settle, so the entry is
        written at its flat price; call `refund` if the request was rejected before billing.
        """

        usd = round(PER_IMAGE_USD[model] * images, 8)
        with self._locked() as doc:
            total = sum(e["usd"] for e in doc["entries"])
            if total + usd > self.cap_usd:
                raise BudgetExceeded(f"refused: ${total:.4f} spent + ${usd:.4f} for this call "
                                     f"> ${self.cap_usd:.2f} cap")
            eid = uuid.uuid4().hex[:12]
            doc["entries"].append({
                "id": eid, "ts": _now(), "model": model, "purpose": purpose, "status": "ok",
                "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "usd": usd,
                "estimated": False, "price_assumed": False, "images": images,
                "note": note[:200]})
            return eid

    def refund(self, eid: str, note: str = "") -> None:
        """Set an entry to $0 (the provider rejected the request, nothing was billed)."""

        with self._locked() as doc:
            e = next((x for x in doc["entries"] if x["id"] == eid), None)
            if e is None:
                raise KeyError(eid)
            e.update(usd=0.0, status="error", note=note[:200])

    def seed(self, rows: list[dict]) -> int:
        """Add earlier recorded usage once (rows carry a unique `key`). Returns rows added."""

        added = 0
        with self._locked() as doc:
            have = {e.get("key") for e in doc["entries"]}
            for r in rows:
                if r["key"] in have:
                    continue
                usd = cost_usd(r["model"], r["input_tokens"], r["output_tokens"])
                doc["entries"].append({
                    "id": uuid.uuid4().hex[:12], "ts": r.get("ts", _now()), "model": r["model"],
                    "purpose": r.get("purpose", "earlier-recorded"), "status": "ok",
                    "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
                    "cached_tokens": 0, "usd": usd, "estimated": False, "key": r["key"],
                    "note": r.get("note", "")})
                added += 1
        return added
