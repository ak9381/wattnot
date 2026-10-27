"""A small planner loop for the GPU/CPU expert split (`--n-cpu-moe`).

llama.cpp keeps whole layers' experts on one side: `--n-cpu-moe N` leaves the first N
layers' experts in RAM and puts the other 62-N on the GPU. More on the GPU is faster until
VRAM runs out, and the failure costs a reload of a few minutes. So the loop is short:
at most `max_tries` tries, each one a server restart.

Who decides the next try:

1. Muse Spark gets a small JSON summary (knobs tried, tok/s, J per token, which loads
   failed) and answers with one next try or `stop`. Numbers only, no prompts.
2. Our code checks the answer: `n_cpu_moe` must be inside the VRAM-feasible range, above
   any value that already failed to load, and not a repeat. A bad answer is replaced by
   the fixed policy and the record says `fallback`.
3. If the Meta call is refused (spend guard) or fails, the fixed policy decides.

Quality is gated the same way as the stage: answers must agree with the baseline try
(UD-IQ2_S, all experts on CPU, op offload off) within 1 point, and p99 within 10%.
A saving only counts on the GPU+host boundary with host power MEASURED. Without RAPL the
host number is an ESTIMATE, the try is ranked on tok/s, and no saving is claimed.

    python -m wattnot.spark_open.tune --rig tiny --models-dir DIR --out OUT   # CPU dry run
    python -m wattnot.spark_open.tune --rig real --models-dir /data/spark-open --out OUT
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from wattnot.gates.fixed import pass_rate
from wattnot.receipt.canon import canonical_sha256
from wattnot.spark_open.gate import (
    P99_SLACK,
    QUALITY_DROP_PTS,
    Energy,
    agreement_pct,
    energy_verdict,
    p99,
)
from wattnot.spark_open.knobs import (
    DEFAULT_QUANT,
    DEFAULT_THREADS,
    N_LAYERS,
    SparkKnobs,
    baseline,
)
from wattnot.spark_open.planner import VRAM_GB, max_hot_layers, vram_estimate_gb

SPEC = "wattnot.spark-tune/1"
MODEL = "muse-spark-1.3"
MAX_TRIES = 6
VRAM_HEADROOM_GB = 2.0  # never ask for a try that the estimate puts above 78 GB
UBATCHES = (2048, 4096)
MAX_REASON = 200

CHOICE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "n_cpu_moe", "ubatch", "reason"],
    "properties": {
        "kind": {"enum": ["try", "stop"]},
        "n_cpu_moe": {"type": ["integer", "null"]},
        "ubatch": {"enum": [*UBATCHES, None]},
        "reason": {"type": "string", "maxLength": MAX_REASON},
    },
}

SYSTEM = (
    "You tune how many MoE layers keep their experts on the GPU for one llama.cpp server. "
    "n_cpu_moe is the number of layers whose experts stay in RAM: lower puts more on the GPU "
    "and is faster, until the GPU runs out of memory and the load fails. Pick ONE next try "
    "inside allowed_n_cpu_moe, above any value in failed_n_cpu_moe, and not already tried; or "
    "stop. Prefer higher tok_s; when joules_per_token_label is MEASURED, prefer lower "
    "joules_per_token. Reason: one short line quoting the number that drove the choice. "
    "JSON only."
)


@dataclass(frozen=True)
class Choice:
    kind: str  # try | stop
    n_cpu_moe: int | None = None
    ubatch: int | None = None
    reason: str = ""
    source: str = "fallback"  # muse-spark-1.3 | fallback | mock
    note: str = ""


# ---- the summary Muse Spark sees -----------------------------------------------------------


def feasible_range(quant: str, ubatch: int) -> tuple[int, int]:
    """Lowest n_cpu_moe whose VRAM ESTIMATE fits, and the highest (all experts on CPU)."""

    lo = N_LAYERS - max_hot_layers(quant, ubatch)
    while (
        lo < N_LAYERS
        and vram_estimate_gb(SparkKnobs(quant, lo, ubatch)) > VRAM_GB - VRAM_HEADROOM_GB
    ):
        lo += 1
    return lo, N_LAYERS


def summarize(tries: list[dict], quant: str, ubatch: int, left: int) -> dict:
    lo, hi = feasible_range(quant, ubatch)
    ok = [t for t in tries if t["ok"] and not t.get("is_baseline")]
    ranked = _rank(ok)
    return {
        "quant": quant,
        "allowed_n_cpu_moe": [lo, hi],
        "allowed_ubatch": list(UBATCHES),
        "tries_left": left,
        "failed_n_cpu_moe": sorted({t["knobs"]["n_cpu_moe"] for t in tries if not t["ok"]}),
        "rejected_n_cpu_moe": sorted(
            {
                t["knobs"]["n_cpu_moe"]
                for t in tries
                if t["ok"] and t.get("quality") and not t["quality"]["passed"]
            }
        ),
        "tried": [
            {
                "n_cpu_moe": t["knobs"]["n_cpu_moe"],
                "ubatch": t["knobs"]["ubatch"],
                "tok_s": t.get("tok_s"),
                "joules_per_token": t.get("j_per_token"),
                "joules_per_token_label": t.get("j_per_token_label"),
                "quality_ok": t["quality"]["passed"] if t.get("quality") else None,
                "loaded": t["ok"],
            }
            for t in tries[-6:]
        ],
        "best_so_far": ranked[0]["knobs"]["n_cpu_moe"] if ranked else None,
    }


# ---- who picks ----------------------------------------------------------------------------


def fallback_policy(s: Mapping) -> Choice:
    """Find the lowest n_cpu_moe that loads and passes, then compare the other ubatch.

    First try the most GPU-heavy value the VRAM ESTIMATE allows. A failed load or a failed
    quality gate raises the floor; after one, bisect between the floor and the best loaded value.
    """

    lo, hi = s["allowed_n_cpu_moe"]
    tried = {(t["n_cpu_moe"], t["ubatch"]) for t in s["tried"]}
    bad = [*s["failed_n_cpu_moe"], *s["rejected_n_cpu_moe"]]
    floor = max(lo, max(bad) + 1) if bad else lo
    good = [t["n_cpu_moe"] for t in s["tried"] if t["loaded"] and t["quality_ok"]]
    upper = min(good) if good else hi
    if s["tries_left"] <= 0:
        return Choice("stop", reason="no tries left")
    ub = UBATCHES[-1]
    if floor < upper:
        n = floor if not bad else floor + (upper - floor) // 2
        while (n, ub) in tried and n < upper:
            n += 1
        if n < upper:
            why = (
                "first try at the most GPU-heavy value that fits the VRAM estimate"
                if not bad
                else f"bisect between {floor} (not ruled out) and {upper} (loads)"
            )
            return Choice("try", n, ub, f"{why}: {n}")
    other = next((u for u in UBATCHES if (upper, u) not in tried), None) if good else None
    if other is not None:
        return Choice("try", upper, other, f"split settled at {upper}; compare ubatch {other}")
    return Choice("stop", reason=f"n_cpu_moe={upper} is the lowest that loads and passes")


def validate(raw: Mapping, s: Mapping) -> tuple[Choice | None, str]:
    """Check a proposed choice. Returns (choice, "") or (None, why it was refused)."""

    kind = raw.get("kind")
    reason = " ".join(str(raw.get("reason", "")).split())[:MAX_REASON]
    if not reason:
        return None, "no reason given"
    if kind == "stop":
        return Choice("stop", reason=reason), ""
    if kind != "try":
        return None, f"unknown kind {kind!r}"
    n, ub = raw.get("n_cpu_moe"), raw.get("ubatch")
    lo, hi = s["allowed_n_cpu_moe"]
    if not isinstance(n, int) or isinstance(n, bool) or not lo <= n <= hi:
        return None, f"n_cpu_moe {n!r} outside {lo}..{hi}"
    if ub not in UBATCHES:
        return None, f"ubatch {ub!r} not in {list(UBATCHES)}"
    bad = [*s["failed_n_cpu_moe"], *s["rejected_n_cpu_moe"]]
    if bad and n <= max(bad):
        return None, f"n_cpu_moe {n} is at or below a value that already failed"
    if (n, ub) in {(t["n_cpu_moe"], t["ubatch"]) for t in s["tried"]}:
        return None, "repeat of an earlier try"
    return Choice("try", n, ub, reason), ""


Transport = Callable[[dict], dict]


def request_body(summary: Mapping, *, model: str = MODEL, max_tokens: int = 400) -> dict:
    return {
        "model": model,
        "reasoning_effort": "minimal",
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(summary, separators=(",", ":"))},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "NextTry", "schema": CHOICE_SCHEMA, "strict": True},
        },
    }


def guarded_transport(purpose: str = "spark1gpu-tune", ledger=None) -> Transport:
    """Live Muse Spark through the spend guard. A refusal raises, and the loop falls back."""

    from wattnot.meta.client import GuardedClient

    client = GuardedClient(ledger=ledger, timeout_s=30)
    return lambda body: client(body, purpose=purpose)


def mock_transport(body: dict) -> dict:
    """MOCK planner for the dry run: answers like a sensible model, makes no call."""

    s = json.loads(body["messages"][1]["content"])
    c = fallback_policy(s)
    out = {
        "kind": c.kind,
        "n_cpu_moe": c.n_cpu_moe,
        "ubatch": c.ubatch,
        "reason": "MOCK: " + c.reason,
    }
    return {
        "choices": [{"message": {"content": json.dumps(out)}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "model": "mock",
    }


class Planner:
    def __init__(self, transport: Transport | None, *, source: str = MODEL) -> None:
        self.transport, self.source = transport, source
        self.calls = 0
        self.tokens = {"input": 0, "output": 0}
        self.errors: list[str] = []

    def choose(self, s: Mapping) -> Choice:
        if self.transport is None:
            return fallback_policy(s)
        try:
            self.calls += 1
            raw = self.transport(request_body(s))
            u = raw.get("usage") or {}
            self.tokens["input"] += int(u.get("prompt_tokens", 0))
            self.tokens["output"] += int(u.get("completion_tokens", 0))
            data = json.loads(raw["choices"][0]["message"]["content"])
        except Exception as e:  # refusal, network, bad JSON: the fixed policy decides
            self.errors.append(type(e).__name__)
            c = fallback_policy(s)
            return Choice(
                c.kind,
                c.n_cpu_moe,
                c.ubatch,
                c.reason,
                "fallback",
                f"planner call failed ({type(e).__name__})",
            )
        ch, why = validate(data, s)
        if ch is None:
            c = fallback_policy(s)
            return Choice(
                c.kind,
                c.n_cpu_moe,
                c.ubatch,
                c.reason,
                "fallback",
                f"planner answer refused: {why}",
            )
        return Choice(ch.kind, ch.n_cpu_moe, ch.ubatch, ch.reason, self.source)


def quality_gate(items, base: dict, r: dict) -> dict:
    """Fixed answer checks, not exact text. Free text drifts a little with batch size and
    thread count even at temperature 0, so exact agreement is reported but does not decide.
    A try passes when its pass rate drops at most 1 point and p99 stays within 10%."""

    bp, n = pass_rate(base["outputs"], items, truncated=base.get("truncated"))
    cp, _ = pass_rate(r["outputs"], items, truncated=r.get("truncated"))
    rp, c99 = p99(base["latencies"]), p99(r["latencies"])
    p99_ok = rp <= 0 or c99 <= rp * (1 + P99_SLACK)
    drop = round(bp - cp, 3)
    return {
        "gate": "fixed answer checks",
        "checked_items": n,
        "base_pass_pct": round(bp, 3),
        "pass_pct": round(cp, 3),
        "drop_pts": drop,
        "quality_ok": drop <= QUALITY_DROP_PTS,
        "agreement_pct": round(agreement_pct(base["outputs"], r["outputs"]), 3),
        "p99_s": round(c99, 4),
        "ref_p99_s": round(rp, 4),
        "p99_ok": p99_ok,
        "passed": drop <= QUALITY_DROP_PTS and p99_ok,
    }


# ---- ranking and records ------------------------------------------------------------------


def _rank(ok: list[dict]) -> list[dict]:
    """Quality-passing tries first; whole-node J/token when MEASURED, else tok/s."""

    good = [t for t in ok if t["quality"] and t["quality"]["passed"]] or []
    if good and all(t.get("j_per_token_label") == "MEASURED" for t in good):
        return sorted(good, key=lambda t: (t["j_per_token"], -(t["tok_s"] or 0)))
    return sorted(good, key=lambda t: (-(t["tok_s"] or 0), t["knobs"]["n_cpu_moe"]))


def _record(
    knobs: SparkKnobs,
    r: dict | None,
    err: str,
    base: dict | None,
    choice: Choice,
    *,
    simulated: bool,
    prev: str | None,
    items=(),
) -> dict:
    rec: dict = {
        "spec": SPEC,
        "knobs": knobs.as_dict(),
        "ok": r is not None,
        "error": err or None,
        "vram_est_gb": vram_estimate_gb(knobs),
        "vram_label": "ESTIMATE",
        "chosen_by": {"source": choice.source, "reason": choice.reason, "note": choice.note},
        "prev_sha256": prev,
    }
    if r is not None:
        tok = r["out_tokens"]
        host_measured = r.get("host_label") == "MEASURED"
        gpu_ok = r["gpu_j"] is not None
        whole = (r["gpu_j"] + r["host_j"]) if (gpu_ok and host_measured) else None
        rec.update(
            {
                "tok_s": r["tok_s"],
                "tok_s_label": "SIMULATED" if simulated else "MEASURED",
                "load_s": round(r["load_s"], 1),
                "out_tokens": tok,
                "wall_s": r.get("wall_s"),
                "energy": {
                    "gpu_j": r["gpu_j"],
                    "gpu_label": r["gpu_label"],
                    "host_j": r["host_j"],
                    "host_label": r["host_label"],
                    "host_method": r.get("host_method"),
                    "boundary": "GPU+host" if whole is not None else "GPU-only ESTIMATE",
                },
                "j_per_token": None if whole is None or not tok else round(whole / tok, 4),
                "j_per_token_label": "MEASURED" if whole is not None else "ESTIMATE",
            }
        )
        if base is None:
            rec.update(is_baseline=True, quality=None, savings=None, billable=False)
        else:
            q = quality_gate(items, base, r)
            e = energy_verdict(
                Energy(
                    base["gpu_j"] or 0.0,
                    base["host_j"] if base.get("host_label") == "MEASURED" else None,
                    base["out_tokens"],
                ),
                Energy(r["gpu_j"] or 0.0, r["host_j"] if host_measured else None, r["out_tokens"]),
                simulated=simulated,
            )
            if not gpu_ok or base["gpu_j"] is None:
                e.update(
                    counted=False,
                    total_saving_pct=None,
                    label="ESTIMATE",
                    reason="no GPU power reading on this box; nothing is counted",
                )
            rec.update(
                is_baseline=False, quality=q, savings=e, billable=bool(q["passed"] and e["counted"])
            )
    rec["sha256"] = canonical_sha256(rec)
    return rec


def run_loop(
    rig,
    out: Path,
    *,
    items=(),
    quant: str = DEFAULT_QUANT,
    planner: Planner | None = None,
    max_tries: int = MAX_TRIES,
    threads: int = DEFAULT_THREADS,
    simulated: bool = False,
    label: str = "",
) -> dict:
    """Try 0 is the baseline; then up to `max_tries` planner-chosen tries."""

    out.mkdir(parents=True, exist_ok=True)
    planner = planner or Planner(None)
    recs: list[dict] = []
    base_knobs = (
        SparkKnobs(quant, N_LAYERS, 512, False, threads) if quant != DEFAULT_QUANT else baseline()
    )
    try:
        base = rig.run(base_knobs)
        err = ""
    except Exception as e:  # the baseline must load; if not, there is nothing to compare
        base, err = None, f"{type(e).__name__}: {e}"[:160]
    first = Choice("try", N_LAYERS, 512, "baseline: all experts on CPU, op offload off", "fixed")
    recs.append(
        _record(base_knobs, base, err, None, first, simulated=simulated, prev=None, items=items)
    )
    stopped = "baseline failed to load" if base is None else "max tries"
    for i in range(max_tries if base is not None else 0):
        s = summarize(recs, quant, 4096, max_tries - i)
        c = planner.choose(s)
        if c.kind == "stop":
            stopped = f"planner stopped: {c.reason}"
            break
        k = SparkKnobs(quant, c.n_cpu_moe, c.ubatch, True, threads)
        try:
            r, err = rig.run(k), ""
        except Exception as e:  # a load failure (OOM) is a result, not a crash
            r, err = None, f"{type(e).__name__}: {e}"[:160]
        recs.append(
            _record(k, r, err, base, c, simulated=simulated, prev=recs[-1]["sha256"], items=items)
        )
    best = (_rank([t for t in recs if t["ok"] and not t.get("is_baseline")]) or [None])[0]
    summary = {
        "spec": SPEC,
        "stage": "spark_open_tune",
        "label": label or ("SIMULATED" if simulated else "MEASURED"),
        "quant": quant,
        "tries": recs,
        "stopped": stopped,
        "planner": {
            "source": planner.source if planner.transport else "fallback",
            "calls": planner.calls,
            "tokens": planner.tokens,
            "errors": planner.errors,
        },
        "best": None
        if best is None
        else {
            "knobs": best["knobs"],
            "tok_s": best["tok_s"],
            "tok_s_label": best["tok_s_label"],
            "j_per_token": best["j_per_token"],
            "j_per_token_label": best["j_per_token_label"],
            "ranked_by": "J per token (GPU+host MEASURED)"
            if best["j_per_token_label"] == "MEASURED"
            else "tok/s (energy is GPU-only ESTIMATE, no saving claimed)",
            "billable": best["billable"],
            "quality": best["quality"],
        },
    }
    (out / "tune.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None) -> int:  # pragma: no cover - thin CLI, exercised by the dry-run script
    ap = argparse.ArgumentParser(prog="wattnot.spark_open.tune")
    ap.add_argument(
        "--rig",
        choices=("tiny", "real"),
        default="tiny",
        help="tiny = CPU dry run on a small GGUF; real = the GPU box",
    )
    ap.add_argument("--models-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--quant", default=DEFAULT_QUANT)
    ap.add_argument("--planner", choices=("fallback", "mock", "muse"), default="fallback")
    ap.add_argument("--max-tries", type=int, default=MAX_TRIES)
    ap.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    ap.add_argument("--items", type=int, default=6)
    a = ap.parse_args(argv)

    from wattnot.runner.workload import load_workload, workloads_dir
    from wattnot.spark_open.bench import LocalRig

    items = list(load_workload(workloads_dir() / "cloud-effort-v1.jsonl"))[: a.items]
    out = Path(a.out)
    transport = {"fallback": None, "mock": mock_transport, "muse": None}[a.planner]
    if a.planner == "muse":
        transport = guarded_transport()
    planner = Planner(transport, source="mock" if a.planner == "mock" else MODEL)
    rig = LocalRig(Path(a.models_dir), items, out, resident_gb=1.0 if a.rig == "tiny" else 250.0)
    s = run_loop(
        rig,
        out,
        items=items,
        quant=a.quant,
        planner=planner,
        max_tries=a.max_tries,
        threads=a.threads,
        label="MEASURED (tiny CPU model: plumbing check only)" if a.rig == "tiny" else "MEASURED",
    )
    b = s["best"]
    print(
        f"tune: {len(s['tries'])} tries, stopped: {s['stopped']}; best n_cpu_moe="
        f"{b and b['knobs']['n_cpu_moe']} tok/s={b and b['tok_s']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
