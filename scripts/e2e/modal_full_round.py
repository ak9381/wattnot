"""One full-feature Modal round (option B): Glimmer 30B on our own vLLM, A100-80GB, every WattNot
lever the platform allows, then the off-GPU half (Spark grades, signed receipt with SCI, the full
flow: Wonder, Spark 4-call trace, approvals, EU report, label lint, Muse Code patch).

    python scripts/e2e/modal_full_round.py --round r04 [--limit 1] [--live-meta] [--live-pr]
    python scripts/e2e/modal_full_round.py --round r04 --fake      # MOCK, no Modal, $0

Guards: WATTNOT_MODAL_GO=1 for any GPU call; the $60 stage cap in data/modal-full-1101/stage_spend.json
(refuses when spent + this round's worst case > cap); Meta soft cap $100 before Spark grades;
`modal app list` checked after, and any wattnot-full-stage app is stopped.
Muse Code writes the patch every round; --live-pr also opens the PR, only on
ak9381/wattnot-demo-deploy (needs Akash's go). Keys come from the environment, never printed.
Never reads BASETEN_METRICS_KEY.
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
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def _load_stage():
    import importlib.util
    spec = importlib.util.spec_from_file_location("full_stage", ROOT / "modal" / "full_stage.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


full_stage = _load_stage()

DATA = ROOT / "data" / "modal-full-1101"
STAGE_CAP_USD = 60.0   # raised from $40 with Akash's yes (1 Nov, 5:39 PM IST); covers the later combined stage too
SOFT_META_USD = 100.0
SPEND = DATA / "stage_spend.json"
KEY = ROOT / "verifier" / "samples" / "test-key.pem"
PR_REPO = "ak9381/wattnot-demo-deploy"
DEMO_REPO = Path(os.environ.get("WATTNOT_DEMO_REPO", "/workspace/hackathon/meta6/demo2"))


def stage_spent() -> float:
    return json.loads(SPEND.read_text())["usd"] if SPEND.exists() else 0.0


def reserve(tag: str, usd: float | None):
    """Write the worst case to disk BEFORE the GPU starts, so a crash can never hide a paid round."""
    d = json.loads(SPEND.read_text()) if SPEND.exists() else {
        "usd": 0.0, "rounds": [], "label": "ESTIMATE", "method": "container wall minutes x A100-80GB list price"}
    res = d.setdefault("reserved", {})
    if usd is None:
        res.pop(tag, None)
    else:
        res[tag] = round(usd, 4)
    SPEND.parent.mkdir(parents=True, exist_ok=True)
    SPEND.write_text(json.dumps(d, indent=1))


def stage_committed() -> float:
    """Spent + any reservation left by a round that never reconciled (counts against the cap)."""
    d = json.loads(SPEND.read_text()) if SPEND.exists() else {}
    return float(d.get("usd", 0.0)) + sum((d.get("reserved") or {}).values())


def add_spend(round_id, usd, minutes):
    d = json.loads(SPEND.read_text()) if SPEND.exists() else {
        "usd": 0.0, "rounds": [], "label": "ESTIMATE", "method": "container wall minutes x A100-80GB list price"}
    d["usd"] = round(d["usd"] + usd, 4)
    d["rounds"].append({"round": round_id, "usd": round(usd, 4), "minutes": round(minutes, 1),
                        "at": datetime.now(UTC).isoformat()})
    SPEND.parent.mkdir(parents=True, exist_ok=True)
    SPEND.write_text(json.dumps(d, indent=1))


def app_running() -> list[str] | None:
    """Running wattnot-full-stage apps; None when `modal app list` could not be read (never 'empty')."""
    try:
        p = subprocess.run(["modal", "app", "list", "--json"], capture_output=True, text=True, timeout=120)
        apps = json.loads(p.stdout)
    except Exception:  # noqa: BLE001
        return None
    out = []
    for a in apps:
        low = {str(k).lower().replace(" ", "_"): v for k, v in a.items()}
        if full_stage.APP_NAME in json.dumps(a) and str(low.get("state", "")).lower() != "stopped":
            out.append(low.get("app_id") or low.get("id") or "UNKNOWN-ID")
    return out


def wait_app_stopped(deadline: float, lister=None, poll_s: float = 60, sleep=time.sleep,
                     now=time.time) -> dict:
    """Wait until no wattnot-full-stage app is running (or the deadline).

    The run is launched with ``modal run --detach``: if the local client drops (r04e LangChain
    run, 13:18 IST: "Stopping app - local client disconnected" after missed heartbeats), the
    container keeps going and still syncs its results to the volume. So a non-zero client exit
    is not the end of the run: wait for the app to stop, then recover from the volume."""
    lister = lister or app_running
    polls = 0
    while now() < deadline:
        live = lister()
        polls += 1
        if live == []:
            return {"stopped": True, "polls": polls}
        sleep(poll_s)
    return {"stopped": False, "polls": polls, "why": "deadline"}


OWNER = Path(os.environ.get("WATTNOT_MODAL_OWNER", "/tmp/wattnot-modal-owner.json"))


def foreign_owner() -> dict | None:
    """The paid round that owns the live wattnot-full-stage app, if it is another live process.
    r04b was stopped from outside at 04:28 IST: every stop path matched apps by NAME, so any other
    script's teardown could kill a running round. Now nothing stops the app while another live
    process holds this lock."""
    try:
        d = json.loads(OWNER.read_text())
        pid = int(d["pid"])
    except Exception:  # noqa: BLE001 - no lock / unreadable lock = no owner
        return None
    if pid == os.getpid():
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    return d


def take_owner(tag: str):
    OWNER.write_text(json.dumps({"pid": os.getpid(), "round": tag, "t": time.time()}))


def drop_owner():
    try:
        if json.loads(OWNER.read_text()).get("pid") == os.getpid():
            OWNER.unlink()
    except Exception:  # noqa: BLE001
        pass


def teardown() -> dict:
    fo = foreign_owner()
    if fo:
        return {"stopped": [], "still_running": None, "verified_empty": False,
                "skipped": f"app owned by live round {fo.get('round')} (pid {fo.get('pid')}); not stopping it"}
    stopped, left = [], None
    for _ in range(5):
        ids = app_running()
        if ids == []:
            left = []
            break
        for i in ids or []:
            if i == "UNKNOWN-ID":
                subprocess.run(["modal", "app", "stop", "-y", full_stage.APP_NAME], capture_output=True, timeout=120)
                continue
            try:
                subprocess.run(["modal", "app", "stop", "-y", i], capture_output=True, timeout=120)
                stopped.append(i)
            except Exception:  # noqa: BLE001 - keep trying the others, re-checked below
                pass
        time.sleep(10)
    else:
        left = app_running()
    return {"stopped": stopped, "still_running": left, "verified_empty": left == [],
            "checked_at": datetime.now(UTC).isoformat()}


def meta_total() -> float:
    from wattnot.meta.ledger import SpendLedger
    return SpendLedger().total()


def spark_grade(rdir: Path, round_id: str) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    from wattnot.longrun.spark_grade import SparkGrader, load_config
    spark = SparkGrader(model=load_config().get("spark_grade_model"), round_id=round_id)
    tot = dis = 0
    for tag in ("without", "with"):
        p = rdir / "raw" / f"{tag}.answers.jsonl"
        if not p.exists():
            continue
        rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
        with ThreadPoolExecutor(4) as ex:
            outs = list(ex.map(lambda r: spark(r["id"], r["dataset"], r["prompt"], r["answer"],
                                               r["deterministic"]), rows))
        for r, o in zip(rows, outs):
            r.update(spark=o["spark"], spark_reason=o["spark_reason"], spark_label=o["spark_label"],
                     disagree=o["disagree"])
            tot += o["spark"] is not None
            dis += bool(o["spark"] is not None and o["spark"] != r["deterministic"])
        with open(rdir / "raw" / f"{tag}.answers.graded.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    return {"label": spark.label, "graded": tot, "disagreements": dis,
            "agreement": round(1 - dis / tot, 4) if tot else None, "stopped": spark.stopped}


def build_receipt_for(rdir: Path, round_id: str, inner: dict, fake: bool) -> Path:
    from wattnot.receipt.build import build_receipt, verify_hash
    from wattnot.receipt.canon import canonical_sha256
    from wattnot.receipt.sign import sign
    S = inner["steps"]
    wo, wi = S["without"], S["with"]
    lab = "MOCK" if fake else "MEASURED"
    run = {
        "run_id": f"run_20261101_modal_full_{round_id}",
        "subject": {"model": "meta-models/Muse-Glimmer-30B",
                    "host": {"provider": "modal", "gpu": inner["gpu"], "driver": "recorded"},
                    "mode": "container",
                    "workload": {"name": "+".join({"ours": "ours92", "gsm8k": "gsm8k60", "humaneval": "humaneval40"}[x]
                                                  for x in inner.get("sets") or ("ours", "gsm8k", "humaneval")),
                                 "version": "1",
                                 "n_requests": wo["answers"]}},
        "baseline": {"params": {"power_cap_w": None, **S["without_server"]},
                     "wh_samples": wo["wh_per_item_samples"]["values"],
                     "p99_ms": round((wo["item_latency_s"]["p99"] or 0) * 1000),
                     "quality": {"fixed_pass_rate": wo["accuracy"]}},
        "candidate": {"params": {"power_cap_w": None, **S["with_server"], "concurrency": wi["concurrency"],
                                 "router": "no-think first, per-tier thinking budget on a failed check"},
                      "wh_samples": wi["wh_per_item_samples"]["values"],
                      "p99_ms": round((wi["item_latency_s"]["p99"] or 0) * 1000),
                      "quality": {"fixed_pass_rate": wi["accuracy"]}},
        "measurement": {"meter": "nvml", "hz": 1, "label": "SIMULATED" if fake else "MEASURED", "idle_w": inner["idle_w"]["value"],
                        "csv": [{"path": str(p.relative_to(ROOT)),
                                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                                for p in sorted((rdir / "raw").glob("*.power.csv"))
                                + sorted((rdir / "raw").glob("with*.jsonl"))]},
    }
    eye, sp = rdir / "eye" / "eye_test.json", rdir / "spoken" / "spoken_answer.json"
    r = build_receipt(run, eye_test=json.loads(eye.read_text()) if eye.exists() else None,
                      spoken_test=json.loads(sp.read_text()) if sp.exists() else None)
    r["modal_stage"] = {"label": lab, "cap_probe": inner["cap_probe"],
                        "batch_kv": S.get("batch_kv", {}).get("kept"),
                        "fp8": {"kept": S.get("fp8", {}).get("kept"), "gate": S.get("fp8", {}).get("gate")},
                        "limits": S.get("limits"),
                        "note": ("Signed locally with the public TEST key (fake run); not published to Rekor."
                                 if fake else "Signed locally with the WattNot certificate key "
                                 "(ed25519-wattnot, verifier/trusted-keys.json); not published to Rekor.")}
    r["integrity"]["sha256"] = canonical_sha256(r)
    assert verify_hash(r)[0], "hash did not re-seal"
    d = rdir / "sweep"
    d.mkdir(exist_ok=True)
    path = d / f"{r['receipt_id']}.json"
    path.write_text(json.dumps(r, indent=2) + "\n")
    # live rounds sign with the real certificate key and fail closed without it; only fake runs
    # (tests, samples) use the public test key
    from wattnot.receipt.certkey import require_cert_key
    sign(path, no_rekor=True, key_path=KEY if fake else require_cert_key())
    return path


def modal_metered() -> float | None:
    """Modal's own metered cost for ephemeral apps this month (MEASURED, from `modal billing summary`)."""
    try:
        p = subprocess.run(["modal", "billing", "summary", "--json"], capture_output=True, text=True, timeout=120)
        return float(json.loads(p.stdout)["metered_cost_breakdown"]["ephemeral_apps"])
    except Exception:  # noqa: BLE001
        return None


