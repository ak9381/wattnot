"""The Spark Open side stage: 2 quants x 3 VRAM expert budgets on one H100.

    python -m wattnot.spark_open --rig fake --out results/x     # SIMULATED, no GPU
    python -m wattnot.spark_open --rig real --out results/x     # on the box

Each try restarts llama-server, replays a short prompt set, and writes a
hash-chained record with GPU and host energy, the quality gate, and the
load-shift check. The stage skips itself (exit 0) on low disk or RAM.
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
from wattnot.spark_open.knobs import QUANTS, REPO, STAGE_QUANTS, SparkKnobs, grid
from wattnot.spark_open.llama_server import LlamaServer

MIN_DISK_GB = 500
MIN_RAM_GB = 200
SPEC = "wattnot.spark-open-try/v1"


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
    need = sum(QUANTS[q].approx_gb for q in quants if q not in have) * 1.05
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
        vram_frac = 1 - knobs.n_cpu_moe / 62
        low = knobs.quant == "UD-IQ1_S"
        tok = 128 * self.n_items
        gpu_j = tok * (2.0 + 1.5 * vram_frac) * (0.85 if low else 1.0)
        host_j = tok * (6.0 - 3.0 * vram_frac) * (0.8 if low else 1.0)
        if low and knobs.n_cpu_moe == 62:  # GPU falls, CPU climbs: the shift trap
            gpu_j, host_j = tok * 1.2, tok * 7.0
        wrong = 4 if low else 0
        outs = {f"q{i}": ("other" if i < wrong else f"answer {i}") for i in range(self.n_items)}
        lat = [20.0 / (1 + vram_frac) * (0.9 if low else 1.0)] * self.n_items
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

    def download(self, quant: str) -> None:
        import subprocess
        if any((self.models_dir / quant).glob("*.gguf")):
            return
        subprocess.run(["huggingface-cli", "download", REPO, "--include", f"*{quant}*",
                        "--local-dir", str(self.models_dir)], check=True)
        # unsloth splits shards under a <quant>/ folder already
    def run(self, knobs: SparkKnobs) -> dict:
        self.download(knobs.quant)
        load_s = self.server.start(knobs)
        client = self._client(self.server.base_url, "spark-open", chat=True, timeout_s=900)
        meter = self._meter.open(f"spark-{knobs.quant}-{knobs.n_cpu_moe}",
                                 traces_root=self.out / "traces")
        rapl_ok = self.rapl.available
        h0 = self.rapl.read() if rapl_ok else None
        meter.start(f"spark-{knobs.quant}-{knobs.n_cpu_moe}")
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


def run_stage(rig, out: Path, *, tries=None, reference: HostedReference | None = None,
              ref_outputs: dict | None = None) -> dict:
    """Run every try; the first try (UD-IQ2_S, all experts on CPU) is the baseline."""

    out.mkdir(parents=True, exist_ok=True)
    tries = tries or grid()
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
                          "host_label": host_label, "out_tokens": r["out_tokens"]},
               "quality": q, "savings": e, "load_s": round(r["load_s"], 1),
               "reference": "hosted" if ref_outputs else "local baseline (UD-IQ2_S, CPU experts)",
               "billable": bool(q["passed"] and e["counted"] and r is not base),
               "prev_sha256": prev}
        rec["sha256"] = canonical_sha256(rec)
        prev = rec["sha256"]
        records.append(rec)
    summary = {"stage": "spark_open", "simulated": rig.simulated, "tries": records,
               "reference_config": reference.describe(),
               "best": _best(records)}
    (out / "spark_open.json").write_text(json.dumps(summary, indent=2))
    return summary


def _best(records) -> dict | None:
    ok = [r for r in records if r["billable"]]
    if not ok:
        return None
    b = max(ok, key=lambda r: r["savings"]["total_saving_pct"] or 0)
    return {"knobs": b["knobs"], "total_saving_pct": b["savings"]["total_saving_pct"],
            "label": b["savings"]["label"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wattnot.spark_open")
    ap.add_argument("--rig", choices=("fake", "real"), default="fake")
    ap.add_argument("--out", required=True)
    ap.add_argument("--models-dir", default="/data/spark-open")
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out) / "spark_open"
    if a.rig == "fake":
        s = run_stage(FakeSparkRig(n_items=5 if a.quick else 20), out)
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
        s = run_stage(RealSparkRig(out, Path(a.models_dir), items), out,
                      reference=HostedReference.from_env())
    n = sum(r["billable"] for r in s["tries"])
    print(f"spark_open: {len(s['tries'])} tries, {n} billable, best={s['best']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
