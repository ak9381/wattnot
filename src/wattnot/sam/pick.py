"""Choose which mask document the rack view shows.

The first photo set present wins: `rack/cisco/` (the SAM-friendly photo),
then `rack/real/`, then `rack/`.
SAM masks win only when `masks.sam.json` exists, passes every check and
has status `ok`. Anything else falls back to the manual layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wattnot.sam.segment import MasksError, load_masks, repo_root


def photo_dir(root: Path | None = None) -> Path:
    root = root or repo_root()
    for name in ("cisco", "real"):
        d = root / "rack" / name
        if (d / "masks.manual.json").is_file():
            return d
    return root / "rack"


def pick_masks(root: Path | None = None, *, directory: Path | None = None
               ) -> tuple[dict[str, Any], bool]:
    """Return (document, used_fallback)."""

    d = directory or photo_dir(root)
    sam = d / "masks.sam.json"
    if sam.is_file():
        try:
            doc = load_masks(sam)
        except MasksError:
            doc = None
        if doc is not None and doc["status"] == "ok":
            return doc, False
    return load_masks(d / "masks.manual.json"), True
