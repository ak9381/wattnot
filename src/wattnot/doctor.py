# ruff: noqa: E501
"""``wattnot doctor``: one plain pass/fail list before a judge runs WattNot.

Read-only. It never sets a power limit, never sends a prompt and never prints a key. Each
check prints PASS, WARN or FAIL with the exact fix. At the end it says which mode will run:

- full: NVIDIA GPU you control + NVML + power-limit permission + vLLM reachable
- savers-only: no usable power cap (no GPU, untested GPU, no root, serverless) -> the power-cap
  features turn off and the token savers (thinking budget, context saver, cheapest-first
  routing, agentwaste proxy) still run against any OpenAI-compatible endpoint
- sim: nothing live is reachable -> a SIMULATED run with every number labelled MOCK

Exit 0 when at least one mode can run (it always can: sim needs nothing), 2 when Python itself
is too old to run WattNot.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Only GPUs we actually ran on. Anything else is UNTESTED: power caps stay off by default.
TESTED_GPUS = {
    "H100 80GB": "full stack (power caps through NVML, vLLM, quality gate, receipts)",
}
HOSTED_ONLY = {"A100-SXM4-80GB": "tested on Modal as hosted GPU; power cap not tested"}
MIN_PY = (3, 11)
MIN_FREE_GB = 2.0


def _gpu_tested(name: str) -> str | None:
    n = name.upper().replace("NVIDIA", "").strip()
    if "H100" in n and "80" in n:
        return "H100 80GB"
    return None


def _check(name: str, status: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"check": name, "status": status, "detail": detail, "fix": fix}


def _vllm_reachable(url: str, timeout: float = 4.0) -> tuple[bool, str]:
    base = url.split("/v1/")[0].rstrip("/")
    try:
        with urllib.request.urlopen(base + "/v1/models", timeout=timeout) as r:
            ids = [m.get("id") for m in json.loads(r.read()).get("data", [])]
        return True, "serving " + (", ".join(i for i in ids if i) or "a model")
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"[:160]


def run(env: dict | None = None, *, preflight: Callable | None = None,
        disk: Callable = shutil.disk_usage, py: tuple = tuple(sys.version_info[:2]),
        nvml_ok: Callable | None = None, reach: Callable = _vllm_reachable) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)
    checks: list[dict[str, str]] = []

    ok_py = py >= MIN_PY
    checks.append(_check("python", "PASS" if ok_py else "FAIL", f"Python {py[0]}.{py[1]}",
                         "" if ok_py else "install Python 3.11+ (python.org or `brew install python@3.12`)"))

    free = disk(Path.cwd()).free / 1e9
    checks.append(_check("disk", "PASS" if free >= MIN_FREE_GB else "WARN", f"{free:.1f} GB free",
                         "" if free >= MIN_FREE_GB else f"free at least {MIN_FREE_GB:.0f} GB here"))

    try:
        pf = (preflight or _preflight)(env)
    except Exception as e:  # noqa: BLE001  never crash halfway
        pf = {"gpu": {"present": False, "reason": f"preflight error: {e}"},
              "power_cap": {"allowed": False, "reason": "preflight error"}}
    gpu, cap = pf.get("gpu", {}), pf.get("power_cap", {})
    tested = None
    if gpu.get("present"):
        name = gpu["gpus"][0]["name"]
        checks.append(_check("driver", "PASS", f"{name}, driver {gpu.get('driver')}, CUDA {gpu.get('cuda')}"))
        tested = _gpu_tested(name)
        if tested:
            checks.append(_check("gpu tested", "PASS", f"{name}: {TESTED_GPUS[tested]}"))
        else:
            checks.append(_check("gpu tested", "WARN", f"{name} is UNTESTED with power caps",
                                 "power caps stay off; set WATTNOT_ALLOW_UNTESTED_GPU=1 to try them "
                                 "(the stock limit is still restored on exit)"))
    else:
        checks.append(_check("driver", "WARN", gpu.get("reason", "no GPU"),
                             "fine for a laptop: token savers still run. For power caps, install the "
                             "NVIDIA driver (nvidia-smi must work) on a GPU you control"))

    nv = (nvml_ok or _nvml_ok)() if gpu.get("present") else (False, "no GPU")
    if gpu.get("present"):
        checks.append(_check("nvml", "PASS" if nv[0] else "FAIL", nv[1],
                             "" if nv[0] else "install the NVIDIA driver package that ships libnvidia-ml (nvidia-smi should work too)"))

    allowed = cap.get("allowed")
    if gpu.get("present"):
        checks.append(_check("power-limit permission",
                             "PASS" if allowed else ("WARN" if allowed is None else "FAIL"),
                             cap.get("reason", ""),
                             "" if allowed else "run as root (sudo -E ./run-local.sh gpu), or ask the "
                             "host admin; check with `sudo nvidia-smi -pl <stock W>`"))

    url = env.get("WATTNOT_VLLM_URL", "")
    if url:
        up, why = reach(url)
        checks.append(_check("vllm", "PASS" if up else "FAIL", f"{url}: {why}",
                             "" if up else "start vLLM, e.g. `vllm serve <model> --port 8000`, and "
                             "set WATTNOT_VLLM_URL=http://127.0.0.1:8000/v1/chat/completions"))
    else:
        checks.append(_check("vllm", "WARN", "WATTNOT_VLLM_URL not set",
                             "set it to your vLLM chat URL for the GPU path; not needed otherwise"))

    key = env.get("META_API_KEY", "")
    has_key = bool(key) and not key.startswith("your-")
    checks.append(_check("meta api key", "PASS" if has_key else "WARN",
                         "META_API_KEY is set (value not shown)" if has_key else "META_API_KEY not set",
                         "" if has_key else "export META_API_KEY=... for live Meta model calls; "
                         "without it only the simulated run works"))

    missing = []
    if not has_key:
        missing.append("META_API_KEY")
    if gpu.get("present") and not url:
        missing.append("WATTNOT_VLLM_URL")
    if missing:
        checks.append(_check(".env", "WARN", "not set: " + ", ".join(missing),
                             "cp .env.example .env, then fill these in (values are never printed)"))

    untested_ok = env.get("WATTNOT_ALLOW_UNTESTED_GPU") == "1"
    caps_on = bool(gpu.get("present") and nv[0] and allowed is not False
                   and (tested or untested_ok) and url)
    if caps_on and url and reach is not None:
        mode = "full"
    elif has_key or url:
        mode = "savers-only"
    else:
        mode = "sim"
    why_off = None if caps_on else next(
        (c["detail"] for c in checks if c["check"] in ("driver", "gpu tested", "nvml",
                                                     "power-limit permission", "vllm")
         and c["status"] != "PASS"), "no GPU")
    return {"format": "wattnot.doctor/1", "checks": checks, "mode": mode,
            "missing_vars": missing, "power_caps": "on" if caps_on else "off", "power_caps_off_because": why_off,
            "python_ok": ok_py}


def _preflight(env) -> dict:
    from wattnot import preflight
    return preflight.run(env=env)


def _nvml_ok() -> tuple[bool, str]:
    try:
        from wattnot.probe import load_nvml
        load_nvml(None)
        return True, "NVML library loads"
    except Exception as e:  # noqa: BLE001
        return False, f"NVML not usable: {type(e).__name__}"


def render(r: dict) -> str:
    out = ["WattNot doctor (read-only: changes nothing, prints no keys)"]
    for c in r["checks"]:
        out.append(f"  [{c['status']:4}] {c['check']:22} {c['detail']}")
        if c["fix"]:
            out.append(f"         fix: {c['fix']}")
    out.append("")
    out.append(f"Mode: {r['mode']}.  Power caps: {r['power_caps']}"
               + (f" ({r['power_caps_off_because']})" if r["power_caps"] == "off" else ""))
    if r["power_caps"] == "off":
        out.append("  Token savers (thinking budget, context saver, cheapest-first routing, "
                   "agentwaste proxy) still run.")
    return "\n".join(out)


def load_dotenv(path: Path, env) -> None:
    """Read KEY=VALUE lines from .env into env, never overriding what is already set."""
    if not path.is_file():
        return
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        v = v.split(" #")[0].strip().strip('"').strip("'")
        if v:
            env.setdefault(k.strip(), v)


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    load_dotenv(Path.cwd() / ".env", os.environ)
    r = run()
    print(json.dumps(r, indent=1) if "--json" in argv else render(r))
    return 0 if r["python_ok"] else 2
