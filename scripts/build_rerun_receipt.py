"""Build and sign the H100 rerun receipt (Oct 31) from the pulled result files.

Signs LOCALLY with the committed test key (verifier/samples/test-key.pem). Nothing is
published anywhere, so the grade stays below VALID. All numbers are MEASURED (NVML).

    python scripts/build_rerun_receipt.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from wattnot.receipt.build import build_receipt, verify_hash
from wattnot.receipt.sign import sign

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "h100-rerun-1031"
OUT = ROOT / "receipts" / "rerun-1031"
KEY = ROOT / "verifier" / "samples" / "test-key.pem"
P99_LIMIT_PCT = 10.0
BEST = 325


def _j(p: Path) -> dict:
    return json.loads(p.read_text())


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rel(p: Path) -> dict:
    return {"path": str(p.relative_to(ROOT)), "sha256": _sha(p)}


def main() -> Path:
    best_p = DATA / "steps" / f"cap{BEST}.json"
    best = _j(best_p)
    stock_wh = [j / 3600 for j in best["j_per_req_stock"]]
    cap_wh = [j / 3600 for j in best["j_per_req_cap"]]
    sweep = []
    for p in sorted((DATA / "steps").glob("cap*.json")):
        s = _j(p)
        sweep.append({"cap_w": s["cap_w"], "label": "MEASURED",
                      "energy_per_req_change_pct": round(s["energy_change_pct"]["mean"], 2),
                      "p99_change_pct": round(s["p99_change_pct"]["mean"], 2),
                      "pass_pct": {"stock": s["quality"]["stock_pct"], "cap": s["quality"]["cap_pct"]},
                      "accepted": s["accepted"], "why_not": s["why_not"], "source": _rel(p)})
    comb_p = DATA / "prefix_cache_summary.json"
    comb = _j(comb_p)
    pub_p = DATA / "public_bench_summary.json"
    pub = _j(pub_p)
    public = {}
    for name, s in pub["sets"].items():
        public[name] = {"label": "MEASURED", "reps": s["reps"],
                        "j_per_correct_change_pct": s["j_per_correct_change_pct"]["mean"],
                        "p_value": s["j_per_correct_change_pct"]["p_value"],
                        "ci95_excludes_zero": s["j_per_correct_change_pct"]["ci95_excludes_zero"],
                        "p99_change_pct": s["p99_change_pct"]["mean"],
                        "pass_pct": {"stock": s["stock"]["pass_pct"], "cap": s["cap"]["pass_pct"]},
                        "dataset": s["source"]}
    run = {
        "run_id": "run_20261031_rerun_cap325",
        "subject": {"model": "meta-models/Muse-Glimmer-30B",
                    "host": {"provider": "dedicated-gpu-rental", "gpu": "H100 80GB", "driver": "recorded"},
                    "mode": "container",
                    "workload": {"name": "proof-v1 tiers", "version": "1",
                                 "n_requests": best["quality"]["checked"],
                                 "sha256": _sha(best_p)}},
        "baseline": {"params": {"power_cap_w": 700}, "wh_samples": stock_wh,
                     "p99_ms": round(best["p99_s"]["stock"] * 1000),
                     "quality": {"fixed_pass_rate": round(best["quality"]["stock_pct"] / 100, 5)}},
        "candidate": {"params": {"power_cap_w": BEST}, "wh_samples": cap_wh,
                      "p99_ms": round(best["p99_s"]["cap"] * 1000),
                      "quality": {"fixed_pass_rate": round(best["quality"]["cap_pct"] / 100, 5)}},
        "measurement": {"meter": "nvml", "hz": 10, "label": "MEASURED", "idle_w": None,
                        "csv": [_rel(best_p)]},
    }
    r = build_receipt(run, created_at="2026-10-31T09:48:47+00:00")
    r["rerun"] = {
        "label": "MEASURED",
        "p99_limit_pct": P99_LIMIT_PCT,
        "best_cap_w": BEST,
        "sweep": sweep,
        "combined_cap_plus_prefix": {
            "label": "MEASURED", "cap_w": comb["caps"]["on_prefix_w"],
            "baseline_w": comb["caps"]["off_baseline_w"],
            "j_per_correct_change_pct": comb["headline"]["j_per_correct_change_pct"],
            "ttft_change_pct": comb["headline"]["ttft_change_pct"],
            "p99_change_pct": comb["headline"]["p99_change_pct"],
            "gate": comb["gate"], "source": _rel(comb_p)},
        "public_benchmarks": public,
        "public_benchmarks_source": _rel(pub_p),
        "note": "Signed locally with the public TEST key; not published to any transparency log.",
    }
    # Re-seal the hash after adding the rerun block.
    from wattnot.receipt.canon import canonical_sha256
    r["integrity"]["sha256"] = canonical_sha256(r)
    ok, _, _ = verify_hash(r)
    assert ok, "hash did not re-seal"
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{r['receipt_id']}.json"
    path.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    sign(path, no_rekor=True, key_path=KEY)
    return path


if __name__ == "__main__":
    print(main())
