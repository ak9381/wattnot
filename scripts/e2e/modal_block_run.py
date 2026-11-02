"""Block-only Modal run attached to a recorded round (never overwrites it).

r04e's LangChain block failed (rc 5). Akash: rerun LangChain on its own, with its own run id,
and attach its signed result to r04e's round view. This script:

* refuses unless WATTNOT_MODAL_GO=1 (paid), the stage and round caps hold, no other paid round
  owns the Modal lock and no wattnot-full-stage app is running (same rules as a full round);
* runs ``modal run modal/full_stage.py::round_ --only <blocks>`` (with-WattNot server as r04e
  kept it: BF16, prefix cache, --max-num-seqs 128), container cap BLOCK_CAP_S;
* writes data/modal-full-1101/attachments/<round>/<run_id>/ (refuses if it exists) with an
  ``attachment.json`` sealed by hash and signed with the WattNot certificate key (fake: test key);
* never writes inside data/modal-full-1101/<round>/.

    python scripts/e2e/modal_block_run.py --attach r04e --only langchain --plan   # free: prints the estimate
    python scripts/e2e/modal_block_run.py --attach r04e --only langchain --fake   # free: CPU, MOCK labels
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts" / "e2e"), str(ROOT / "scripts")]
import modal_full_round as R  # noqa: E402

FS = R.full_stage
DATA = R.DATA
SERVER_START_S = 117.1          # MEASURED r04e without_server start (inner.json)
BLOCK_EST_S = {"langchain": 2320.1}   # MEASURED r04e langchain wall (both arms); the fix adds <=20 finalize calls
FINALIZE_EXTRA_S = 20 * 14.4    # ESTIMATE: <=1 finalize call per item at ~14.4 s (r04c thinking-on mean)
OVERHEAD_S = 6 * 60             # ESTIMATE: image + container start + tar pull + teardown


def plan(only: list[str]) -> dict:
    run_s = SERVER_START_S + sum(BLOCK_EST_S.get(k, 900) for k in only) + \
        (FINALIZE_EXTRA_S if "langchain" in only else 0) + OVERHEAD_S
    usd_h = FS.GPU_USD_H
    return {"expected_min": round(run_s / 60, 1), "expected_usd": round(run_s / 3600 * usd_h, 2),
            "worst_min": FS.BLOCK_CAP_S / 60, "worst_usd": round(FS.BLOCK_CAP_S / 3600 * usd_h, 2),
            "meta_usd": 0.0, "label": "ESTIMATE",
            "method": f"r04e MEASURED block wall + server start, x ${usd_h}/h A100-80GB list price; "
                      "worst = container cap"}


wait_app_stopped = R.wait_app_stopped   # shared with full rounds (modal_full_round)


def attachment(rdir: Path, attach: str, run_id: str, only: list[str], fake: bool, spend: dict) -> Path:
    from wattnot.receipt.canon import canonical_sha256
    from wattnot.receipt.sign import sign
    inner = json.loads((rdir / "inner.json").read_text())
    blocks = inner["steps"].get("blocks") or {}
    res = {}
    for k in only:
        f = {"langchain": rdir / "langchain" / "langchain_pass.json"}.get(k)
        doc = json.loads(f.read_text()) if f and f.exists() else None
        res[k] = {"rc": (blocks.get(k) or {}).get("rc"), "energy_j": (blocks.get(k) or {}).get("energy_j"),
                  "file": f.relative_to(ROOT).as_posix() if f and f.exists() and f.is_relative_to(ROOT) else None,
                  "file_sha256": hashlib.sha256(f.read_bytes()).hexdigest() if f and f.exists() else None,
                  "quality_held": (doc or {}).get("quality_held"),
                  "arms": {a: {"correct": v.get("correct"), "n": v.get("n"), "gpu_j": v.get("gpu_j"),
                               "j_per_correct": v.get("j_per_correct")}
                           for a, v in ((doc or {}).get("arms") or {}).items()}}
    ok = all(r["rc"] == 0 and r["quality_held"] for r in res.values())
    lc = res.get("langchain") or {}
    arms = lc.get("arms") or {}
    summary = (f"LangChain saver {arms.get('saver_on', {}).get('correct')}/{arms.get('saver_on', {}).get('n')} "
               f"vs {arms.get('saver_off', {}).get('correct')}/{arms.get('saver_off', {}).get('n')} without"
               if arms else ", ".join(f"{k}: rc {v['rc']}" for k, v in res.items()))
    doc = {"spec": "wattnot.round-attachment/1", "run_id": run_id, "attaches_to": attach,
           "block": ",".join(only), "status": "passed" if ok else "failed",
           "label": "MOCK" if fake else "MEASURED", "summary": summary,
           "created_at": datetime.now(UTC).isoformat(), "server": inner["steps"].get("with_server"),
           "gpu": inner.get("gpu"), "results": res, "spend": spend,
           "note": "Separate run with its own id; the round's own files are never changed.",
           "integrity": {"sha256": ""}}
    doc["integrity"]["sha256"] = canonical_sha256(doc)
    path = rdir / "attachment.json"
    path.write_text(json.dumps(doc, indent=2) + "\n")
    from wattnot.receipt.certkey import require_cert_key
    sign(path, no_rekor=True, key_path=R.KEY if fake else require_cert_key())
    return path


def partial_out(got: Path, dest: Path, attach: str, run_id: str, spend: dict, rc: int) -> int:
    """A cut run: keep whatever synced (per-item LangChain lines included) and score it, unsigned."""
    import langchain_pass as LP
    if not got.exists():
        return 5
    shutil.move(str(got), str(dest))
    scores = LP.partial_scores(dest / "langchain")
    (dest / "partial.json").write_text(json.dumps(
        {"run_id": run_id, "attaches_to": attach, "status": "partial", "label": "MEASURED", "client_rc": rc,
         "scores_so_far": scores, "spend": spend,
         "note": "Cut before the block finished; scores are items finished so far (not signed)."}, indent=1))
    print(f"[partial] {json.dumps(scores)} -> {dest}")
    return 5


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attach", required=True, help="recorded round to attach to, e.g. r04e")
    ap.add_argument("--only", nargs="+", default=["langchain"])
    ap.add_argument("--run-id", default="")
    ap.add_argument("--plan", action="store_true", help="print the cost/time ESTIMATE and exit (free)")
    ap.add_argument("--fake", action="store_true", help="CPU only, MOCK labels (free)")
    ap.add_argument("--limit", type=int, default=0, help="--fake only: items per block")
    a = ap.parse_args(argv)
    p = plan(a.only)
    print("[plan] " + json.dumps(p))
    if a.plan:
        return 0
    if not (DATA / a.attach / "round.json").exists() and not a.fake:
        print(f"[stop] round {a.attach} has no round.json")
        return 2
    run_id = a.run_id or f"{a.attach}-{'-'.join(a.only)}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    base = DATA / ("fake" if a.fake else "") / "attachments" / a.attach
    dest = base / run_id
    if dest.exists():
        print(f"[stop] {dest} exists; never overwriting a recorded run")
        return 2
    base.mkdir(parents=True, exist_ok=True)
    spend: dict = {}
    if a.fake:
        subprocess.run([sys.executable, "scripts/e2e/modal_full_inner.py", "--fake", "--out", str(dest),
                        "--limit", str(a.limit or 2), "--only", *a.only], cwd=ROOT, check=True,
                       env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"})
    else:
        if os.environ.get(FS.GO_ENV) != "1":
            print(f"[stop] set {FS.GO_ENV}=1 for a paid block run")
            return 3
        worst = p["worst_usd"]
        if worst > FS.ROUND_HARD_USD:
            print(f"[stop] worst ${worst:.2f} > ${FS.ROUND_HARD_USD:.0f} round cap")
            return 4
        if R.stage_committed() + worst > R.STAGE_CAP_USD:
            print(f"[stop] stage ${R.stage_committed():.2f} + worst ${worst:.2f} > ${R.STAGE_CAP_USD:.0f} cap")
            return 4
        if R.app_running() != []:
            print("[stop] a wattnot-full-stage app is already running (or `modal app list` failed)")
            return 4
        if R.foreign_owner():
            print(f"[stop] another paid round holds {R.OWNER}; one job at a time")
            return 4
        R.reserve(run_id, worst)
        R.take_owner(run_id)
        inc = DATA / "_incoming"
        events = DATA / "attachments" / a.attach / f"{run_id}.events.jsonl"
        t0, rc = time.time(), 1
        try:
            rc = subprocess.run(["modal", "run", "--detach", "modal/full_stage.py::round_", "--round", run_id,
                                 "--only", ",".join(a.only), "--out", str(inc), "--events", str(events)],
                                cwd=ROOT, timeout=FS.BLOCK_CAP_S + 900).returncode
        except Exception as e:  # noqa: BLE001
            print(f"[fail] modal run: {type(e).__name__}")
        finally:
            if rc != 0:   # client gone or call failed: the detached app may still be working
                print(f"[wait] client rc={rc}; waiting for the detached app to stop", flush=True)
                print("[wait] " + json.dumps(wait_app_stopped(t0 + FS.BLOCK_CAP_S + 600)), flush=True)
            mins = (time.time() - t0) / 60
            R.add_spend(run_id, mins / 60 * FS.GPU_USD_H, mins)
            R.reserve(run_id, None)
            try:
                td = R.teardown()
            except Exception as e:  # noqa: BLE001
                td = {"verified_empty": False, "error": type(e).__name__}
            R.drop_owner()
            print(f"[teardown] {json.dumps(td)}")
        spend = {"modal_usd": {"value": round(mins / 60 * FS.GPU_USD_H, 4), "label": "ESTIMATE"},
                 "stage_total_usd": {"value": R.stage_spent(), "label": "ESTIMATE"}, "teardown": td}
        if rc != 0 or not (inc / run_id / "inner.json").exists():
            if R.recover_partial(run_id, inc) is None:
                print(f"[fail] modal run rc={rc}")
                return partial_out(inc / run_id, dest, a.attach, run_id, spend, rc)
        shutil.move(str(inc / run_id), str(dest))
    path = attachment(dest, a.attach, run_id, a.only, a.fake, spend)
    print(f"[attached] {path} -> round {a.attach}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
