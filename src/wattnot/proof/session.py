"""Paired measurement of one cap against stock, and the accept/reject decision.

For one cap and R repeats the blocks run A B A B ...: stock (the GPU's default limit), then the
cap, then stock again, then the cap again. Each cap block is compared with the stock block
right before it, so slow drift (heat, clocks) hits both arms alike.

Accepted when all three hold:
  quality  pooled pass rate at the cap is no more than `max_drop_pts` below pooled stock
  p99      mean p99 change versus stock is at most `p99_slack_pct`
  energy   the 95% confidence interval of the J/request change is entirely below zero
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from wattnot.gates.fixed import fixed_checks
from wattnot.proof.rig import Rig, pooled_items
from wattnot.proof.stats import ci95, mean_sd, pct_change
from wattnot.runner.workload import Workload

MAX_DROP_PTS = 2.0
P99_SLACK_PCT = 10.0


def burn_in(rig: Rig, log=None) -> None:
    """One unmetered stock block before the first measurement of a process, when `rig.burn_in`
    asks for it: the first metered block after a cold start or a long idle reads differently
    from the rest (RUN2's control: 14.6 vs 15.8 J/request for the same setting)."""
    n = int(getattr(rig, "burn_in", 0) or 0)
    if n <= 0 or getattr(rig, "_burned_in", False):
        return
    rig._burned_in = True  # type: ignore[attr-defined]
    for i in range(n):
        if log:
            log("burn_in", {"set_w": rig.default_w, "n": i + 1, "of": n})
        rig.block(rig.default_w)


def stock_noise(stock: list[dict]) -> dict:
    """How much stock moves against itself in this step: the coefficient of variation of J/request
    over the stock blocks. A cap's effect has to clear this to mean anything."""
    j = [b["j_per_req"] for b in stock]
    m, sd = mean_sd(j)
    return {"label": "MEASURED", "blocks": len(j), "cv_pct": (100 * sd / m) if m else float("nan")}


def _pool(blocks: list[dict], repeat: int) -> tuple[dict, dict]:
    outs, cut = {}, {}
    for r, b in enumerate(blocks):
        for k, v in b["outputs"].items():
            i, c = k.rsplit("#", 1)
            outs[f"{i}#{r * repeat + int(c)}"] = v
            cut[f"{i}#{r * repeat + int(c)}"] = b["cut"][k]
    return outs, cut


def summarize(cap_w: int, stock: list[dict], capped: list[dict], workload: Workload, repeat: int,
              *, max_drop_pts: float = MAX_DROP_PTS, p99_slack_pct: float = P99_SLACK_PCT) -> dict:
    n = len(stock)
    pairs = list(zip(stock, capped, strict=True))

    def delta(key: str) -> list[float]:
        return [pct_change(c[key], s[key]) for s, c in pairs]

    d_energy, d_p99, d_p50, d_tok = (delta("j_per_req"), delta("p99_s"), delta("p50_s"),
                                     delta("j_per_token"))
    items = pooled_items(workload, n * repeat)
    s_out, s_cut = _pool(stock, repeat)
    c_out, c_cut = _pool(capped, repeat)
    q = fixed_checks(s_out, c_out, items, max_drop_pts=max_drop_pts,
                     base_truncated=s_cut, cand_truncated=c_cut)
    e_mean, e_sd = mean_sd(d_energy)
    _, e_half = ci95(d_energy)
    p_mean, p_sd = mean_sd(d_p99)
    e_ok = n >= 2 and e_mean + e_half < 0
    p_ok = p_mean <= p99_slack_pct
    why = []
    if not q.passed:
        why.append(f"quality: {q.reason}")
    if not p_ok:
        why.append(f"p99 up {p_mean:.1f}% (limit {p99_slack_pct:.0f}%)")
    if not e_ok:
        why.append("energy change not clearly below zero")
    return {
        "cap_w": cap_w, "repeats": n, "label": "MEASURED",
        "j_per_req_stock": [s["j_per_req"] for s in stock],
        "j_per_req_cap": [c["j_per_req"] for c in capped],
        "energy_change_pct": {"per_pair": d_energy, "mean": e_mean, "sd": e_sd,
                              "ci95_half": e_half},
        "p99_change_pct": {"per_pair": d_p99, "mean": p_mean, "sd": p_sd},
        "p50_change_pct": {"per_pair": d_p50, "mean": mean_sd(d_p50)[0]},
        "j_per_token_change_pct": {"mean": mean_sd(d_tok)[0]},
        "stock_noise": stock_noise(stock),
        "p99_s": {"stock": mean_sd([s["p99_s"] for s in stock])[0],
                  "cap": mean_sd([c["p99_s"] for c in capped])[0]},
        "quality": {"stock_pct": q.detail["baseline_pct"], "cap_pct": q.detail["candidate_pct"],
                    "checked": q.detail["checked"], "passed": q.passed, "reason": q.reason},
        "mean_w": {"stock": mean_sd([s["counter_mean_w"] for s in stock])[0],
                   "cap": mean_sd([c["counter_mean_w"] for c in capped])[0]},
        "max_temp_c": max(b["max_temp_c"] or 0 for b in stock + capped),
        "max_temp_c_by_arm": {"stock": max(b["max_temp_c"] or 0 for b in stock),
                              "cap": max(b["max_temp_c"] or 0 for b in capped)},
        "errors": sum(b["errors"] for b in stock + capped),
        "accepted": bool(q.passed and p_ok and e_ok and not any(b["errors"] for b in capped)),
        "why_not": why,
    }


def save_raw(out: Path, tag: str, block: dict) -> dict:
    """Write a block's replies to raw/<tag>.jsonl and return the block without them."""
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    p = raw / f"{tag}.jsonl"
    with p.open("w") as f:
        for k, v in block["outputs"].items():
            f.write(json.dumps({"id": k, "finish_cut": block["cut"][k], "text": v},
                               ensure_ascii=False) + "\n")
    slim = {k: v for k, v in block.items() if k not in ("outputs", "cut")}
    slim["raw"] = f"raw/{p.name}"
    slim["raw_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    return slim


def run_step(rig: Rig, cap_w: int, reps: int, out: Path, *, workload: Workload, log=None,
             tag: str = "", clock: str | None = None) -> dict:
    """reps x (stock block, cap block). Saves every block's replies and a step summary.

    `clock` (a wattnot.proof.clocks profile) locks SM clocks for the cap arm only. If the card
    refuses, the step runs caps-only and says so (`clock_applied` False, `clock_fallback`)."""
    lock = None
    if clock and clock != "base":
        from wattnot.proof.clocks import ClockedRig, ClockLock
        lock = getattr(rig, "clock_lock", None) or ClockLock(rig.nvml, rig.handle)
        try:
            rig.clock_lock = lock  # one lock per process: a refusal sticks
        except AttributeError:
            pass
        rig = ClockedRig(rig, lock, clock)
    stock_w = rig.default_w
    stock, capped = [], []
    burn_in(rig, log)
    for r in range(reps):
        order = (("stock", stock_w), ("cap", cap_w))
        if getattr(rig, "counterbalance", False) and r % 2:
            order = order[::-1]  # cap first on odd rounds, so drift hits both arms alike
        for arm, w in order:
            if log:
                log("block_start", {"cap_w": cap_w, "arm": arm, "rep": r, "set_w": w})
            b = rig.block(w)
            if log:
                log("block", {"cap_w": cap_w, "arm": arm, "rep": r, "set_w": w,
                              "j_per_req": b["j_per_req"], "p99_s": b["p99_s"],
                              "pass_pct": b["pass_pct"], "wall_s": b["wall_s"],
                              "mean_w": b["counter_mean_w"], "temp_c": b["max_temp_c"]})
            (stock if arm == "stock" else capped).append(b)
    name = f"{tag}cap{cap_w}"
    s_slim = [save_raw(out, f"{name}-stock-r{i}", b) for i, b in enumerate(stock)]
    c_slim = [save_raw(out, f"{name}-cap-r{i}", b) for i, b in enumerate(capped)]
    summ = summarize(cap_w, stock, capped, workload, rig.repeat)
    summ["blocks"] = {"stock": s_slim, "cap": c_slim}
    summ["clock_requested"] = clock or "base"
    if lock is not None:
        lock.reset()
        applied = bool(rig.applied)
        summ["clock_applied"] = applied
        summ["clock_profile"] = clock if applied else "base"
        if not applied:
            summ["clock_fallback"] = f"caps-only: {lock.reason}"
    else:
        summ["clock_applied"], summ["clock_profile"] = True, "base"
    (out / "steps").mkdir(parents=True, exist_ok=True)
    (out / "steps" / f"{name}.json").write_text(json.dumps(summ, indent=1))
    rig.set_cap(stock_w)
    return summ


def run_control(rig: Rig, out: Path, *, workload: Workload, scale: float = 0.2, log=None) -> dict:
    """The gate's own test: stock vs a deliberately bad setting (answers cut to `scale` of
    their token budget). The gate must reject it; otherwise it can't tell good from bad."""
    stock_w = rig.default_w
    burn_in(rig, log)

    def blk(name: str, **kw) -> dict:
        if log:
            log("block_start", {"name": name, "set_w": stock_w, **kw})
        return rig.block(stock_w, **kw)

    a = blk("stock")
    b = blk("truncated", token_scale=scale)
    a2 = blk("stock2")  # a second stock block: how much does stock move on its own
    extra = [blk(f"stock{i + 3}") for i in range(int(getattr(rig, "control_blocks", 0) or 0))]
    sa, sb, sa2 = (save_raw(out, f"control-{n}", x) for n, x in
                   (("stock", a), ("truncated", b), ("stock2", a2)))
    s_extra = [save_raw(out, f"control-stock{i + 3}", x) for i, x in enumerate(extra)]
    j_stock = [x["j_per_req"] for x in (a, a2, *extra)]
    m_stock, sd_stock = mean_sd(j_stock)
    items = pooled_items(workload, rig.repeat)
    bad = fixed_checks(a["outputs"], b["outputs"], items, max_drop_pts=MAX_DROP_PTS,
                       base_truncated=a["cut"], cand_truncated=b["cut"])
    same = fixed_checks(a["outputs"], a2["outputs"], items, max_drop_pts=MAX_DROP_PTS,
                        base_truncated=a["cut"], cand_truncated=a2["cut"])
    res = {"label": "MEASURED", "stock_pass_pct": a["pass_pct"], "stock2_pass_pct": a2["pass_pct"],
           "truncated_pass_pct": b["pass_pct"], "token_scale": scale,
           "bad_setting_rejected": not bad.passed, "stock_vs_stock_accepted": same.passed,
           "stock_vs_stock_j_per_req_change_pct": pct_change(a2["j_per_req"], a["j_per_req"]),
           "stock_blocks": len(j_stock), "stock_j_per_req": j_stock,
           "stock_noise_cv_pct": (100 * sd_stock / m_stock) if m_stock else float("nan"),
           "stock_range_pct": (100 * (max(j_stock) - min(j_stock)) / m_stock) if m_stock else None,
           "blocks": {"stock": sa, "truncated": sb, "stock2": sa2,
                      **({"extra_stock": s_extra} if s_extra else {})}}
    (out / "control.json").write_text(json.dumps(res, indent=1))
    if log:
        log("control", {k: v for k, v in res.items() if k != "blocks"})
    return res
