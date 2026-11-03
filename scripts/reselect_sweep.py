"""Re-run only the select / approve / receipt step of a finished sweep, offline.

Run 1103's t1-sweep finished with no accepted cap under the old acceptance rule (95% CI from
2 pairs, which can never clear), so `approve` ran with nothing to approve and the receipt has
best_cap_w null. The cap_sweep stage was later re-judged from the saved data (f076cce rule), but
approve / finished / receipt were never rebuilt. This rebuilds them from the checkpoint with no
GPU work and writes NEW files next to the originals:

    python scripts/reselect_sweep.py RUN_DIR --sign-key receipt-offline-key.pem \
        [--policy ~/.config/wattnot/approval-policy.json]

Outputs: <receipt>.reselected.json (+ .sigstore.json bundle) and checkpoint.reselected.json.
The grade stays ESTIMATE: signed offline, no public log entry (VALID needs Akash's okay).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from wattnot.proof.run import best_of
from wattnot.receipt.canon import canonical_sha256, scrub_nonfinite
from wattnot.receipt.sign import sign


def policy_decision(best: dict, stock_w: float, policy: dict) -> tuple[str, list[str]]:
    rule = (policy.get("kinds") or {}).get("power_cap") or {}
    why = []
    if not policy.get("enabled"):
        why.append("policy disabled")
    if best.get("label") not in (policy.get("allowed_labels") or []):
        why.append(f"label {best.get('label')} not allowed")
    saving = -best["energy_change_pct"]["mean"]
    if saving < rule.get("min_saving_pct", 0):
        why.append(f"saving {saving:.1f}% below {rule['min_saving_pct']}%")
    p99 = best["p99_change_pct"]["mean"]
    if p99 > rule.get("max_p99_increase_pct", 1e9):
        why.append(f"p99 +{p99:.1f}% above {rule['max_p99_increase_pct']}%")
    drop = (stock_w - best["cap_w"]) / stock_w * 100
    if drop > rule.get("max_cap_drop_pct", 100):
        why.append(f"cap drop {drop:.1f}% above {rule['max_cap_drop_pct']}%")
    return ("approved" if not why else "rejected"), why


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--sign-key", type=Path, required=True)
    ap.add_argument("--policy", type=Path,
                    default=Path("~/.config/wattnot/approval-policy.json").expanduser())
    a = ap.parse_args(argv)
    d = a.run_dir
    ck = json.loads((d / "checkpoint.json").read_text())
    st = ck["stages"]
    done = st["cap_sweep"]["done_steps"]
    best = best_of(done)
    rec_path = Path(st["receipt"]["rec"]["path"])
    if not rec_path.is_absolute() or not rec_path.exists():
        rec_path = d / rec_path.name
    body = json.loads(rec_path.read_text())
    stock_w = body["hardware"]["default_cap_w"]
    policy = json.loads(a.policy.read_text()) if a.policy.exists() else {}
    decision, why = (policy_decision(best, stock_w, policy) if best else (None, ["no accepted cap"]))

    new = copy.deepcopy(body)
    by_cap = {s["cap_w"]: s for s in done}
    for s in new.get("steps", []):
        j = by_cap.get(s["cap_w"])
        if j:
            s["accepted"], s["why_not"] = j["accepted"], j["why_not"]
    new["best_cap_w"] = best["cap_w"] if best else None
    new["receipt_id"] = body["receipt_id"] + "-reselected"
    new["created"] = datetime.now(UTC).isoformat(timespec="seconds")
    new["method"] = dict(new.get("method") or {}, accept=(
        "quality drop <= 1 pt, mean p99 change <= +10%, and with 2-3 pairs every pair's J/request "
        "drop > max(3%, 2x stock noise cv) (f076cce); 95% CI used from 4+ pairs"))
    new["approve"] = {"decision": decision, "by": "operator policy (offline re-select)",
                      "policy_set_by": policy.get("set_by"), "reasons_if_rejected": why,
                      "cap_w": best["cap_w"] if best else None}
    new["reselected"] = {
        "from_receipt": rec_path.name,
        "from_receipt_sha256": body["integrity"]["sha256"],
        "why": "sweep re-judged from saved data; approve/receipt rebuilt offline, no new GPU work",
        "checkpoint_sha256": hashlib.sha256((d / "checkpoint.json").read_bytes()).hexdigest(),
    }
    res = d / "vm" / "results.json"
    if res.exists():
        new["results_sha256"] = hashlib.sha256(res.read_bytes()).hexdigest()
    new["integrity"] = {"sha256": None, "signer": None, "rekor": None}
    clean, _ = scrub_nonfinite(new)
    new = clean
    new["integrity"]["sha256"] = canonical_sha256(new)
    out = rec_path.with_name(rec_path.stem + ".reselected.json")
    out.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    sign(out, no_rekor=True, key_path=a.sign_key)
    signed = json.loads(out.read_text())

    ck2 = copy.deepcopy(ck)
    ck2["stages"]["approve"] = {"decision": decision, "chg": None, "done": True, "offline": True}
    ck2["stages"]["finished"] = {"best_cap_w": new["best_cap_w"], "done": True}
    ck2["stages"]["receipt"] = {"rec": {"path": str(out), "sha256": signed["integrity"]["sha256"],
                                        "grade": "ESTIMATE"}, "done": True}
    (d / "checkpoint.reselected.json").write_text(json.dumps(ck2, indent=2) + "\n")
    print(json.dumps({"best_cap_w": new["best_cap_w"], "decision": decision, "why": why,
                      "receipt": str(out), "sha256": signed["integrity"]["sha256"],
                      "signer": signed["integrity"].get("signer"), "grade": "ESTIMATE",
                      "label": new.get("label")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
