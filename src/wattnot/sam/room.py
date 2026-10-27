"""Room scan: SAM finds the racks in a room photo, a thermal frame says which ones run hot,
GPU power telemetry says why, and the answer goes through the same planner and gate.

Pipeline (one pass, offline by default):

1. SAM 3.1 ("server rack") outlines every rack in the room photo. MEASURED: the count, the
   outlines. The ids (R01, R02, ... left to right) and the rack-to-GPU map are SIMULATED.
2. A thermal frame of the same view. Two sources, same code path after this point:
   * a real IR still (`thermal=` path): colour -> degrees with `from_rgb`, ESTIMATE;
   * no IR still: a SIMULATED frame painted from the power replay below.
3. Per rack mask, read the frame: p90 temperature, hotspot ranking.
4. Cross-check with GPU power (REPLAY of the real H100 RUN1 trace, fanned out to every GPU):
   hot and high power -> lower the rack's cap ceiling; hot and low power -> cooling fault,
   hold (a cap would hide the fault, not fix it); otherwise leave it.
5. The ceiling or the hold goes into `RackAudit.thermal`, which the planner, the gate and the
   recommendations already read (see physical.py).

Camera heat is not a power meter. The thermal frame says where it is hot; telemetry says how
much power goes in. The decision needs both. A plain CCTV camera sees no heat at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from wattnot.sam.physical import ESTIMATE, MEASURED, SIMULATED, FleetAudit, RackAudit, RackSpec

REPLAY, ADVICE = "REPLAY", "ADVICE"
PROMPT = "server rack"
ROOT = Path(__file__).resolve().parents[3]

# ---- SAM output -> rack masks -----------------------------------------------------------

_REC = re.compile(r"(?:<\d+f>)?(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)"
                  r"(?:;c=[\d.]+)?\|><\|mask;[^|]*?data=(\d+),(\d+),(.*?)\|>(?=,\d+<\|box|\s*$)",
                  re.S)


@dataclass(frozen=True)
class RackMask:
    id: str
    box: tuple  # x1, y1, x2, y2 in photo pixels
    size: tuple  # photo (width, height)
    raster: np.ndarray  # bool, the mask's own rows x columns, origin at (x1, y1)

    def full(self) -> np.ndarray:
        """A photo-sized boolean mask."""

        w, h = self.size
        out = np.zeros((h, w), bool)
        x1, y1 = self.box[0], self.box[1]
        r = self.raster[: h - y1, : w - x1]
        out[y1:y1 + r.shape[0], x1:x1 + r.shape[1]] = r
        return out


def decode_mask(height: int, width: int, payload: str) -> np.ndarray:
    """One SAM mask payload (`~` lossless or `!` one_bit) to a bool raster. Needs the
    `meta-sam-parser` package (the `vision` extra)."""

    try:
        from meta_sam_parser import SegmentationMask, decode_mask_to_raster
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("install the vision extra: pip install 'wattnot[vision]'") from e
    enc = "lossless" if payload.startswith("~") else "one_bit"
    raw = decode_mask_to_raster(SegmentationMask(enc, payload, width, height))
    return np.frombuffer(raw, np.uint8).reshape(height, width).astype(bool)


def masks_from_raw(raw: str) -> list[RackMask]:
    """Rack masks from SAM's raw text, ids R01.. from left to right (by box centre)."""

    recs = []
    for m in _REC.finditer(raw):
        _, x1, y1, x2, y2, w, h, mh, mw, payload = m.groups()
        recs.append(((int(x1), int(y1), int(x2), int(y2)), (int(w), int(h)),
                     decode_mask(int(mh), int(mw), payload)))
    recs.sort(key=lambda r: (r[0][0] + r[0][2], r[0][1]))
    return [RackMask(f"R{i + 1:02d}", b, s, a) for i, (b, s, a) in enumerate(recs)]


