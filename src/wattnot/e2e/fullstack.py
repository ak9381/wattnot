"""The with-WattNot arm as the whole framework, shared by every hosted end-to-end runner.

"End to end" means WattNot as a framework: every feature that can run on the stage is ON in
the with-WattNot arm, and every feature that cannot is listed OFF with the reason. Runners
(Baseten, Modal shared endpoint) use the same three pieces:

  FEATURES        the ON/OFF table (written into every round file and receipt)
  RequestLevers   per-request levers wrapped around the with-WattNot chat call
  post_round()    the stages that run after the answers: eye check, hearing check, SAM 3.1,
                  Wonder planner and chart, Spark 4-call trace, approvals, Muse Code PR,
                  Code Mode, signed receipt with SCI, EU report, label lint

mode="live" makes paid Meta calls (eye, hearing, SAM, Wonder); mode="smoke" uses each
module's offline path and labels the result MOCK. Nothing here reads or prints a key.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import traceback
from datetime import UTC, datetime
from pathlib import Path

HOSTED = "hosted API (per-token, no GPU access)"
GPU = "needs our own GPU / our own vLLM server"

# (id, name, status, reason)
FEATURES: list[tuple[str, str, str, str]] = [
    ("effort_routing", "Reasoning-effort + cheapest-first routing (low, step up on a failed check)", "ON", HOSTED),
    ("thinking_budget", "Thinking-budget enforcer (max_tokens per effort)", "ON", HOSTED),
    ("router", "Backend/effort router", "ON", "effort leg only; one hosted backend on this stage"),
    ("context_saver", "Agent saver: tool-output dedup + memory compression", "ON", HOSTED),
    ("waste_guard", "Agent-waste proxy (repeat/no-op/cycle/overspend detectors)", "ON", HOSTED),
    ("code_mode", "Code Mode with locked sandbox + network boundary", "ON", "runs on the box over recorded traces"),
    ("prefix_cache_provider", "Prefix caching (provider side, reported cached tokens)", "ON",
     "MEASURED cached_tokens from the API; we cannot control the cache"),
    ("concurrency_batching", "Concurrency batching (client side)", "ON", "client concurrency only"),
    ("gates", "Noise gate, fixed answer gate, LLM judge, reasoning-leak strip", "ON", HOSTED),
    ("spark_grades", "Muse Spark grades every answer", "ON", "Meta API"),
    ("joule_slo", "Joule-SLO + user p99 limit", "ON", "energy ESTIMATE from tokens"),
    ("eye_check", "Eye check: vision gate on the SYNTHETIC Muse Image set", "ON", "Meta API"),
    ("sam_hotspot", "SAM 3.1 hotspot audit", "ON", "Meta API"),
    ("hearing_check", "Hearing check: Muse Voice Transcribe on spoken answers", "ON", "Meta API"),
    ("wonder_planner", "Muse Wonder planner + Wonderchart", "ON", "Meta API"),
    ("spark_trace", "Spark 4-call trace (probe, proposal, gate, receipt)", "ON", "Meta API"),
    ("approvals", "Approve-before-apply with risk classifier, undo", "ON", "local"),
    ("musecode_pr", "Muse Code PR flow (ak9381/wattnot-demo-deploy only)", "ON",
     "patch always built; PR opened only with --open-pr at stage time"),
    ("receipt", "Signed receipt + SCI carbon/water + joules per good answer", "ON", "local signing key"),
    ("eu_report", "EU report", "ON", "local"),
    ("label_lint", "Label-honesty lint", "ON", "local"),
    ("spend_guard", "Spend guards (Baseten hard cap, Meta soft cap, ledger)", "ON", "local"),
    ("power_caps", "Power-cap sweep + J/correct gate", "OFF", GPU),
    ("fp8", "FP8 quantization keep/reject gate", "OFF", GPU),
    ("kv_batch", "Batch-size + KV-cache sweep", "OFF", GPU),
    ("vllm_prefix", "vLLM prefix-cache control", "OFF", GPU),
    ("clock_sidecar", "Phase-aware clock sidecar, marginal-joule knee", "OFF", GPU),
    ("moe", "MoE top-k / per-expert pinning", "OFF", "needs Spark Open self-served (8x B200)"),
    ("measured_energy", "MEASURED GPU energy (NVML)", "OFF", "hosted API exposes no power meter; energy is ESTIMATE"),
    ("flex", "Flex grid-event load shedding", "OFF", "simulator only"),
    ("rekor", "Rekor publish", "OFF", "only after the long run, with Akash's yes"),
    ("own_gpu_serving", "Own-GPU vLLM serving + E2E rig", "OFF", GPU),
]

# Which live-graph nodes each feature drives. A node is greyed before the run when every feature mapped
# to it is OFF on the stage. The Modal stage reuses this with its own FEATURES statuses.
FEATURE_NODES: dict[str, list[str]] = {
    "effort_routing": ["effort_router"], "router": ["route"], "context_saver": ["context"],
    "prefix_cache_provider": ["cache"], "gates": ["gates"], "spark_trace": ["agent"],
    "wonder_planner": ["agent"], "sam_hotspot": ["sam"], "approvals": ["proposals"],
    "receipt": ["receipts", "certificate"], "eu_report": ["eu", "report"],
    "power_caps": ["apply", "optimizer"], "kv_batch": ["optimizer"], "clock_sidecar": ["apply"],
    "moe": ["adaptive"], "measured_energy": ["telemetry", "meters_roadmap"], "flex": ["flex"],
    "own_gpu_serving": ["e2e_rig"],
}


def stage_graph(features: list[tuple[str, str, str, str]] | None = None, stage: str = "baseten") -> dict:
    """{stage, off_nodes: {node: reason}} for the live graph, from the stage feature table."""
    feats = features or FEATURES
    by_node: dict[str, list[tuple[str, str, str]]] = {}
    for fid, name, status, why in feats:
        for n in FEATURE_NODES.get(fid, []):
            by_node.setdefault(n, []).append((name, status, why))
    off = {n: "; ".join(sorted({f"{nm}: {w}" for nm, st, w in rows}))
           for n, rows in by_node.items() if all(st == "OFF" for _, st, _ in rows)}
    return {"stage": stage, "off_nodes": off}


def feature_table() -> list[dict]:
    return [{"id": i, "name": n, "status": s, "reason": r} for i, n, s, r in FEATURES]


def agent_blocks_status(model: str | None = None) -> dict:
    """Can the agent blocks (context saver, agent-waste guard) really run on this stage?

    They dedup tool output and guard tool-call loops, so they need a tool-calling-capable model.
    If the chosen model does not serve tool calls, the blocks are reported skipped with the
    reason instead of pretending to be ON (see wattnot.cloud.supports_tool_calling)."""
    from wattnot.cloud import MODEL, supports_tool_calling
    m = model or MODEL
    if supports_tool_calling(m):
        return {"status": "ON", "tool_calling": True,
                "reason": f"{m} serves OpenAI tool calls; agent saver + waste guard active"}
    return {"status": "skipped", "tool_calling": False,
            "reason": f"{m} has no tool calling on this provider; agent saver + waste guard need it"}


class RequestLevers:
    """Per-request levers for the with-WattNot arm: agent saver + waste guard.

    Thread-safe enough for the runners (each call gets fresh guard state; totals are summed).
    The agent blocks need a tool-calling-capable model; when the model has none we send no
    `tools` field (an unsupported one would be rejected) and record the blocks as skipped."""

    def __init__(self, tools_supported: bool | None = None):
        import threading
        from wattnot.cloud import supports_tool_calling
        from wattnot.context_saver.middleware import ContextSaver
        # the user's on/off and threshold; off = a pass-through saver so the totals still add up
        self.saver = ContextSaver.from_settings() or ContextSaver(middlewares=[])
        self.lock = threading.Lock()
        self.tools_supported = supports_tool_calling() if tools_supported is None else bool(tools_supported)
        self.stats = {"calls": 0, "tokens_saved_est": 0, "guard_stops": 0, "guard_flags": 0,
                      "tool_calling": self.tools_supported, "tools_skipped": 0}

    def apply(self, body: dict) -> tuple[dict | None, dict]:
        from wattnot.agentwaste.guard import WasteGuard
        msgs, info = self.saver.transform(list(body.get("messages") or []))
        body = {**body, "messages": msgs}
        tools_skipped = False
        if body.get("tools") and not self.tools_supported:
            # Never send a tools field to a model that cannot answer it; the guard's tool-level
            # detectors cannot run here, so drop tools and mark the agent block skipped.
            body = {k: v for k, v in body.items() if k != "tools"}
            tools_skipped = True
        v = WasteGuard().before(body)
        with self.lock:
            self.stats["calls"] += 1
            self.stats["tokens_saved_est"] += int(info.get("tokens_saved", 0))
            self.stats["guard_flags"] += len(v.flags or [])
            if v.action == "stop":
                self.stats["guard_stops"] += 1
            if tools_skipped:
                self.stats["tools_skipped"] += 1
        return ((v.body or body) if v.action != "stop" else None,
                {"saver": info, "guard": v.action,
                 "agent_blocks": "skipped: no tool calling" if tools_skipped else "ok"})


def _stage(results: dict, name: str, fn):
    try:
        out = fn()
        results[name] = {"status": "ok", **(out if isinstance(out, dict) else {"out": out})}
    except SystemExit as e:
        results[name] = {"status": "ok" if not e.code else "error", "exit": e.code}
    except Exception as e:  # noqa: BLE001 - one stage failing never stops the others
        results[name] = {"status": "error", "error": f"{type(e).__name__}: {e}"[:300],
                         "where": traceback.format_exc().splitlines()[-3:]}


def _main(mod: str, argv: list[str]) -> dict:
    import importlib
    rc = importlib.import_module(mod).main(argv)
    if rc:
        raise RuntimeError(f"{mod} exited {rc}")
    return {"argv": argv}


MODAL_GPU_OVERRIDES: dict[str, tuple[str, str]] = {
    "fp8": ("ON", "restart vLLM with --quantization fp8; kept only if accuracy holds (-2 pts) and "
            "J/correct drops, with the eye and hearing checks re-run on the candidate"),
    "kv_batch": ("ON", "sweep client concurrency x --max-num-seqs on a 24-item slice, keep the lowest "
                 "MEASURED J/correct inside the quality gate"),
    "vllm_prefix": ("ON", "--enable-prefix-caching in the with-WattNot server, off in the stock server"),
    "prefix_cache_provider": ("ON", "our own vLLM; cached_tokens MEASURED from usage"),
    "measured_energy": ("ON", "NVML total-energy counter brackets each arm and each chunk of items"),
    "concurrency_batching": ("ON", "server-side batching via the kept --max-num-seqs"),
    "router": ("ON", "thinking off first, per-tier thinking budget on a failed check; one backend"),
    "joule_slo": ("ON", "Joule-SLO + p99 limit on MEASURED energy"),
    "power_caps": ("OFF", "Modal refuses NVML power-limit writes (probed each round, recorded)"),
    "clock_sidecar": ("OFF", "needs GPU clock writes, which Modal also refuses"),
    "own_gpu_serving": ("ON", "Glimmer 30B on our own vLLM, one A100 on Modal"),
    "moe": ("OFF", "Glimmer 30B is served here; MoE top-k needs Spark Open self-served (8x B200)"),
}


def gpu_feature_table(failed: dict | None = None) -> list[dict]:
    """FEATURES for the own-GPU Modal stage: GPU-side levers switched ON on top of the hosted set.
    `failed` = {feature_id: reason}: an ON feature that broke during the round reads FAILED."""
    rows = feature_table()
    for r in rows:
        if r["id"] in MODAL_GPU_OVERRIDES:
            r["status"], r["reason"] = MODAL_GPU_OVERRIDES[r["id"]]
        if r["id"] == "spend_guard":
            r["reason"] = "Modal $40 stage cap + container timeout, Meta soft cap, ledger"
        if failed and r["id"] in failed and r["status"] == "ON":
            r["status"], r["reason"] = "FAILED", str(failed[r["id"]])[:300]
    return rows


# ---- true end-to-end: the live post-answer stages, their routes, and worst-case Meta spend ----
# Each live feature stage records which service it ran on. In live mode a stage that falls back to a
# mock/offline path (or errors) is marked FAILED, never a silent green; `no_mocks` is True only when
# every one ran live.
FEATURE_STAGE_ROUTES: dict[str, str] = {
    "eye_check": "baseten:meta-models/muse-spark-open",
    "hearing_check": "meta-api:muse-voice-transcribe-1.0 + baseten:meta-models/muse-spark-open",
    "sam_hotspot": "meta-api:sam-3.1",
    "wonder_chart": "meta-api:muse-wonder-1.0",
    "spark_trace": "meta-api:muse-spark-1.3",
}
# SAM 3.1 and Muse Wonder are WattNot-only planning features: they have no plain without-WattNot arm.
WATTNOT_ONLY_STAGES = ("sam_hotspot", "wonder_chart")

# Fixed-size synthetic sets the live post-answer stages exercise, independent of the smoke item limit.
EYE_ITEMS = 10          # eye check: Baseten muse-spark-open calls (per arm)
HEARING_ITEMS = 20      # hearing: Meta voice transcribe + Baseten answer calls (per arm)
SAM_SCENES = 6          # SAM 3.1 hotspot scenes (Meta, per image)
WONDER_CALLS = 1        # Muse Wonder chart read (Meta)
SPARK_TRACE_CALLS = 4   # Spark 4-call trace (Meta)
GRADE_USD_PER_ANSWER = 0.0021575  # worst-case Muse Spark grade per answer
VOICE_USD_PER_CLIP = round(10.0 / 3600 * 0.18, 6)  # 10s worst-case clip at $0.18/h


def meta_feature_worst_case_usd(graded_answers: int = 0) -> tuple[float, str]:
    """Worst-case Meta API spend for one round's live post-answer stages (SAM, Wonder, Spark
    trace, voice transcribe, Spark grades). Eye and hearing answers are Baseten, not Meta, and
    are not counted here. Returns (usd, basis). Priced from the repo's Meta price table."""
    from wattnot.meta.ledger import PER_IMAGE_USD
    from wattnot.meta.ledger import cost_usd as _mcost
    sam = SAM_SCENES * PER_IMAGE_USD["sam-3.1"]
    wonder = _mcost("muse-wonder-1.0", 2000, 1200) * WONDER_CALLS
    trace = _mcost("muse-spark-1.3", 1200, 1500) * SPARK_TRACE_CALLS
    voice = 2 * HEARING_ITEMS * VOICE_USD_PER_CLIP          # both arms transcribe their clips
    grade = graded_answers * GRADE_USD_PER_ANSWER
    total = sam + wonder + trace + voice + grade
    basis = (f"SAM {SAM_SCENES}x${PER_IMAGE_USD['sam-3.1']:.4f}/img + Wonder {WONDER_CALLS} call + "
             f"Spark trace {SPARK_TRACE_CALLS} calls + voice {2 * HEARING_ITEMS} clips@${VOICE_USD_PER_CLIP} + "
             f"Spark grades {graded_answers}x${GRADE_USD_PER_ANSWER}")
    return round(total, 4), basis


