"""The node agent: what a data center runs next to a GPU to add WattNot.

    python -m wattnot.node.agent --config wattnot.yaml [--approve] [--sim]

Steps, each written to the node log (out/gateway/node.jsonl) that the live view reads:
1. find the GPU (real NVML -> MEASURED; none -> the RUN2 replay H100 -> REPLAY/SIMULATED)
2. rack check: SAM 3.1 on the rack photo via the Meta API (live; falls back to the saved SAM
   output, labelled REPLAY). Only servers SAM can see are touched; the GPU's rack must be one.
3. approval: manual mode stops here until --approve (or the Approve button)
4. cap sweep, high to low inside the configured range, with the quality check watching each step
   (pass rate within 2 points of stock, p99 latency up at most 10%). A failed step rolls back to
   the last good cap and the sweep stops.
5. keep the chosen cap and write a hash-chained receipt for every event.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from wattnot.node.simgpu import ROOT, SimulatedH100, open_gpu, step_at

PHOTO = ROOT / "rack" / "room" / "arsat" / "photo.jpg"
SAVED_RAW = ROOT / "rack" / "room" / "arsat" / "sam-raw" / "photo.server-rack.txt"


class NodeLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.prev = None
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    self.prev = json.loads(line).get("receipt_id") or self.prev
                except ValueError:
                    pass

    def write(self, rec: dict) -> dict:
        rec = {"ts": round(time.time(), 3), **rec, "prev_receipt_id": self.prev}
        rid = "nd_" + hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()[:12]
        rec["receipt_id"] = self.prev = rid
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec


def boxes_from_raw(raw: str) -> list:
    """Rack ids and boxes from SAM's raw text without decoding the masks (no vision extra)."""
    from types import SimpleNamespace

    from wattnot.sam.room import _REC
    recs = sorted(((int(x1), int(y1), int(x2), int(y2)) for _, x1, y1, x2, y2, *_ in _REC.findall(raw)),
                  key=lambda b: (b[0] + b[2], b[1]))
    return [SimpleNamespace(id=f"R{i + 1:02d}", box=b) for i, b in enumerate(recs)]


def rack_check(sam_client=None, photo: Path = PHOTO, rack: str | None = None) -> dict:
    """SAM 3.1 finds the racks it can see. Returns the visible rack ids and the one we act on."""
    from wattnot.sam.room import PROMPT, masks_from_raw
    label, note = "MEASURED (live SAM 3.1 call)", None
    try:
        if sam_client is None:
            from wattnot.sam.client import SamClient
            sam_client = SamClient()
        raw = sam_client.segment(Path(photo).read_bytes(), PROMPT, purpose="sam:node-rack-check")
        masks = masks_from_raw(raw)
        if not masks:
            raise ValueError("SAM found no racks")
    except Exception as e:  # noqa: BLE001  no key, budget, network, bad output
        label, note = "REPLAY (saved SAM 3.1 output)", f"live call not used: {type(e).__name__}"
        try:
            masks = masks_from_raw(SAVED_RAW.read_text())
        except Exception:  # noqa: BLE001  no mask decoder here: the saved boxes are enough to find racks
            masks = boxes_from_raw(SAVED_RAW.read_text())
            note += "; boxes only (mask decoder not installed)"
    visible = [m.id for m in masks]
    target = rack or (visible[0] if visible else None)
    ok = target in visible
    return {"visible_racks": visible, "rack": target, "ok": ok, "label": label, "note": note,
            "boxes": {m.id: list(m.box) for m in masks},
            "why": None if ok else f"rack {target} is not visible in the photo; nothing is touched"}


def caps_to_try(lo: int, hi: int, step: int = 25) -> list[int]:
    return list(range(int(hi), int(lo) - 1, -int(step)))


