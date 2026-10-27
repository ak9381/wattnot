"""Read an expert-routing capture and group its experts.

A capture is the JSONL written by ``scripts/moe_capture.py``: one ``meta`` line, then ``tick``
lines (one per model forward pass), then ``unit_tokens`` and ``vocab``. A *unit* is one expert in
one layer, numbered ``layer * experts + expert``.

Groups come in two kinds. ``tier`` ranks the experts of each layer by how often they were
routed to: the busiest quarter is hot, the next 40 percent warm, the rest cold. ``kmeans``
clusters all units on what they did: their share of their layer's traffic, which topics sent
them tokens, and how deep in the model they sit.

Energy: a measured energy window is split across units by their share of expert FLOPs. Every
routed token costs the same FLOPs in a given model (all its experts have one shape), so the
share is the routed-token share. The split is an ESTIMATE. The window total is the measurement.
"""

from __future__ import annotations

import gzip
import json
import random
from collections.abc import Iterator
from pathlib import Path

TIER_NAMES = ("hot", "warm", "cold")
TIER_SPLIT = (0.25, 0.40)  # share of a layer's experts that are hot, then warm; the rest are cold


def open_text(path: Path | str):
    path = Path(path)
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else open(
        path, encoding="utf-8")


def iter_lines(path: Path | str) -> Iterator[dict]:
    with open_text(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


class Capture:
    """A parsed capture. ``ticks`` keep their file order."""

    def __init__(self, lines: list[dict]):
        self.meta = next(x for x in lines if x["type"] == "meta")
        self.ticks = [x for x in lines if x["type"] == "tick"]
        self.vocab = {int(k): v for x in lines if x["type"] == "vocab"
                      for k, v in x["tokens"].items()}
        self.unit_tokens = next((x["units"] for x in lines if x["type"] == "unit_tokens"), {})
        self.texts = {(x["step"], x["arm"]): x["text"] for x in lines if x["type"] == "step.done"}
        self.L = self.meta["layers"]
        self.E = self.meta["experts"]
        self.topic = {s["id"]: s["topic"] for s in self.meta["steps"]}
        self.topics = sorted(set(self.topic.values()))

    @classmethod
    def load(cls, path: Path | str) -> Capture:
        return cls(list(iter_lines(path)))

    # -- counts
    def counts(self, arm: str, step: int | None = None) -> list[int]:
        """Routed-token counts per unit (flat, length L*E) for one arm, optionally one step."""
        out = [0] * (self.L * self.E)
        for t in self.ticks:
            if t["arm"] != arm or (step is not None and t["step"] != step):
                continue
            for u, n in t["r"]:
                out[u] += n
        return out

    def topic_counts(self, arm: str = "off") -> dict[str, list[int]]:
        out = {tp: [0] * (self.L * self.E) for tp in self.topics}
        for t in self.ticks:
            if t["arm"] != arm:
                continue
            row = out[self.topic[t["step"]]]
            for u, n in t["r"]:
                row[u] += n
        return out

    def active_per_token(self, arm: str) -> float:
        kept = sum(t["kept_slots"] for t in self.ticks if t["arm"] == arm)
        slots = sum(t["slots"] for t in self.ticks if t["arm"] == arm)
        return kept / slots if slots else 0.0

    def tokens(self, arm: str) -> int:
        return sum(t["tokens"] for t in self.ticks if t["arm"] == arm)

    def generated(self, arm: str) -> int:
        """Tokens the model wrote (one per decode tick). The prompt is not counted."""
        return sum(1 for t in self.ticks if t["arm"] == arm and t["phase"] == "decode")

    def routed_slots(self, arm: str) -> int:
        return sum(n for t in self.ticks if t["arm"] == arm for _, n in t["r"])


# -- tiers
def tier_assignment(counts: list[int], L: int, E: int) -> list[int]:
    """0 hot, 1 warm, 2 cold for every unit, ranked inside its own layer (ties: lower id first)."""
    hot = round(E * TIER_SPLIT[0])
    warm = round(E * TIER_SPLIT[1])
    out = [2] * (L * E)
    for layer in range(L):
        row = counts[layer * E:(layer + 1) * E]
        order = sorted(range(E), key=lambda e: (-row[e], e))
        for rank, e in enumerate(order):
            out[layer * E + e] = 0 if rank < hot else 1 if rank < hot + warm else 2
    return out


# -- k-means (small, seeded, no numpy)
def _dist2(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))


def kmeans(points: list[list[float]], k: int, seed: int = 0, iters: int = 60) -> list[int]:
    rng = random.Random(seed)
    cents = [points[rng.randrange(len(points))]]
    while len(cents) < k:  # k-means++ start
        d = [min(_dist2(p, c) for c in cents) for p in points]
        tot = sum(d)
        if tot == 0:
            cents.append(points[rng.randrange(len(points))])
            continue
        r, acc = rng.random() * tot, 0.0
        for p, di in zip(points, d, strict=True):
            acc += di
            if acc >= r:
                cents.append(p)
                break
    assign = [0] * len(points)
    for _ in range(iters):
        new = [min(range(k), key=lambda c: _dist2(p, cents[c])) for p in points]
        if new == assign and _ > 0:
            break
        assign = new
        for c in range(k):
            members = [p for p, a in zip(points, assign, strict=True) if a == c]
            if members:
                cents[c] = [sum(col) / len(members) for col in zip(*members, strict=True)]
    return assign


