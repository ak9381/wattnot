"""Step 1 of the run: the room scan, with the real H100 in one rack slot.

The scan itself (rack masks from SAM 3.1, a thermal frame, a power cross-check, a decision per
rack) comes from `wattnot.sam.room`. By default it replays SAM's saved raw output, so it costs
nothing; `live_sam=True` segments the photo again (about $0.0025 on the spend ledger).

For the run, one rack slot (`scan["real_rack"]`) is the actual H100. Its numbers are replaced by
readings off the card (NVML): power from the energy counter, temperature from the GPU's own
sensor. That temperature is the die sensor, not a camera surface reading, and the label says so.
The photo, the other racks, their temperatures and the thermal frame stay SIMULATED, as the
scan's own `labels` already say. Before and after for the slot are the stock blocks and the
chosen-cap blocks of the real measurement.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

SLOT_SOURCE = "h100-run2-live"


def build(
    room_id: str = "arsat",
    *,
    live_sam: bool = False,
    out: Path | None = None,
    scan_json: Path | None = None,
    root: Path | None = None,
) -> dict:
    """The room scan dict (public form). Needs `wattnot.sam.room` (or a saved scan JSON)."""
    if scan_json is not None:
        import json

        return json.loads(Path(scan_json).read_text())
    from wattnot.sam import room

    client = None
    if live_sam:
        from wattnot.sam.client import SamClient

        client = SamClient()
    kw: dict[str, Any] = {"live_sam": client, "out": out}
    if root is not None:
        kw["root"] = root
    return room.public(room.build_scan(room_id, **kw))


def slot_of(scan: dict) -> dict:
    sid = scan.get("real_rack") or scan["racks"][0]["id"]
    return next(r for r in scan["racks"] if r["id"] == sid)


def place_h100(scan: dict, snap: dict, *, label: str) -> dict:
    """Put the live reading into the slot, as the "before" (stock cap, under load if measured)."""
    scan = copy.deepcopy(scan)
    r = slot_of(scan)
    live = snap.get("loaded") or snap["idle"]
    r["source"] = SLOT_SOURCE
    r["real_h100"] = True
    r["before"] = {"temp_c": live["temp_c"], "power_w": round(live["power_w"], 1)}
    r["gpu_name"] = snap["idle"]["name"]
    r["cap_w"] = snap["idle"]["cap_w"]
    r["decision"], r["reason"] = "pending", "the run is measuring this card now"
    labels = scan.setdefault("labels", {})
    labels.update(
        {
            "h100_slot_power_w": label,
            "h100_slot_temp_c": label,
            "h100_slot_temp_note": "GPU die sensor (NVML), not a camera surface reading",
            "h100_slot_decision": "MEASURED" if label == "MEASURED" else label,
        }
    )
    scan["real_rack"] = r["id"]
    return scan


def hint(scan: dict) -> dict:
    """What the room scan says about the H100's rack, for the planner prompt (ADVICE)."""
    try:
        from wattnot.sam import room

        t = next(x for x in room.targets(scan) if x["rack"] == scan.get("real_rack"))
        return {
            "rack": t["rack"],
            "action": t["action"],
            "camera_c": t["camera_c"],
            "power_w": t["power_w"],
            "suggested_ceiling_w": t["cap_w"],
            "label": "ADVICE (simulated thermal frame)",
        }
    except Exception:  # noqa: BLE001 - the hint is optional
        return {}


def finalize_h100(
    scan: dict, *, before: dict, after: dict | None, cap_w: int | None, reason: str, label: str
) -> dict:
    """Fill the slot's after and decision from the measured stock vs chosen-cap blocks."""
    scan = copy.deepcopy(scan)
    r = slot_of(scan)
    r["before"] = {"temp_c": before["temp_c"], "power_w": round(before["power_w"], 1)}
    if after is not None and cap_w is not None:
        r["after"] = {"temp_c": after["temp_c"], "power_w": round(after["power_w"], 1)}
        r["decision"], r["cap_w"] = "derate", cap_w
    else:
        r["after"] = dict(r["before"])
        r["decision"] = "ok"
    r["reason"] = reason
    scan["labels"]["h100_slot_decision"] = "MEASURED" if label == "MEASURED" else label
    _recompute(scan)
    return scan


def _recompute(scan: dict) -> None:
    """Room totals from the racks after the slot changed (same sums the scan module uses)."""
    hot = (scan.get("thermal") or {}).get("hot_c", 45.0)
    for phase in ("before", "after"):
        vals = [(x[phase] if phase in x else x["before"]) for x in scan["racks"]]
        temps = [v["temp_c"] for v in vals if v.get("temp_c") is not None]
        scan[phase] = {
            **scan.get(phase, {}),
            "max_c": round(max(temps), 1),
            "mean_c": round(sum(temps) / len(temps), 1),
            "hot_racks": sum(t > hot for t in temps),
            "room_power_w": round(
                sum(
                    v["power_w"] * len(x.get("gpus", [1]))
                    for v, x in zip(vals, scan["racks"], strict=True)
                )
            ),
        }
    scan["after"]["saved_w"] = scan["before"]["room_power_w"] - scan["after"]["room_power_w"]


def payload(scan: dict, phase: str) -> dict:
    """The `room_scan` progress payload (same shape as `wattnot.sam.room.event_payload`)."""
    try:
        from wattnot.sam import room

        return room.event_payload(scan, phase)
    except Exception:  # noqa: BLE001 - module missing: build the same shape here
        after = phase == "after"
        return {
            "kind": "room_scan",
            "phase": phase,
            "room_id": scan.get("room_id"),
            "image": scan.get("image"),
            "labels": scan.get("labels"),
            "racks": [
                {
                    "id": r["id"],
                    "polygon": r.get("polygon"),
                    "temp_c": (r["after"] if after else r["before"])["temp_c"],
                    "power_w": (r["after"] if after else r["before"])["power_w"],
                    "gpus": [g["id"] if isinstance(g, dict) else g for g in r.get("gpus", [])],
                    "decision": r["decision"],
                    "cap_w": r.get("cap_w"),
                    "reason": r["reason"],
                    "source": r.get("source"),
                }
                for r in scan["racks"]
            ],
            "summary": scan["after" if after else "before"],
            "sam": scan.get("sam"),
        }
