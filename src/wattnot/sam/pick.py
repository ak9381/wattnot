"""Choose which mask document the rack view shows.

SAM masks win only when `rack/masks.sam.json` exists, passes every check
and has status `ok`. Anything else falls back to the manual overlay.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wattnot.sam.segment import MasksError, load_manual_overlay, load_masks, repo_root


def pick_masks(root: Path | None = None) -> tuple[dict[str, Any], bool]:
    """Return (document, used_fallback)."""

    root = root or repo_root()
    sam = root / "rack" / "masks.sam.json"
    if sam.is_file():
        try:
            doc = load_masks(sam)
        except MasksError:
            doc = None
        if doc is not None and doc["status"] == "ok":
            return doc, False
    return load_manual_overlay(root), True
