"""Load the SYNTHETIC spoken-answer assets (recordings + known answers).

The manifest is ``answers.json`` next to the ``.wav`` recordings. Every item
carries a ``SYNTHETIC`` watermark, a ``noise`` flag (``clean`` or
``datacenter``), the reference question and its ground-truth answer. The
recordings are transcribed once by Muse Voice Transcribe via
``scripts/gen_spoken_assets.py`` (a paid, one-time script never run by tests);
the cached transcripts live in ``transcripts.json``. The committed ``.wav``
files are offline placeholders so the gate and its tests always have something
to read without a paid call.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# The asset directory, relative to the repo root (…/data/voice/spoken).
DEFAULT_ASSETS_DIR = Path(__file__).resolve().parents[3] / "data" / "voice" / "spoken"

KINDS = ("text", "number")
NOISES = ("clean", "datacenter")


@dataclass(frozen=True)
class SpokenItem:
    id: str
    file: str
    kind: str
    noise: str
    question: str
    answer: str
    label: str = "SYNTHETIC"

    def path(self, base: Path) -> Path:
        return base / self.file


@dataclass(frozen=True)
class SpokenSet:
    items: tuple[SpokenItem, ...]
    label: str
    base_dir: Path
    transcriber: str

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    @property
    def ids(self) -> list[str]:
        return [it.id for it in self.items]


def load_spoken_set(assets_dir: str | Path | None = None, *,
                    require_files: bool = False) -> SpokenSet:
    """Read ``answers.json`` and return the parsed, validated spoken set.

    ``require_files=True`` also checks every recording exists (used by the
    one-time generator and by live runs, not by the fixture tests).
    """

    base = Path(assets_dir) if assets_dir is not None else DEFAULT_ASSETS_DIR
    manifest = json.loads((base / "answers.json").read_text(encoding="utf-8"))
    label = str(manifest.get("label", "SYNTHETIC"))
    if label != "SYNTHETIC":
        raise ValueError(f"spoken-answer assets must be labelled SYNTHETIC, got {label!r}")
    transcriber = str((manifest.get("transcriber") or {}).get("model", "muse-voice-transcribe-1.0"))
    items: list[SpokenItem] = []
    seen: set[str] = set()
    for raw in manifest.get("items", []):
        kind = raw.get("kind")
        if kind not in KINDS:
            raise ValueError(f"unknown spoken-answer kind {kind!r} for item {raw.get('id')!r}")
        noise = raw.get("noise")
        if noise not in NOISES:
            raise ValueError(f"unknown noise {noise!r} for item {raw.get('id')!r}")
        item = SpokenItem(
            id=str(raw["id"]), file=str(raw["file"]), kind=kind, noise=noise,
            question=str(raw["question"]), answer=str(raw["answer"]),
            label=str(raw.get("watermark", "SYNTHETIC")),
        )
        if item.id in seen:
            raise ValueError(f"duplicate spoken-answer item id {item.id!r}")
        seen.add(item.id)
        if require_files and not item.path(base).exists():
            raise FileNotFoundError(f"missing recording {item.path(base)}")
        items.append(item)
    if not items:
        raise ValueError("spoken-answer manifest has no items")
    return SpokenSet(tuple(items), label, base, transcriber)


def load_transcripts(assets_dir: str | Path | None = None) -> dict[str, str]:
    """{id: transcript} cached from Muse Voice Transcribe (one-time paid)."""

    base = Path(assets_dir) if assets_dir is not None else DEFAULT_ASSETS_DIR
    raw = json.loads((base / "transcripts.json").read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in (raw.get("transcripts") or {}).items()}


def ground_truth(items: Sequence[SpokenItem]) -> dict[str, str]:
    """{id: answer} — the offline/fallback answerer returns exactly this."""
    return {it.id: it.answer for it in items}