def meta_spend_guard(worst_meta_usd: float, meta_cap_usd: float | None = None, *,
                     ledger_total: float | None = None) -> dict:
    """Check that the live Meta feature stages fit under the ledger soft cap and, when set, a
    per-run Meta cap. Pure: pass `ledger_total` in tests to avoid reading the real ledger."""
    from wattnot.meta.ledger import SOFT_USD
    if ledger_total is None:
        from wattnot.meta.ledger import SpendLedger
        ledger_total = float(SpendLedger().total())
    reasons = []
    if ledger_total + worst_meta_usd > SOFT_USD:
        reasons.append(f"ledger ${ledger_total:.2f} + worst ${worst_meta_usd:.2f} crosses the "
                       f"${SOFT_USD:.0f} soft cap")
    if meta_cap_usd and worst_meta_usd > meta_cap_usd:
        reasons.append(f"worst Meta ${worst_meta_usd:.2f} over the per-smoke cap ${meta_cap_usd:.2f}")
    return {"ok": not reasons, "why": "; ".join(reasons) or "within caps",
            "ledger_total_usd": round(ledger_total, 4), "worst_meta_usd": round(worst_meta_usd, 4),
            "soft_cap_usd": SOFT_USD, "meta_cap_usd": meta_cap_usd}


def _live_fallback_reason(sid: str, st: dict, d: Path) -> str | None:
    """In live mode, did this feature stage fall back to a mock/offline path or fail? The reason
    string if so, else None. Reads the stage's own output file for the mock/offline markers."""
    if st.get("status") in ("error", "bad"):
        return (st.get("error") or st.get("why") or "stage errored")
    if sid == "wonder_chart":
        try:
            w = json.loads((d / "wonder" / "wonder_chart.json").read_text())
        except (OSError, ValueError):
            return "no wonder_chart.json written (live Wonder did not run)"
        if w.get("source") == "fallback" or w.get("mode") == "fake":
            return f"Wonder source={w.get('source')} / mode={w.get('mode')} (offline fallback)"
    if sid == "sam_hotspot":
        try:
            s = json.loads((d / "sam" / "hotspot_set.json").read_text())
        except (OSError, ValueError):
            return "no hotspot_set.json written (live SAM did not run)"
        if s.get("mode") == "fake":
            return "SAM ran offline (mode=fake)"
    if sid == "spark_trace" and st.get("ok", 1) == 0:
        return "0 Spark trace calls succeeded"
    return None


