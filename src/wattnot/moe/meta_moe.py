"""Which Meta MoE adaptive / reduced top-k runs on in the long run (prep only, no GPU here).

Checked 2026-10-28:
* Muse Glimmer 30B is dense (num_experts 0), so there is no expert router to trim. Not usable.
* Llama 4 Scout 17B-16E routes each token to 1 routed expert plus a shared expert, so top-k
  cannot go below 1. Not usable for top-k (and the repo is gated).
* Muse Spark Open 816B-A42B is a Meta MoE we already serve on one H100 through llama.cpp
  (spark1gpu). llama.cpp can lower the experts used per token at load time with
  ``--override-kv <arch>.expert_used_count=int:N``. That is a fixed reduced top-k, not the
  per-token adaptive rule, and it is the Meta-model path for the long run.

Granite 3.1 3B-A800M stays only as the CPU test model and as the earlier (Oct 27-30) MEASURED
adaptive result; it is off the main demo path.
"""

from __future__ import annotations

CANDIDATES = {
    "muse-spark-open": {"repo": "meta-models/Muse-Spark-Open-816B-A42B", "moe": True,
                        "usable": True, "how": "llama.cpp expert_used_count override"},
    "llama-4-scout": {"repo": "meta-llama/Llama-4-Scout-17B-16E-Instruct", "moe": True,
                      "usable": False, "why": "top-1 routing plus a shared expert: no k to lower"},
    "muse-glimmer-30b": {"repo": "meta-models/Muse-Glimmer-30B", "moe": False,
                         "usable": False, "why": "dense model, no experts"},
}
DEFAULT = "muse-spark-open"


def topk_override_args(arch: str, k: int, default_k: int) -> list[str]:
    """llama-server args for a reduced top-k arm. ``arch`` is the GGUF general.architecture."""

    if not 1 <= k <= default_k:
        raise ValueError(f"k must be within 1..{default_k}")
    if k == default_k:
        return []
    return ["--override-kv", f"{arch}.expert_used_count=int:{k}"]


def sweep_plan(arch: str, default_k: int, ks=None) -> list[dict]:
    """Arms for the long run: stock k, then lower k until the gate fails (gate decides)."""

    ks = ks or sorted({default_k, max(1, default_k * 3 // 4), max(1, default_k // 2)}, reverse=True)
    return [{"k": k, "args": topk_override_args(arch, k, default_k),
             "label": "MEASURED (in the long run)"} for k in ks]
