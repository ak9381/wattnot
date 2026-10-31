"""Preflight: what this machine can do, read-only.

Reports the NVIDIA driver, CUDA version, whether power caps can be set, and the vLLM version,
then lists every WattNot layer as available or greyed out with the reason. It never writes a
power cap or a clock: permission is judged from who we run as and where, not by trying.

On a machine with no GPU it says so and still lists the layers that need no GPU (routing,
agent waste saver, Muse Code PRs, proof and receipts).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

from wattnot.hostinfo import detect_provider, detect_vllm_version

SERVERLESS = ("modal", "baseten")


def _smi(run: Callable, args: list[str]) -> str | None:
    try:
        r = run(["nvidia-smi", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def gpu_info(run: Callable = subprocess.run, which: Callable = shutil.which) -> dict[str, Any]:
    if which("nvidia-smi") is None:
        return {"present": False, "reason": "no nvidia-smi on PATH (no NVIDIA driver or no GPU)"}
    q = _smi(run, ["--query-gpu=name,driver_version,power.limit,power.min_limit,power.max_limit",
                   "--format=csv,noheader,nounits"])
    if not q or not q.strip():
        return {"present": False, "reason": "nvidia-smi found but reports no GPU"}
    gpus = []
    for ln in q.strip().splitlines():
        f = [x.strip() for x in ln.split(",")]
        gpus.append({"name": f[0], "driver": f[1] if len(f) > 1 else None,
                     "power_limit_w": f[2] if len(f) > 2 else None,
                     "power_range_w": f"{f[3]}-{f[4]}" if len(f) > 4 else None})
    head = _smi(run, []) or ""
    m = re.search(r"CUDA Version:\s*([\d.]+)", head)
    return {"present": True, "gpus": gpus, "driver": gpus[0]["driver"],
            "cuda": m.group(1) if m else None}


def power_cap_permission(gpu: dict, provider: str, euid: int | None = None) -> dict[str, Any]:
    if not gpu.get("present"):
        return {"allowed": False, "reason": "no GPU"}
    if provider in SERVERLESS:
        return {"allowed": False, "reason": f"{provider} is serverless; the platform owns the "
                "power limit (readings still work)"}
    if any((g.get("power_limit_w") or "").startswith("[N/A") for g in gpu.get("gpus", [])):
        return {"allowed": False, "reason": "the driver does not expose a power limit"}
    euid = os.geteuid() if euid is None and hasattr(os, "geteuid") else euid
    if euid == 0:
        return {"allowed": True, "reason": "running as root; not tested by writing (read-only check)"}
    if shutil.which("sudo"):
        return {"allowed": None, "reason": "needs root; sudo is present, so likely with sudo "
                "(not tested: preflight never writes)"}
    return {"allowed": False, "reason": "needs root to set a power limit"}


def features(gpu: dict, cap: dict, vllm: str | None) -> list[dict[str, Any]]:
    g = gpu.get("present")
    f = [
        ("power_caps", "Hardware: GPU power caps", cap["allowed"] is not False, cap["reason"]),
        ("clock_tuning", "Hardware: prefill/decode clock tuning", cap["allowed"] is not False,
         cap["reason"] if cap["allowed"] is False else "same permission as power caps"),
        ("power_readings", "Proof: GPU power readings", bool(g),
         "nvidia-smi present" if g else gpu.get("reason")),
        ("serving", "Serving: batching, KV cache, prefix cache, quantization", bool(vllm),
         f"vLLM {vllm}" if vllm else "vLLM not installed here (use the sidecar or our image)"),
        ("routing", "Routing: cheapest backend that still passes", True, "no GPU needed"),
        ("agent_waste", "Agents: waste saver proxy", True, "no GPU needed"),
        ("musecode", "Code: Muse Code sandboxed PRs", True, "no GPU needed; needs a Meta key to run"),
        ("receipts", "Proof: quality + p99 checks, signed receipts", True, "no GPU needed"),
    ]
    return [{"id": i, "title": t, "available": bool(a), "reason": r} for i, t, a, r in f]


def run(*, runner: Callable = subprocess.run, which: Callable = shutil.which,
        env=None, euid: int | None = None) -> dict[str, Any]:
    provider = detect_provider(env)
    gpu = gpu_info(runner, which)
    cap = power_cap_permission(gpu, provider, euid)
    vllm = detect_vllm_version()
    return {"format": "wattnot.preflight/1", "provider": provider, "gpu": gpu,
            "driver": gpu.get("driver"), "cuda": gpu.get("cuda"), "power_cap": cap,
            "vllm": vllm, "features": features(gpu, cap, vllm)}


def render(r: dict) -> str:
    g = r["gpu"]
    lines = ["WattNot preflight (read-only)",
             f"  host      {r['provider']}",
             f"  GPU       {', '.join(x['name'] for x in g['gpus']) if g.get('present') else 'none: ' + g['reason']}",
             f"  driver    {r['driver'] or '-'}",
             f"  CUDA      {r['cuda'] or '-'}",
             f"  power cap {'yes' if r['power_cap']['allowed'] else 'maybe' if r['power_cap']['allowed'] is None else 'no'}: {r['power_cap']['reason']}",
             f"  vLLM      {r['vllm'] or 'not installed'}", "", "Layers:"]
    for f in r["features"]:
        lines.append(f"  [{'on ' if f['available'] else 'off'}] {f['title']}: {f['reason']}")
    return "\n".join(lines)
