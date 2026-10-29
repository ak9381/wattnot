"""Adaptive top-k for MoE models: an OPTIONAL trial, off by default.

What it is. A MoE router picks the top-k experts for each token (Granite 3.1 3B-A800M: k = 8
of 40). Adaptive top-k keeps only as many of those k as it takes to cover a chosen share of the
router's weight (`mass`, for example 0.9) and skips the rest. Tokens the router is sure about use
fewer experts, so there is less expert compute per token, at some cost in agreement with the
normal model.

This is a KNOWN technique, not a WattNot idea. Prior art is listed in `PRIOR_ART` and
docs/ADAPTIVE-TOPK.md. WattNot's part is only the measurement and the paperwork around it:
joules per token from the NVML energy counter, p99, agreement with normal top-k, a signed
receipt, and an approval step before anything is switched on.

Why a transformers hook and not vLLM. As far as we checked, vLLM's fused MoE layer takes a fixed
`top_k` (a scalar) and has no per-token adaptive option. So this runs the model with Hugging Face
transformers and a small hook (`hf_backend.py`). Both arms use the same eager expert loop, so the
comparison is like for like. A saving here is NOT a claim about vLLM's fused kernels.

This module has no torch import: the experiment, the gates, the receipt and the proposal are plain
Python and are tested with a fake backend. `hf_backend.HFBackend` is the real one (needs a GPU).

Labels: MEASURED only when the NVML energy counter moved in every window; SIMULATED for the fake
backend. If a real run's counter did not move, no receipt is written (an ESTIMATE would break the
"every receipt is MEASURED" check of the H100 kit) and the stage says why.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from wattnot.meter.base import Window
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.grade import SPEC, claimed_grade
from wattnot.receipt.joules import with_joules
from wattnot.receipt.lint import lint
from wattnot.schemas import validate_receipt

# Earlier (RUN2, non-Meta) MoE: IBM Granite 3.1 3B-A800M Instruct (Apache 2.0, ungated). 3.3B parameters, 40
# experts per layer, top-8, so about 7 GB in bf16: it fits one H100 80GB with room to spare, and
# it is the model the CPU test used, so the numbers line up. OLMoE-1B-7B (64 experts, top-8) is
# the usual alternative, but the hook is only wired for the Granite layout today.
DEFAULT_MODEL = "meta-llama/Llama-4-Scout-17B-16E-Instruct"
# Meta-only rule (Oct 29): the default is Meta's Llama 4 Scout. Scout routes each token to ONE
# expert plus an always-on shared expert, so there is no k to lower: the hook refuses top-1
# routers with a clear error. The Granite model above stays only as the CPU test fixture
# (tests pass it explicitly) and as the dated RUN2 history.
# 0.95, tuned on a CPU (scripts/tune_adaptive_mass.py, data/adaptive-tune). RUN2 used 0.9 and got
# 94.5% token agreement on the H100, under the 95% gate. On the CPU 0.95 keeps agreement at
# 96.7% or better on three prompt sets (lower 95% bound 95.5%), with about 7.7 of 8 experts active.
DEFAULT_MASS = 0.95
MIN_MASS, MAX_MASS = 0.5, 0.999  # the range a trial grant covers
DEFAULT_BUDGET_S = 1500.0  # about 25 minutes, the whole stage
MIN_AGREEMENT_PCT = 95.0   # quality gate: token agreement with normal top-k
MAX_P99_INCREASE_PCT = 10.0  # p99 gate, same budget as the other H100 gates

PRIOR_ART = (
    "Lu et al. 2024, Not All Experts are Equal (dynamic expert skipping), arXiv:2402.14800",
    "Huang et al. 2024, Harder Tasks Need More Experts (top-p routing), arXiv:2403.07652",
    "Yue et al. 2025, Ada-K Routing (learned per-token expert counts), ICLR 2025",
)
SCOPE = ("One MoE model, one GPU, one load shape, eager transformers expert loop in both arms. "
         "Not a vLLM kernel result and not a site or grid saving.")
NOT_CLAIMED = [
    "not a new method: skipping low-weight experts is published prior art",
    "no vLLM or fused-kernel claim",
    "no site or grid level saving",
    "no carbon claim",
]


class AdaptiveError(RuntimeError):
    """The trial could not run or could not be trusted. The rest of the H100 run goes on."""


def kept_slots(weights: list[float], mass: float) -> int:
    """How many of the top-k slots adaptive top-k keeps for one token.

    `weights` are the router weights of the chosen experts, largest first. The first slot is
    always kept; each next slot is kept while the weight before it covers less than `mass` of the
    total. This is the reference version of what the torch hook does, used in tests.
    """
    total = sum(weights)
    if total <= 0 or not weights:
        return len(weights)
    cum, n = 0.0, 0
    for w in weights:
        if n >= 1 and cum >= mass:
            break
        cum += w / total
        n += 1
    return n


@dataclass(frozen=True)
class AdaptiveConfig:
    model: str = DEFAULT_MODEL
    mass: float = DEFAULT_MASS
    batch: int = 8              # prompts per generate call; a "request" is one prompt
    n_prompts: int = 32
    max_new_tokens: int = 64    # forced length (no early stop) so both arms do the same work
    reps: int = 5               # most repeats per arm; fewer if the time box runs out
    budget_s: float = DEFAULT_BUDGET_S
    agree_prompts: int = 32     # RUN2 used 16 x 32 = 512 positions: too few for a 95% gate
    agree_new_tokens: int = 48
    min_agreement_pct: float = MIN_AGREEMENT_PCT
    max_p99_increase_pct: float = MAX_P99_INCREASE_PCT

    def __post_init__(self) -> None:
        if not MIN_MASS <= self.mass <= MAX_MASS:
            raise ValueError(f"adaptive mass must be between {MIN_MASS} and {MAX_MASS}")
        if self.batch < 1 or self.n_prompts < self.batch or self.reps < 1:
            raise ValueError("batch >= 1, n_prompts >= batch and reps >= 1 are required")
        if self.budget_s <= 0:
            raise ValueError("budget_s must be positive")


class Meter(Protocol):
    def start(self, label: str) -> None: ...
    def stop(self) -> Window: ...


class MoeBackend(Protocol):
    simulated: bool
    gpu: str
    driver: str

    def prepare(self, deadline: float, clock: Callable[[], float]) -> dict: ...
    def set_arm(self, arm: str) -> None: ...          # "off" | "on"
    def generate(self, prompts: list[str]) -> tuple[float, int]: ...  # (seconds, new tokens)
    def reset_counters(self) -> None: ...
    def active_experts(self) -> float | None: ...      # mean experts used per token and layer
    def agreement(self) -> dict: ...                   # {"pct", "positions", "method"}
    def energy_meter(self) -> Meter: ...
    def close(self) -> None: ...


@dataclass
class ArmRun:
    arm: str
    rep: int
    wh: float
    seconds: float
    tokens: int
    requests: int
    latencies_ms: list[float]
    label: str
    method: str
    csv_path: str
    csv_sha256: str
    active_experts: float | None

    @property
    def j_per_token(self) -> float:
        return self.wh * 3600 / self.tokens


@dataclass
class TrialResult:
    cfg: AdaptiveConfig
    simulated: bool
    gpu: str
    driver: str
    runs: list[ArmRun] = field(default_factory=list)
    agreement: dict = field(default_factory=dict)
    prepare: dict = field(default_factory=dict)
    truncated: str | None = None
    seconds: float = 0.0
    top_k: int | None = None


def p99(values: list[float]) -> float:
    """Nearest-rank 99th percentile."""
    s = sorted(values)
    return s[max(0, math.ceil(0.99 * len(s)) - 1)]


def _arm(result: TrialResult, arm: str) -> list[ArmRun]:
    return [r for r in result.runs if r.arm == arm]


def label_of(result: TrialResult) -> str:
    """SIMULATED, MEASURED (every window read the energy counter) or ESTIMATE."""
    if result.simulated:
        return "SIMULATED"
    ok = bool(result.runs) and all(r.label == "MEASURED" and r.method == "counter"
                                   for r in result.runs)
    return "MEASURED" if ok else "ESTIMATE"


def summarize(result: TrialResult) -> dict:
    """Per-arm joules per token, p99, active experts, the saving, and the two gates."""
    off, on = _arm(result, "off"), _arm(result, "on")
    if not off or not on:
        raise AdaptiveError("no finished pair of runs: nothing to compare")
    cfg = result.cfg

    def arm_stats(runs: list[ArmRun]) -> dict:
        j = [r.j_per_token for r in runs]
        lat = [x for r in runs for x in r.latencies_ms]
        act = [r.active_experts for r in runs if r.active_experts is not None]
        return {"reps": len(runs), "j_per_token": {
                    "mean": statistics.fmean(j),
                    "sd": statistics.stdev(j) if len(j) > 1 else 0.0, "samples": j},
                "p99_ms": p99(lat), "tokens": sum(r.tokens for r in runs),
                "requests": sum(r.requests for r in runs),
                "active_experts": statistics.fmean(act) if act else None}

    a, b = arm_stats(off), arm_stats(on)
    # The saving and p99 change are expressed as a fraction of the normal top-k ("off") arm, so a
    # zero baseline (a degenerate run with no measured energy or latency) has no defined change.
    # Fail with a clear AdaptiveError rather than a bare ZeroDivisionError.
    if a["j_per_token"]["mean"] <= 0 or a["p99_ms"] <= 0:
        raise AdaptiveError("normal top-k arm has zero joules/token or p99: nothing to compare")
    saving = 100 * (a["j_per_token"]["mean"] - b["j_per_token"]["mean"]) / a["j_per_token"]["mean"]
    n = min(len(off), len(on))
    lower = sum(1 for x, y in zip(off[:n], on[:n], strict=True) if y.j_per_token < x.j_per_token)
    inc = 100 * (b["p99_ms"] - a["p99_ms"]) / a["p99_ms"]
    agree = float(result.agreement.get("pct", 0.0))
    gates = {
        "quality": {"passed": agree >= cfg.min_agreement_pct,
                    "gate": f"token agreement with normal top-k >= {cfg.min_agreement_pct:g}%",
                    "agreement_pct": round(agree, 2)},
        "p99": {"passed": inc <= cfg.max_p99_increase_pct,
                "gate": f"p99 within {cfg.max_p99_increase_pct:g}% of normal top-k",
                "increase_pct": round(inc, 2)},
        "energy": {"passed": saving > 0, "gate": "joules per token lower than normal top-k"},
    }
    reduction = None
    if a["active_experts"] and b["active_experts"]:
        reduction = 100 * (a["active_experts"] - b["active_experts"]) / a["active_experts"]
    return {"off": a, "on": b, "saving_pct": saving, "pairs": n, "pairs_on_lower": lower,
            "p99_increase_pct": inc, "expert_reduction_pct": reduction, "gates": gates,
            "all_gates_passed": all(g["passed"] for g in gates.values()),
            "label": label_of(result)}


def _base_prompts() -> list[str]:
    from wattnot.flex import load as ld
    from wattnot.runner.workload import load_replay_set

    return [i.prompt for i in load_replay_set()] + [p.prompt for p in ld.load_prompts(
        ld.DEFAULT_PROMPTS)]


def _prompts(cfg: AdaptiveConfig) -> list[str]:
    """The same fixed prompts, in the same order, for both arms: the public replay set, then
    the short flex questions, tiled up to `n_prompts`."""
    base = _base_prompts()
    return [base[i % len(base)] for i in range(cfg.n_prompts)]


def _agree_prompts(cfg: AdaptiveConfig) -> list[str]:
    """The prompts for the agreement check. The first ones are the same as `_prompts` (and as
    RUN2's 16); the list is as long as `agree_prompts`, so it does not stop at `n_prompts`."""
    base = _base_prompts()
    return [base[i % len(base)] for i in range(cfg.agree_prompts)]


def run_trial(backend: MoeBackend, cfg: AdaptiveConfig, *, session=None,
              clock: Callable[[], float] = time.monotonic,
              prompts: list[str] | None = None) -> TrialResult:
    """Normal top-k (off) against adaptive top-k (on), same prompts, same load, alternating arms.

    Raises `NotApproved` (from the grant) or `AdaptiveError`; the runbook records either and
    carries on with the rest of the run.
    """
    if session is not None:
        session.check_moe(cfg.mass)  # no grant for this knob, no trial
    t0 = clock()
    deadline = t0 + cfg.budget_s
    prompts = prompts or _prompts(cfg)
    res = TrialResult(cfg, bool(backend.simulated), backend.gpu, backend.driver)
    try:
        res.prepare = backend.prepare(deadline, clock)
        res.top_k = res.prepare.get("top_k")
        batches = [prompts[i:i + cfg.batch] for i in range(0, len(prompts), cfg.batch)]
        # warm-up (not metered), which also gives the time one run takes
        w0 = clock()
        for arm in ("off", "on"):
            backend.set_arm(arm)
            backend.generate(batches[0])
        est = (clock() - w0) / 2 * len(batches)
        backend.set_arm("off")
        res.agreement = backend.agreement()
        meter = backend.energy_meter()
        for rep in range(cfg.reps):
            if clock() + 2.2 * est > deadline:
                res.truncated = (f"time box of {cfg.budget_s:g} s reached after {rep} "
                                 f"repeat(s) per arm")
                break
            for arm in (("off", "on") if rep % 2 == 0 else ("on", "off")):
                backend.set_arm(arm)
                backend.reset_counters()
                lat, toks, s0 = [], 0, clock()
                meter.start(f"moe-{arm}-{rep}")
                for b in batches:
                    sec, n = backend.generate(b)
                    lat += [sec * 1000.0] * len(b)
                    toks += n
                w = meter.stop()
                est = max(est * 0.5, clock() - s0)
                res.runs.append(ArmRun(arm, rep, w.wh, w.seconds, toks, len(prompts), lat,
                                       w.label, w.method, w.csv_path, w.sha256,
                                       backend.active_experts()))
    finally:
        backend.close()
        res.seconds = clock() - t0
    return res


# ---------------------------------------------------------------- receipt

def _wh_block(runs: list[ArmRun], requests: int) -> dict:
    per_req = [r.wh / requests for r in runs]
    return {"mean": round(statistics.fmean(per_req), 9),
            "sd": round(statistics.stdev(per_req), 9) if len(per_req) > 1 else 0.0,
            "samples": [round(x, 9) for x in per_req]}


def build_receipt(result: TrialResult, *, prev_sha256: str | None = None,
                  approval: dict | None = None, created_at: str | None = None) -> dict:
    """A `wattnot.receipt/1` receipt, profile `moe_adaptive_topk`. Raises AdaptiveError if the
    run is not labelled MEASURED (real) or SIMULATED (fake): the kit never carries an ESTIMATE."""
    s = summarize(result)
    label = s["label"]
    if label not in ("MEASURED", "SIMULATED"):
        raise AdaptiveError("the NVML energy counter did not move in every window; "
                            "no receipt (it would only be an ESTIMATE)")
    cfg, n_req = result.cfg, len(_arm(result, "off")[0].latencies_ms)
    off, on = _arm(result, "off"), _arm(result, "on")
    windows = [{"path": r.csv_path, "sha256": r.csv_sha256} for r in result.runs]
    rid = "rcpt_moe_" + hashlib.sha256(json.dumps(
        [(r.arm, r.rep, r.wh, r.tokens) for r in result.runs]).encode()).hexdigest()[:12]
    simulated = label == "SIMULATED"
    quality_on = {"token_agreement_pct": round(float(result.agreement["pct"]), 2)}
    r: dict[str, Any] = {
        "spec": SPEC, "profile": "moe_adaptive_topk", "receipt_id": rid,
        "created_at": created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": f"moe_adaptive_{rid[-12:]}",
        "subject": {"model": cfg.model,
                    "host": {"provider": "fake" if simulated else "e2e", "gpu": result.gpu,
                             "driver": result.driver},
                    "mode": "moe-adaptive-topk",
                    "workload": {"name": "replay-v1 + flex questions", "n_requests": n_req,
                                 "batch": cfg.batch, "max_new_tokens": cfg.max_new_tokens}},
        "baseline": {"params": {"adaptive_topk": False}, "reps": len(off),
                     "wh_per_req": _wh_block(off, n_req), "p99_ms": round(s["off"]["p99_ms"], 3),
                     "quality": {"token_agreement_pct": 100.0}},
        "candidate": {"params": {"adaptive_topk": True, "adaptive_mass": cfg.mass},
                      "reps": len(on), "wh_per_req": _wh_block(on, n_req),
                      "p99_ms": round(s["on"]["p99_ms"], 3), "quality": quality_on},
        "result": {"saving_pct": {"mean": round(s["saving_pct"], 4), "ci_low": None,
                                  "ci_high": None,
                                  "basis": "joules per token, adaptive vs normal top-k"},
                   "welch": {"applicable": False,
                             "reason": "a handful of repeats on one card; grade capped"},
                   "billable": False, "billed_saving_pct": 0.0, "fee_share": 0.0,
                   "currency": "USD"},
        "measurement": {"meter": "simulated" if simulated else "nvml", "hz": 10,
                        "csv": windows, "label": label},
        "moe_adaptive_topk": {
            "label": label,
            "technique": "adaptive top-k: skip experts once a share of router weight is covered",
            "known_technique": True, "prior_art": list(PRIOR_ART),
            "implementation": "transformers hook; same eager expert loop in both arms",
            "vllm_native": False, "adaptive_mass": cfg.mass, "default": "off",
            "joules_per_token": {"off": {k: (round(v, 6) if isinstance(v, float) else
                                             [round(x, 6) for x in v])
                                         for k, v in s["off"]["j_per_token"].items()},
                                 "on": {k: (round(v, 6) if isinstance(v, float) else
                                            [round(x, 6) for x in v])
                                        for k, v in s["on"]["j_per_token"].items()},
                                 "saving_pct": round(s["saving_pct"], 3),
                                 "pairs_on_lower": f"{s['pairs_on_lower']}/{s['pairs']}"},
            "p99_ms": {"off": round(s["off"]["p99_ms"], 3), "on": round(s["on"]["p99_ms"], 3),
                       "increase_pct": round(s["p99_increase_pct"], 3),
                       "request": "one prompt; latency is its batch's generate time"},
            "active_experts": {"top_k": result.top_k,
                               "off_mean": _r(s["off"]["active_experts"]),
                               "on_mean": _r(s["on"]["active_experts"]),
                               "reduction_pct": _r(s["expert_reduction_pct"])},
            "agreement": {"pct": round(float(result.agreement["pct"]), 3),
                          "positions": result.agreement.get("positions"),
                          "method": str(result.agreement.get("method", ""))[:190]},
            "gates": s["gates"], "all_gates_passed": s["all_gates_passed"],
            "truncated": result.truncated, "seconds": round(result.seconds, 1),
            "scope": SCOPE, "not_claimed": NOT_CLAIMED},
        "planner_trace": [],
    }
    if approval:
        r["approval"] = dict(approval)
    with_joules(r)
    r["grade"] = claimed_grade(r, rekor=False)
    r["integrity"] = {"canonicalization": "JCS RFC 8785", "sha256": canonical_sha256(r),
                      "prev_sha256": prev_sha256, "signer": None, "rekor": None}
    problems = lint(r)
    from wattnot.cloud.receipt import BANNED

    text = json.dumps(r)
    problems += [f"banned wording: {w}" for w in BANNED if w in text.lower()]
    if simulated and "MEASURED" in text:
        problems.append("a SIMULATED receipt must not say MEASURED")
    if problems:
        raise AdaptiveError("moe receipt failed checks: " + "; ".join(problems))
    validate_receipt(r)
    return r


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def write_receipt(result: TrialResult, out: str | Path, *, sign_key: str | None = None,
                  prev_sha256: str | None = None, approval: dict | None = None) -> dict:
    """Write the receipt (and an offline signature bundle) plus the per-run details."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    r = build_receipt(result, prev_sha256=prev_sha256, approval=approval)
    path = out / f"{r['receipt_id']}.json"
    path.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    (out / "adaptive_topk_runs.json").write_text(json.dumps({
        "label": r["measurement"]["label"], "model": result.cfg.model,
        "runs": [{"arm": x.arm, "rep": x.rep, "wh": x.wh, "seconds": x.seconds,
                  "tokens": x.tokens, "j_per_token": x.j_per_token,
                  "active_experts": x.active_experts, "csv": x.csv_path} for x in result.runs],
        "prepare": result.prepare}, indent=2, default=str) + "\n", encoding="utf-8")
    if sign_key:
        from wattnot.receipt.sign import sign
        sign(path, no_rekor=True, key_path=sign_key)
    return {"receipt": r, "path": str(path)}


def read_setting(root: str | Path | None = None) -> float | None:
    """The approved adaptive mass, or None (= off). Serving code reads this and nothing else, so
    it only ever sees a setting a person approved and applied."""
    from wattnot.approvals.active import read_active

    a = read_active("moe_routing", root)
    cfg = (a or {}).get("config") or {}
    return float(cfg["adaptive_mass"]) if cfg.get("adaptive_topk") else None


# ---------------------------------------------------------------- fake backend

class _SimMeter:
    def __init__(self, backend: FakeMoeBackend) -> None:
        self.b, self.label = backend, ""

    def start(self, label: str) -> None:
        self.label, self.b.wh = label, 0.0

    def stop(self) -> Window:
        return Window(self.b.wh, 60.0, self.b.wh * 3600 / 60.0, f"traces/{self.label}.csv",
                      "0" * 64, "fake", "SIMULATED", "simulated")


class FakeMoeBackend:
    """Dry run: no GPU, no torch. Every number is SIMULATED (made up, not measured)."""

    simulated = True
    gpu = "H100 80GB (simulated)"
    driver = "simulated"

    def __init__(self, seed: int = 7, *, saving: float = 0.13, agreement: float = 96.2,
                 top_k: int = 8, fail: str | None = None) -> None:
        self.rng, self.saving, self.agree = random.Random(seed), saving, agreement
        self.top_k = top_k
        self.fail, self.arm, self.wh, self.used, self.slots = fail, "off", 0.0, 0.0, 0
        self.closed = False

    def prepare(self, deadline, clock) -> dict:
        if self.fail == "prepare":
            raise AdaptiveError("simulated failure while loading the model")
        return {"top_k": self.top_k, "sanity": {"hooked_off_matches_stock": True,
                                                "simulated": True}}

    def set_arm(self, arm: str) -> None:
        self.arm = arm

    def generate(self, prompts: list[str]) -> tuple[float, int]:
        if self.fail == "generate":
            raise AdaptiveError("simulated failure during generation")
        on = self.arm == "on"
        n = len(prompts) * 64
        self.wh += n * 0.00075 * (1 - self.saving if on else 1.0) * (1 + self.rng.gauss(0, 0.01))
        self.used += self.top_k * (0.85 if on else 1.0) * n
        self.slots += n
        return 2.0 * (0.96 if on else 1.0) * (1 + self.rng.uniform(-0.03, 0.03)), n

    def reset_counters(self) -> None:
        self.used, self.slots = 0.0, 0

    def active_experts(self) -> float | None:
        return self.used / self.slots if self.slots else None

    def agreement(self) -> dict:
        return {"pct": self.agree, "positions": 512, "method": "simulated"}

    def energy_meter(self) -> Meter:
        return _SimMeter(self)

    def close(self) -> None:
        self.closed = True
