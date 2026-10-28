"""Context saver: cut wasted tokens on the agent path without dropping answer quality.

Three levers, all honest about their labels:

* prefix caching — a vLLM serving lever (``enable_prefix_caching``) measured by an A/B experiment
  (:mod:`wattnot.context_saver.prefix_cache`);
* memory compression — summarise old agent turns with Llama 8B into a chunk store and retrieve the
  relevant chunks back in (:class:`MemoryCompressionMiddleware`);
* tool-output dedup — replace a repeated file read or an identical tool result with a short
  reference (:class:`ToolDedupMiddleware`).

The two middlewares sit on the agent-waste path (:class:`wattnot.agentwaste.WasteProxy`). Every cut
is counted (tokens before/after) and must pass the quality gate: if the pass rate drops by more
than one point the cut is rolled back. See docs/CONTEXT-SAVER.md.
"""

from __future__ import annotations

from wattnot.context_saver.middleware import (
    receipt_parts,
    ChunkStore,
    ContextSaver,
    MemoryCompressionMiddleware,
    MockSummarizer,
    ToolDedupMiddleware,
    conversation_tokens,
    estimate_tokens,
    quality_gate,
    saver_receipt_block,
)

__all__ = [
    "receipt_parts",
    "ChunkStore",
    "ContextSaver",
    "MemoryCompressionMiddleware",
    "MockSummarizer",
    "ToolDedupMiddleware",
    "conversation_tokens",
    "estimate_tokens",
    "quality_gate",
    "saver_receipt_block",
]