def run(cfg: dict, *, approve: bool = False, gpu=None, sam_client=None, samples_per_step: int = 20,
        sleep_s: float = 0.0, log_path=None, p99_limit: float = 10.0, answer_check=None) -> dict:
    """answer_check: optional object with .stock() and .at(cap) -> {"accepted", "energy_change_pct",
    "p99_change_pct", "why_not", "label"}; on a real GPU it re-asks a sample at each cap and compares
    with the stock answers. Without it, a real GPU never keeps a lower cap (rolls back to stock)."""
    node = cfg.get("node") or {}
    log = NodeLog(log_path or node.get("log") or "out/gateway/node.jsonl")
    gpu = gpu or open_gpu()
    sim = isinstance(gpu, SimulatedH100)
    glabel = gpu.label
    ev = lambda step, **kw: log.write({"kind": "event", "step": step, "gpu_label": glabel, **kw})  # noqa: E731
    ev("gpu_found", name=gpu.nvmlDeviceGetName(None), count=gpu.nvmlDeviceGetCount(),
       label=glabel, note="no real GPU found: replaying the recorded H100 RUN2" if sim else None)

    if node.get("sam", True):
        rc = rack_check(sam_client, rack=node.get("rack"))
        ev("rack_check", **rc)
        if not rc["ok"]:
            return {"status": "stopped", "why": rc["why"]}
    if node.get("approval", "manual") == "manual" and not approve:
        ev("waiting_approval", note="approve to start the cap sweep")
        return {"status": "waiting_approval"}

    def sample(cap_w, n):
        for _ in range(n):
            mw = gpu.nvmlDeviceGetPowerUsage(None)
            w = mw / 1000
            log.write({"kind": "sample", "gpu": 0, "power_w": round(w, 2), "joules": round(w * 1.0, 2),
                       "cap_w": cap_w, "label": glabel if not sim else "REPLAY"})
            if sleep_s:
                time.sleep(sleep_s)

    stock = gpu.nvmlDeviceGetPowerManagementLimit(None) // 1000
    sample(stock, samples_per_step)
    if answer_check is not None and not sim:
        ref = answer_check.stock()
        ev("stock_answers", n=ref.get("n"), joules_per_answer=ref.get("joules_per_answer"), label="MEASURED")
    try:
        return _sweep(cfg, node, gpu, sim, glabel, ev, sample, stock, samples_per_step, p99_limit, answer_check)
    except BaseException:
        gpu.nvmlDeviceSetPowerManagementLimit(None, stock * 1000)   # any failure: back to stock first
        ev("rolled_back", to_cap_w=stock, failed=["error during the power check"])
        raise


def _sweep(cfg, node, gpu, sim, glabel, ev, sample, stock, samples_per_step, p99_limit, answer_check):
    good, results = stock, []
    for cap in caps_to_try(node.get("cap_min_w", 300), node.get("cap_max_w", 375)):
        gpu.nvmlDeviceSetPowerManagementLimit(None, cap * 1000)
        ev("cap_applied", cap_w=cap)
        sample(cap, samples_per_step)
        if sim:
            st = step_at(gpu.steps, cap, p99_limit)
        elif answer_check is not None:   # real GPU: re-ask the sample at this cap, compare with stock
            if hasattr(answer_check, "warmup") and not getattr(answer_check, "warm", None):
                ev("gpu_warmup", **answer_check.warmup())   # same check as sequential mode
                gpu.nvmlDeviceSetPowerManagementLimit(None, cap * 1000)
            st = {"cap_w": cap, **answer_check.at(cap)}
        else:
            st = {"cap_w": cap, "accepted": None, "label": "MEASURED power only",
                  "why_not": ["no live answer check given: a real GPU stays at stock"]}
        results.append(st)
        ev("quality_check", **{k: v for k, v in st.items() if k != "label"}, check_label=st["label"])
        if st["accepted"]:
            good = cap
            continue
        gpu.nvmlDeviceSetPowerManagementLimit(None, good * 1000)
        ev("rolled_back", to_cap_w=good, failed=st.get("why_not"))
        break
    best = next((r for r in results if r["cap_w"] == good), None)
    ev("kept", cap_w=good, energy_change_pct=(best or {}).get("energy_change_pct"),
       label=(best or {}).get("label", glabel))
    sample(good, samples_per_step)
    return {"status": "done", "cap_w": good, "results": results, "label": glabel}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wattnot node")
    ap.add_argument("--config", default="wattnot.yaml")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--sim", action="store_true", help="use the RUN2 replay H100 even if a GPU is present")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between 1 s samples (1.0 = real time)")
    a = ap.parse_args(argv)
    from wattnot.gateway.build import load_config
    cfg = load_config(a.config if Path(a.config).exists() else None)
    out = run(cfg, approve=a.approve, gpu=open_gpu("sim" if a.sim else None), sleep_s=a.sleep)
    print(json.dumps({k: v for k, v in out.items() if k != "results"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
