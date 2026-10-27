"""Rack photo audit: what SAM can see decides which GPUs WattNot may touch, and how hard.

The site inventory (a spreadsheet, a CMDB) says which GPU node sits in which rack slot. It
can be wrong: a node was pulled, a box was added. SAM 3.1 outlines the server units in the
rack photo. This module reconciles the two and returns, per rack:

* which declared GPUs have a unit outlined at their slot (`verified`) and which do not,
* units SAM sees that the inventory does not list (`unlisted`; they draw power too),
* a per-node power ceiling: the rack budget divided by the nodes that are really there.

The planner, the recommendations and the approval gate read that result. See
docs/SAM-ROLE.md for how, and what is measured versus assumed.

Labels:

* the unit boxes and counts are MEASURED from the photo (SAM output, replayable offline);
* the inventory, the slot-to-GPU map, the rack budget and node power are inputs we assume
  for the demo: SIMULATED when they come from `rack/audit/fleet.json`;
* a ceiling in watts is an ESTIMATE: budget / nodes, GPU power only.

SAM is not trusted blindly. A unit it misses is "unverified" and is left alone; a box it
invents beyond the inventory only tightens the ceiling. Both errors fail on the safe side.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from wattnot.sam.sam_parse import parse_boxes

PROMPTS = ("1U server", "rack server", "server")  # fine boxes first, then stack-wide boxes
MAX_AREA_FRAC = 0.85  # a box this large is the whole rack, not a unit
MIN_STACK_PITCHES = 1.6  # a box this many unit-heights tall may hide several units
MIN_GAP_PITCHES = 0.6  # empty space this tall (in unit heights) counts as a free slot
DEDUPE_IOU = 0.5
SOURCE = "sam-3.1 photo audit"

MEASURED, ESTIMATE, SIMULATED = "MEASURED", "ESTIMATE", "SIMULATED"


class NotVerified(RuntimeError):
    """A GPU has no unit outlined in the rack photo, so WattNot will not change it."""


# ---- boxes -> units ---------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    slot: int  # 0 = top unit in the photo, counting down (empty slots count too)
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in photo pixels
    how: str  # "outlined" (SAM drew this box) | "inferred" (stack height / unit height)

    def to_dict(self) -> dict:
        return {"slot": self.slot, "box": [round(v) for v in self.box], "how": self.how}


@dataclass(frozen=True)
class Scan:
    image: str
    width: int
    height: int
    pitch_px: float
    units: tuple[Unit, ...]
    prompt_boxes: dict  # prompt -> number of boxes SAM returned (kept as evidence)
    sha256: str = ""

    @property
    def slots(self) -> list[int]:
        return [u.slot for u in self.units]

    def to_dict(self) -> dict:
        return {"image": self.image, "size": [self.width, self.height],
                "pitch_px": round(self.pitch_px, 1), "units": [u.to_dict() for u in self.units],
                "prompt_boxes": self.prompt_boxes, "sha256": self.sha256, "label": MEASURED,
                "source": SOURCE}


def _box(b: Mapping) -> tuple[float, float, float, float]:
    return (b["x1"], b["y1"], b["x2"], b["y2"])


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua


def units_from_raw(raw: Mapping[str, str], *, image: str = "", sha256: str = "") -> Scan:
    """Server units from SAM's raw output, one text per prompt. Pure: no network, no files.

    1. The prompt that outlined the most separate boxes gives the unit boxes.
    2. A stack-wide box from another prompt can cover units SAM did not split. Space it
       covers that no unit box does is cut into units one unit-height tall (`inferred`).
    3. Free space between units is counted so slot numbers match the rack.
    """

    per_prompt: dict[str, list[dict]] = {p: parse_boxes(t) for p, t in raw.items()}
    sizes = [(b["w"], b["h"]) for bs in per_prompt.values() for b in bs if "w" in b and "h" in b]
    width, height = (int(sizes[0][0]), int(sizes[0][1])) if sizes else (0, 0)
    counts = {p: len(bs) for p, bs in per_prompt.items()}

    def whole_rack(b: Mapping) -> bool:
        area = (b["x2"] - b["x1"]) * (b["y2"] - b["y1"])
        return width > 0 and area > MAX_AREA_FRAC * width * height

    cands = {p: [b for b in bs if not whole_rack(b)] for p, bs in per_prompt.items()}
    best = max(cands, key=lambda p: len(cands[p]), default=None)
    fine = sorted((_box(b) for b in cands[best]), key=lambda b: b[1]) if best else []
    if not fine:
        return Scan(image, width, height, 0.0, (), counts, sha256)

    pitch = statistics.median(b[3] - b[1] for b in fine)
    found: list[tuple[tuple[float, float, float, float], str]] = [(b, "outlined") for b in fine]

    # stack-wide boxes from the other prompts: cut uncovered space into units
    wide = [_box(b) for p, bs in cands.items() if p != best for b in bs
            if b["y2"] - b["y1"] >= MIN_STACK_PITCHES * pitch]
    for w in wide:
        covered = sorted((b for b, _ in found
                          if _iou(b, w) > 0 or (b[1] >= w[1] - 1 and b[3] <= w[3] + 1)),
                         key=lambda b: b[1])
        cursor = w[1]
        spans = []
        for b in covered:
            if b[1] - cursor >= MIN_GAP_PITCHES * pitch:
                spans.append((cursor, b[1]))
            cursor = max(cursor, b[3])
        if w[3] - cursor >= MIN_GAP_PITCHES * pitch:
            spans.append((cursor, w[3]))
        for y1, y2 in spans:
            n = max(1, round((y2 - y1) / pitch))
            step = (y2 - y1) / n
            for k in range(n):
                found.append(((w[0], y1 + k * step, w[2], y1 + (k + 1) * step), "inferred"))

    # drop duplicates (keep the outlined one), order top to bottom
    found.sort(key=lambda t: (t[1] != "outlined", t[0][1]))
    kept: list[tuple[tuple[float, float, float, float], str]] = []
    for b, how in found:
        if all(_iou(b, k) < DEDUPE_IOU for k, _ in kept):
            kept.append((b, how))
    kept.sort(key=lambda t: t[0][1])

    units, slot, prev_y2 = [], 0, None
    for b, how in kept:
        if prev_y2 is not None:
            gap = b[1] - prev_y2
            if gap >= MIN_GAP_PITCHES * pitch:
                slot += round(gap / pitch)  # free slots between units
            slot += 1
        units.append(Unit(slot, b, how))
        prev_y2 = b[3]
    return Scan(image, width, height, pitch, tuple(units), counts, sha256)


def load_raw(directory: str | Path, prompts: Iterable[str] = PROMPTS) -> dict[str, str]:
    """Saved SAM output for a photo: `<directory>/sam-raw/<prompt with _ for spaces>.txt`."""

    d = Path(directory) / "sam-raw"
    out = {}
    for p in prompts:
        f = d / f"{p.replace(' ', '_')}.txt"
        if f.is_file():
            out[p] = f.read_text(encoding="utf-8")
    return out


def scan_dir(directory: str | Path, photo: str = "rack.jpg") -> Scan:
    """Replay a saved scan: the photo's units from its saved SAM output. Offline."""

    d = Path(directory)
    img = d / photo
    sha = hashlib.sha256(img.read_bytes()).hexdigest() if img.is_file() else ""
    return units_from_raw(load_raw(d), image=photo, sha256=sha)


