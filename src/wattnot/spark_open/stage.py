"""The Spark Open side stage: the llama.cpp three-tier hybrid on one H100.

About 32 tries (4 quants x 4 hot-layer levels x 2 batch modes, see planner.py)
inside the 3-4.5 h side-run budget. Tiers are per layer (llama.cpp), not per
expert.

    python -m wattnot.spark_open --rig fake --out results/x     # SIMULATED, no GPU
    python -m wattnot.spark_open --rig real --out results/x     # on the box

Each try restarts llama-server, replays a short prompt set, and writes a
hash-chained record with GPU and host energy, the CPU share of energy, the
per-tier split, the quality gate, and the load-shift check.
The stage skips itself (exit 0) on low disk or RAM.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from wattnot.receipt.canon import canonical_sha256
from wattnot.spark_open.gate import Energy, HostedReference, energy_verdict, quality_verdict
from wattnot.spark_open.hostpower import Rapl, boundary
from wattnot.spark_open.knobs import N_LAYERS, QUANTS, REPO, STAGE_QUANTS, SparkKnobs
from wattnot.spark_open.llama_server import LlamaServer
from wattnot.spark_open.planner import Plan, plan, vram_estimate_gb

MIN_DISK_GB = 550  # baseline quant kept + one other at a time (others evicted)
MIN_RAM_GB = 200
SPEC = "wattnot.spark-open-try/v2"


@dataclass
class Preflight:
    ok: bool
    reason: str
    disk_free_gb: float
    ram_gb: float


def preflight(models_dir: Path, *, disk_free_gb: float | None = None,
              ram_gb: float | None = None, quants=STAGE_QUANTS) -> Preflight:
    if disk_free_gb is None:
        models_dir.mkdir(parents=True, exist_ok=True)
        disk_free_gb = shutil.disk_usage(models_dir).free / 1e9
    if ram_gb is None:
        ram_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    have = {q for q in quants if any((models_dir / q).glob("*.gguf"))}
    # Quants run one after another and each non-baseline quant is deleted when
    # its tries finish, so disk must hold the baseline plus the largest other.
    missing = [QUANTS[q].approx_gb for q in quants if q not in have]
    need = (QUANTS[quants[0]].approx_gb if quants[0] not in have else 0)
    need = (need + max(missing, default=0)) * 1.05
    need = max(need, MIN_DISK_GB if not have else 0)
    if disk_free_gb < need:
        return Preflight(False, f"low disk: {disk_free_gb:.0f} GB free, need {need:.0f} GB",
                         disk_free_gb, ram_gb)
    if ram_gb < MIN_RAM_GB:
        return Preflight(False, f"low RAM: {ram_gb:.0f} GB, need {MIN_RAM_GB} GB",
                         disk_free_gb, ram_gb)
    return Preflight(True, "ok", disk_free_gb, ram_gb)


class FakeSparkRig:
    """Deterministic numbers for tests and --dry-run. Everything is SIMULATED.

    The model is simple on purpose: more experts in VRAM means less CPU work and
    lower host joules; the 1-bit quant loses agreement. One cell is built to
    look like a GPU saving that is really a shift to the CPU.
    """

    simulated = True

    def __init__(self, n_items: int = 20, host_power: bool = True) -> None:
        self.n_items, self.host_power = n_items, host_power

    def run(self, knobs: SparkKnobs) -> dict:
        vram_frac = knobs.hot_layers / N_LAYERS
        oo = knobs.op_offload
        qf = {"UD-IQ1_S": 0.8, "UD-IQ1_M": 0.85, "UD-IQ2_XXS": 0.9}.get(knobs.quant, 1.0)
        tok = 128 * self.n_items
        gpu_j = tok * (2.0 + 4.0 * vram_frac + (1.0 if oo else 0.0)) * (0.5 + qf / 2)
        host_j = tok * (6.0 - 6.0 * vram_frac - (1.5 if oo else 0.0)
                        - (0.1 * knobs.ubatch / 4096 if oo else 0.0)) * qf
        host_j *= 1 + (knobs.threads - 24) * 0.005
        if knobs.quant == "UD-IQ1_S" and knobs.n_cpu_moe == N_LAYERS and not oo:
            gpu_j, host_j = tok * 1.2, tok * 7.0  # GPU falls, CPU climbs: the shift trap
        wrong = {"UD-IQ1_S": 4, "UD-IQ1_M": 3, "UD-IQ2_XXS": 2}.get(knobs.quant, 0)
        outs = {f"q{i}": ("other" if i < wrong else f"answer {i}") for i in range(self.n_items)}
        lat = [20.0 / (1 + vram_frac) / (1.3 if oo else 1.0) * (0.5 + qf / 2)] * self.n_items
        return {"outputs": outs, "latencies": lat, "gpu_j": gpu_j,
                "host_j": host_j if self.host_power else None, "out_tokens": tok,
                "load_s": 0.0}


class RealSparkRig:  # pragma: no cover - needs the GPU box
    simulated = False

    def __init__(self, out: Path, models_dir: Path, items) -> None:
        from wattnot.meter.nvml import NvmlMeter
        from wattnot.runner.vllm_server import OpenAIClient
        self.server = LlamaServer(models_dir, log_dir=out / "logs")
        self.items, self.out, self.models_dir = items, out, models_dir
        self.rapl = Rapl()
        self._meter, self._client = NvmlMeter, OpenAIClient
        self._last_quant: str | None = None

    def download(self, quant: str) -> None:
        import subprocess
        if any((self.models_dir / quant).glob("*.gguf")):
            return
        subprocess.run(["huggingface-cli", "download", REPO, "--include", f"*{quant}*",
                        "--local-dir", str(self.models_dir)], check=True)
        # unsloth splits shards under a <quant>/ folder already

    def evict(self, quant: str) -> None:
        """Delete a finished quant to make room (only files we downloaded)."""
        d = self.models_dir / quant
        if d.is_dir() and quant != STAGE_QUANTS[0]:
            shutil.rmtree(d)

    def run(self, knobs: SparkKnobs) -> dict:
        if self._last_quant and self._last_quant != knobs.quant:
            self.evict(self._last_quant)
        self._last_quant = knobs.quant
        self.download(knobs.quant)
        load_s = self.server.start(knobs)
        client = self._client(self.server.base_url, "spark-open", chat=True, timeout_s=900)
        meter = self._meter.open(f"spark-{knobs.tag}",
                                 traces_root=self.out / "traces")
        rapl_ok = self.rapl.available
        h0 = self.rapl.read() if rapl_ok else None
        meter.start(f"spark-{knobs.tag}")
        outs, lat, tok = {}, [], 0
        for it in self.items:
            c = client.complete(it)
            outs[it.id], tok = c.text, tok + c.completion_tokens
            lat.append(c.latency_s)
        w = meter.stop()
        host_j = self.rapl.joules_between(h0, self.rapl.read()) if rapl_ok else None
        self.server.stop()
        return {"outputs": outs, "latencies": lat, "gpu_j": w.wh * 3600, "host_j": host_j,
                "out_tokens": tok, "load_s": load_s, "gpu_label": w.label,
                "trace_sha256": w.sha256}


def cpu_share(gpu_j: float, host_j: float | None) -> float | None:
    """CPU package + DRAM share of whole-node energy, in percent."""
    if host_j is None or gpu_j + host_j <= 0:
        return None
    return round(100 * host_j / (gpu_j + host_j), 2)


def run_stage(rig, out: Path, *, tries=None, the_plan: Plan | None = None,
              reference: HostedReference | None = None,
              ref_outputs: dict | None = None) -> dict:
    """Run every try; try 0 (UD-IQ2_S, all experts on CPU, op offload off) is the
    baseline."""

    out.mkdir(parents=True, exist_ok=True)
    the_plan = the_plan or plan()
    tries = tries or the_plan.tries
    reference = reference or HostedReference()
    records, prev = [], None
    base = None
    for knobs in tries:
        r = rig.run(knobs)
        if base is None:
            base = r
        ref_o = ref_outputs or base["outputs"]
        q = quality_verdict(ref_o, base["latencies"], r["outputs"], r["latencies"])
        e = energy_verdict(Energy(base["gpu_j"], base["host_j"], base["out_tokens"]),
                           Energy(r["gpu_j"], r["host_j"], r["out_tokens"]),
                           simulated=rig.simulated)
        host_label = ("SIMULATED" if rig.simulated else
                      "MEASURED" if r["host_j"] is not None else "ESTIMATE")
        rec = {"spec": SPEC, "knobs": knobs.as_dict(), "is_baseline": r is base,
               "boundary": boundary(r["host_j"] is not None),
               "energy": {"gpu_j": round(r["gpu_j"], 3),
                          "gpu_label": "SIMULATED" if rig.simulated
                          else r.get("gpu_label", "ESTIMATE"),
                          "host_j": None if r["host_j"] is None else round(r["host_j"], 3),
                          "host_label": host_label, "out_tokens": r["out_tokens"],
                          "cpu_share_pct": cpu_share(r["gpu_j"], r["host_j"]),
                          "cpu_share_label": (host_label if r["host_j"] is not None
                                              else "unavailable (GPU-only)")},
               "tiers": {**knobs.tiers(), "vram_est_gb": vram_estimate_gb(knobs),
                         "vram_label": "ESTIMATE"},
               "quality": q, "savings": e, "load_s": round(r["load_s"], 1),
               "reference": ("hosted" if ref_outputs else
                             "local baseline (UD-IQ2_S, CPU experts, op offload off)"),
               "billable": bool(q["passed"] and e["counted"] and r is not base),
               "prev_sha256": prev}
        rec["sha256"] = canonical_sha256(rec)
        prev = rec["sha256"]
        records.append(rec)
    summary = {"stage": "spark_open", "simulated": rig.simulated, "tries": records,
               "reference_config": reference.describe(),
               "plan": the_plan.describe(),
               "placement": "per layer (llama.cpp --n-cpu-moe); per-expert pinning not used",
               "best": _best(records)}
    (out / "spark_open.json").write_text(json.dumps(summary, indent=2))
    return summary


def _best(records) -> dict | None:
    ok = [r for r in records if r["billable"]]
    if not ok:
        return None
    b = max(ok, key=lambda r: r["savings"]["total_saving_pct"] or 0)
    return {"knobs": b["knobs"], "total_saving_pct": b["savings"]["total_saving_pct"],
            "cpu_share_pct": b["energy"]["cpu_share_pct"], "tiers": b["tiers"],
            "label": b["savings"]["label"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wattnot.spark_open")
    ap.add_argument("--rig", choices=("fake", "real"), default="fake")
    ap.add_argument("--out", required=True)
    ap.add_argument("--models-dir", default="/data/spark-open")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--budget-h", type=float, default=4.5)
    ap.add_argument("--threads", type=int, default=24)
    ap.add_argument("--gpu-ubatch", type=int, default=4096, choices=(512, 2048, 4096))
    a = ap.parse_args(argv)
    out = Path(a.out) / "spark_open"
    the_plan = plan(budget_h=a.budget_h, gpu_ubatch=a.gpu_ubatch, threads=a.threads)
    if a.rig == "fake":
        s = run_stage(FakeSparkRig(n_items=5 if a.quick else 20), out, the_plan=the_plan)
    else:  # pragma: no cover - GPU box
        pf = preflight(Path(a.models_dir))
        if not pf.ok:
            out.mkdir(parents=True, exist_ok=True)
            (out / "spark_open.json").write_text(json.dumps(
                {"stage": "spark_open", "skipped": True, "reason": pf.reason}, indent=2))
            print(f"spark_open skipped: {pf.reason}")
            return 0
        from wattnot.runner.workload import load_replay_set
        items = list(load_replay_set())[: 10 if a.quick else 30]
        s = run_stage(RealSparkRig(out, Path(a.models_dir), items), out, the_plan=the_plan,
                      reference=HostedReference.from_env())
    n = sum(r["billable"] for r in s["tries"])
    print(f"spark_open: {len(s['tries'])} tries, {n} billable, est {s['plan']['est_hours']} h "
          f"(ESTIMATE), best={s['best'] and s['best']['knobs']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
