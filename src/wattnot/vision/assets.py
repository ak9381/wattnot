"""Load the SYNTHETIC eye-test assets (images + known answers).

The manifest is ``answers.json`` next to the images. Every item carries a
``SYNTHETIC`` watermark label, a question, and the ground-truth answer. The
images themselves are generated once by ``scripts/gen_vision_assets.py`` (a
paid, one-time script never run by tests); the files checked in are offline
placeholders so the gate and its tests always have something to read.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# The asset directory, relative to the repo root (…/data/vision/eyetest).
DEFAULT_ASSETS_DIR = Path(__file__).resolve().parents[3] / "data" / "vision" / "eyetest"

# ESTIMATE energy stamp per generated image. Not metered; always labelled
# ESTIMATE on the receipt and in the UI. Overridden by the manifest if present.
EST_WH_PER_IMAGE = 0.5

KINDS = ("text", "number", "count", "color")


@dataclass(frozen=True)
class EyeTestItem:
    id: str
    file: str
    kind: str
    question: str
    answer: str
    label: str = "SYNTHETIC"

    def path(self, base: Path) -> Path:
        return base / self.file


@dataclass(frozen=True)
class EyeTest:
    items: tuple[EyeTestItem, ...]
    label: str
    wh_per_image: float
    base_dir: Path

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    @property
    def ids(self) -> list[str]:
        return [it.id for it in self.items]


def load_eye_test(assets_dir: str | Path | None = None, *, require_files: bool = False) -> EyeTest:
    """Read ``answers.json`` and return the parsed, validated eye test.

    ``require_files=True`` also checks every image file exists (used by the
    one-time generator and by live runs, not by the fixture tests).
    """

    base = Path(assets_dir) if assets_dir is not None else DEFAULT_ASSETS_DIR
    manifest = json.loads((base / "answers.json").read_text(encoding="utf-8"))
    label = str(manifest.get("label", "SYNTHETIC"))
    if label != "SYNTHETIC":
        raise ValueError(f"eye-test assets must be labelled SYNTHETIC, got {label!r}")
    wh = float((manifest.get("energy_per_image_wh") or {}).get("value", EST_WH_PER_IMAGE))
    items: list[EyeTestItem] = []
    seen: set[str] = set()
    for raw in manifest.get("items", []):
        kind = raw.get("kind")
        if kind not in KINDS:
            raise ValueError(f"unknown eye-test kind {kind!r} for item {raw.get('id')!r}")
        item = EyeTestItem(
            id=str(raw["id"]), file=str(raw["file"]), kind=kind,
            question=str(raw["question"]), answer=str(raw["answer"]),
            label=str(raw.get("watermark", "SYNTHETIC")),
        )
        if item.id in seen:
            raise ValueError(f"duplicate eye-test item id {item.id!r}")
        seen.add(item.id)
        if require_files and not item.path(base).exists():
            raise FileNotFoundError(f"missing eye-test image {item.path(base)}")
        items.append(item)
    if not items:
        raise ValueError("eye-test manifest has no items")
    return EyeTest(tuple(items), label, wh, base)


def ground_truth(items: Sequence[EyeTestItem]) -> dict[str, str]:
    """{id: answer} — the offline/fallback answerer returns exactly this."""
    return {it.id: it.answer for it in items}