def post_round(run_dir: Path, round_doc: dict, *, mode: str = "smoke", open_pr: bool = False,
               demo_repo: Path | None = None, log=None, cap=None, rates=None, do_pr: bool = True, table=None, change: dict | None = None,
               energy_label: str = "ESTIMATE", kind: str = "hosted-e2e", decision: dict | None = None,
               budget_s: float | None = None, clock=None, meta_cap_usd: float | None = None,
               prior_checks: dict | None = None) -> dict:
    """Run every post-answer feature stage; returns {stage: result} and writes features.json.

    `budget_s` caps the wall time the slow/risky stages (the paid Meta checks and the Muse Code
    PR) may collectively use. Each such stage checks the remaining budget before it starts and is
    recorded `skipped: not enough time` rather than starving the cheap local finalizers (receipt,
    EU report, label lint), which always run so the round still produces a signed receipt."""
    import time as _time
    fake = mode != "live"
    lab = "MOCK" if fake else "MEASURED"
    d = Path(run_dir) / "features"
    d.mkdir(parents=True, exist_ok=True)
    res: dict = {"mode": mode, "label": lab}
    fk = ["--fake"] if fake else []
    _clock = clock or _time.monotonic
    _t0 = _clock()

    def _slow_stage(results: dict, name: str, fn):
        """A slow/risky stage: skip (with the reason) when the time budget is already spent."""
        if budget_s is not None and (_clock() - _t0) >= budget_s:
            results[name] = {"status": "skipped",
                             "why": f"not enough time: {budget_s:.0f}s budget for the slow stages is spent"}
            return
        _stage(results, name, fn)

    from wattnot.e2e import live as L

    def emit(node, sub, summary, **kw):
        if log:
            log.emit("step.finished", node=node, sub=sub, summary=summary, **kw)
    client = L.baseten_client(cap, rates, log) if (not fake and cap is not None) else None
    if client:
        # Both arms run the eye and hearing checks so the report compares them: the without-WattNot
        # arm is a plain medium-effort call, the with-WattNot arm is effort low (WattNot steps up to
        # medium only on a failed check). SAM and Wonder are WattNot-only and stay single-arm below.
        client_plain = L.baseten_client(cap, rates, log, effort="medium")

        def eye_both():
            w = L.live_eye(d / "eye", client)
            o = L.live_eye(d / "eye_plain", client_plain)
            return {**w, "arms": {"with_wattnot": {**w, "effort": "low+step-up (WattNot)"},
                                  "without_wattnot": {**o, "effort": "medium (plain)"}}}

        def hearing_both():
            w = L.live_hearing(d / "voice", client, log)
            o = L.live_hearing(d / "voice_plain", client_plain, log)
            return {**w, "arms": {"with_wattnot": {**w, "effort": "low+step-up (WattNot)"},
                                  "without_wattnot": {**o, "effort": "medium (plain)"}}}
        _slow_stage(res, "eye_check", eye_both)
        _slow_stage(res, "hearing_check", hearing_both)
    elif prior_checks and all(Path(p).exists() for p in prior_checks.values()):
        # GPU stage: the eye/hearing blocks already ran LIVE on our own server (both arms, NVML).
        # Reuse those results; never rerun them offline (that printed '[fake]' / 'MOCK' lines).
        for name, p in prior_checks.items():
            doc = json.loads(Path(p).read_text())
            res[name] = {"status": "ok" if doc.get("passed") else "error", "passed": bool(doc.get("passed")),
                         "reason": doc.get("reason"), "mode": doc.get("mode"), "source": str(p),
                         "energy": doc.get("energy_arms"), "label": doc.get("answers_label", lab)}
    else:
        _slow_stage(res, "eye_check", lambda: _main("wattnot.vision.run", ["--out", str(d / "eye")] + fk))
        _slow_stage(res, "hearing_check", lambda: _main("wattnot.spokenanswer.run", ["--out", str(d / "voice")] + fk))
    emit("gates", "vision", f"eye check: {res['eye_check'].get('reason', res['eye_check']['status'])}",
         status="ok" if res["eye_check"].get("passed", res["eye_check"]["status"] == "ok") else "bad", label=lab,
         passed=bool(res["eye_check"].get("passed", res["eye_check"]["status"] == "ok")), model="muse-image eye check")
    emit("gates", "voice", f"hearing check: {res['hearing_check'].get('reason', res['hearing_check']['status'])}",
         status="ok" if res["hearing_check"].get("passed", res["hearing_check"]["status"] == "ok") else "bad", label=lab,
         passed=bool(res["hearing_check"].get("passed", res["hearing_check"]["status"] == "ok")), model="muse-voice-transcribe-1.0")
    def sam():
        import os
        old = os.environ.get("WATTNOT_META_MOCK")
        if fake:  # the offline mask is test-only; scoped to this stage and restored
            os.environ["WATTNOT_META_MOCK"] = "1"
        try:
            return _main("wattnot.sam.hotspot_set", ["--out", str(d / "sam")] + (fk or ["--live"]))
        finally:
            if old is None:
                os.environ.pop("WATTNOT_META_MOCK", None)
            else:
                os.environ["WATTNOT_META_MOCK"] = old
    _slow_stage(res, "sam_hotspot", sam)
    emit("sam", "audit", f"SAM 3.1 hotspot audit: {res['sam_hotspot']['status']}", model="sam-3.1",
         status=res["sam_hotspot"]["status"], label=lab)

    per = (round_doc.get("results") or {}).get("per_arm") or {}
    trials = []
    for arm, s in per.items():
        trials.append({"name": arm, "effort": "medium" if arm.startswith("without") else "low+step-up",
                       "accuracy": s.get("accuracy"), "energy_wh": (s.get("energy_wh") or {}).get("value"),
                       "label": "ESTIMATE"})
    (d / "trials.json").write_text(json.dumps({"trials": trials}, indent=1))
    _slow_stage(res, "wonder_chart", lambda: _main("wattnot.wonderchart.run",
                                              ["--sweep", str(d / "trials.json"), "--out", str(d / "wonder")]
                                              + (fk or ["--live"])))
    emit("agent", "study", f"Muse Wonder chart: {res['wonder_chart']['status']}", model="muse-wonder-1.0",
         status=res["wonder_chart"]["status"], label=lab)

    def trace():
        from wattnot.planner.trace import build
        ts = datetime.now(UTC).isoformat()
        ev = [{"type": "step.started", "step_id": f"s{i}", "ts": ts,
               "data": {"kind": "agent.call", "inputs": k}} for i, k in
              enumerate(("probe", "proposal", "gate", "receipt"))]
        t = build(ev)
        (d / "spark_trace.json").write_text(json.dumps(t, indent=1, default=str))
        return {"steps": 4}
    r_ = round_doc.get("results") or {}
    pa = r_.get("per_arm") or {}
    evidence = {"accuracy_without": r_.get("accuracy_without"), "accuracy_with": r_.get("accuracy_with"),
                "p99_without": ((pa.get("without_wattnot") or {}).get("item_latency_s") or {}).get("p99"),
                "p99_with": ((pa.get("with_wattnot") or {}).get("item_latency_s") or {}).get("p99"),
                "spark_agreement": (round_doc.get("spark_summary") or {}).get("agreement"),
                "j_per_correct_without": r_.get("j_per_correct_without"),
                "j_per_correct_with": r_.get("j_per_correct_with"),
                "eye_check": res["eye_check"].get("passed", None if fake else False),
                "hearing_check": res["hearing_check"].get("passed", None if fake else False)}
    if fake:
        _stage(res, "spark_trace", trace)
    else:
        _slow_stage(res, "spark_trace", lambda: L.spark_trace(d, evidence, log))

    held = ({"kind": "vllm_config", "change": change} if change else
            {"kind": "reasoning_effort", "change": {"reasoning_effort": "low", "step_up": "medium"}})
    change = held

    def approvals():
        from wattnot.approvals.risk import classify
        r = classify(change)
        risk = getattr(r, "tier", None) or getattr(r, "level", None) or str(r)[:120]
        out = {"held_change": held, "risk": risk, "reasons": getattr(r, "reasons", None), "evidence": evidence}
        if fake:
            out["review"] = {"decision": "recorded (no apply in smoke)", "auto_approve": False}
        else:
            out["review"] = L.review_change(change["change"], risk, evidence)
        if decision is not None:   # the runner verified the held change against its gates
            out.update(decision=decision.get("decision"))
            out["review"] = {**decision, "auto_approve": False}
        (d / "approval.json").write_text(json.dumps(out, indent=1, default=str))
        emit("proposals", "review", f"change {out['review']['decision']} after review (risk {risk})",
             status="ok" if out["review"]["decision"] == "approved" else "bad", label="MEASURED",
             verdict=out["review"]["decision"], result=f"risk {risk}")
        return out
    _stage(res, "approvals", approvals)

    def musecode():
        from wattnot.musecode.apply_pr import apply_pr
        repo = demo_repo or Path("/workspace/wattnot-demo-deploy")
        if not repo.exists():
            subprocess.run(["git", "clone", "-q", "https://github.com/ak9381/wattnot-demo-deploy", str(repo)],
                           check=True, timeout=120)
        sha = hashlib.sha256(json.dumps(round_doc, sort_keys=True, default=str).encode()).hexdigest()
        r = apply_pr(held["change"] if held["kind"] == "vllm_config" else {"max_num_seqs": 16}, repo, receipt_sha256=sha, out_dir=d / "pr",
                     mock=fake, open_pr=bool(open_pr and not fake))
        return {"result": str(r)[:300], "pr_opened": bool(open_pr and not fake)}
    if fake:
        _slow_stage(res, "musecode_pr", musecode)
    elif do_pr:
        sha = hashlib.sha256(json.dumps(round_doc, sort_keys=True, default=str).encode()).hexdigest()
        _slow_stage(res, "musecode_pr", lambda: L.musecode_pr(d / "pr", change["change"], sha, log=log, open_pr=open_pr,
                                                         review=(res.get("approvals") or {}).get("review") or {}))
    else:
        res["musecode_pr"] = {"status": "skipped", "why": "one PR per stage, opened after the last round"}

    def codemode():
        from wattnot.agentwaste.codemode import analyze
        p = Path(__file__).resolve().parents[3] / "data" / "agent-waste" / "runs.jsonl"
        runs = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
        out = analyze(runs)
        (d / "codemode.json").write_text(json.dumps(out, indent=1, default=str))
        return {"runs": len(runs), "label": "ESTIMATE"}
    _stage(res, "code_mode", codemode)

    def receipt():
        from wattnot.receipt.sign import sign_run_receipt
        rec = {"kind": kind, "approvals": (res.get("approvals") or {}),
               "musecode_pr": res.get("musecode_pr"), "feature_stages": {k: v.get("status") for k, v in res.items()
                                                                          if isinstance(v, dict)}, "round": round_doc.get("round"), "model": round_doc.get("model"),
               "features": (table or feature_table)(), "results": round_doc.get("results"),
               "spend": round_doc.get("spend"), "provider_usage": round_doc.get("provider_usage"),
               "server_config": round_doc.get("server_config"), "labels": {"energy": energy_label, "tokens": "MEASURED"},
               "integrity": {"signer": None}}
        body = {k: v for k, v in rec.items() if k != "integrity"}
        rec["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        try:
            from wattnot.receipt.sci import grid_from_env
            pa = (round_doc.get("results") or {}).get("per_arm") or {}
            grid = grid_from_env()
            g = float(grid.get("gco2_per_kwh") or grid.get("value") or 0) or None
            rec["sci"] = {"grid": grid, "label": "ESTIMATE",
                          "gco2_per_arm": {a: ((s.get("energy_wh") or {}).get("value") or 0) / 1000 * g
                                           for a, s in pa.items()} if g else None}
        except Exception as e:  # noqa: BLE001
            rec["sci"] = {"error": str(e)[:200]}
        from wattnot.receipt.canon import canonical_sha256, scrub_nonfinite
        rec = json.loads(json.dumps(rec, default=str))
        rec, _ = scrub_nonfinite(rec)
        rec["integrity"] = {"signer": None, "sha256": canonical_sha256(rec)}
        p = d / "receipt.json"
        p.write_text(json.dumps(rec, indent=2))
        b, signer = sign_run_receipt(p)
        emit("receipts", "sign", f"receipt signed ({signer})", status="ok", label="MEASURED")
        return {"path": str(p), "signer": str(signer), "bundle": str(b) if b else None}
    _stage(res, "receipt", receipt)

    def eu():
        from wattnot.report.eu import check_wording
        rec = json.loads((d / "receipt.json").read_text())
        html = ("<h1>EU 2024/1364 fields (hosted stage)</h1><pre>" +
                json.dumps({"round": rec.get("round"), "results": rec.get("results"), "sci": rec.get("sci")},
                           indent=1, default=str) + "</pre>")
        (d / "eu_report.html").write_text(html)
        emit("eu", "fields", "EU report written", status="ok", label="MEASURED")
        return {"wording_issues": check_wording(html)}
    _stage(res, "eu_report", eu)

    def lint():
        from wattnot.receipt.lint import lint as L
        return {"issues": L(json.loads((d / "receipt.json").read_text()))[:10]}
    _stage(res, "label_lint", lint)

    # ---- true end-to-end audit: in live mode every feature stage must have run live ----
    if not fake:
        no_mocks = True
        audit: dict = {}
        for sid, route in FEATURE_STAGE_ROUTES.items():
            st = res.get(sid)
            if not isinstance(st, dict):
                st = res[sid] = {"status": "error", "error": "stage missing"}
            st["route"] = route
            if st.get("status") == "skipped":
                st["live_label"] = "SKIPPED"
                no_mocks = False
            else:
                reason = _live_fallback_reason(sid, st, d)
                if reason:
                    st["status"], st["live_label"], st["fell_back"] = "bad", "FAILED", reason
                    no_mocks = False
                else:
                    st["live_label"] = "LIVE"
            if sid in WATTNOT_ONLY_STAGES:
                st["without_wattnot"] = "not applicable (WattNot feature)"
            audit[sid] = {"route": route, "label": st["live_label"], "status": st.get("status"),
                          "fell_back": st.get("fell_back"),
                          "without_wattnot": ("not applicable (WattNot feature)"
                                              if sid in WATTNOT_ONLY_STAGES else "compared in both arms")}
        res["no_mocks"] = no_mocks
        res["feature_audit"] = audit
        # Meta spend guard recorded beside the receipt (the runner's pre-flight is the gate).
        worst_meta, meta_basis = meta_feature_worst_case_usd(len(round_doc.get("answers") or []))
        res["meta_guard"] = {**meta_spend_guard(worst_meta, meta_cap_usd), "basis": meta_basis,
                             "label": "ESTIMATE"}
    else:
        res["no_mocks"] = False

    (d / "features.json").write_text(json.dumps({"table": (table or feature_table)(), "stages": res,
                                                 "no_mocks": res.get("no_mocks", False)}, indent=1, default=str))
    return res


def provider_usage(start_utc: str, end_utc: str, own: dict, *, model: str = "meta-models/muse-spark-open",
                   env_name: str = "BASETEN_METRICS_KEY", tries: int = 4, wait_s: float = 45.0,
                   opener=None) -> dict:
    """Baseten's own usage (tokens, cached, requests) for the round window and today's billing
    subtotal, read with free management calls, cross-checked against our per-call counts.

    `own` = {"requests", "input_tokens", "cached_input_tokens", "output_tokens", "usd"}.
    Usage arrives with a lag, so we retry until the request count stops being short.
    Never logs or returns the key. Energy is not available here (Model APIs expose no power).
    """
    import os
    import time
    import urllib.parse
    import urllib.request
    key = os.environ.get(env_name, "")
    if not key:
        return {"status": "skipped", "why": f"{env_name} not set"}
    op = opener or urllib.request.urlopen

    def get(path, params):
        u = "https://api.baseten.co" + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(u, headers={"Authorization": f"Api-Key {key}"})
        with op(req, timeout=60) as r:  # noqa: S310
            return json.loads(r.read())

    # minute buckets must cover whole minutes
    st = start_utc[:16] + ":00Z" if not start_utc.endswith("Z") or len(start_utc) > 17 else start_utc
    en_dt = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
    en = (en_dt.replace(second=0, microsecond=0)).strftime("%Y-%m-%dT%H:%M:00Z")
    en = datetime.fromtimestamp(datetime.fromisoformat(en.replace("Z", "+00:00")).timestamp() + 120, UTC
                                ).strftime("%Y-%m-%dT%H:%M:00Z")
    tot = {}
    for i in range(tries):
        try:
            doc = get("/v1/model_apis/usage", [("start_time", st), ("end_time", en), ("bucket_width", "1m"),
                                                ("limit", "1440"), ("group_by", "model")])
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": f"usage: {type(e).__name__}"}
        tot = {"requests": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
        for b in doc.get("items") or []:
            for r in b.get("results") or []:
                if r.get("model") == model:
                    tot["requests"] += int(r.get("request_count") or 0)
                    tot["input_tokens"] += int(r.get("input_tokens") or 0)
                    tot["cached_input_tokens"] += int(r.get("cached_input_tokens") or 0)
                    tot["output_tokens"] += int(r.get("output_tokens") or 0)
        if tot["requests"] >= int(own.get("requests") or 0) or i == tries - 1:
            break
        time.sleep(wait_s)
    bill = None
    try:
        day = en_dt.strftime("%Y-%m-%d")
        bd = get("/v1/billing/model_apis", [])
        bill = {"date_utc": day, "subtotal_usd": sum(float(r.get("subtotal") or 0) for it in bd.get("items") or []
                                                     if it.get("date") == day for r in it.get("results") or []),
                "note": "whole-day workspace subtotal, includes any other use that day"}
    except Exception as e:  # noqa: BLE001
        bill = {"error": f"billing: {type(e).__name__}"}

    def diff(k):
        a, b = tot.get(k), own.get(k)
        return {"baseten": a, "ours": b, "diff_pct": round(100 * (a - b) / b, 2) if b else None}
    checks = {k: diff(k) for k in ("requests", "input_tokens", "cached_input_tokens", "output_tokens")}
    worst = max((abs(v["diff_pct"]) for v in checks.values() if v["diff_pct"] is not None), default=None)
    return {"status": "ok", "label": "MEASURED", "source": "Baseten GET /v1/model_apis/usage + /v1/billing/model_apis",
            "window_utc": [st, en], "baseten": tot, "billing": bill, "crosscheck": checks,
            "worst_diff_pct": worst, "match": worst is not None and worst <= 2.0,
            "note": ("window is workspace-wide for this model: other use of the same key in the window "
                     "shows up here; energy is not exposed for Model APIs and stays ESTIMATE")}