def recover_partial(tag: str, inc: Path, fetch=None) -> Path | None:
    """Pull whatever the container synced to the wattnot-results volume for this round.
    A free storage read (no GPU, no spend). Returns inc/tag if it now holds an inner.json, else None."""
    def _default_fetch(t, dest):
        base = ["modal", "volume", "get", full_stage.RESULTS_VOL, t, str(dest)]
        try:
            p = subprocess.run(base + ["--force"], capture_output=True, text=True, timeout=300)
            if p.returncode != 0:   # older CLI has no --force: retry the plain form
                subprocess.run(base, capture_output=True, text=True, timeout=300)
        except Exception:  # noqa: BLE001 - recovery is best-effort; absence is handled by the caller
            pass
    (fetch or _default_fetch)(tag, inc)
    return (inc / tag) if (inc / tag / "inner.json").exists() else None


def write_partial(rdir: Path, inner: dict, stage: dict, state) -> Path:
    """Record what a partial/crashed round managed to finish (no receipt, no grading, no pipeline)."""
    steps = inner.get("steps") or {}
    p = rdir / "partial.json"
    p.write_text(json.dumps({"status": "partial", "label": "MEASURED",
                             "recovered_from": "modal volume wattnot-results", "state": state,
                             "finished_steps": list(steps), "blocks": steps.get("blocks"),
                             "failed": inner.get("failed"), "fatal": inner.get("fatal"),
                             "spend": stage}, indent=1))
    return p


