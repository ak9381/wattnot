"""Muse Spark Open side stage: the 816B MoE on one H100 through llama.cpp offload.

Two new knobs (quant level and VRAM expert budget), host power next to GPU power,
and a quality gate against a reference config. See docs/runbooks/H100.md.
"""
