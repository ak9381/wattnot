"""One eligibility check for every feature and every Meta model, driven by the run configuration.

The Configure screen, every dashboard page and the end-to-end runners read the same verdicts:
a feature or model is Eligible or Ineligible for a configuration, with a one-line reason.

Configuration = {provider, model, datasets, p99_pct, approvals, open_pr, caps}. Only the provider
(and the model) change what can run; the rest is recorded for the launch.

Feature list: the 110 rows of docs/WALKTHROUGH.md section 0 (parsed, then checked into
features.json next to this file so the browser and the runners never parse markdown).
Each row carries a requirement tag; a provider either has that capability or it does not.
The runners' ON/OFF tables (wattnot.e2e.fullstack FEATURES / MODAL_GPU_OVERRIDES) are checked
against these verdicts by tests/test_eligibility.py, so the two can never drift silently.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FEATURES_JSON = HERE / "features.json"

# What each provider can do. "combined" = Baseten + Modal A100 + E2E H100 in one plan.
PROVIDERS: dict[str, dict] = {
    "baseten": {
        "label": "Baseten (hosted Spark Open, per token)", "runner": "baseten",
        "caps": {"meta_api", "hosted_api", "local"},
        "default_model": "spark-open", "cost_round_usd": [3.0, 4.0], "hard_cap_usd": 8.0,
        "spend": "Baseten card (BASETEN_METRICS_KEY)", "energy_label": "ESTIMATE",
    },
    "modal_a100": {
        "label": "Modal A100 (our own vLLM, Glimmer 30B)", "runner": "modal",
        "caps": {"meta_api", "own_vllm", "nvml_read", "serverless_gpu", "local"},
        "default_model": "glimmer-30b", "cost_round_usd": [3.0, 3.6], "hard_cap_usd": 40.0,
        "spend": "Modal hackathon credits", "energy_label": "MEASURED",
    },
    "e2e_h100": {
        "label": "E2E H100 (rented VM, full GPU control)", "runner": None,
        "caps": {"meta_api", "own_vllm", "nvml_read", "power_write", "clock_write", "gpu_vm", "local"},
        "default_model": "glimmer-30b", "cost_round_usd": None, "hard_cap_usd": None,
        "spend": "E2E prepaid balance", "energy_label": "MEASURED",
    },
    "combined": {
        "label": "Combined (Baseten + Modal A100 + E2E H100)", "runner": None,
        "caps": {"meta_api", "hosted_api", "own_vllm", "nvml_read", "power_write", "clock_write",
                 "serverless_gpu", "gpu_vm", "multi_provider", "local"},
        "default_model": "glimmer-30b", "cost_round_usd": None, "hard_cap_usd": None,
        "spend": "all three", "energy_label": "MEASURED",
    },
}

# requirement tag -> (capability needed or None, reason shown when the provider lacks it)
REQS: dict[str, tuple[str | None, str]] = {
    "any": (None, ""),
    "meta_api": ("meta_api", "Meta API call"),
    "hosted_api": ("hosted_api", "needs a hosted per-token provider (Baseten)"),
    "own_vllm": ("own_vllm", "no GPU access: needs our own vLLM server"),
    "nvml_read": ("nvml_read", "no GPU power meter on a hosted API; energy is ESTIMATE"),
    "power_write": ("power_write", "power caps need NVML writes; refused here"),
    "clock_write": ("clock_write", "clock control needs NVML clock writes; refused here"),
    "gpu_vm": ("gpu_vm", "needs the rented E2E H100 VM"),
    "serverless_gpu": ("serverless_gpu", "needs the Modal serverless GPU"),
    "multi_provider": ("multi_provider", "needs two providers in one plan (combined)"),
    "spark_open_self": ("__never__", "needs Spark Open self-served on 8x B200; too costly, not run"),
    "simulator": ("__never__", "runs only as a simulator, never in a live round"),
    "after_long_run": ("__never__", "only after the long run, with Akash's yes"),
    "planned": ("__never__", "planned, not built yet"),
}

# Walkthrough row number -> requirement tag. Rows not listed run anywhere ("any").
ROW_REQ: dict[int, str] = {
    2: "power_write", 4: "nvml_read", 5: "own_vllm", 7: "spark_open_self", 8: "simulator",
    13: "own_vllm", 23: "meta_api", 24: "meta_api", 25: "meta_api", 26: "meta_api",
    27: "spark_open_self", 28: "own_vllm", 29: "meta_api", 30: "meta_api", 31: "meta_api", 33: "meta_api",
    39: "multi_provider", 59: "own_vllm", 60: "hosted_api", 61: "serverless_gpu", 62: "meta_api",
    64: "power_write", 65: "power_write", 71: "clock_write", 73: "clock_write", 76: "meta_api",
    78: "spark_open_self", 79: "spark_open_self", 81: "own_vllm", 83: "gpu_vm", 86: "planned",
    99: "planned", 100: "after_long_run", 102: "meta_api", 106: "meta_api", 110: "meta_api",
}

# Runner feature id (wattnot.e2e.fullstack) -> requirement tag + walkthrough rows it covers.
RUNNER: dict[str, tuple[str, list[int]]] = {
    "effort_routing": ("any", [15, 26]), "thinking_budget": ("any", [6]), "router": ("any", [75]),
    "context_saver": ("any", [11, 12]), "waste_guard": ("any", [70]), "code_mode": ("any", [14, 21]),
    "prefix_cache_provider": ("any", [13]), "concurrency_batching": ("any", []),
    "gates": ("any", [16, 17, 18, 19]), "spark_grades": ("meta_api", [106]), "joule_slo": ("any", [74, 94]),
    "eye_check": ("meta_api", [76, 33]), "sam_hotspot": ("meta_api", [25]), "hearing_check": ("meta_api", [30]),
    "wonder_planner": ("meta_api", [23, 24]), "spark_trace": ("meta_api", [102]), "approvals": ("any", [22, 67]),
    "musecode_pr": ("meta_api", [29]), "receipt": ("any", [1, 34, 36, 101]), "eu_report": ("any", [37]),
    "label_lint": ("any", [20]), "spend_guard": ("any", [56, 58]), "power_caps": ("power_write", [2]),
    "fp8": ("own_vllm", [5]), "kv_batch": ("own_vllm", [81]), "vllm_prefix": ("own_vllm", [13]),
    "clock_sidecar": ("clock_write", [71, 73]), "moe": ("spark_open_self", [7, 78, 79]),
    "measured_energy": ("nvml_read", [4]), "flex": ("simulator", [8]), "rekor": ("after_long_run", [100]),
    "own_gpu_serving": ("own_vllm", [59]),
}

MODELS: list[dict] = [
    {"id": "spark", "name": "Muse Spark", "req": "meta_api", "role": "planner, grader, 4-call trace"},
    {"id": "wonder", "name": "Muse Wonder", "req": "meta_api", "role": "Pareto planner + Wonderchart"},
    {"id": "glimmer-30b", "name": "Muse Glimmer 30B", "req": "own_vllm", "role": "main model on our own vLLM"},
    {"id": "sam-3.1", "name": "SAM 3.1", "req": "meta_api", "role": "hotspot audit"},
    {"id": "muse-image", "name": "Muse Image", "req": "meta_api", "role": "eye-check test set",
     "label": "SYNTHETIC", "note": "images are SYNTHETIC test inputs only"},
    {"id": "muse-voice", "name": "Muse Voice Transcribe", "req": "meta_api", "role": "hearing check, voice Ask"},
    {"id": "muse-code", "name": "Muse Code", "req": "meta_api", "role": "applies the change as a PR (demo repo only)"},
    {"id": "spark-open", "name": "Muse Spark Open", "req": "hosted_api",
     "role": "main model, hosted on Baseten", "self_note": "self-serving needs 8x B200"},
]
MAIN_MODELS = {"spark-open": "hosted_api", "glimmer-30b": "own_vllm"}


def parse_walkthrough(md: str) -> list[dict]:
    """Rows of section 0 of docs/WALKTHROUGH.md -> [{n, section, name, label, where}]."""
    out: list[dict] = []
    sec0 = md.split("\n## 1.")[0] if "\n## 1." in md else md
    for line in sec0.splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|(.*)\|\s*$", line)
        if not m:
            continue
        n = int(m.group(1))
        if any(r["n"] == n for r in out):
            continue  # later tables restart numbering
        cells = [c.strip() for c in m.group(2).split("|")]
        if len(cells) >= 5:
            section, name, _status, label, where = cells[:5]
        else:  # rows 101-110: name | status | where
            section, name, label, where = "Merged 1 Nov", cells[0], "", cells[-1] if len(cells) > 2 else ""
        out.append({"n": n, "section": section, "name": name, "label": label, "where": where})
    return out


def load_features() -> list[dict]:
    return json.loads(FEATURES_JSON.read_text())["features"]


def _verdict(req: str, provider: str) -> tuple[bool, str]:
    cap, why = REQS[req]
    if cap is None:
        return True, "runs on every configuration"
    if cap in PROVIDERS[provider]["caps"]:
        return True, {"meta_api": "Meta API, works with any provider", "local": "runs locally"}.get(
            req, f"{PROVIDERS[provider]['label']} has it")
    if req == "nvml_read" or req == "own_vllm":
        why = {"baseten": "Baseten is a hosted API: no GPU access, " + why.split(": ", 1)[-1]}.get(provider, why)
    if req == "power_write" and provider == "modal_a100":
        why = "Modal refuses NVML power-limit writes (probed and recorded each round)"
    if req == "clock_write" and provider == "modal_a100":
        why = "Modal refuses NVML clock writes"
    return False, why


def check(config: dict) -> dict:
    """Eligibility for a configuration: {provider, model, features[], models[], runner[], counts, problems[]}."""
    prov = config.get("provider") or "baseten"
    if prov not in PROVIDERS:
        raise ValueError(f"unknown provider {prov!r}")
    model = config.get("model") or PROVIDERS[prov]["default_model"]
    problems = []
    if model in MAIN_MODELS and not _verdict(MAIN_MODELS[model], prov)[0]:
        problems.append(f"{model} cannot be the main model on {PROVIDERS[prov]['label']}")
    feats = []
    for f in load_features():
        ok, why = _verdict(f["req"], prov)
        feats.append({**f, "eligible": ok, "reason": why})
    models = []
    for m in MODELS:
        ok, why = _verdict(m["req"], prov)
        if m["id"] == "spark-open" and not ok:
            why = "Spark Open is hosted on Baseten; " + m["self_note"]
        if m["id"] in MAIN_MODELS and ok:
            why = "main model for this run" if m["id"] == model else "available, not the main model chosen"
        models.append({**m, "eligible": ok, "reason": why, "label": m.get("label")})
    runner = [{"id": k, "req": r, "rows": rows, "eligible": _verdict(r, prov)[0], "reason": _verdict(r, prov)[1]}
              for k, (r, rows) in RUNNER.items()]
    n_ok = sum(f["eligible"] for f in feats)
    return {"provider": prov, "provider_label": PROVIDERS[prov]["label"], "model": model,
            "energy_label": PROVIDERS[prov]["energy_label"], "features": feats, "models": models,
            "runner": runner, "counts": {"eligible": n_ok, "ineligible": len(feats) - n_ok, "total": len(feats)},
            "problems": problems}


def runner_table(provider: str) -> list[dict]:
    """The ON/OFF shape the runners write ({id, status, reason}) computed from eligibility."""
    return [{"id": r["id"], "status": "ON" if r["eligible"] else "OFF", "reason": r["reason"]}
            for r in check({"provider": provider})["runner"]]


def build(walkthrough: Path) -> dict:
    rows = parse_walkthrough(walkthrough.read_text())
    for r in rows:
        r["req"] = ROW_REQ.get(r["n"], "any")
    doc = {"source": "docs/WALKTHROUGH.md section 0", "count": len(rows), "features": rows,
           "providers": {k: {kk: (sorted(vv) if isinstance(vv, set) else vv) for kk, vv in v.items()}
                         for k, v in PROVIDERS.items()},
           "reqs": {k: {"cap": v[0], "why": v[1]} for k, v in REQS.items()},
           "models": MODELS, "runner": {k: {"req": v[0], "rows": v[1]} for k, v in RUNNER.items()},
           "main_models": MAIN_MODELS}
    FEATURES_JSON.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    return doc


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parents[3]
    d = build(root / "docs" / "WALKTHROUGH.md")
    print(f"{d['count']} features written to {FEATURES_JSON}", file=sys.stderr)