def scan_live(directory: str | Path, client, photo: str = "rack.jpg",
              prompts: Iterable[str] = PROMPTS) -> Scan:
    """Call SAM once per prompt (guarded by the spend ledger), save the raw output, scan it."""

    d = Path(directory)
    data = (d / photo).read_bytes()
    out = d / "sam-raw"
    out.mkdir(exist_ok=True)
    for p in prompts:
        text = client.segment(data, p, purpose="sam:rack-audit")
        (out / f"{p.replace(' ', '_')}.txt").write_text(text, encoding="utf-8")
    return scan_dir(d, photo)


# ---- units + inventory -> audit ---------------------------------------------------------


@dataclass(frozen=True)
class RackSpec:
    """One rack as the site inventory describes it. Demo values live in rack/audit/fleet.json."""

    id: str
    directory: str
    budget_w: int  # GPU power the rack may draw (breaker or contract), ESTIMATE
    node_max_w: int  # most one node may be capped at (its driver or board limit)
    declared: dict  # slot -> GPU index, as listed in the inventory
    photo: str = "rack.jpg"
    gpus_per_node: int = 1  # demo: one GPU per photographed unit


@dataclass(frozen=True)
class PhysicalLimit:
    """What the planner and the gate need to know about one GPU."""

    gpu: int
    rack: str
    eligible: bool
    ceiling_w: int
    reason: str