def kmeans_assignment(cap: Capture, k: int = 4, seed: int = 0) -> list[int]:
    """Cluster every unit on (layer share, topic mix, depth). Clusters are numbered by how busy
    they are, busiest first, so cluster 0 is always the heaviest."""
    L, E = cap.L, cap.E
    total = cap.counts("off")
    topic = cap.topic_counts("off")
    feats = []
    for u in range(L * E):
        layer = u // E
        row = total[layer * E:(layer + 1) * E]
        share = total[u] / max(sum(row), 1) * E  # 1.0 = a fair share of its layer
        n = max(total[u], 1)
        mix = [topic[tp][u] / n for tp in cap.topics]
        feats.append([share, *mix, layer / max(L - 1, 1)])
    raw = kmeans(feats, k, seed)
    load = [0] * k
    for u, c in enumerate(raw):
        load[c] += total[u]
    rank = {c: i for i, c in enumerate(sorted(range(k), key=lambda c: -load[c]))}
    return [rank[c] for c in raw]


# -- what each group did
def group_report(cap: Capture, assign: list[int], names: list[str], arm: str = "off",
                 top_n: int = 8) -> list[dict]:
    """Per group: how many units, share of routed tokens, topic mix and top tokens.

    Everything here is counted from the capture (MEASURED routing). Nothing is estimated."""
    L, E = cap.L, cap.E
    k = len(names)
    counts = cap.counts(arm)
    total = max(sum(counts), 1)
    topic = cap.topic_counts(arm)
    out = []
    for g in range(k):
        units = [u for u in range(L * E) if assign[u] == g]
        n = sum(counts[u] for u in units)
        tmix = {tp: sum(topic[tp][u] for u in units) for tp in cap.topics}
        tt = sum(tmix.values()) or 1
        toks: dict[int, int] = {}
        for u in units:
            for tid, c in cap.unit_tokens.get(str(u), []):
                toks[tid] = toks.get(tid, 0) + c
        top = sorted(toks.items(), key=lambda kv: -kv[1])[:top_n]
        out.append({
            "group": g, "name": names[g], "units": len(units),
            "units_per_layer": [sum(1 for e in range(E) if assign[layer * E + e] == g)
                                for layer in range(L)],
            "routed": n, "share": n / total,
            "topics": {tp: tmix[tp] / tt for tp in cap.topics},
            "top_tokens": [[cap.vocab.get(t, f"#{t}"), c] for t, c in top],
        })
    return out


def energy_split(window_j: float, counts: list[int], assign: list[int], k: int) -> dict:
    """Split one measured energy window across groups by their share of routed tokens.

    Returns the total (the measurement) and the per-group parts (ESTIMATE). The parts add up
    to the total by construction."""
    tot = sum(counts)
    parts = [0.0] * k
    if tot:
        for u, n in enumerate(counts):
            parts[assign[u]] += window_j * n / tot
    return {"total_j": window_j, "total_label": "MEASURED", "parts_j": parts,
            "parts_label": "ESTIMATE"}


def build_meta(cap: Capture, energy: dict | None) -> dict:
    """Everything the browser needs besides the ticks themselves."""
    off = cap.counts("off")
    tiers = tier_assignment(off, cap.L, cap.E)
    km = kmeans_assignment(cap)
    km_names = [f"cluster {chr(65 + i)}" for i in range(4)]
    modes = {
        "tier": {"names": list(TIER_NAMES), "assign": tiers,
                 "report": group_report(cap, tiers, list(TIER_NAMES))},
        "kmeans": {"names": km_names, "assign": km, "report": group_report(cap, km, km_names)},
    }
    return {
        "meta": cap.meta, "topics": cap.topics, "modes": modes,
        "texts": {f"{s}:{a}": t for (s, a), t in cap.texts.items()},
        "vocab": {str(k): v for k, v in cap.vocab.items()},
        "energy": energy,
        "totals": {arm: {"tokens": cap.tokens(arm), "generated": cap.generated(arm),
                         "routed_slots": cap.routed_slots(arm),
                         "active_per_token": cap.active_per_token(arm)}
                   for arm in ("off", "on")},
        "counts": {arm: cap.counts(arm) for arm in ("off", "on")},
        "unit_tokens": cap.unit_tokens,
        "label": "MEASURED routing from a real model run; see meta.capture for where it ran",
        "gini_off": gini([c for c in off if c is not None]),
    }


def gini(xs: list[float]) -> float:
    xs = sorted(xs)
    n, s = len(xs), sum(xs)
    if n == 0 or s == 0:
        return 0.0
    return sum((2 * (i + 1) - n - 1) * x for i, x in enumerate(xs)) / (n * s)


__all__ = ["Capture", "build_meta", "energy_split", "gini", "group_report", "kmeans",
           "kmeans_assignment", "tier_assignment"]