def fresh_demo_clone(tag: str) -> Path:
    """A clean clone of the demo repo on main for this round (never the user's working copy)."""
    d = Path("/tmp/wn-demo") / tag
    if d.exists():
        shutil.rmtree(d)
    d.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", f"https://github.com/{PR_REPO}", str(d)], check=True, timeout=180)
    return d


def verify_change(inner: dict, spark: dict, receipt: Path, no_spark: bool = False) -> dict:
    """Approve-before-apply: verify the held change against every gate. No auto-approve."""
    from wattnot.receipt.build import verify_hash
    S = inner["steps"]
    lim = S.get("limits") or {}
    qg = lim.get("quality_gate") or {}
    agree = spark.get("agreement")
    spark_off = no_spark or spark.get("stopped") == "not run"   # Spark deliberately not run this round
    spark_chk = ({"ok": None, "skipped": "Spark grading off for this run", "value": agree,
                  "rule": "Spark agrees with the fixed checks on >= 90% of answers"} if spark_off
                 else {"ok": agree is not None and agree >= 0.90, "value": agree,
                       "rule": "Spark agrees with the fixed checks on >= 90% of answers"})
    checks = {
        "accuracy_vs_baseline": {"ok": bool(qg.get("accuracy_ok")), "drop_pts": qg.get("acc_drop_pts"),
                                 "rule": "accuracy may drop at most 2 points"},
        "p99": {"ok": bool(lim.get("p99_ok")), "rule": "with-arm item p99 within +10% of without",
                "without_s": S["without"]["item_latency_s"]["p99"], "with_s": S["with"]["item_latency_s"]["p99"]},
        "joule_slo": {"ok": bool(lim.get("joule_slo_ok")), "rule": "J/correct with <= without",
                      "without": S["without"]["j_per_correct"]["value"], "with": S["with"]["j_per_correct"]["value"]},
        "spark_agreement": spark_chk,
        "fp8_gate": {"ok": (not (S.get("fp8") or {}).get("kept")) or bool(((S.get("fp8") or {}).get("gate") or {}).get("keep")),
                     "rule": "FP8 only if its own gate kept it"},
        "receipt_hash": {"ok": bool(verify_hash(json.loads(receipt.read_text()))[0]), "rule": "receipt re-seals"},
        "no_fatal": {"ok": not inner.get("fatal"), "value": inner.get("fatal")},
    }
    failed_checks = [k for k, c in checks.items() if c["ok"] is False]
    skipped_checks = [k for k, c in checks.items() if c["ok"] is None]
    if failed_checks:
        decision = "rejected"
    elif skipped_checks:
        decision = "held"       # a check was skipped: needs human review, not auto-approved
    else:
        decision = "approved"
    return {"decision": decision, "auto_approve": False, "checks": checks,
            "failed_checks": failed_checks, "skipped_checks": skipped_checks,
            "at": datetime.now(UTC).isoformat(), "by": "runner verification (rules above), recorded as evidence"}


