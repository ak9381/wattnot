"""Served models WattNot tunes, default first.

Default: Muse Glimmer 30B (Meta open weights, Apache 2.0). It fits one H100 80GB
in BF16 (about 60 GB of weights, about 72 GB to serve) and in FP8 (about 33-40 GB).
Muse Spark Open (816B MoE) needs at least 700 GB of VRAM even in NVFP4, so it does
not fit one H100; WattNot still uses Muse Spark through the Meta Model API to plan.
Llama 3.1 8B Instruct stays as the comparison model.
Sources: huggingface.co/meta-models/Muse-Glimmer-30B, dev.meta.ai/docs/muse-glimmer/deploy,
recipes.vllm.ai/meta-models/Muse-Glimmer-30B, huggingface.co/meta-models/Muse-Spark-Open-816B-A42B.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServedModel:
    key: str
    repo: str
    params_b: float
    licence: str
    vram_gb_bf16: float
    chat: bool  # use /v1/chat/completions (chat template) instead of /v1/completions
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    vllm_image: str | None = None  # Docker image when pip vLLM lacks support
    note: str = ""
    min_vram_gb: float = 0.0  # total VRAM needed at the precision we serve
    tp: int = 1               # GPUs one server spans (tensor/expert parallel)


MUSE_GLIMMER = ServedModel(
    "muse-glimmer", "meta-models/Muse-Glimmer-30B", 29.6, "Apache-2.0", 72.0, True,
    ("--served-model-name", "meta-models/Muse-Glimmer-30B",
     "--reasoning-parser", "muse_glimmer", "--max-model-len", "8192"),
    "vllm/vllm-openai:muse-glimmer",
    "Meta open weights; BF16 fits 1x H100 80GB, FP8 candidate halves weight memory", 72.0)
LLAMA_31_8B = ServedModel(
    "llama-3.1-8b", "meta-llama/Llama-3.1-8B-Instruct", 8.0, "Llama 3.1 Community", 18.0,
    False, (), None, "comparison model", 18.0)
MUSE_SPARK_OPEN = ServedModel(
    "muse-spark-open", "meta-models/Muse-Spark-Open-816B-A42B-NVFP4", 816.0,
    "Muse Model License", 1650.0, True,
    ("--served-model-name", "meta-models/Muse-Spark-Open-816B-A42B-NVFP4",
     "--tensor-parallel-size", "8", "--enable-expert-parallel", "--max-model-len", "8192"),
    None, "NVFP4 (~537 GB weights) needs >= 700 GB VRAM: 8x H200 141GB or 8x B200, "
    "not 1x or 8x H100 80GB", 700.0, 8)

MODELS = {m.key: m for m in (MUSE_GLIMMER, LLAMA_31_8B, MUSE_SPARK_OPEN)}
DEFAULT = MUSE_GLIMMER
# What the runbook serves, in order. Models that don't fit the box's VRAM are skipped
# and recorded, so one plan works on 1x H100 (Glimmer + Llama) and 8x H200 (Spark Open).
RUN_ORDER = (MUSE_GLIMMER, MUSE_SPARK_OPEN, LLAMA_31_8B)


def fits(model: ServedModel, vram_gb: float) -> bool:
    return (model.min_vram_gb or model.vram_gb_bf16) <= vram_gb