def outline(mask: RackMask, step: int = 6) -> list:
    """A polygon (photo pixels) around the mask: left edge down, right edge back up."""

    f = mask.full()
    rows = np.flatnonzero(f.any(axis=1))
    if rows.size == 0:
        return []
    ys = list(rows[::step]) + ([int(rows[-1])] if rows[-1] % step else [])
    left = [[int(np.flatnonzero(f[y])[0]), int(y)] for y in ys]
    right = [[int(np.flatnonzero(f[y])[-1]) + 1, int(y)] for y in ys]
    return left + right[::-1]


def svg_path(mask: RackMask, step: int = 3) -> str:
    """The exact mask as one SVG path of row runs (for hit testing and drawing)."""

    f = mask.full()
    d = []
    for y in range(0, f.shape[0], step):
        row = f[y:y + step].any(axis=0).astype(np.int8)
        if not row.any():
            continue
        e = np.diff(np.concatenate(([0], row, [0])))
        for a, b in zip(np.flatnonzero(e == 1), np.flatnonzero(e == -1), strict=True):
            d.append(f"M{a} {y}h{b - a}v{step}h{a - b}z")
    return "".join(d)


# ---- colour <-> degrees -----------------------------------------------------------------

_ANCHORS = [(0.0, (0, 0, 4)), (0.12, (40, 0, 110)), (0.30, (120, 10, 140)),
            (0.48, (200, 40, 90)), (0.64, (245, 100, 20)), (0.80, (255, 175, 15)),
            (0.92, (255, 235, 90)), (1.0, (255, 255, 225))]
_X = np.array([a for a, _ in _ANCHORS])
LUT = np.stack([np.interp(np.linspace(0, 1, 256), _X, [c[i] for _, c in _ANCHORS])
                for i in range(3)], axis=1).round().astype(np.uint8)  # black-purple-orange-white


def to_rgb(temp_c: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Paint degrees with the palette (used only for the SIMULATED frame and the legend)."""

    f = np.clip((np.asarray(temp_c, float) - lo) / (hi - lo), 0, 1)
    return LUT[np.round(f * 255).astype(int)]


def _inverse() -> np.ndarray:
    g = (np.arange(32) * 8 + 4).astype(float)
    r, gg, b = np.meshgrid(g, g, g, indexing="ij")
    cells = np.stack([r.ravel(), gg.ravel(), b.ravel()], axis=1)
    d = ((cells[:, None, :] - LUT[None].astype(float)) ** 2).sum(axis=2)
    return (d.argmin(axis=1) / 255.0).reshape(32, 32, 32)


_INV = _inverse()


def from_rgb(img: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Degrees from a palette image: each pixel takes the nearest palette colour, and the
    palette runs linearly from `lo` to `hi`. ESTIMATE: it assumes the camera's colour scale
    spans exactly lo..hi (the tool's auto-range is not in the file), and JPEG blurs colours."""

    q = (np.asarray(img)[..., :3] >> 3).astype(int)
    return lo + _INV[q[..., 0], q[..., 1], q[..., 2]] * (hi - lo)


def read_masks(temps: np.ndarray, masks: list[RackMask]) -> dict:
    """Per rack: p90 / mean / max degrees inside the mask. p90 is the rack's reading."""

    out = {}
    for m in masks:
        v = temps[m.full()]
        out[m.id] = {"p90": float(np.percentile(v, 90)), "mean": float(v.mean()),
                     "max": float(v.max()), "px": int(v.size)} if v.size else None
    return out


# ---- telemetry: REPLAY power, SIMULATED temperature --------------------------------------


def load_trace(path: str | Path = ROOT / "data/room/h100-power-trace.json") -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"t_s": np.array(doc["t_s"], float), "power_w": np.array(doc["power_w"], float),
            "source": doc["source"]}