def emit(path: Path, run: str, type_: str, **data):
    """Append one event to the live events file the dashboard follows."""
    ev = {"type": type_, "ts": datetime.now(UTC).isoformat(), "run": run, "step_id": f"l{time.time_ns()}",
          "data": data}
    with path.open("a") as f:
        f.write(json.dumps(ev, default=str) + "\n")


STAGE_FEATURE = {"eye_check": "eye_check", "hearing_check": "hearing_check", "sam_hotspot": "sam_hotspot",
                 "wonder_chart": "wonder_planner", "spark_trace": "spark_trace", "approvals": "approvals",
                 "musecode_pr": "musecode_pr", "code_mode": "code_mode", "receipt": "receipt",
                 "eu_report": "eu_report", "label_lint": "label_lint"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip", default="")
    ap.add_argument("--sets", nargs="+", default=[],
                    help="datasets (ours gsm8k humaneval); empty = all three. Public round: --sets gsm8k humaneval")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--no-spark", action="store_true")
    ap.add_argument("--live-meta", action="store_true", help="Wonder/SAM/Spark trace live (paid, cents)")
    ap.add_argument("--live-pr", action="store_true", help=f"open the Muse Code PR on {PR_REPO}")
    ap.add_argument("--events", type=Path, default=None,
                    help="live events file for the dashboard (--events on the server); default <data>/<tag>.events.jsonl")
    a = ap.parse_args(argv)
    tag = f"smoke-{a.round}" if a.limit else a.round
    rdir = DATA / ("fake" if a.fake else "") / tag
    if rdir.exists() and not a.fake:
        print(f"[stop] {rdir} exists; never overwriting a recorded round")
        return 2
    stage = {"limit": a.limit, "sets": a.sets or ["ours", "gsm8k", "humaneval"]}
    recovered, state = False, None
    # a --fake run keeps its events under data/.../fake/ so it can never sit where a live round writes
    a.events = (a.events or (DATA / "fake" if a.fake else DATA) / f"{tag}.events.jsonl").resolve()
    a.events.parent.mkdir(parents=True, exist_ok=True)
    if a.fake:
        subprocess.run([sys.executable, "scripts/e2e/modal_full_inner.py", "--fake", "--out", str(rdir),
                        "--limit", str(a.limit or 2)] + (["--sets", *a.sets] if a.sets else []),
                       cwd=ROOT, check=True)
    else:
        if os.environ.get(full_stage.GO_ENV) != "1":
            print(f"[stop] set {full_stage.GO_ENV}=1 for a paid round")
            return 3
        cap_s = full_stage.SMOKE_CAP_S if a.limit else full_stage.ROUND_CAP_S
        worst = cap_s / 3600 * full_stage.GPU_USD_H
        if worst > (full_stage.SMOKE_HARD_USD if a.limit else full_stage.ROUND_HARD_USD):
            print(f"[stop] round worst ${worst:.2f} > hard ${full_stage.ROUND_HARD_USD:.0f} cap")
            return 4
        if app_running() != []:
            print("[stop] a wattnot-full-stage app is already running (or `modal app list` failed); one job at a time")
            return 4
        meas0 = modal_metered()
        if a.limit > 3:
            print("[stop] smoke rounds take --limit 1..3 (32-min container cap, $1.33 worst)")
            return 4
        if stage_committed() + worst > STAGE_CAP_USD:
            print(f"[stop] stage ${stage_committed():.2f} + worst ${worst:.2f} > ${STAGE_CAP_USD:.0f} cap")
            return 4
        if foreign_owner():
            print(f"[stop] another paid round holds {OWNER}; one job at a time")
            return 4
        reserve(tag, worst)
        take_owner(tag)
        inc = DATA / "_incoming"
        t0, rc = time.time(), 1
        try:
            rc = subprocess.run(["modal", "run", "--detach", "modal/full_stage.py::round_", "--round", tag, "--limit",
                                 str(a.limit), "--skip", a.skip, "--out", str(inc), "--events", str(a.events)]
                                + (["--sets", ",".join(a.sets)] if a.sets else []), cwd=ROOT,
                                timeout=cap_s + 900).returncode
        except Exception as e:  # noqa: BLE001 - timeout / CLI missing: still record spend + teardown
            print(f"[fail] modal run: {type(e).__name__}")
            rc = 1
        finally:
            if rc != 0:   # detached: a dropped client does not stop the app; wait for it to finish
                print("[wait] " + json.dumps(wait_app_stopped(t0 + cap_s + 600)), flush=True)
            mins = (time.time() - t0) / 60
            add_spend(tag, mins / 60 * full_stage.GPU_USD_H, mins)
            reserve(tag, None)
            try:
                td = teardown()
            except Exception as e:  # noqa: BLE001
                td = {"verified_empty": False, "error": f"{type(e).__name__}"}
            drop_owner()
            print(f"[teardown] {json.dumps(td)}")
            if not td["verified_empty"]:
                print("[ALERT] Modal app still running or unverifiable; stop it by hand: modal app list")
        stage.update(modal_usd={"value": round(mins / 60 * full_stage.GPU_USD_H, 4), "label": "ESTIMATE",
                                "method": "wall minutes incl. image and container start x list price"},
                     stage_total_usd={"value": stage_spent(), "label": "ESTIMATE"}, teardown=td)
        time.sleep(60)   # metered cost lags a little
        meas1 = modal_metered()
        if meas0 is not None and meas1 is not None:
            stage["modal_metered_usd"] = {"value": round(meas1 - meas0, 4), "label": "MEASURED",
                                          "source": "modal billing summary: ephemeral_apps delta (may lag)"}
            est = stage["modal_usd"]["value"]
            if meas1 - meas0 > est:   # the cap counts whichever is higher
                add_spend(f"{tag}-metered-true-up", meas1 - meas0 - est, 0)
        if rc != 0 or not (inc / tag / "inner.json").exists():
            if recover_partial(tag, inc) is None:   # nothing synced to the volume either: give up
                print(f"[fail] modal run rc={rc}")
                return 5
            recovered = True
            st = inc / tag / "status.json"
            state = json.loads(st.read_text()).get("state") if st.exists() else None
            print(f"[recover] partial results from volume (state={state})")
        shutil.move(str(inc / tag), str(rdir))
    if a.fake and (rdir / "events.jsonl").exists():
        full_stage.route_events((rdir / "events.jsonl").read_text().splitlines(), str(a.events), {})
    inner = json.loads((rdir / "inner.json").read_text())
    lab0 = "MOCK" if a.fake else "MEASURED"
    with_ev = Path(full_stage.arm_events_path(str(a.events), "with"))
    E = lambda t, **d: emit(with_ev, f"modal {tag}", t, label=d.pop("label", lab0), **d)  # noqa: E731
    if "with" not in inner["steps"]:
        state = state or ("crashed" if inner.get("fatal") else "incomplete")
        write_partial(rdir, inner, stage, state)   # crashed/partial runs still record what finished
        try:
            E("run.finished", arm="with_wattnot", status="partial", state=state)
        except Exception:  # noqa: BLE001 - the partial.json is the record; the event is a nicety
            pass
        if recovered:
            print(f"[recover] with-WattNot arm missing; wrote partial.json (state={state})")
            return 7
        print("[fail] the with-WattNot arm did not finish; see inner.log")
        return 6
    if a.fake:
        os.environ["WATTNOT_META_MOCK"] = "1"
    spark = {"label": None, "stopped": "not run"}
    if not a.no_spark:
        m0 = meta_total()
        n = inner["steps"]["without"]["answers"] * 2
        if m0 + n * 0.0022 > SOFT_META_USD:
            spark["stopped"] = f"Meta ${m0:.2f} + worst ${n * 0.0022:.2f} would cross the $100 soft cap"
        else:
            try:
                E("step.started", node="agent", sub="grade", summary="Muse Spark grades every answer")
                spark = spark_grade(rdir, tag)
                E("step.finished", node="gates", sub="judge", status="ok",
                  summary=f"Spark graded {spark.get('graded')}, agreement {spark.get('agreement')}")
            except Exception as e:  # noqa: BLE001
                spark = {"label": None, "stopped": f"error: {type(e).__name__}: {e}"[:300]}
            stage["meta_usd"] = {"value": round(meta_total() - m0, 4), "label": "MEASURED",
                                 "source": "Meta spend ledger"}
    try:
        receipt = build_receipt_for(rdir, tag, inner, a.fake)
    except Exception as e:  # noqa: BLE001 - keep the paid data; mark the receipt FAILED
        (rdir / "RECEIPT_ERROR.txt").write_text(f"{type(e).__name__}: {e}")
        inner.setdefault("failed", {})["receipt"] = f"NVML receipt build failed: {type(e).__name__}: {e}"[:300]
        receipt = None
    flow = [sys.executable, "-m", "wattnot.flow", "--out", str(rdir / "flow"), "--gpu-dir", str(rdir),
            "--tag", tag] + (["--live-meta"] if a.live_meta else [])
    fr = subprocess.run(flow, cwd=ROOT, capture_output=True, text=True, timeout=3600,
                        env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}",
                             **({} if a.fake else {"WATTNOT_REQUIRE_CERT_KEY": "1"})})
    (rdir / "flow.log").write_text((fr.stdout + fr.stderr)[-100000:])
    stage["flow_rc"] = fr.returncode
    S = inner["steps"]
    wo, wi = S["without"], S["with"]
    lab = "MOCK" if a.fake else "MEASURED"
    pct = lambda x, y: round(100 * (x - y) / x, 2) if x and y is not None else None  # noqa: E731
    doc = {"round": a.round, "tag": tag, "leg": "modal", "status": "done", "label": lab,
           "model": "meta-models/Muse-Glimmer-30B", "where": f"Modal {inner['gpu']}, our own vLLM",
           "billing": "Modal hackathon credits", "smoke": bool(a.limit), "fake": a.fake,
           "started_utc": inner["started_utc"], "ended_utc": inner.get("ended_utc"),
           "levers": {"without_wattnot": "BF16, prefix cache off, thinking on (3072-token budget), concurrency 4",
                      "with_wattnot": "every lever Modal allows; table in docs/MODAL-FULL-STAGE.md"},
           "results": {"accuracy_without": wo["accuracy"], "accuracy_with": wi["accuracy"],
                       "j_per_correct_without": wo["j_per_correct"]["value"],
                       "j_per_correct_with": wi["j_per_correct"]["value"],
                       "energy_saved_pct": {"value": pct(wo["energy_j"]["value"], wi["energy_j"]["value"]), "label": lab},
                       "j_per_correct_saved_pct": {"value": pct(wo["j_per_correct"]["value"],
                                                                wi["j_per_correct"]["value"]), "label": lab},
                       "per_arm": {"without_wattnot": wo, "with_wattnot": wi}},
           "levers_detail": {k: S.get(k) for k in ("batch_kv", "fp8", "limits", "blocks")},
           "cap_probe": inner["cap_probe"], "spark_summary": spark,
           "receipt": str(receipt.relative_to(ROOT)) if receipt else None, "raw_dir": str(rdir.relative_to(ROOT)), "spend": stage}
    if recovered:   # the with arm ran but the tar was lost; results came back off the volume
        doc["status"], doc["recovered"] = "recovered", True
    # shared with-WattNot pipeline (same wiring as the Baseten stage), GPU feature table
    from wattnot.e2e.fullstack import gpu_feature_table, post_round
    failed = dict(inner.get("failed") or {})
    if not a.no_spark and spark.get("agreement") is None and spark.get("stopped") != "not run":
        failed["spark_grades"] = f"Spark grading did not complete: {spark.get('stopped')}"
    decision = verify_change(inner, spark, receipt, a.no_spark) if receipt else {
        "decision": "rejected", "auto_approve": False, "failed_checks": ["receipt_hash"], "checks": {},
        "at": datetime.now(UTC).isoformat()}
    (rdir / "approval_evidence.json").write_text(json.dumps(decision, indent=1))
    print(f"[approval] held change {decision['decision']}: failed checks {decision['failed_checks']}")
    E("step.finished", node="proposals", sub="review", status="ok" if decision["decision"] == "approved" else "bad",
      summary=f"held change {decision['decision']} after checks" + (f" (failed: {', '.join(decision['failed_checks'])})"
                                                                    if decision["failed_checks"] else ""))
    S0 = inner["steps"]
    change = {"vllm_prefix_caching": True,
              "quantization": "fp8" if (S0.get("fp8") or {}).get("applied") else "bf16",
              "max_num_seqs": S0["with_server"]["max_num_seqs"], "router": "cheapest-first",
              "thinking_budget": "per-tier, two-phase enforcer",
              "saving_pct": doc["results"]["j_per_correct_saved_pct"]["value"],
              "labels": {"saving_pct": lab, "energy": lab}}
    open_pr = a.live_pr and decision["decision"] == "approved"
    demo = fresh_demo_clone(tag) if open_pr else DEMO_REPO
    stages = post_round(rdir, doc, mode="live" if a.live_meta and not a.fake else "smoke",
                        open_pr=open_pr, demo_repo=demo, table=lambda: gpu_feature_table(failed),
                        change=change, energy_label=lab, kind="modal-own-gpu-e2e", decision=decision)
    node_of = {"receipt": ("receipts", "sign"), "eu_report": ("eu", "fields"), "sam_hotspot": ("sam", "map"),
               "spark_trace": ("agent", "trace"), "wonder_chart": ("agent", "plan"), "musecode_pr": ("apply", "pr"),
               "eye_check": ("gates", "eye"), "hearing_check": ("gates", "hearing")}
    for st, (nd, sb) in node_of.items():
        if isinstance(stages.get(st), dict):
            E("step.finished", node=nd, sub=sb, status="ok" if stages[st].get("status") == "ok" else "bad",
              summary=f"{st}: {stages[st].get('status')}")
    for st, fid in STAGE_FEATURE.items():
        if (stages.get(st) or {}).get("status") == "error":
            failed.setdefault(fid, f"{st} stage error: {stages[st].get('error')}")
    if fr.returncode != 0:
        doc["flow_note"] = "flow exited non-zero (see flow.log); the efficiency certificate now declines a NOISE grade with exit 0, so it is not the cause"
    doc["feature_stages"] = stages
    doc["failed"] = failed
    doc["features"] = gpu_feature_table(failed)
    from importlib import util as _u
    _sp = _u.spec_from_file_location("mfi", ROOT / "scripts" / "e2e" / "modal_full_inner.py")
    off_nodes = {"adaptive": "MoE top-k OFF", "flex": "Flex OFF"}
    try:
        _m = _u.module_from_spec(_sp)
        _sp.loader.exec_module(_m)
        off_nodes = _m.OFF_NODES
    except Exception:  # noqa: BLE001
        pass
    (rdir / "features" / "stage_features.json").write_text(json.dumps(
        {"stage": "modal", "features": doc["features"], "off_nodes": off_nodes,
         "baseline_nodes": ["workload", "models", "telemetry", "gates"]}, indent=1))
    E("stage.features", stage="modal", features=doc["features"], off_nodes=off_nodes)
    E("run.finished", arm="with_wattnot")
    doc["approval"] = decision
    (rdir / "features" / "features.json").write_text(json.dumps({"table": doc["features"], "stages": stages},
                                                               indent=1, default=str))
    stage["musecode"] = doc["feature_stages"].get("musecode_pr")
    mdir = rdir / "smoke-matrix" if (a.limit or a.fake) else ROOT / "out" / "matrix"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / f"{a.round}.json").write_text(json.dumps(doc, indent=1))
    (rdir / "round.json").write_text(json.dumps(doc, indent=1))
    r = doc["results"]
    print(json.dumps({"round": tag, "acc": [r["accuracy_without"], r["accuracy_with"]],
                      "j_per_correct": [r["j_per_correct_without"], r["j_per_correct_with"]],
                      "saved_pct": r["j_per_correct_saved_pct"], "spark": spark, "flow_rc": fr.returncode,
                      "musecode": stage.get("musecode"), "modal_usd": stage.get("modal_usd"),
                      "approval": decision["decision"], "failed": sorted(failed)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
