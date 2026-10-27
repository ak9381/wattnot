"""Read back what was approved and applied.

Serving code that wants the approved settings reads them from here instead of from a
proposal, so it only ever sees changes a person approved:

    from wattnot.approvals.active import read_active
    cfg = read_active("cloud_policy")     # None until something is approved and applied
"""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.approvals.store import resolve_root


def read_active(kind: str, root: str | Path | None = None) -> dict | None:
    """The settings last applied for `kind` (`vllm_config`, `cloud_policy`, `moe_routing`), or None.
    The result has `proposal_id`, `content_sha256`, `label` and the `config` itself
    (plus `vllm_args` for vLLM)."""
    f = resolve_root(root) / "active" / f"{kind}.json"
    if not f.is_file():
        return None
    return json.loads(f.read_text(encoding="utf-8"))
