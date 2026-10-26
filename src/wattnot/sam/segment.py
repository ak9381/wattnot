"""Load and check a rack mask overlay.

The manual overlay is ``rack/masks.manual.json``: polygons drawn by hand on
``rack/rack.jpg``. This module reads that file and checks it. It does not
call a model and it does not use the network.

Coordinate space is the photo's pixels. The origin is the top-left corner,
x grows to the right, y grows down. A polygon is a list of ``[x, y]`` points
with at least three vertices. The ring is closed implicitly: do not repeat
the first point. Rings must be convex and sit inside the photo.

An object is a ``rack`` (the cabinet, listing every GPU it holds), a ``slot``
(one GPU bay, listing that one index), or a ``pdu`` (no GPU index). Each GPU
index is on one rack and one slot, and each slot sits inside its rack.

``source`` is ``manual`` for this file or ``sam3.1`` for a later mask in the
same shape. A manual file is the reference, so ``iou_vs_manual`` is 1.
``status`` is ``ok`` or ``cut``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from wattnot.sam.compare import point_in_convex_polygon, polygon_area, polygon_is_convex

__all__ = [
    "MasksError",
    "image_size",
    "load_manual_overlay",
    "load_masks",
    "manual_overlay_path",
    "validate_masks",
]

# Local document shape. This is not a canonical run-event or receipt schema.
MASKS_SCHEMA: dict[str, Any] = {
    "$id": "wattnot.masks/1",
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "image", "objects", "iou_vs_manual", "status"],
    "properties": {
        "source": {"enum": ["sam3.1", "manual"]},
        "image": {
            "type": "string",
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]*\.jpg$",
        },
        "objects": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/object"},
        },
        "iou_vs_manual": {"type": "number", "minimum": 0, "maximum": 1},
        "status": {"enum": ["ok", "cut"]},
    },
    "$defs": {
        "object": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "kind", "polygon", "gpu_indices"],
            "properties": {
                "id": {"type": "string", "pattern": r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$"},
                "kind": {"enum": ["rack", "pdu", "slot"]},
                "polygon": {
                    "type": "array",
                    "minItems": 3,
                    "items": {
                        "type": "array",
                        "prefixItems": [{"type": "number"}, {"type": "number"}],
                        "minItems": 2,
                        "maxItems": 2,
                        "items": False,
                    },
                },
                "gpu_indices": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "integer", "minimum": 0},
                },
            },
        }
    },
}

# JPEG start-of-frame markers that store height and width.
_SOF = {
    0xC0, 0xC1, 0xC2, 0xC3,
    0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB,
    0xCD, 0xCE, 0xCF,
}


class MasksError(ValueError):
    """A rack mask file could not be read or failed a check."""


def manual_overlay_path(root: Path | None = None) -> Path:
    """Path of the hand-drawn overlay, ``rack/masks.manual.json``."""

    base = repo_root() if root is None else Path(root)
    return base / "rack" / "masks.manual.json"


def load_manual_overlay(root: Path | None = None) -> dict[str, Any]:
    """Load and check the hand-drawn overlay."""

    return load_masks(manual_overlay_path(root))


def load_masks(path: str | Path) -> dict[str, Any]:
    """Read a mask file from disk and check it, including the photo."""

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MasksError(f"cannot read {path}: {exc}") from exc
    try:
        doc = json.loads(text, parse_constant=_reject_constant)
    except MasksError:
        raise
    except json.JSONDecodeError as exc:
        raise MasksError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise MasksError(f"{path}: mask file must be a JSON object")
    image_path = _image_path(path.parent, doc.get("image"))
    validate_masks(doc, image_path=image_path)
    return doc


def validate_masks(doc: dict[str, Any], *, image_path: str | Path | None = None) -> None:
    """Raise :class:`MasksError` if ``doc`` is not a valid overlay.

    When ``image_path`` is set, the photo must exist and every polygon point
    must lie inside it. Omit it to check the document alone.
    """

    if not isinstance(doc, dict):
        raise MasksError("mask file must be a JSON object")
    _check_schema(doc)
    if doc["source"] == "manual" and doc["iou_vs_manual"] != 1:
        raise MasksError(
            "a manual overlay is the reference, so iou_vs_manual must be 1"
        )
    _check_ids(doc["objects"])
    for obj in doc["objects"]:
        _check_polygon(obj)
    _check_gpus(doc["objects"])
    # A point past the photo is reported before the slot-inside-rack check,
    # so a bad corner is not described as a containment miss.
    if image_path is not None:
        _check_image(doc, Path(image_path))
    _check_slots_inside_racks(doc["objects"])


def image_size(path: str | Path) -> tuple[int, int]:
    """Return ``(width, height)`` of a JPEG, read from its start-of-frame marker."""

    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MasksError(f"cannot read {path}: {exc}") from exc
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise MasksError(f"{path} is not a JPEG")
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            raise MasksError(f"{path} is not a JPEG")
        i += 1
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1
        # Standalone markers carry no length.
        if marker in (0x01, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > n:
            break
        length = int.from_bytes(data[i : i + 2], "big")
        if length < 2 or i + length > n:
            raise MasksError(f"{path} is not a JPEG")
        if marker in _SOF:
            if length < 7:
                raise MasksError(f"{path} is not a JPEG")
            height = int.from_bytes(data[i + 3 : i + 5], "big")
            width = int.from_bytes(data[i + 5 : i + 7], "big")
            if width <= 0 or height <= 0:
                raise MasksError(f"{path} has an empty image")
            return width, height
        i += length
    raise MasksError(f"{path} has no JPEG size marker")


def repo_root() -> Path:
    """Repo root: the directory that holds ``pyproject.toml`` and ``src/wattnot``."""

    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "wattnot").is_dir():
            return parent
    raise MasksError("could not locate the repo root")


def _image_path(directory: Path, name: Any) -> Path | None:
    if not isinstance(name, str) or not _safe_basename(name):
        return None
    return directory / name


def _safe_basename(name: str) -> bool:
    return "/" not in name and "\\" not in name and name not in ("", ".", "..")


def _reject_constant(token: str) -> None:
    raise MasksError(f"JSON number must be finite, got {token}")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    Draft202012Validator.check_schema(MASKS_SCHEMA)
    return Draft202012Validator(MASKS_SCHEMA)


def _check_schema(doc: dict[str, Any]) -> None:
    errors = sorted(_validator().iter_errors(doc), key=lambda err: list(err.absolute_path))
    if not errors:
        return
    err = errors[0]
    where = "/".join(str(part) for part in err.absolute_path) or "mask"
    raise MasksError(f"{where}: {err.message}") from err


def _check_ids(objects: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for obj in objects:
        if obj["id"] in seen:
            raise MasksError(f"duplicate id {obj['id']}")
        seen.add(obj["id"])


def _check_polygon(obj: dict[str, Any]) -> None:
    pts = [(float(x), float(y)) for x, y in obj["polygon"]]
    if pts[0] == pts[-1]:
        raise MasksError(f"{obj['id']}: polygon repeats its first point; omit it")
    for index in range(1, len(pts)):
        if pts[index] == pts[index - 1]:
            raise MasksError(f"{obj['id']}: polygon has a repeated point")
    if not polygon_is_convex(pts):
        raise MasksError(f"{obj['id']}: polygon is not convex")
    if polygon_area(pts) <= 1e-6:
        raise MasksError(f"{obj['id']}: polygon has no area")


def _check_gpus(objects: list[dict[str, Any]]) -> None:
    racks: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    pdus: list[dict[str, Any]] = []
    for obj in objects:
        gpus = obj["gpu_indices"]
        if obj["kind"] == "pdu":
            if gpus:
                raise MasksError(f"{obj['id']}: a pdu lists no gpu indices")
            pdus.append(obj)
        elif obj["kind"] == "slot":
            if len(gpus) != 1:
                raise MasksError(f"{obj['id']}: a slot lists exactly one gpu index")
            slots.append(obj)
        else:
            if len(gpus) < 1:
                raise MasksError(f"{obj['id']}: a rack lists the gpu indices it holds")
            racks.append(obj)
    if not racks:
        raise MasksError("overlay needs at least one rack")
    if not slots:
        raise MasksError("overlay needs at least one slot")
    if not pdus:
        raise MasksError("overlay needs at least one pdu")
    rack_of = _one_owner(racks)
    slot_of = _one_owner(slots)
    for gpu in sorted(set(rack_of) - set(slot_of)):
        raise MasksError(f"gpu {gpu} is on {rack_of[gpu]} but has no slot")
    for gpu in sorted(set(slot_of) - set(rack_of)):
        raise MasksError(f"gpu {gpu} is on {slot_of[gpu]} but is not listed on a rack")


def _one_owner(objects: list[dict[str, Any]]) -> dict[int, str]:
    found: dict[int, str] = {}
    for obj in objects:
        for gpu in obj["gpu_indices"]:
            if gpu in found:
                raise MasksError(f"gpu {gpu} is listed on {found[gpu]} and {obj['id']}")
            found[gpu] = obj["id"]
    return found


def _check_slots_inside_racks(objects: list[dict[str, Any]]) -> None:
    racks = [obj for obj in objects if obj["kind"] == "rack"]
    for slot in objects:
        if slot["kind"] != "slot":
            continue
        gpu = slot["gpu_indices"][0]
        rack = next(obj for obj in racks if gpu in obj["gpu_indices"])
        for point in slot["polygon"]:
            if not point_in_convex_polygon(point, rack["polygon"]):
                raise MasksError(f"{slot['id']} is not inside {rack['id']}")


def _check_image(doc: dict[str, Any], image_path: Path) -> None:
    if not image_path.is_file():
        raise MasksError(
            f"image {image_path.name} is missing next to the mask file ({image_path})"
        )
    width, height = image_size(image_path)
    for obj in doc["objects"]:
        for x, y in obj["polygon"]:
            if x < 0 or y < 0 or x > width or y > height:
                raise MasksError(
                    f"{obj['id']}: polygon point [{x}, {y}] is outside "
                    f"{image_path.name} ({width}x{height})"
                )