@dataclass(frozen=True)
class RackAudit:
    spec: RackSpec
    scan: Scan | None
    verified: dict  # GPU index -> slot with a unit outlined there
    unverified: tuple  # declared GPU indices with no unit at their slot
    unlisted: tuple  # slots with a unit that the inventory does not list
    present_nodes: int  # units seen in the photo (SAM-off: the declared count)
    ceiling_w: int  # per-node ceiling for the nodes that are there
    declared_ceiling_w: int  # the same, trusting the inventory
    sam: bool = True

    @property
    def over_budget_w_if_trusting_inventory(self) -> int:
        """Watts above budget if every real node ran at the inventory's ceiling (ESTIMATE)."""

        return max(0, self.present_nodes * self.declared_ceiling_w - self.spec.budget_w)

    def limit_for(self, gpu: int) -> PhysicalLimit:
        s = self.spec
        if gpu in self.verified:
            why = (f"unit seen at slot {self.verified[gpu]}" if self.sam
                   else f"listed at slot {self.verified[gpu]} (inventory only, no photo check)")
            return PhysicalLimit(gpu, s.id, True, self.ceiling_w, why)
        if gpu in self.unverified:
            slot = next(k for k, g in s.declared.items() if g == gpu)
            return PhysicalLimit(gpu, s.id, False, 0,
                                 f"no unit outlined at slot {slot} in the {s.id} photo")
        return PhysicalLimit(gpu, s.id, False, 0, f"GPU {gpu} is not in rack {s.id}")

    def recommend_opts(self) -> dict:
        """Arguments for `recommend.for_run`: the rack as it is, not as the inventory says."""

        return {"gpus_per_rack": max(1, self.present_nodes), "rack_budget_w": self.spec.budget_w}

    def recommend_items(self, gpu: int, *, start_cap_w: int | None = None) -> list:
        """Recommendation items for this rack. Counts from the photo are MEASURED; every watt
        figure is an ESTIMATE (budget and node power are assumed inputs); advice has no number."""

        from wattnot.recommend import ADVICE, Item

        s, n, m = self.spec, self.present_nodes, len(self.spec.declared)
        if not self.sam:
            return [Item(ADVICE, "no rack photo audit: the inventory is trusted as written, "
                         "so the per-node ceiling assumes every listed node is present",
                         "physical_audit", "off")]
        items = [Item(MEASURED, f"rack photo audit: SAM 3.1 found {n} server units in rack "
                      f"{s.id}; the inventory lists {m}", "physical_nodes_seen", n, "units"),
                 Item(ESTIMATE, f"per-node cap ceiling for rack {s.id}: {self.ceiling_w} W "
                      f"({s.budget_w:,} W rack budget / {n} nodes, GPU power only)",
                      "physical_ceiling_w", self.ceiling_w, "W")]
        if self.unverified:
            ids = ", ".join(str(g) for g in self.unverified)
            items.append(Item(ADVICE, "GPUs with no unit in the photo were left alone; check "
                              "the rack and fix the inventory", "physical_unverified_gpus", ids))
        if self.unlisted:
            items.append(Item(ADVICE, "units in the photo that the inventory does not list "
                              "draw power too: add them, or find out what they are",
                              "physical_unlisted_units", len(self.unlisted), "units"))
            over = self.over_budget_w_if_trusting_inventory
            if over:
                items.append(Item(ESTIMATE, f"trusting the inventory, {n} nodes at "
                                  f"{self.declared_ceiling_w} W would draw "
                                  f"{n * self.declared_ceiling_w:,} W against a "
                                  f"{s.budget_w:,} W budget ({over:,} W over)",
                                  "physical_over_budget_w", over, "W"))
        if start_cap_w is not None and start_cap_w > self.ceiling_w:
            items.append(Item(ESTIMATE, f"the starting cap of {start_cap_w} W is above this "
                              f"rack's ceiling of {self.ceiling_w} W; the planner will not go "
                              "above the ceiling", "physical_start_over_ceiling_w",
                              start_cap_w - self.ceiling_w, "W"))
        return items

    def summary(self) -> dict:
        """The small, number-only block Muse Spark sees. No images, no host names."""

        s = self.spec
        return {"rack": s.id, "nodes_declared": len(s.declared), "nodes_seen": self.present_nodes,
                "unverified_gpus": list(self.unverified), "unlisted_units": len(self.unlisted),
                "ceiling_w": self.ceiling_w, "budget_w": s.budget_w,
                "source": SOURCE if self.sam else "inventory only"}

    def to_dict(self) -> dict:
        s = self.spec
        return {"rack": s.id, "sam": self.sam, "budget_w": s.budget_w, "node_max_w": s.node_max_w,
                "declared": {str(k): v for k, v in sorted(s.declared.items())},
                "verified": {str(g): sl for g, sl in sorted(self.verified.items())},
                "unverified": list(self.unverified), "unlisted_slots": list(self.unlisted),
                "present_nodes": self.present_nodes, "ceiling_w": self.ceiling_w,
                "declared_ceiling_w": self.declared_ceiling_w,
                "over_budget_w_if_trusting_inventory": self.over_budget_w_if_trusting_inventory,
                "labels": {"units": MEASURED if self.sam else SIMULATED,
                           "inventory": SIMULATED, "budget_w": ESTIMATE, "ceiling_w": ESTIMATE},
                "scan": self.scan.to_dict() if self.scan else None}


