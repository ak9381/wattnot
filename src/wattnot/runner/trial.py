"""One trial: warm up, then N metered reps of the workload.

FR-KNOB-02: discard the first 20 requests as warm-up, then replay the whole
workload `reps` times (5 by default). Each rep is its own meter window, so a
rep gets its own 10 Hz CSV and hash. Latency percentiles use linear
interpolation between closest ranks (the numpy default).

Events, in order: step.started, step.progress per rep, trial.measured,
step.finished. On failure an `error` event is written and the error re-raised.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from wattnot.meter.attribution import split_wh
from wattnot.meter.base import Meter, Window
from wattnot.runner.vllm_server import Client, Completion, TrialParams
from wattnot.runner.workload import Workload

WARMUP_REQUESTS = 20


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("no values")
    if not 0 <= q <= 100:
        raise ValueError("q must be in [0, 100]")
    data = sorted(values)
    pos = (len(data) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


@dataclass(frozen=True)
class RepResult:
    rep: int
    window: Window
    requests: int
    output_tokens: int
    wh_per_request: float
    p50_s: float
    p95_s: float
    p99_s: float


@dataclass(frozen=True)
class TrialResult:
    trial_id: str
    params: TrialParams
    reps: tuple[RepResult, ...]
    warmup_discarded: int
    wh_per_request: float  # mean over reps
    wh_per_1k_tokens: float
    p50_s: float  # over all metered requests
    p95_s: float
    p99_s: float

    def summary(self) -> dict:
        return {
            "trial_id": self.trial_id,
            "params": asdict(self.params),
            "reps": len(self.reps),
            "warmup_discarded": self.warmup_discarded,
            "wh_per_request": self.wh_per_request,
            "wh_per_1k_tokens": self.wh_per_1k_tokens,
            "p50_s": self.p50_s,
            "p95_s": self.p95_s,
            "p99_s": self.p99_s,
            "rep_wh": [r.window.wh for r in self.reps],
            "rep_sha256": [r.window.sha256 for r in self.reps],
            "label": self.reps[0].window.label if self.reps else None,
        }


def run_trial(params: TrialParams, workload: Workload, meter: Meter, client: Client, *,
              reps: int = 5, emit=None, trial_id: str = "t0",
              warmup: int = WARMUP_REQUESTS) -> TrialResult:
    """Run one trial against an already-started server. `emit` is an Emitter or None."""

    if reps < 1:
        raise ValueError("reps must be >= 1")
    if len(workload) == 0:
        raise ValueError("empty workload")

    def _emit(kind: str, data: dict) -> None:
        if emit is not None:
            emit.emit(kind, data, step_id=f"trial:{trial_id}")

    _emit("step.started", {"trial_id": trial_id, "params": asdict(params),
                           "workload": workload.name, "reps": reps, "warmup": warmup})
    try:
        items = list(workload)
        for i in range(warmup):
            client.complete(items[i % len(items)])

        rep_results: list[RepResult] = []
        all_lat: list[float] = []
        total_tokens = 0
        for rep in range(1, reps + 1):
            meter.start(f"{trial_id}-rep{rep}")
            try:
                done: list[Completion] = [client.complete(item) for item in items]
            finally:
                window = meter.stop()
            lats = [c.latency_s for c in done]
            toks = [c.completion_tokens for c in done]
            all_lat += lats
            total_tokens += sum(toks)
            per_req = split_wh(window.wh, toks)
            rr = RepResult(rep, window, len(done), sum(toks), statistics.fmean(per_req),
                           percentile(lats, 50), percentile(lats, 95), percentile(lats, 99))
            rep_results.append(rr)
            _emit("step.progress", {"trial_id": trial_id, "rep": rep, "wh": window.wh,
                                    "seconds": window.seconds, "p99_s": rr.p99_s,
                                    "csv_sha256": window.sha256})

        total_wh = sum(r.window.wh for r in rep_results)
        result = TrialResult(
            trial_id=trial_id,
            params=params,
            reps=tuple(rep_results),
            warmup_discarded=warmup,
            wh_per_request=statistics.fmean(r.wh_per_request for r in rep_results),
            wh_per_1k_tokens=(total_wh / total_tokens * 1000) if total_tokens else 0.0,
            p50_s=percentile(all_lat, 50),
            p95_s=percentile(all_lat, 95),
            p99_s=percentile(all_lat, 99),
        )
    except Exception as exc:
        _emit("error", {"trial_id": trial_id, "message": str(exc)[:500]})
        raise
    _emit("trial.measured", result.summary())
    _emit("step.finished", {"trial_id": trial_id, "ok": True})
    return result