def gpu_power(trace: dict, rack_i: int, gpu_i: int, load: float) -> np.ndarray:
    """One GPU's power series: the real trace, shifted in time per GPU (so they do not move in
    lockstep) and scaled by a load factor. Shape is REPLAY; the factor is SIMULATED."""

    p = trace["power_w"]
    return np.roll(p, (rack_i * 37 + gpu_i * 11) % len(p)) * load


def surface_c(power_w: float, t_in: float, r_air: float, extra_c: float = 0.0) -> float:
    """What a thermal camera sees on the rack: inlet + R * power (+ a fault). SIMULATED model."""

    return t_in + r_air * power_w + extra_c


def die_c(power_w: np.ndarray, t_in: float, r_die: float = 0.085,
          tau_s: float = 15.0) -> np.ndarray:
    """GPU temperature from power, first order with lag. SIMULATED: RUN1 logged no temperature."""

    a = 1.0 / tau_s
    out, t = np.empty_like(power_w), t_in + r_die * power_w[0]
    for i, p in enumerate(power_w):
        t += a * ((t_in + r_die * p) - t)
        out[i] = t
    return out


# ---- the decision -----------------------------------------------------------------------


def decide(camera_c: float | None, power_w: float, cfg: dict, *, node_max_w: int = 700) -> dict:
    """Hot + high power -> derate; hot + low power -> hold (cooling fault); else ok.

    The new cap is the power at which the rack would sit `margin_c` under the hot line, using
    the model slope r_air (ADVICE: a starting point, the planner and gate still decide).
    """

    if camera_c is None:
        return {"action": "none", "reason": "no thermal reading for this rack"}
    hot, hi_w = cfg["hot_c"], cfg["high_power_w"]
    if camera_c < hot:
        return {"action": "ok", "reason": f"{camera_c:.1f} C is under the {hot:g} C line"}
    if power_w < hi_w:
        return {"action": "hold", "reason": (
            f"hot ({camera_c:.1f} C) at low power ({power_w:.0f} W per GPU): suspect airflow or "
            "a cooling fault; a cap would hide it, not fix it")}
    target = hot - cfg["margin_c"]
    cap = int(max(cfg["floor_w"], min(node_max_w, power_w - (camera_c - target) / cfg["r_air"])))
    return {"action": "derate", "cap_w": cap, "reason": (
        f"hot ({camera_c:.1f} C) at high power ({power_w:.0f} W per GPU): lower the cap "
        f"ceiling to {cap} W")}


# ---- build a room scan --------------------------------------------------------------------


def _paint(masks, temps_by_rack, t_in, lo, hi, size, seed=7):
    """SIMULATED thermal frame: each rack mask gets its rack's surface temperature (warmer
    toward the top, small per-GPU steps), the rest sits at the inlet temperature."""

    w, h = size
    from PIL import Image, ImageDraw, ImageFilter

    t = np.full((h, w), t_in + 1.0)
    for m in masks:
        full = m.full()
        ys = np.flatnonzero(full.any(axis=1))
        if not ys.size:
            continue
        y0, y1 = ys[0], ys[-1]
        frac = (np.arange(h)[:, None] - y0) / max(1, y1 - y0)  # 0 top, 1 bottom
        base, steps = temps_by_rack[m.id]
        k = np.clip(((1 - frac) * len(steps)).astype(int), 0, len(steps) - 1)
        field = base + np.asarray(steps)[k] + 1.5 * (1 - frac)
        t = np.where(full, np.broadcast_to(field, t.shape), t)
    bg = t_in + 1.0
    g8 = Image.fromarray(np.clip((t - lo) / (hi - lo) * 255, 0, 255).astype("uint8"))
    glow = lo + np.asarray(g8.filter(ImageFilter.GaussianBlur(28))) / 255.0 * (hi - lo)
    inside = np.zeros(t.shape, bool)
    for m in masks:
        inside |= m.full()
    t = np.where(inside, t, bg + 0.55 * (glow - bg))  # heat leaks into the air around a rack
    t = t + np.random.default_rng(seed).normal(0, 0.25, t.shape)  # sensor noise
    rgb = to_rgb(t, lo, hi)
    img = Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(1.6))
    ImageDraw.Draw(img).text((14, h - 22), "SIMULATED thermal frame - not a camera image",
                             fill=(255, 255, 255))
    return img, rng_note(seed)


