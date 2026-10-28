"""Power cross-check: compare the NVML energy counter against independent power sources.

NVML is our primary meter. This module checks it against whatever else the box exposes, so a
reader can see the energy number is not an artefact of one driver path:

* ``nvidia-smi`` — always tried (it is present wherever NVML is). GPU-scope power draw.
* ``dcgmi`` (DCGM / nv-hostengine) — tried only if the binary is on PATH. GPU-scope.
* ``ipmitool dcmi power reading`` — tried only if the BMC exposes DCMI. Whole-node power, so a
  large positive gap against GPU-only NVML is expected and labelled as such.

Each source is optional and auto-detected. For every source we turn a mean-watts reading into
energy over the measured window and report the gap against NVML energy as a percent. On a CPU box
with no GPU, ``mock=True`` substitutes synthetic readers so the whole path runs and is testable.

Nothing here reads secrets, and every number carries a label: MEASURED for a live source, MOCK in
mock mode. Energy derived from a mean-power reading is an ESTIMATE of that source's energy (we do
not get an energy counter from nvidia-smi or IPMI), but the comparison against NVML is the point.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass

SPEC = "wattnot.power-crosscheck/1"
# A GPU-scope source agreeing with NVML to within this percent is "ok"; node-scope is informational.
GPU_GAP_OK_PCT = 5.0
_RUN = Callable[[list[str]], str]


def _run_text(cmd: list[str], timeout: float = 5.0) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,  # noqa: S603
                          check=True).stdout


@dataclass(frozen=True)
class Source:
    name: str            # nvidia-smi | dcgm | ipmi
    scope: str           # gpu | node
    read: Callable[[], float | None]  # mean watts over the window, or None if unavailable


def nvidia_smi_w(run: _RUN = _run_text) -> float | None:
    """Total GPU board power (W), summed across visible GPUs. None if nvidia-smi is absent."""
    if run is _run_text and not shutil.which("nvidia-smi"):
        return None
    try:
        out = run(["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"])
    except Exception:
        return None
    vals = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+", out)]
    return round(sum(vals), 3) if vals else None


def dcgm_w(run: _RUN = _run_text) -> float | None:
    """Total GPU power (W) from DCGM field 155. None if dcgmi is absent or unparseable."""
    if run is _run_text and not shutil.which("dcgmi"):
        return None
    try:
        out = run(["dcgmi", "dmon", "-e", "155", "-c", "1"])
    except Exception:
        return None
    vals = []
    for line in out.splitlines():
        if line.strip().startswith(("#", "GPU-Id", "Entity")):
            continue
        nums = re.findall(r"[-+]?\d*\.?\d+", line)
        if nums:
            vals.append(float(nums[-1]))
    return round(sum(vals), 3) if vals else None


def ipmi_w(run: _RUN = _run_text) -> float | None:
    """Whole-node power (W) from the BMC via DCMI. None if ipmitool/DCMI is absent."""
    if run is _run_text and not shutil.which("ipmitool"):
        return None
    try:
        out = run(["ipmitool", "dcmi", "power", "reading"])
    except Exception:
        return None
    m = re.search(r"Instantaneous power reading:\s*([\d.]+)\s*Watts", out)
    return round(float(m.group(1)), 3) if m else None


def detect_sources() -> list[Source]:
    """Every independent source present on this host, nvidia-smi first."""
    return [Source("nvidia-smi", "gpu", nvidia_smi_w),
            Source("dcgm", "gpu", dcgm_w),
            Source("ipmi", "node", ipmi_w)]


def mock_sources(base_w: float) -> list[Source]:
    """Synthetic readers for a CPU box: GPU sources near NVML, node source higher (overhead)."""
    return [Source("nvidia-smi", "gpu", lambda: round(base_w * 1.01, 3)),
            Source("dcgm", "gpu", lambda: round(base_w * 0.99, 3)),
            Source("ipmi", "node", lambda: round(base_w * 1.6, 3))]


def _one(src: Source, nvml_energy_wh: float, seconds: float, label: str) -> dict:
    w = None
    try:
        w = src.read()
    except Exception:
        w = None
    if w is None:
        return {"source": src.name, "scope": src.scope, "available": False, "mean_w": None,
                "energy_wh": None, "gap_pct_vs_nvml": None, "label": None,
                "note": "not present on this host"}
    energy = w * seconds / 3600.0
    gap = None if not nvml_energy_wh else round(100.0 * (energy - nvml_energy_wh) / nvml_energy_wh, 2)
    note = ("whole-node power incl. CPU/fans/PSU; a positive gap vs GPU-only NVML is expected"
            if src.scope == "node" else "GPU-scope; should track NVML closely")
    return {"source": src.name, "scope": src.scope, "available": True, "mean_w": round(w, 3),
            "energy_wh": round(energy, 6), "gap_pct_vs_nvml": gap, "label": label, "note": note}


def cross_check(nvml_energy_wh: float, seconds: float, *, sources: list[Source] | None = None,
                mock: bool = False, base_w: float | None = None) -> dict:
    """Compare NVML energy against every available source over the same window.

    ``nvml_energy_wh`` is the primary (MEASURED) energy; ``seconds`` is the window length. In mock
    mode, synthetic sources are built around ``base_w`` (defaults to NVML's mean power).
    """

    if seconds <= 0:
        raise ValueError("seconds must be positive")
    label = "MOCK" if mock else "MEASURED"
    if sources is None:
        if mock:
            bw = base_w if base_w is not None else nvml_energy_wh * 3600.0 / seconds
            sources = mock_sources(bw)
        else:
            sources = detect_sources()
    rows = [_one(s, nvml_energy_wh, seconds, label) for s in sources]
    gpu_gaps = [abs(r["gap_pct_vs_nvml"]) for r in rows
                if r["available"] and r["scope"] == "gpu" and r["gap_pct_vs_nvml"] is not None]
    worst = max(gpu_gaps) if gpu_gaps else None
    return {
        "spec": SPEC,
        "nvml_energy_wh": round(nvml_energy_wh, 6),
        "nvml_mean_w": round(nvml_energy_wh * 3600.0 / seconds, 3) if seconds else None,
        "seconds": round(seconds, 3),
        "mock": mock,
        "sources": rows,
        "available_sources": [r["source"] for r in rows if r["available"]],
        "worst_gpu_gap_pct": worst,
        "gpu_sources_agree": None if worst is None else worst <= GPU_GAP_OK_PCT,
        "threshold_pct": GPU_GAP_OK_PCT,
        "how": "energy = mean watts x window seconds / 3600; gap = (source - NVML) / NVML",
    }


def source_dicts(sources: list[Source]) -> list[dict]:
    """Small helper for logging which sources were detected (name/scope only)."""
    return [{k: v for k, v in asdict(s).items() if k != "read"} for s in sources]