def _ceiling(budget_w: int, nodes: int, node_max_w: int) -> int:
    return node_max_w if nodes <= 0 else min(node_max_w, budget_w // nodes)


def audit_rack(spec: RackSpec, scan: Scan | None) -> RackAudit:
    """Reconcile one rack. `scan=None` is the SAM-off baseline: trust the inventory."""

    declared = {int(k): int(v) for k, v in spec.declared.items()}
    d_ceiling = _ceiling(spec.budget_w, len(declared), spec.node_max_w)
    if scan is None:
        return RackAudit(spec, None, {g: k for k, g in declared.items()}, (), (),
                         len(declared), d_ceiling, d_ceiling, sam=False)
    seen = set(scan.slots)
    verified = {g: k for k, g in declared.items() if k in seen}
    unverified = tuple(sorted(g for k, g in declared.items() if k not in seen))
    unlisted = tuple(sorted(k for k in seen if k not in declared))
    nodes = len(scan.units)
    return RackAudit(spec, scan, verified, unverified, unlisted, nodes,
                     _ceiling(spec.budget_w, nodes, spec.node_max_w), d_ceiling)


# ---- the fleet --------------------------------------------------------------------------


@dataclass(frozen=True)
class FleetAudit:
    racks: tuple[RackAudit, ...]
    label: str = SIMULATED  # the inventory is demo data
    note: str = ""
    _by_gpu: dict = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        by_gpu = {}
        for r in self.racks:
            for g in r.spec.declared.values():
                by_gpu[int(g)] = r
        object.__setattr__(self, "_by_gpu", by_gpu)

    def rack_of(self, gpu: int) -> RackAudit | None:
        return self._by_gpu.get(gpu)

    def limit_for(self, gpu: int) -> PhysicalLimit:
        r = self.rack_of(gpu)
        if r is None:
            return PhysicalLimit(gpu, "", False, 0, f"GPU {gpu} is in no audited rack")
        return r.limit_for(gpu)

    def eligible_gpus(self) -> list[int]:
        return sorted(g for g in self._by_gpu if self.limit_for(g).eligible)

    def require(self, gpu: int) -> PhysicalLimit:
        lim = self.limit_for(gpu)
        if not lim.eligible:
            raise NotVerified(lim.reason)
        return lim

    def to_dict(self) -> dict:
        return {"format": "wattnot.audit/1", "label": self.label, "note": self.note,
                "eligible_gpus": self.eligible_gpus(), "racks": [r.to_dict() for r in self.racks]}


def load_fleet(path: str | Path) -> tuple[list[RackSpec], dict]:
    """Read `fleet.json`: the demo inventory. Returns (rack specs, the file's header)."""

    p = Path(path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("label") != SIMULATED:
        raise ValueError(f"{p}: the demo inventory must be labelled {SIMULATED}")
    root = p.parent.parent  # rack/audit/fleet.json -> rack/
    specs = []
    for r in doc["racks"]:
        specs.append(RackSpec(
            id=r["id"], directory=str(root / r["dir"]), budget_w=int(r["budget_w"]),
            node_max_w=int(r["node_max_w"]), photo=r.get("photo", "rack.jpg"),
            declared={int(k): int(v) for k, v in r["declared"].items()},
            gpus_per_node=int(r.get("gpus_per_node", 1))))
    return specs, {k: v for k, v in doc.items() if k != "racks"}


def audit_fleet(specs: Sequence[RackSpec], *, sam: bool = True, label: str = SIMULATED,
                note: str = "") -> FleetAudit:
    """Replay each rack's saved SAM output (sam=True) or trust the inventory (sam=False)."""

    racks = [audit_rack(s, scan_dir(s.directory, s.photo) if sam else None) for s in specs]
    return FleetAudit(tuple(racks), label, note)


__all__ = ["FleetAudit", "NotVerified", "PhysicalLimit", "RackAudit", "RackSpec", "Scan", "Unit",
           "audit_fleet", "audit_rack", "load_fleet", "load_raw", "scan_dir", "scan_live",
           "units_from_raw"]
