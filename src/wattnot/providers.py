"""One place for provider-specific behaviour: ``--provider modal|baseten|h100|combined``.

Every WattNot install ships the whole framework. What changes per provider is only what the
platform lets us touch, where each model runs, the caps, and which runner script drives the
round. Before this module those rules were spread over scripts/e2e/*.py, modal/full_stage.py,
scripts/multiprovider_route.py and wattnot.longrun.driver. They still live there (the runners
enforce them); this module reads them back into one table so a round is started the same way on
every provider, and tests/test_providers.py fails if the table and the runners drift apart.

    if modal:    our vLLM + Muse Glimmer 30B on one A100-80GB. GPU levers: batch/KV sweep, FP8
                 candidate, prefix cache, thinking budget. Power caps are OFF (Modal refuses NVML
                 writes). Energy MEASURED (NVML counter). Smoke = 32 min / $1.50; full = 225 min
                 ceiling, $10 hard per round, $60 stage cap.
    if baseten:  hosted Muse Spark Open on the Model API, paid key BASETEN_METRICS_KEY only. No
                 GPU access, so request levers only (effort routing low -> medium, context saver,
                 agent waste guard). Text, grading and eye on muse-spark-open (Baseten); hearing,
                 SAM 3.1 and Wonder on the Meta API. Energy ESTIMATE (tokens -> FLOPs -> J).
                 $8 hard per round, $1 smoke.
    if h100:     our vLLM + Glimmer 30B on the one E2E Networks H100 VM. Every GPU lever,
                 including power caps (NVML writes allowed). Energy MEASURED. Booking-bound:
                 ctl.py deletes the VM before the booking ends; Rs budget cap per plan.
    if combined: the H100 public round (filed as r11) plus the Baseten and Modal public shares on
                 the same prompts, then per-class routing (wattnot.providers.combine + route).

The public round is the matrix's round 2 run on the public data set only: GSM8K (first 60) +
HumanEval (first 40), 100 items per arm, without vs with WattNot.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_SETS = ("gsm8k", "humaneval")
PUBLIC_ITEMS = {"gsm8k": 60, "humaneval": 40}
# round 2 of each leg in scripts/e2e/round-matrix.json
PUBLIC_ROUND = {"baseten": "r02", "modal": "r05", "h100": "r08", "combined": "r11"}
USD_PER_INR = 1 / 88.0   # ASSUMED exchange rate, same as scripts/multiprovider_route.py

PROFILES: dict[str, dict] = {
    "modal": {
        "where": "Modal, one A100-80GB, our vLLM", "model": "meta-models/Muse-Glimmer-30B",
        "routes": {"text": "Glimmer 30B on Modal (our vLLM)", "eye": "Glimmer 30B on Modal",
                   "hearing": "Muse Voice Transcribe (Meta API) -> Glimmer", "grading": "Muse Spark (Meta API)",
                   "sam": "SAM 3.1 (Meta API)", "wonder": "Muse Wonder (Meta API)"},
        "levers_on": ["batch_kv", "fp8_candidate", "prefix_cache", "thinking_budget", "effort_router",
                      "context_saver", "agent_waste_guard", "langchain", "vllm_proxy", "eye", "hearing",
                      "sam", "wonder", "approval_gate", "eu_report"],
        "levers_off": {"power_cap": "Modal refuses the NVML power-limit write (MEASURED in r04)"},
        "energy_label": "MEASURED",
        "caps": {"round_hard_usd": 10.0, "smoke_hard_usd": 1.50, "stage_cap_usd": 60.0,
                 "round_ceiling_min": 225, "smoke_ceiling_min": 32, "gpu_usd_h": 2.50},
        "smoke": {"limit": 1, "agent_block": "1 rep x 4 cases, 768 tokens, 240 s", "grid": "2 batch points"},
        "full": {"limit": 0, "agent_block": "12 cases x 1 rep, 768 tokens, inside the 900 s cap",
                 "grid": "3 batch points + FP8 last, with-arm stays BF16 unless FP8 passes"},
        "go_env": "WATTNOT_MODAL_GO",
        # Full-round per-block wall limits (s). Rule shared with H100: a block's worst case must fit in
        # 80% of its limit. Worst cases: agentwaste ~43 min (ESTIMATE, 12 cases x 1 rep at 768 tokens);
        # langchain 898 s MEASURED in r04-try1 with the guard cutting the saver arm short, so ~2x that
        # once every item runs (ESTIMATE); think 446 s MEASURED; eye 32 s, spoken 15 s MEASURED.
        # The container ceiling (225 min, $9.38 worst) is unchanged, so the round still fits $10.
        "block_worst_s": {"agentwaste": 2610, "langchain": 1720, "think": 450, "eye": 120, "spoken": 120},
        "block_limits_s": {"agentwaste": 3300, "langchain": 2160, "think": 900, "eye": 300, "spoken": 300},
    },
    "baseten": {
        "where": "Baseten Model API (hosted)", "model": "meta-models/muse-spark-open",
        "routes": {"text": "muse-spark-open on Baseten", "grading": "muse-spark-open on Baseten (judge)",
                   "eye": "muse-spark-open on Baseten (exact read, no tolerance)",
                   "hearing": "Muse Voice Transcribe (Meta API) -> muse-spark-open",
                   "sam": "SAM 3.1 (Meta API)", "wonder": "Muse Wonder (Meta API)"},
        "levers_on": ["effort_router", "context_saver", "agent_waste_guard", "langchain", "eye", "hearing",
                      "sam", "wonder", "approval_gate", "eu_report"],
        "levers_off": {"power_cap": "hosted API, no GPU access", "batch_kv": "hosted API, no GPU access",
                       "fp8": "hosted API, no GPU access", "prefix_cache": "provider-managed"},
        "energy_label": "ESTIMATE",
        "token_caps": "max_tokens = answer budget + allowance; low 768 (runaway cap, steps up), "
                      "medium 12000 for BOTH arms so no final answer is cut by the cap",
        "caps": {"round_hard_usd": 8.0, "smoke_hard_usd": 1.0, "judge_cap": "min($98, round cap)"},
        "key_env": "BASETEN_METRICS_KEY",
        "go_env": None,
    },
    "h100": {
        "where": "E2E Networks, one H100 VM, our vLLM", "model": "meta-models/Muse-Glimmer-30B",
        "routes": {"text": "Glimmer 30B on the H100", "eye": "Glimmer 30B on the H100",
                   "hearing": "cached Muse Voice Transcribe transcripts -> Glimmer (live transcribe off-box)",
                   "grading": "Muse Spark (Meta API), off-box after copy-back",
                   "sam": "SAM 3.1 (Meta API), off-box", "wonder": "Muse Wonder (Meta API) planner"},
        "levers_on": ["power_cap", "batch_kv", "fp8_candidate", "prefix_cache", "thinking_budget",
                      "effort_router", "context_saver", "agent_waste_guard", "langchain", "vllm_proxy",
                      "eye", "hearing", "sam", "wonder", "approval_gate", "eu_report"],
        "levers_off": {},
        "energy_label": "MEASURED",
        "caps": {"inr_per_h": 451.2, "public_booking_h": 7.0, "max_booking_h": 16.0},
        "go_env": None,
    },
}
PROFILES["combined"] = {
    "where": "H100 + Baseten + Modal on the same public prompts",
    "legs": ["h100", "baseten", "modal"],
    "routing": "per class (GSM8K, HumanEval): providers within 1 point of the best pass rate, then "
               "the lowest $ per correct answer (ESTIMATE allocation)",
}


BLOCK_FIT = 0.8   # a block's worst case must fit in 80% of its wall limit (same rule as H100)


def block_limit_s(provider: str, block: str) -> int:
    return int(PROFILES[provider]["block_limits_s"][block])


def block_fit_problems(provider: str) -> list[str]:
    """Blocks whose worst case does not fit in BLOCK_FIT of their limit, plus a check that every
    block's limit together still fits inside the round's container ceiling."""
    prof = PROFILES[provider]
    worst, lim = prof.get("block_worst_s", {}), prof.get("block_limits_s", {})
    bad = [f"{k}: worst {w}s > {BLOCK_FIT:.0%} of {lim.get(k)}s" for k, w in worst.items()
           if k not in lim or w > BLOCK_FIT * lim[k]]
    ceil_s = prof["caps"]["round_ceiling_min"] * 60
    if sum(lim.values()) > ceil_s - 60 * 60:   # leave >= 60 min for server start, arms and sweep
        bad.append(f"block limits {sum(lim.values())}s leave < 60 min of the {ceil_s}s ceiling")
    return bad


def public_round(provider: str) -> dict:
    if provider not in PUBLIC_ROUND:
        raise ValueError(f"unknown provider {provider!r}")
    return {"provider": provider, "round": PUBLIC_ROUND[provider], "sets": list(PUBLIC_SETS),
            "items_per_arm": sum(PUBLIC_ITEMS.values()), "arms": ["without_wattnot", "with_wattnot"]}


def commands(provider: str, *, dry_run: bool, smoke: bool = False, out: str = "/tmp/wattnot-dry") -> list[list[str]]:
    """The exact command list for the public round. Nothing here runs anything."""
    py = "python"
    sets = list(PUBLIC_SETS)
    if provider == "modal":
        r = PUBLIC_ROUND["modal"]
        if dry_run:
            return [[py, "scripts/e2e/modal_full_round.py", "--round", r, "--fake", "--sets", *sets]]
        return [[py, "scripts/e2e/modal_stage.py", "--rounds", r, "--sets", *sets]
                + (["--limit", "1"] if smoke else [])]
    if provider == "baseten":
        r = PUBLIC_ROUND["baseten"]
        if dry_run:
            return [[py, "scripts/e2e/baseten_round.py", "--round", r, "--sets", *sets, "--dry-run"]]
        return [[py, "scripts/e2e/baseten_stage.py", "--rounds", r, "--pr-round", r, "--sets", *sets]
                + (["--smoke"] if smoke else [])]
    if provider in ("h100", "combined"):
        mode = ["--dry-run", "--out", f"{out}/h100-{provider}", "--drop-link", ""] if dry_run else \
               ["--live", "--out", f"run-public-{PUBLIC_ROUND[provider]}"]
        h = [["env", f"WATTNOT_PUBLIC_MATRIX_ROUND={PUBLIC_ROUND[provider]}", py, "-m",
              "wattnot.longrun.driver", *mode, "--model", "glimmer-test", "--plan", "public"]]
        if provider == "h100":
            return h
        return h + commands("baseten", dry_run=dry_run, smoke=smoke) + commands("modal", dry_run=dry_run, smoke=smoke)
    raise ValueError(f"unknown provider {provider!r}")


def estimate(provider: str) -> dict:
    """Spend ESTIMATE for one public round (no call made)."""
    if provider == "baseten":
        # bigger live smoke (MEASURED): $0.124 + $0.040 for 30 items/arm incl. the eye/hearing checks
        per_item = (0.124 + 0.040) / 30 / 2
        txt = per_item * 100 * 2 * 1.6   # x1.6: public items are longer than the smoke mix, and the
        return {"baseten_usd": round(txt + 0.10, 2), "meta_usd": 0.6,  # higher medium cap lets a few run long
                "label": "ESTIMATE", "hard_cap_usd": 8.0,
                "basis": "smoke job 20261101-191931-6df1 $/item x 200 answers x 1.6, + checks; Meta = Spark "
                         "grades (200) + hearing + SAM + Wonder"}
    if provider == "modal":
        return {"modal_usd": round(110 / 60 * 2.5, 2), "label": "ESTIMATE", "hard_cap_usd": 10.0,
                "basis": "~110 min (public 100 items/arm instead of 192, same blocks) x $2.50/h A100"}
    if provider == "h100":
        h = PROFILES["h100"]["caps"]["public_booking_h"]
        inr = h * PROFILES["h100"]["caps"]["inr_per_h"]
        return {"inr": round(inr), "usd": round(inr * USD_PER_INR, 2), "label": "ESTIMATE",
                "basis": f"{h} h booking x Rs 451.2/h; Meta grading off-box ~$0.5"}
    if provider == "combined":
        legs = {p: estimate(p) for p in ("h100", "baseten", "modal")}
        usd = legs["h100"]["usd"] + legs["baseten"]["baseten_usd"] + legs["modal"]["modal_usd"]
        return {"legs": legs, "total_usd": round(usd, 2), "label": "ESTIMATE",
                "note": "the Baseten and Modal shares can reuse their own public rounds (same prompts) "
                        "instead of paying twice"}
    raise ValueError(provider)


# ---- combined: same-prompt results -> per-class routing --------------------------------------

def _share(total: float | None, n: int, n_all: int) -> float | None:
    return None if total is None or not n_all else total * n / n_all


def combine(baseten_doc: dict | None, modal_inner: dict | None, h100_doc: dict | None,
            *, h100_usd: float | None = None, modal_usd: float | None = None) -> dict:
    """Build multiprovider_route.decide input {provider: {class: {correct, n, j, usd}}} from the
    with-WattNot arm of each provider's public round. Per-class $ is the arm total split by item
    count (ESTIMATE allocation). j is only filled for MEASURED energy (Modal, H100)."""
    res: dict = {}
    if baseten_doc:
        w = baseten_doc["results"]["per_arm"]["with_wattnot"]
        n_all = sum(v["answers"] for v in w["per_set"].values())
        res["baseten"] = {c: {"correct": v["correct"], "n": v["answers"], "j": None,
                              "usd": _share(w["usd"]["value"], v["answers"], n_all)}
                          for c, v in w["per_set"].items() if c in PUBLIC_SETS}
    if modal_inner:
        w = modal_inner["steps"]["with"]
        n_all = sum(v["answers"] for v in w["per_set"].values())
        meas = w["energy_j"].get("label") == "MEASURED"
        res["modal"] = {c: {"correct": v["correct"], "n": v["answers"],
                            "j": _share(w["energy_j"]["value"], v["answers"], n_all) if meas else None,
                            "usd": _share(modal_usd, v["answers"], n_all)}
                        for c, v in w["per_set"].items() if c in PUBLIC_SETS}
    if h100_doc:
        pa = h100_doc["results"]["per_arm"]["with_wattnot"]
        rows = {c: pa[c] for c in PUBLIC_SETS if c in pa}
        n_all = sum(v[1] for v in rows.values())
        res["h100"] = {c: {"correct": v[0], "n": v[1], "j": None, "usd": _share(h100_usd, v[1], n_all)}
                       for c, v in rows.items()}
    return res


def route(results: dict) -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location("multiprovider_route", ROOT / "scripts" / "multiprovider_route.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for row in results.values():   # decide() needs a number for $ when no j is present
        for c in row.values():
            c["usd"] = c["usd"] if c["usd"] is not None else 0.0
    return {"label": "ESTIMATE", "decision": mod.decide(results, classes={c: c for c in PUBLIC_SETS}),
            "inputs": results}


def load_json(p: str | Path | None) -> dict | None:
    return json.loads(Path(p).read_text()) if p and Path(p).exists() else None