def rng_note(seed):
    return f"seed {seed}"


def _rng(i: int, j: int) -> float:
    """Deterministic jitter in [-1, 1], no global state."""

    return (((i * 7919 + j * 104729) * 2654435761) % 1000) / 500.0 - 1.0


def build_scan(room: str, *, root: Path = ROOT, thermal: str | Path | None = None,
               value_range: tuple | None = None, live_sam=None, out: Path | None = None) -> dict:
    """The room scan JSON (schema in docs/SAM-ROLE.md). Writes the images into `out`.

    `thermal` is a real IR still of the same view: it replaces the SIMULATED frame, and the
    camera labels become ESTIMATE. `value_range` is (low C, high C) for its palette.
    `live_sam` is a SamClient: segment now (costs a cent) instead of replaying saved output.
    """

    from PIL import Image

    rdir = root / "rack" / "room" / room
    cfg = json.loads((root / "rack/room/scenario.json").read_text(encoding="utf-8"))
    sc = cfg["rooms"][room]
    photo = rdir / "photo.jpg"
    if live_sam is not None:
        raw = live_sam.segment(photo.read_bytes(), PROMPT, purpose="sam:room")
        (rdir / "sam-raw").mkdir(exist_ok=True)
        (rdir / "sam-raw" / "photo.server-rack.txt").write_text(raw)
    else:
        raw = (rdir / "sam-raw" / "photo.server-rack.txt").read_text()
    masks = masks_from_raw(raw)
    size = masks[0].size
    trace = load_trace(root / "data/room/h100-power-trace.json")
    n_g, th = cfg["gpus_per_rack"], cfg["thresholds"]
    lo, hi = (value_range or cfg["range_c"])

    # telemetry, per rack and GPU (REPLAY shape x SIMULATED load)
    series, t_now = {}, None
    for i, m in enumerate(masks):
        load = sc["loads"].get(m.id, sc["default_load"])
        series[m.id] = [gpu_power(trace, i, g, load * (1 + 0.04 * _rng(i, g))) for g in range(n_g)]
        if m.id == sc.get("real_rack"):  # the one real H100: its own log, no shift, no scale
            series[m.id][0] = trace["power_w"].copy()
    tot = sum(sum(s) for r in series.values() for s in r)
    t_now = int(np.argmax(tot))
    rack_w = {k: float(np.mean([s[t_now] for s in v])) for k, v in series.items()}

    # the thermal frame and the reading
    faults = sc.get("faults", {})
    t_in, r_air = cfg["t_in_c"], cfg["r_air_c_per_w"]
    if thermal is None:
        paint = {}
        for m in masks:
            base = surface_c(rack_w[m.id], t_in, r_air, faults.get(m.id, 0.0))
            steps = [r_air * (series[m.id][g][t_now] - rack_w[m.id]) for g in range(n_g)]
            paint[m.id] = (base, steps)
        img, _ = _paint(masks, paint, t_in, lo, hi, size)
        cam_label, frame_label, mode = SIMULATED, SIMULATED, "simulated"
        method = ("painted from REPLAY power with a SIMULATED model (inlet + R x power + fault), "
                  "then read back from the pixels with the same palette-to-C function a real "
                  "IR still goes through")
    else:
        img = Image.open(thermal).convert("RGB").resize(size)
        cam_label, frame_label, mode = ESTIMATE, MEASURED, "ir-still"
        method = (f"palette to degrees, linear from {lo:g} C to {hi:g} C (the camera's range is "
                  "assumed, not read from the file); p90 of the pixels inside each SAM mask")
    reading = read_masks(from_rgb(np.asarray(img), lo, hi), masks)

    # decisions and the audit that the planner and gate read
    specs, audits, racks = [], [], []
    for i, m in enumerate(masks):
        gpus = [i * n_g + g for g in range(n_g)]
        cam = reading[m.id]["p90"] if reading[m.id] else None
        d = decide(cam, rack_w[m.id], {**th, "r_air": r_air}, node_max_w=cfg["node_max_w"])
        spec = RackSpec(m.id, str(rdir), cfg["rack_budget_w"], cfg["node_max_w"],
                        {s: g for s, g in enumerate(gpus)})
        base_ceiling = min(cfg["node_max_w"], cfg["rack_budget_w"] // n_g)
        t = None
        if d["action"] in ("derate", "hold"):
            t = {**d, "camera_c": round(cam, 1), "power_w": round(rack_w[m.id]),
                 "source": "SIMULATED frame" if mode == "simulated" else "IR still"}
        ceil = min(base_ceiling, d.get("cap_w", base_ceiling))
        audits.append(RackAudit(spec, None, {g: s for s, g in enumerate(gpus)}, (), (), n_g,
                                ceil, base_ceiling, sam=False, thermal=t))
        specs.append(spec)
        # after: power is held at the cap, the camera temperature drops by r_air x the cut
        p_after = min(rack_w[m.id], d.get("cap_w", rack_w[m.id]))
        cam_after = None if cam is None else cam - r_air * (rack_w[m.id] - p_after)
        racks.append({
            "id": m.id, "box": list(m.box), "polygon": outline(m), "path": svg_path(m),
            "temp_c": None if cam is None else round(cam, 1),
            "temp_max_c": None if cam is None else round(reading[m.id]["max"], 1),
            "source": "h100-run1" if m.id == sc.get("real_rack") else "example",
            "power_w": round(rack_w[m.id]), "gpus": [
                {"id": g, "power_w": round(float(series[m.id][k][t_now])),
                 "measured": bool(m.id == sc.get("real_rack") and k == 0),
                 "gpu_temp_c": round(float(die_c(series[m.id][k], t_in)[t_now]), 1)}
                for k, g in enumerate(gpus)],
            "decision": d["action"], "cap_w": d.get("cap_w"), "reason": d["reason"],
            "before": {"temp_c": None if cam is None else round(cam, 1),
                       "power_w": round(rack_w[m.id])},
            "after": {"temp_c": None if cam_after is None else round(cam_after, 1),
                      "power_w": round(p_after)},
            "series_w": [round(float(v), 1) for v in np.mean(series[m.id], axis=0)[::6]]})
    fleet = FleetAudit(tuple(audits), SIMULATED, "room scan: rack-to-GPU map is a demo input")

    def agg(key):
        t = [r[key]["temp_c"] for r in racks if r[key]["temp_c"] is not None]
        return {"max_c": max(t), "mean_c": round(sum(t) / len(t), 1),
                "hot_racks": sum(1 for x in t if x >= th["hot_c"]),
                "room_power_w": sum(r[key]["power_w"] * n_g for r in racks)}

    before, after = agg("before"), agg("after")
    after["saved_w"] = before["room_power_w"] - after["room_power_w"]
    # SAM off: the hottest pixel cannot be tied to a rack, so the controller cuts every GPU in
    # the room by the same number of watts (the cut that would fix the hottest reading)
    floor, target = th["floor_w"], th["hot_c"] - th["margin_c"]
    cut = max(0.0, (before["max_c"] - target) / r_air)
    lost = [max(0.0, min(cut, r["power_w"] - floor)) for r in racks]
    off = {"action": "derate" if cut else "ok", "cut_w": round(cut),
           "reason": ("without rack masks the hottest pixel cannot be tied to a rack or its "
                      "GPUs, so every GPU is cut by the same watts, and a cooling fault looks "
                      "like load"),
           "racks_cut": sum(1 for x in lost if x > 0),
           "racks_cut_needlessly": sum(1 for x, r in zip(lost, racks, strict=True)
                                       if x > 0 and r["decision"] != "derate"),
           "cooling_faults_missed": sum(1 for r in racks if r["decision"] == "hold"),
           "watts_lost": round(sum(lost) * n_g),
           "watts_lost_with_sam": before["room_power_w"] - after["room_power_w"]}
    hot = sorted((r for r in racks if r["temp_c"] is not None), key=lambda r: -r["temp_c"])
    scan = {
        "format": "wattnot.room/1", "room_id": room, "image": "photo.jpg",
        "thermal_image": "thermal.jpg", "thermal_after_image": None if thermal else
        "thermal_after.jpg", "size": list(size),
        "labels": {"rack_count": MEASURED, "rack_outline": MEASURED, "rack_to_gpu_map": SIMULATED,
                   "thermal_frame": frame_label, "rack_temp_c": cam_label, "gpu_power_w": REPLAY,
                   "gpu_temp_c": SIMULATED, "decision": ADVICE, "after": SIMULATED,
                   "load_factors": SIMULATED, "example_racks": SIMULATED,
                   "h100_gpu_power_w": MEASURED},
        "thermal": {"mode": mode, "range_c": [lo, hi], "method": method, "hot_c": th["hot_c"],
                    "high_power_w": th["high_power_w"], "t_in_c": t_in,
                    "slope_c_per_w": r_air, "t_index": t_now},
        "sam": {"model": "sam-3.1", "prompt": PROMPT, "racks_found": len(masks)},
        "power_source": trace["source"],
        "real_rack": sc.get("real_rack"),
        "racks": racks, "hotspots": [r["id"] for r in hot if r["temp_c"] >= th["hot_c"]],
        "before": before, "after": after, "sam_off": off}
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        Image.open(photo).convert("RGB").save(out / "photo.jpg", quality=88)
        img.save(out / "thermal.jpg", quality=90)
        if thermal is None:
            # after: same masks, rack temperatures recomputed at the capped power
            paint2 = {}
            for r, m in zip(racks, masks, strict=True):
                steps = [0.0] * n_g
                paint2[m.id] = (r["after"]["temp_c"] or t_in, steps)
            img2, _ = _paint(masks, paint2, t_in, lo, hi, size)
            img2.save(out / "thermal_after.jpg", quality=90)
        (out / "scan.json").write_text(json.dumps(scan, separators=(",", ":")), encoding="utf-8")
    scan["_fleet"] = fleet  # for the planner path; stripped before JSON
    return scan


def read_ir_still(path: str | Path, lo: float, hi: float, bands: int = 8) -> dict:
    """Read a real IR still with no masks: whole-frame stats and `bands` horizontal strips
    (rack-unit heights), hottest first. ESTIMATE: palette to degrees, see `from_rgb`."""

    from PIL import Image

    t = from_rgb(np.asarray(Image.open(path).convert("RGB")), lo, hi)
    h = t.shape[0]
    rows = []
    for i in range(bands):
        v = t[i * h // bands:(i + 1) * h // bands]
        rows.append({"band": i + 1, "p90_c": round(float(np.percentile(v, 90)), 1),
                     "mean_c": round(float(v.mean()), 1)})
    return {"label": ESTIMATE, "range_c": [lo, hi], "p90_c": round(float(np.percentile(t, 90)), 1),
            "mean_c": round(float(t.mean()), 1), "max_c": round(float(t.max()), 1),
            "bands_top_to_bottom": rows,
            "hottest_band": max(rows, key=lambda r: r["p90_c"])["band"]}


def targets(scan: dict) -> list[dict]:
    """Candidate targets for Muse Spark in RUN2, most urgent first. Numbers and ids only: no
    images, no host names. `derate` racks come first (hot at high power: a cap helps), then
    `hold` (hot at low power: fix cooling, do not cap), then racks with the least headroom.
    The planner and the gate still decide; this only orders the question."""

    th = scan["thermal"]["hot_c"]
    rank = {"derate": 0, "hold": 1, "ok": 2, "none": 3}
    out = []
    for r in scan["racks"]:
        out.append({"rack": r["id"], "gpus": [g["id"] for g in r["gpus"]], "action": r["decision"],
                    "camera_c": r["temp_c"], "power_w": r["power_w"], "cap_w": r["cap_w"],
                    "headroom_c": None if r["temp_c"] is None else round(th - r["temp_c"], 1),
                    "real_h100": r["source"] == "h100-run1", "reason": r["reason"]})
    out.sort(key=lambda t: (rank[t["action"]],
                            99 if t["headroom_c"] is None else t["headroom_c"]))
    return out


def event_payload(scan: dict, phase: str) -> dict:
    """The `room_scan` payload for one phase ("before" or "after"). Compact: per rack the
    outline, the reading, the power and the decision. Same shape for both phases."""

    after = phase == "after"
    racks = []
    for r in scan["racks"]:
        v = r["after"] if after else r["before"]
        racks.append({"id": r["id"], "polygon": r["polygon"], "temp_c": v["temp_c"],
                      "power_w": v["power_w"], "gpus": [g["id"] for g in r["gpus"]],
                      "decision": r["decision"], "cap_w": r["cap_w"], "reason": r["reason"],
                      "source": r["source"]})
    agg = scan["after"] if after else scan["before"]
    return {"kind": "room_scan", "phase": phase, "room_id": scan["room_id"],
            "image": scan["image"],
            "thermal_image": (scan["thermal_after_image"] or scan["thermal_image"]) if after
            else scan["thermal_image"],
            "labels": scan["labels"], "thermal_mode": scan["thermal"]["mode"],
            "range_c": scan["thermal"]["range_c"], "hot_c": scan["thermal"]["hot_c"],
            "racks": racks, "summary": agg, "hotspots": scan["hotspots"],
            "targets": targets(scan) if not after else None,
            "sam": scan["sam"]}


def emit_events(scan: dict, emitter, *, step_id: str = "room-scan",
                parent_step_id: str | None = None) -> list[dict]:
    """Write the room scan into a run's event log, step 1 of a run. Uses only event types
    the frozen schema already has: `step.started`, two `step.progress` events whose data
    carries the `room_scan` payload (before, then after the planned caps) and `step.finished`.
    The UI reads `data.kind == "room_scan"`."""

    kw = {"step_id": step_id, "parent_step_id": parent_step_id}
    out = [emitter.emit("step.started", {"kind": "room_scan", "label": "Room scan",
                                         "agent": "wattnot", "model": "sam-3.1",
                                         "effort": None}, **kw)]
    out.append(emitter.emit("step.progress", {"pct": 50, "message": (
        f"SAM 3.1 found {scan['sam']['racks_found']} racks; "
        f"{len(scan['hotspots'])} over the {scan['thermal']['hot_c']:g} C line"),
        **event_payload(scan, "before")}, **kw))
    out.append(emitter.emit("step.progress", {"pct": 100, "message": (
        f"after the planned caps: {scan['after']['saved_w']} W less, "
        f"{scan['after']['hot_racks']} racks still over the line"),
        **event_payload(scan, "after")}, **kw))
    out.append(emitter.emit("step.finished", {"status": "ok", "duration_ms": 0, "tokens": None,
                                              "meta_cost_usd": 0.0025, "retry": 0}, **kw))
    return out


def public(scan: dict) -> dict:
    return {k: v for k, v in scan.items() if not k.startswith("_")}


__all__ = ["RackMask", "build_scan", "decide", "from_rgb", "masks_from_raw", "public",
           "read_ir_still", "read_masks", "to_rgb", "targets", "event_payload", "emit_events"]
