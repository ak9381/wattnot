"""SAM synthetic hotspot set: a detector self-test for SAM 3.1.

A small, fixed set of SYNTHETIC IR-style thermal FAILURE scenes (blocked fan,
clogged heatsink, cable blocking airflow) lives in the repo
(``data/vision/hotspots``). Each scene carries a ``SYNTHETIC`` watermark and a
ground-truth hotspot region (bbox + polygon). This module runs the SAM 3.1
hotspot detector (``wattnot.sam.hotspot_cap.detect_hotspot_mask``) over the set
and scores each detection by IoU against ground truth, so a detector that
stops finding the hot slot is caught before anything relies on it.

Everything is labelled ``SYNTHETIC`` — these are generated test scenes, never a
real thermal measurement. The gate returns the same ``GateResult`` shape as the
eye test / quality / p99 gates, so ``wattnot.gates.decide`` can run it in the
keep/reject loop, and the result is carried on the signed receipt.

The images themselves are generated once by ``scripts/gen_vision_assets.py`` (a
paid, one-time script never run by tests); the files checked in are offline
placeholders so this module and its tests always have something to read.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wattnot.gates.result import GateResult
from wattnot.sam.compare import polygon_iou
from wattnot.sam.hotspot_cap import detect_hotspot_mask

# The set directory, relative to the repo root (…/data/vision/hotspots).
DEFAULT_SET_DIR = Path(__file__).resolve().parents[3] / "data" / "vision" / "hotspots"

# ESTIMATE energy stamp per generated image. Not metered; always labelled
# ESTIMATE on the receipt and in the UI. Overridden by the manifest if present.
EST_WH_PER_IMAGE = 0.5

# Minimum IoU for a detection to count as a hit (overridden by the manifest).
DEFAULT_IOU_PASS = 0.5

# The thermal failure modes the synthetic set exercises.
FAULTS = ("blocked_fan", "clogged_heatsink", "cable_airflow")


@dataclass(frozen=True)
class HotspotScene:
    id: str
    file: str
    fault: str
    slot: int
    scene: str
    bbox: tuple[int, int, int, int]
    polygon: tuple[tuple[int, int], ...]
    label: str = "SYNTHETIC"

    def path(self, base: Path) -> Path:
        return base / self.file


@dataclass(frozen=True)
class HotspotSet:
    scenes: tuple[HotspotScene, ...]
    label: str
    wh_per_image: float
    iou_pass: float
    canvas: tuple[int, int]
    base_dir: Path

    def __iter__(self):
        return iter(self.scenes)

    def __len__(self) -> int:
        return len(self.scenes)

    @property
    def ids(self) -> list[str]:
        return [s.id for s in self.scenes]


def _bbox_from(region: Mapping[str, Any]) -> tuple[int, int, int, int]:
    b = region.get("bbox")
    if b and len(b) == 4:
        return (int(b[0]), int(b[1]), int(b[2]), int(b[3]))
    poly = region.get("polygon") or []
    xs = [int(p[0]) for p in poly]
    ys = [int(p[1]) for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def _poly_from(region: Mapping[str, Any]) -> tuple[tuple[int, int], ...]:
    poly = region.get("polygon")
    if poly:
        return tuple((int(p[0]), int(p[1])) for p in poly)
    x1, y1, x2, y2 = _bbox_from(region)
    return ((x1, y1), (x2, y1), (x2, y2), (x1, y2))


def load_hotspot_set(set_dir: str | Path | None = None, *,
                     require_files: bool = False) -> HotspotSet:
    """Read ``scenes.json`` and return the parsed, validated hotspot set.

    ``require_files=True`` also checks every image file exists (used by the
    one-time generator and by live runs, not by the fixture tests).
    """

    base = Path(set_dir) if set_dir is not None else DEFAULT_SET_DIR
    manifest = json.loads((base / "scenes.json").read_text(encoding="utf-8"))
    label = str(manifest.get("label", "SYNTHETIC"))
    if label != "SYNTHETIC":
        raise ValueError(f"hotspot-set assets must be labelled SYNTHETIC, got {label!r}")
    wh = float((manifest.get("energy_per_image_wh") or {}).get("value", EST_WH_PER_IMAGE))
    iou_pass = float(manifest.get("iou_pass", DEFAULT_IOU_PASS))
    canvas_raw = manifest.get("canvas") or {}
    canvas = (int(canvas_raw.get("w", 640)), int(canvas_raw.get("h", 960)))

    scenes: list[HotspotScene] = []
    seen: set[str] = set()
    for raw in manifest.get("scenes", []):
        fault = raw.get("fault")
        if fault not in FAULTS:
            raise ValueError(f"unknown hotspot fault {fault!r} for scene {raw.get('id')!r}")
        region = raw.get("hotspot") or {}
        scene = HotspotScene(
            id=str(raw["id"]), file=str(raw["file"]), fault=str(fault),
            slot=int(raw["slot"]), scene=str(raw.get("scene", "")),
            bbox=_bbox_from(region), polygon=_poly_from(region),
            label=str(raw.get("watermark", "SYNTHETIC")),
        )
        if scene.id in seen:
            raise ValueError(f"duplicate hotspot scene id {scene.id!r}")
        seen.add(scene.id)
        if require_files and not scene.path(base).exists():
            raise FileNotFoundError(f"missing hotspot scene image {scene.path(base)}")
        scenes.append(scene)
    if not scenes:
        raise ValueError("hotspot-set manifest has no scenes")
    return HotspotSet(tuple(scenes), label, wh, iou_pass, canvas, base)


# A detector takes one scene and returns a mask dict ({bbox, polygon, label}).
Detector = Callable[[HotspotScene], Mapping[str, Any]]


class OfflineDetector:
    """Runs the SAM 3.1 hotspot detector in deterministic mock mode.

    For each scene it calls ``detect_hotspot_mask(target_gpu=slot, mock=True)``,
    which returns the canonical slot polygon. No image is read and no network
    call is made, so this is what ``--fake`` runs and tests use. It exercises
    the detector plumbing; it is NOT a real thermal measurement (masks come
    back labelled ``MOCK``).
    """

    def __call__(self, scene: HotspotScene) -> Mapping[str, Any]:
        return detect_hotspot_mask(target_gpu=scene.slot, mock=True)


class LiveDetector:
    """Runs the real SAM 3.1 segmenter over the scene image. Live only.

    Passes the scene photo to ``detect_hotspot_mask(live=True)`` and returns
    whatever box SAM honestly produced (``MEASURED``), or the canonical slot
    fallback (``SIMULATED``) when SAM returns nothing. Only touches the network
    when called, so importing this module stays side-effect free.
    """

    def __init__(self, base_dir: Path, *, ledger: Any = None) -> None:
        self.base_dir = Path(base_dir)
        self.ledger = ledger

    def __call__(self, scene: HotspotScene) -> Mapping[str, Any]:
        return detect_hotspot_mask(
            photo_path=scene.path(self.base_dir), target_gpu=scene.slot,
            mock=False, live=True, ledger=self.ledger)


def detect_hotspots(hset: HotspotSet, detector: Detector, *, budget_s: float | None = None,
                    clock: Callable[[], float] = time.monotonic) -> tuple[dict[str, dict], dict]:
    """Run ``detector`` over the set, stopping early if ``budget_s`` is spent.

    Returns ``(masks, meta)``. Scenes left undetected by a budget cut are simply
    absent, which the gate scores as a miss (same contract as the eye test).
    """

    masks: dict[str, dict] = {}
    t0 = clock()
    budget_hit = False
    for scene in hset.scenes:
        if budget_s is not None and clock() - t0 >= budget_s:
            budget_hit = True
            break
        masks[scene.id] = dict(detector(scene))
    meta = {
        "detected": len(masks),
        "total": len(hset),
        "budget_s": budget_s,
        "budget_hit": budget_hit,
        "elapsed_s": round(clock() - t0, 6),
    }
    return masks, meta


def score_iou(mask: Mapping[str, Any], scene: HotspotScene) -> float:
    """IoU of the detected region against the scene's ground-truth hotspot.

    Both regions are axis-aligned rectangles (convex), so ``polygon_iou`` is
    exact. A detection with no usable geometry scores 0."""

    try:
        got = _poly_from(mask)
        truth = [list(p) for p in scene.polygon]
        return round(polygon_iou([list(p) for p in got], truth), 6)
    except (ValueError, TypeError, KeyError, IndexError):
        return 0.0


def grade_hotspots(masks: Mapping[str, Mapping[str, Any]], scenes: Sequence[HotspotScene], *,
                   iou_pass: float = DEFAULT_IOU_PASS,
                   wh_per_image: float = EST_WH_PER_IMAGE) -> list[dict]:
    """Per-scene results. A scene with no detection (or IoU below ``iou_pass``)
    is a miss. Each scene carries an ESTIMATE energy stamp."""

    rows: list[dict] = []
    for sc in scenes:
        mask = masks.get(sc.id)
        iou = score_iou(mask, sc) if mask else 0.0
        rows.append({
            "id": sc.id,
            "fault": sc.fault,
            "slot": sc.slot,
            "expected_bbox": list(sc.bbox),
            "got_bbox": list(_bbox_from(mask)) if mask else [],
            "iou": iou,
            "ok": bool(mask is not None and iou >= iou_pass),
            "mask_label": str((mask or {}).get("label", "")) or "none",
            "label": sc.label or "SYNTHETIC",
            "energy_wh": {"value": round(float(wh_per_image), 6), "label": "ESTIMATE"},
        })
    return rows


def hotspot_set_gate(masks: Mapping[str, Mapping[str, Any]], scenes: Sequence[HotspotScene], *,
                     iou_pass: float = DEFAULT_IOU_PASS,
                     wh_per_image: float = EST_WH_PER_IMAGE) -> GateResult:
    """Pass only if the detector finds every SYNTHETIC hotspot (IoU >= iou_pass)."""

    scenes = list(scenes)
    rows = grade_hotspots(masks, scenes, iou_pass=iou_pass, wh_per_image=wh_per_image)
    missed = [r["id"] for r in rows if not r["ok"]]
    checked = len(rows)
    passed = checked > 0 and not missed
    faults = sorted({r["fault"] for r in rows})
    detail = {
        "checked": checked,
        "passed_count": checked - len(missed),
        "missed": missed,
        "scenes": rows,
        "faults": faults,
        "label": "SYNTHETIC",
        "iou_pass": round(float(iou_pass), 6),
        "est_wh_per_image": round(float(wh_per_image), 6),
        "blocks_musecode_pr": bool(missed),
    }
    if checked == 0:
        return GateResult("hotspot_set", False, "no SYNTHETIC hotspot scenes to check", detail)
    if passed:
        return GateResult("hotspot_set", True,
                          f"SAM 3.1 found all {checked} SYNTHETIC hotspots", detail)
    show = ", ".join(missed[:6]) + (" …" if len(missed) > 6 else "")
    return GateResult("hotspot_set", False,
                      f"SAM 3.1 missed {len(missed)}/{checked} SYNTHETIC hotspot(s): {show}", detail)


def run_hotspot_set(set_dir: str | Path | None = None, *, fake: bool = True,
                    budget_s: float | None = None, ledger: Any = None) -> dict:
    """Run the self-test once and return a summary dict (used by the test plan).

    ``fake=True`` (the default) uses the offline deterministic detector: no
    network, no paid call, not a measurement. A live run passes ``fake=False``
    to reach the real SAM 3.1 segmenter over each scene image.
    """

    require = not fake
    hset = load_hotspot_set(set_dir, require_files=require)
    detector: Detector = OfflineDetector() if fake else LiveDetector(hset.base_dir, ledger=ledger)
    masks, run_meta = detect_hotspots(hset, detector, budget_s=budget_s)
    result = hotspot_set_gate(masks, hset.scenes, iou_pass=hset.iou_pass,
                              wh_per_image=hset.wh_per_image)
    return {
        "spec": "wattnot.hotspotset/1",
        "mode": "fake" if fake else "live",
        "detector": "SAM 3.1",
        "label": "SYNTHETIC",
        "passed": result.passed,
        "reason": result.reason,
        "run": run_meta,
        "detail": result.detail,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="WattNot SAM 3.1 synthetic hotspot-set self-test (SYNTHETIC tests)")
    ap.add_argument("--assets", default=None, help="hotspot-set dir (default: data/vision/hotspots)")
    ap.add_argument("--out", default=None, help="write hotspot_set.json here")
    ap.add_argument("--budget-s", type=float, default=None, help="wall-clock budget for the set")
    ap.add_argument("--fake", action="store_true", help="offline detector (no network, not measured)")
    ap.add_argument("--live", action="store_true", help="use the real SAM 3.1 segmenter (paid)")
    args = ap.parse_args(argv)

    fake = not args.live
    summary = run_hotspot_set(args.assets, fake=fake, budget_s=args.budget_s)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "hotspot_set.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"hotspot set [{summary['mode']}] (SYNTHETIC tests): {summary['reason']}"
          + (f" (budget {args.budget_s}s hit)" if summary["run"].get("budget_hit") else ""))
    for row in summary["detail"].get("scenes", []):
        if not row["ok"]:
            print(f"  MISS {row['id']} ({row['fault']}): IoU {row['iou']} < {summary['detail']['iou_pass']}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
