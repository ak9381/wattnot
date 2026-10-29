"""Full RUN3 rehearsal on CPU with mock NVML / GPU / hosted backends.

Runs every block of the real RUN3 chain, in order, with nothing but mock and fake backends, so
the whole pipeline can be exercised and checked before a single paid GPU-hour is spent:

    probe -> cap sweep -> phase-clock A/B (Spark-chosen thresholds) -> power cross-check ->
    escalation chain sample -> noise/quality gate -> net J per correct answer -> keep/rollback ->
    signed receipt + verify -> leaderboard build -> static Pages build

It refuses to touch a real GPU or a cloud endpoint: no NVML is opened, no network call is made, no
secret is read, and ``RUN3_GO`` is never set. Every number it produces is MOCK or SIMULATED, and the
signed receipt is graded SIMULATED on purpose. It writes a summary JSON and exits 0 when every
block ran.

    python -m wattnot.rehearse --out results/rehearse-run3
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import namedtuple
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# A deterministic, RUN2-shaped cap sweep: Wh per request and p99 change per cap. SIMULATED.
SWEEP_WH = {700: 0.004433, 400: 0.00361, 375: 0.00345, 350: 0.003232, 325: 0.00310}
SWEEP_P99_CHANGE = {400: 4.68, 375: 6.0, 350: 7.69, 325: 10.9}
P99_LIMIT_PCT = 10.0
N_REQUESTS = 666
PASS_RATE = 0.93694

_GateRes = namedtuple("GateRes", ["passed", "failed"])


class _MockGuard:
    """Stand-in for the NVML power-cap guard. Pure Python, touches no driver."""

    def __init__(self) -> None:
        self.min_w, self.max_w = 100, 700
        self._cap = 700

    def set_cap_w(self, watts: int, *, restoring: bool = False) -> int:
        self._cap = max(self.min_w, min(self.max_w, int(watts)))
        return self._cap

    def cap_w(self) -> int:
        return self._cap

    def clamp(self, watts: int, *, restoring: bool = False) -> int:
        return max(self.min_w, min(self.max_w, int(watts)))


def _wh_samples(cap: int) -> list[float]:
    base = SWEEP_WH[cap]
    return [round(base * (1 + d), 8) for d in (-0.0005, 0.0, 0.0005)]


def _block(name, status, label, **detail):
    return {"block": name, "status": status, "label": label, **detail}


def _cap_sweep() -> dict:
    from wattnot.control.controller import step_down

    guard = _MockGuard()

    def trial(cap_w):
        return cap_w

    def gate(cap_w):
        ch = SWEEP_P99_CHANGE.get(cap_w, 0.0)
        ok = ch <= P99_LIMIT_PCT
        return _GateRes(ok, [] if ok else [f"p99 +{ch}% over the {P99_LIMIT_PCT:g}% limit"])

    kept, outcomes = step_down(guard, start_w=400, floor_w=325, step_w=25, trial=trial, gate=gate)
    rolled_back = [o.cap_w for o in outcomes if not o.passed]
    return _block("cap_sweep", "ok", "SIMULATED", kept_cap_w=kept,
                  tried=[o.cap_w for o in outcomes], rolled_back=rolled_back,
                  note="controller.step_down over a RUN2-shaped sweep; keeps the lowest cap that holds p99")


def _phase_ab(out: Path) -> dict:
    """Run the real phase A/B dry run (fake rig, mock NVML, Spark-mock thresholds) as a subprocess."""
    pout = out / "phase"
    cmd = [sys.executable, str(ROOT / "scripts/phase_ab.py"), "--fake", "--out", str(pout), "--reps", "1"]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    env.pop("META_API_KEY", None)  # force the mock transport, never a live call
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    summ = pout / "phase_summary.json"
    if r.returncode != 0 or not summ.exists():
        return _block("phase_ab", "failed", "MOCK", returncode=r.returncode,
                      stderr=r.stderr[-300:])
    s = json.loads(summ.read_text())
    thr = s.get("thresholds", {})
    return _block("phase_ab", "ok", s.get("label", "MOCK"),
                  thresholds_source=thr.get("source"), clamped=thr.get("clamped"),
                  low_mhz=s.get("low_mhz"), high_mhz=s.get("high_mhz"),
                  prefill_ratio=s.get("prefill_ratio"), arms=sorted(s.get("arms", {})),
                  trace=str(pout / "phase_thresholds.trace.jsonl"))


def _cross_check() -> dict:
    from wattnot.meter import crosscheck as C

    seconds = 300.0
    mean_w = 350.0  # ~350 W cap arm
    nvml_wh = mean_w * seconds / 3600.0
    out = C.cross_check(nvml_wh, seconds, mock=True, base_w=mean_w)
    return _block("cross_check", "ok", "MOCK", sources=out["available_sources"],
                  worst_gpu_gap_pct=out["worst_gpu_gap_pct"], gpu_sources_agree=out["gpu_sources_agree"],
                  node_source_scope="ipmi is whole-node, so its gap is informational")


def _escalation(n: int) -> dict:
    from wattnot.route import escalate as E
    from wattnot.route.mock_backend import mock_items, sample_prompts

    block = E.evaluate_chain(mock_items(sample_prompts(n)), overhead_joules=120.0,
                             overhead_label="MOCK")
    chain = next(p for p in block["policies"] if p["policy"] == "chain")
    row = E.chain_leaderboard_row(block, setup="WattNot three-tier chain (rehearsal, MOCK)")
    return _block("escalation", "ok", block["label"],
                  escalation_rate_pct=block["escalation_rate_pct"],
                  small_model_pass_pct=block["small_model_pass_pct"],
                  tiers=[(t["name"], t["answered"]) for t in block["tiers"]],
                  j_per_correct=chain["j_per_correct"], leaderboard_row=row), block


_PREFIX_AB: dict = {}


def _prefix_cache() -> dict:
    from wattnot.context_saver import prefix_cache as PC

    ab = PC.mock_ab(reps=3)
    _PREFIX_AB["ab"] = ab
    h = ab["headline"]
    return _block("prefix_cache", "ok" if ab["kept"] else "failed", "MOCK",
                  j_per_correct_change_pct=h["j_per_correct_change_pct"],
                  ttft_change_pct=h["ttft_change_pct"], p99_change_pct=h["p99_change_pct"],
                  cache_hit_rate=h["cache_hit_rate"], gate_passed=ab["gate"]["passed"],
                  workload="workloads/prefix-cache-v1.jsonl")


def _context_saver() -> dict:
    from wattnot.context_saver import ContextSaver, saver_receipt_block, receipt_parts

    # A small agent chat with a repeated tool read and several old turns to compress.
    big = "FILE repo/README.md:\n" + ("WattNot cuts joules per correct answer. " * 40)
    messages = [{"role": "system", "content": "You are an agent working in a repo."}]
    for i in range(6):
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file",
                                        "arguments": '{"path": "repo/README.md"}'}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": big})
        messages.append({"role": "user", "content": f"Step {i}: keep investigating the energy path."})
    messages.append({"role": "user", "content": "What module holds the signed receipts?"})

    saved, acct = ContextSaver().transform(messages)
    # mock A/B arms: tokens from the saver, pass rate held, joules ~ tokens x J/token (MOCK)
    jpt = 0.1497
    off = {"tokens": acct["tokens_before"], "tokens_label": "ESTIMATE",
           "joules": round(acct["tokens_before"] * jpt, 1), "joules_label": "MOCK",
           "pass_pct": 96.0, "pass_pct_label": "MOCK", "cache_hit_rate": None}
    on = {"tokens": acct["tokens_after"], "tokens_label": "ESTIMATE",
          "joules": round(acct["tokens_after"] * jpt, 1), "joules_label": "MOCK",
          "pass_pct": 96.0, "pass_pct_label": "MOCK", "cache_hit_rate": None}
    parts = receipt_parts(acct, prefix_ab=_PREFIX_AB.get("ab"), j_per_token=jpt,
                          pass_before=96.0, pass_after=96.0, label="MOCK")
    rblock = saver_receipt_block(off, on, lever="memory_compression+tool_dedup", parts=parts)
    return _block("context_saver", "ok" if rblock["kept"] else "failed", "MOCK",
                  tokens_before=acct["tokens_before"], tokens_after=acct["tokens_after"],
                  saved_pct=acct["saved_pct"], stages=[s["name"] for s in acct["stages"]],
                  gate_kept=rblock["kept"], receipt_block_spec=rblock["spec"]), rblock


def _gate() -> dict:
    from wattnot.gates.noise import noise_gate

    g = noise_gate(_wh_samples(700), _wh_samples(350), n_boot=2000, seed=7)
    return _block("gate", "ok", "SIMULATED", billable=g.passed,
                  saving_pct=g.detail["saving_pct"], p_value=g.detail["p_value"],
                  note="Welch + bootstrap CI on 350 W vs stock (SIMULATED samples)")


def _receipt(out: Path) -> dict:
    from wattnot.receipt.build import build_receipt
    from wattnot.receipt.sign import generate_test_key, sign
    from wattnot.verify import verify_receipt

    run = {
        "run_id": "run_20261028_000000_abcdef",
        "receipt_id": "rcpt_rehearse_run3",
        "subject": {"model": "meta-llama/Llama-3.1-8B-Instruct",
                    "host": {"provider": "mock", "gpu": "H100 80GB (MOCK)", "driver": "mock"},
                    "mode": "container",
                    "workload": {"name": "proof-v1", "version": "1", "n_requests": N_REQUESTS,
                                 "sha256": "0" * 64}},
        "baseline": {"params": {"power_cap_w": 700}, "wh_samples": _wh_samples(700),
                     "p99_ms": 3605, "quality": {"fixed_pass_rate": PASS_RATE}},
        "candidate": {"params": {"power_cap_w": 350}, "wh_samples": _wh_samples(350),
                      "p99_ms": 3882, "quality": {"fixed_pass_rate": PASS_RATE}},
        "measurement": {"meter": "nvml", "hz": 10, "label": "SIMULATED", "idle_w": None,
                        "csv": [{"path": "mock/rehearsal.csv", "sha256": "1" * 64}]},
        "planner_trace": [
            {"trial_no": 1, "source": "spark", "model": "muse-spark-1.3",
             "params": {"power_cap_w": 400}, "reason": "probe 400 W first",
             "tokens": {"input": 150, "output": 40}},
            {"trial_no": 2, "source": "spark", "model": "muse-spark-1.3",
             "params": {"power_cap_w": 350}, "reason": "400 W held p99; go lower",
             "tokens": {"input": 150, "output": 40}},
            {"trial_no": 3, "source": "spark", "model": "muse-spark-1.3",
             "params": {"power_cap_w": 325}, "reason": "350 W held; probe 325 W",
             "tokens": {"input": 150, "output": 40}},
            {"trial_no": 4, "source": "spark", "model": "muse-spark-1.3",
             "params": {"power_cap_w": None}, "reason": "325 W fails p99; keep 350 W",
             "tokens": {"input": 150, "output": 40}}],
        # exercise the per-model joules roles: a local judge and a hosted gate-explanation model
        "models": [{"role": "judge", "model": "Llama 3.1 8B Instruct (judge fallback)",
                    "where": "local", "joules": 950.0, "label": "SIMULATED"},
                   {"role": "gate", "model": "muse-spark-1.3 (gate explanation)", "tokens": 1200}],
    }
    rdir = out / "receipts"
    rdir.mkdir(parents=True, exist_ok=True)
    receipt = build_receipt(run, created_at="2026-10-28T00:00:00+00:00")
    rpath = rdir / f"{receipt['receipt_id']}.json"
    rpath.write_text(json.dumps(receipt, indent=2) + "\n")
    key = generate_test_key(rdir / "rehearse-key.pem")
    sign(rpath, no_rekor=True, key_path=key)
    rep = verify_receipt(rpath)
    non_rekor_ok = all(s.ok for s in rep.steps if s.name != "rekor")
    pmj = receipt["measurement"]["per_model_joules"]
    return _block("receipt", "ok" if non_rekor_ok else "failed", "SIMULATED",
                  grade=rep.grade, steps_ok=non_rekor_ok,
                  spark_trace_id=receipt["measurement"]["spark_trace_id"],
                  per_model_roles=[x["role"] for x in pmj],
                  candidate_j_per_good_answer=receipt["joules"]["candidate"]["j_per_good_answer"],
                  path=str(rpath)), receipt


def _leaderboard() -> dict:
    from wattnot.dash import leaderboard as L

    b = L.build(ROOT)
    ex_ok = L.validate_submission(b["submission_example"]) == []
    measured = [r for r in b["rows"] if r["status"] == "measured"]
    estimate = [r for r in b["rows"] if r["status"] == "estimate"]
    pending = [r for r in b["rows"] if r["status"] == "pending"]
    return _block("leaderboard", "ok" if ex_ok else "failed", "MEASURED",
                  rows=len(b["rows"]), measured=len(measured), estimate=len(estimate),
                  pending=len(pending), example_valid=ex_ok,
                  schema=b["submission_schema"], j_per_token=b["j_per_token_estimate"])


def _pages(out: Path) -> dict:
    import scripts.build_pages as bp  # noqa: PLC0415

    site = out / "site"
    bp.main(site)
    ok = (site / "index.html").exists() and (site / "dash" / "v2.html").exists()
    return _block("pages", "ok" if ok else "failed", "REPLAY", site=str(site))


def run_rehearsal(out_dir: str | Path, *, n_escalation: int = 20) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))  # so `import scripts.build_pages` works

    blocks: list[dict] = []
    blocks.append(_block("probe", "ok", "MOCK", gpu="H100 80GB (MOCK)", driver="mock",
                         range_w=[100, 700], note="no NVML opened; synthesized mock probe"))
    blocks.append(_cap_sweep())
    blocks.append(_phase_ab(out))
    blocks.append(_cross_check())
    blocks.append(_prefix_cache())
    cs_block, cs_receipt = _context_saver()
    blocks.append(cs_block)
    esc_block, esc_full = _escalation(n_escalation)
    blocks.append(esc_block)
    blocks.append(_gate())
    rcpt_block, receipt = _receipt(out)
    net = {"cap_350_j_per_good_answer": {"value": receipt["joules"]["candidate"]["j_per_good_answer"],
                                         "label": "SIMULATED"},
           "escalation_chain_j_per_correct": {"value": esc_block["j_per_correct"], "label": esc_full["label"]}}
    blocks.append(_block("net_j_per_correct", "ok", "SIMULATED", **net))
    sweep = next(b for b in blocks if b["block"] == "cap_sweep")
    blocks.append(_block("keep_rollback", "ok", "SIMULATED", kept_cap_w=sweep["kept_cap_w"],
                         rolled_back=sweep["rolled_back"]))
    blocks.append(rcpt_block)
    blocks.append(_leaderboard())
    blocks.append(_pages(out))

    all_ok = all(b["status"] == "ok" for b in blocks)
    summary = {
        "spec": "wattnot.rehearse-run3/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "all_ok": all_ok,
        "safety": {"touched_real_gpu": False, "touched_cloud": False, "run3_go_set": False,
                   "note": "mock NVML/GPU and mock hosted backends only; no network, no secrets"},
        "blocks": blocks,
        "net_j_per_correct": net,
        "kept_cap_w": sweep["kept_cap_w"],
        "context_saver_receipt": cs_receipt,
    }
    (out / "rehearse-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wattnot.rehearse",
                                 description="Full RUN3 rehearsal on CPU with mock backends.")
    ap.add_argument("--out", default="results/rehearse-run3", help="output directory")
    ap.add_argument("--n-escalation", type=int, default=20, help="prompts in the escalation sample")
    ap.add_argument("--real", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.real or os.environ.get("RUN3_GO"):
        print("refusing: the rehearsal is mock-only and never drives a real GPU or cloud "
              "(do not pass --real or set RUN3_GO).", file=sys.stderr)
        return 2
    summary = run_rehearsal(a.out, n_escalation=a.n_escalation)
    for b in summary["blocks"]:
        mark = "ok " if b["status"] == "ok" else "XX "
        print(f"  [{mark}] {b['block']:<18} {b['label']}")
    print(f"\nrehearsal {'PASSED' if summary['all_ok'] else 'FAILED'}: "
          f"{sum(b['status'] == 'ok' for b in summary['blocks'])}/{len(summary['blocks'])} blocks ok, "
          f"kept cap {summary['kept_cap_w']} W, summary -> {Path(a.out) / 'rehearse-summary.json'}")
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
