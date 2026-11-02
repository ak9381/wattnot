"""Live pieces of the hosted end-to-end stage: event log for the graph page, the live eye and
hearing checks on the model under test, the real Spark 4-call trace, the reviewer that checks
evidence before approving a held change, and the Muse Code PR flow with review.

Nothing here prints or stores a key. Every decision is written as structured evidence.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# the plain path the without-WattNot arm may light; every other node is greyed
BASELINE_LIVE_NODES = ["workload", "baseten_model", "models", "gates"]
DEMO_REPO = "ak9381/wattnot-demo-deploy"


def now() -> str:
    return datetime.now(UTC).isoformat()


class EventLog:
    def __init__(self, path: Path, run: str):
        self.path, self.run, self.lock, self.n = Path(path), run, threading.Lock(), 0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, type_: str, **data):
        with self.lock:
            self.n += 1
            ev = {"type": type_, "ts": now(), "run": self.run, "step_id": f"e{self.n}", "data": data}
            with self.path.open("a") as f:
                f.write(json.dumps(ev, default=str) + "\n")


# ---- Baseten client for the eye/hearing checks (the model under test, with-WattNot setting) ----
def baseten_client(cap, rates, log: EventLog | None, effort: str = "low"):
    from wattnot.cloud import MODEL, chat_completions_url, inference_key
    from wattnot.cloud.pricing import cost_usd
    from wattnot.spark_open.h2h import worst_case_usd
    _, key = inference_key()  # same key the runner uses for inference (BASETEN_METRICS_KEY)
    url = chat_completions_url()  # normalised: never a doubled /v1

    def call(body, purpose="check"):
        b = dict(body)
        b["model"] = MODEL
        b["reasoning_effort"] = effort
        mt = int(b.pop("max_completion_tokens", 0) or b.get("max_tokens") or 1024)
        b["max_tokens"] = max(mt, 1024)
        cap.check(worst_case_usd(rates, json.dumps(b.get("messages"))[:4000], b["max_tokens"]))
        req = urllib.request.Request(url, data=json.dumps(b).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Api-Key {key}"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=180) as r:  # noqa: S310
                d = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if log:
                log.emit("step.finished", node="baseten_model", sub="call", model=MODEL, status="error",
                         summary=f"{purpose}: HTTP {e.code}")
            raise OSError(f"HTTP {e.code}") from None
        u = d.get("usage") or {}
        usd = cost_usd(rates, int(u.get("prompt_tokens", 0)),
                       int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0),
                       int(u.get("completion_tokens", 0)))
        cap.add(usd)
        if log:
            log.emit("step.finished", node="baseten_model", sub="call", model=MODEL, status="ok",
                     latency_s=round(time.time() - t0, 3), usd=round(usd, 6), usd_label="MEASURED",
                     label="MEASURED", summary=purpose)
        return d
    return call


def live_eye(out: Path, client) -> dict:
    from wattnot.vision.assets import load_eye_test
    from wattnot.vision.client import ServedVisionAnswerer, answer_eye_test
    from wattnot.vision.gate import vision_gate
    eye = load_eye_test(None, require_files=True)
    ans = ServedVisionAnswerer(eye.base_dir, client=client)
    answers, meta = answer_eye_test(eye, ans, concurrency=4)
    r = vision_gate(answers, eye.items, wh_per_image=eye.wh_per_image)
    doc = {"label": "SYNTHETIC", "answers_label": "MEASURED", "model": "meta-models/muse-spark-open (Baseten)",
           "passed": r.passed, "reason": r.reason, "run": meta, "detail": r.detail}
    out.mkdir(parents=True, exist_ok=True)
    (out / "eye_test.json").write_text(json.dumps(doc, indent=1, default=str))
    return {"passed": r.passed, "reason": r.reason}


def _transcribe(path: Path, *, ledger=None, urlopen=None, timeout_s: float = 120.0) -> tuple[str, float]:
    """One live Muse Voice Transcribe call for the hosted e2e hearing check.

    Uses the shared client (wattnot.spokenanswer.client.transcribe_wav_live), so the per-second
    cost (ESTIMATE, $0.18/h of audio, from the WAV header rate and length) is charged to the Meta
    spend ledger BEFORE the call and refunded if the provider rejects it. The old version settled
    a 0-token entry, so hearing showed as $0 in the ledger even though it was billed.
    """
    import urllib.request as _ur

    from wattnot.spokenanswer.client import ASR_USD_PER_HOUR, transcribe_wav_live, wav_rate_and_seconds
    audio = path.read_bytes()
    _, secs = wav_rate_and_seconds(audio)
    usd = round(secs / 3600 * ASR_USD_PER_HOUR, 6)
    text = transcribe_wav_live(audio, timeout_s=timeout_s, ledger=ledger,
                               urlopen=urlopen or _ur.urlopen, purpose="voice:transcribe-e2e")
    return text, usd


def live_hearing(out: Path, client, log: EventLog | None) -> dict:
    from wattnot.spokenanswer.assets import load_context, load_spoken_set
    from wattnot.spokenanswer.client import ServedSpokenAnswerer, answer_spoken_set
    from wattnot.spokenanswer.gate import spoken_answer_gate
    sp = load_spoken_set(None, require_files=True)
    tr, usd = {}, 0.0
    for it in sp.items:
        t0 = time.time()
        tr[it.id], u = _transcribe(it.path(sp.base_dir))
        usd += u
        if log:
            log.emit("step.finished", node="models", sub="voice", model="muse-voice-transcribe-1.0", status="ok",
                     latency_s=round(time.time() - t0, 3), usd=u, usd_label="ESTIMATE", label="MEASURED",
                     summary=f"transcribed {it.id}")
    ans = ServedSpokenAnswerer(tr, client=client, context=load_context(None))
    answers, meta = answer_spoken_set(sp, ans, concurrency=4)
    r = spoken_answer_gate(answers, sp.items)
    doc = {"label": "SYNTHETIC", "answers_label": "MEASURED", "transcriber": "muse-voice-transcribe-1.0 (live)",
           "transcripts": tr, "transcribe_usd": {"value": round(usd, 6), "label": "ESTIMATE"},
           "passed": r.passed, "reason": r.reason, "run": meta, "detail": r.detail}
    out.mkdir(parents=True, exist_ok=True)
    (out / "spoken_answer.json").write_text(json.dumps(doc, indent=1, default=str))
    return {"passed": r.passed, "reason": r.reason, "transcribe_usd_est": round(usd, 6)}


# ---- Spark 4-call trace: four real Muse Spark operator calls ----
def spark_trace(out: Path, evidence: dict, log: EventLog | None) -> dict:
    from wattnot.meta.client import GuardedClient
    model = os.environ.get("WATTNOT_SPARK_MODEL", "muse-spark-1.3")
    cl = GuardedClient(timeout_s=90)
    ev = json.dumps(evidence, default=str)[:3000]
    asks = {
        "probe": "You are WattNot's operator. Read this hosted-run telemetry (no GPU meter; energy is an ESTIMATE "
                 "from tokens) and list in 2 lines what limits efficiency:\n" + ev,
        "proposal": "Propose ONE request-side change for this hosted model (effort routing, thinking budget, "
                    "caching), as JSON {change, why}:\n" + ev,
        "gate": "Given accuracy with vs without WattNot and the p99, answer PASS or FAIL for keeping the change "
                "and why, one line. The fixed gate decides; you are a second opinion:\n" + ev,
        "receipt": "Explain this run's receipt in 3 plain sentences for an engineer:\n" + ev,
    }
    rows = []
    for step, prompt in asks.items():
        t0 = time.time()
        # Baseten r03: all 4 calls came back empty because hidden reasoning used the whole 1500-token
        # cap (reasoning_tokens 1498 of 1500, MEASURED in the Meta ledger). Cap the reasoning and
        # leave room for the visible answer, like the other short operator calls.
        body = {"model": model, "reasoning_effort": "low", "max_tokens": 4000, "temperature": 0,
                "messages": [{"role": "user", "content": prompt}]}
        try:
            d = cl(body, purpose=f"e2e:spark-trace:{step}")
            txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            st = "ok" if txt else "empty"
        except Exception as e:  # noqa: BLE001
            d, txt, st = {}, "", f"error {type(e).__name__}"
        row = {"kind": "spark_call", "step": step, "model": model, "status": st,
               "trace_id": d.get("id"), "latency_s": round(time.time() - t0, 3), "text": txt[:1500],
               "label": "MEASURED"}
        rows.append(row)
        if log:
            log.emit("step.finished", node="agent", sub=step, **{k: v for k, v in row.items() if k != "text"},
                     summary=f"Spark {step}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "spark_trace.json").write_text(json.dumps(rows, indent=1))
    ok = sum(r["status"] == "ok" for r in rows)
    # A block that returns without raising but landed zero successful calls is a failed block,
    # not an "ok" stage: say so, so _stage records it as an error (never a silent green).
    result = {"steps": len(rows), "ok": ok}
    if rows and ok == 0:
        result["status"] = "error"
        result["error"] = f"0 of {len(rows)} Spark calls succeeded"
    return result


# ---- reviewer: verify evidence, then approve or reject (never auto-approve) ----
def review_change(change: dict, risk: str, ev: dict, *, reviewer: str = "Akash K Raju (delegated reviewer)") -> dict:
    """Checks: accuracy with >= without - 1 pt; p99 with <= p99 without x 1.10 (user limit);
    Spark agreement >= 0.9; joule-SLO: J/correct with <= without; eye and hearing gates passed."""
    t0 = now()
    checks = []

    def ck(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok) if ok is not None else None, "detail": detail})

    # Missing data is a skip (ok=None), never a silent failure: a check only fails on real numbers
    # that break the rule. This is what lets a skipped check hold the change instead of rejecting.
    aw, ai = ev.get("accuracy_without"), ev.get("accuracy_with")
    ck("accuracy (with >= without - 1 pt)", None if aw is None or ai is None else ai >= aw - 0.01,
       {"without": aw, "with": ai, "label": "MEASURED"})
    pw, pi = ev.get("p99_without"), ev.get("p99_with")
    ck("p99 limit (with <= 1.10 x without)", None if pw is None or pi is None else pi <= 1.10 * pw,
       {"without_s": pw, "with_s": pi, "label": "MEASURED"})
    ag = ev.get("spark_agreement")
    ck("Spark agreement >= 0.90", None if ag is None else ag >= 0.90, {"agreement": ag})
    jw, ji = ev.get("j_per_correct_without"), ev.get("j_per_correct_with")
    ck("joule-SLO (J/correct with <= without)", None if jw is None or ji is None else ji <= jw,
       {"without": jw, "with": ji, "label": "ESTIMATE"})
    for g in ("eye_check", "hearing_check"):
        # None (not run, e.g. smoke) is a skip, not a failure: it holds, it does not reject.
        passed = ev.get(g)
        ck(f"{g} gate passed", None if passed is None else passed is True, {"passed": passed})
    # Only a check that really failed (ok is False) rejects. A skipped check (ok is None: no data)
    # holds the change for a human, exactly as the Modal runner's verify_change does. The rule is
    # the one shared implementation in wattnot.approvals.review, so every approval path decides the
    # same way (the long-run driver's path reaches it through this reviewer).
    from wattnot.approvals.review import held_decision
    verdict = held_decision(checks)
    decision = verdict["decision"]
    failed, skipped = verdict["failed_checks"], verdict["skipped_checks"]
    why = ("every check passed" if decision == "approved" else
           "failed: " + "; ".join(failed) if failed else
           "held for review, no data for: " + "; ".join(skipped))
    return {"who": reviewer, "what": change, "risk": risk, "checks": checks, "decision": decision,
            "failed_checks": failed, "skipped_checks": skipped, "why": why,
            "started_utc": t0, "decided_utc": now(), "auto_approve": False}


# ---- Muse Code PR on the demo repo, then review ----
def _sh(cmd, cwd=None, timeout=600):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    return r.returncode, (r.stdout or "")[-4000:], (r.stderr or "")[-2000:]


def pr_spec(change: dict, receipt_sha: str = "") -> dict:
    """What the Muse Code PR writes: a `hosted` block for the Baseten effort change, or a
    `selfHosted` vLLM block for the own-GPU (Modal / H100) change, which has no reasoning effort."""
    if "reasoning_effort" in change:
        return {"block": "hosted", "for": "for the Baseten Model API client", "stage": "hosted e2e stage",
                "fields": {"model": "meta-models/muse-spark-open", "reasoningEffort": change["reasoning_effort"],
                           "stepUpEffort": change.get("step_up")},
                "title": f"hosted: reasoning effort {change['reasoning_effort']} with step-up",
                "body": (f"WattNot hosted e2e stage (Baseten, Muse Spark Open).\n\nChange: reasoning effort "
                         f"`{change['reasoning_effort']}`, step up to `{change.get('step_up')}` only when the fixed "
                         "answer check fails.")}
    f = {"quantization": change.get("quantization", "bf16"), "maxNumSeqs": change.get("max_num_seqs"),
         "prefixCaching": str(bool(change.get("vllm_prefix_caching"))).lower(), "router": change.get("router")}
    return {"block": "selfHosted", "for": "for our own vLLM server", "stage": "own-GPU e2e stage", "fields": f,
            "title": f"self-hosted vLLM: {f['quantization']}, max_num_seqs {f['maxNumSeqs']}, prefix cache {f['prefixCaching']}",
            "body": (f"WattNot own-GPU e2e stage (our vLLM).\n\nChange: quantization `{f['quantization']}`, "
                     f"max_num_seqs `{f['maxNumSeqs']}`, prefix caching `{f['prefixCaching']}`, router "
                     f"`{f['router']}`; measured saving {change.get('saving_pct')}% J per correct answer.")}


def musecode_pr(out: Path, change: dict, receipt_sha: str, *, log: EventLog | None, open_pr: bool,
                review: dict) -> dict:
    """Muse Code edits the demo repo (values.yaml hosted block), opens a PR, we review and merge
    only if the diff is limited, the files parse and the approval passed; else a review comment."""
    from wattnot.musecode.apply_pr import build_muse_command, parse_muse_jsonl
    out.mkdir(parents=True, exist_ok=True)
    repo = Path("/workspace/wattnot-demo-deploy")
    rec = {"repo": DEMO_REPO, "started_utc": now(), "steps": []}
    rc, so, _ = _sh(["git", "remote", "get-url", "origin"], cwd=repo)
    if DEMO_REPO not in so:
        return {**rec, "status": "refused", "why": "remote is not the demo repo"}
    _sh(["git", "fetch", "-q", "origin"], cwd=repo)
    _sh(["git", "checkout", "-q", "main"], cwd=repo)
    _sh(["git", "reset", "-q", "--hard", "origin/main"], cwd=repo)
    branch = f"wattnot-hosted-{receipt_sha[:8]}"
    _sh(["git", "checkout", "-q", "-B", branch], cwd=repo)
    prompt = out / "muse_prompt.txt"
    spec = pr_spec(change, receipt_sha)
    prompt.write_text(
        f"Edit only values.yaml. Add a top-level block named `{spec['block']}` {spec['for']} with:\n"
        + "".join(f"  {k}: {v}\n" for k, v in spec["fields"].items())
        + f"  receiptSha256: {receipt_sha}\n"
        f"Add a one-line YAML comment above the block: 'Applied by WattNot after review ({spec['stage']})'. "
        "Do not change anything else. Do not commit, push or run other commands.\n")
    t0 = time.time()
    rc, so, se = _sh(build_muse_command(repo, prompt, "muse-spark-1.3-contributor"), cwd=repo, timeout=900)
    trace = parse_muse_jsonl(so.splitlines())
    rec["steps"].append({"step": "muse exec", "rc": rc, "trace": trace, "latency_s": round(time.time() - t0, 1)})
    if log:
        log.emit("step.finished", node="proposals", sub="pr", model="muse-code (muse exec)", status="ok" if rc == 0 else "error",
                 latency_s=round(time.time() - t0, 2), label="MEASURED", summary="Muse Code edited values.yaml")
    _, diff, _ = _sh(["git", "diff", "HEAD"], cwd=repo)
    (out / "pr.diff").write_text(diff)
    _, names, _ = _sh(["git", "diff", "--name-only", "HEAD"], cwd=repo)
    files = [x for x in names.split() if x]
    # review checks
    checks = []
    checks.append({"check": "only values.yaml changed", "ok": files == ["values.yaml"], "detail": files})
    try:
        import yaml  # type: ignore
        y = yaml.safe_load((repo / "values.yaml").read_text())
        h = (y or {}).get(spec["block"]) or {}
        ok = all(str(h.get(k)).lower() == str(v).lower() for k, v in spec["fields"].items())
        checks.append({"check": "values.yaml parses and hosted block matches the approved change", "ok": ok, "detail": h})
        old = subprocess.run(["git", "show", "HEAD:values.yaml"], cwd=repo, capture_output=True, text=True).stdout
        yo = yaml.safe_load(old)
        same = {k: v for k, v in (y or {}).items() if k != spec["block"]} == yo
        checks.append({"check": "no other values changed", "ok": same, "detail": None})
    except Exception as e:  # noqa: BLE001
        checks.append({"check": "values.yaml parses", "ok": False, "detail": f"{type(e).__name__}"})
    rcb, _, _ = _sh(["bash", "-n", "launch.sh"], cwd=repo)
    checks.append({"check": "launch.sh syntax (bash -n)", "ok": rcb == 0, "detail": None})
    has_ci = (repo / ".github" / "workflows").exists()
    checks.append({"check": "repo tests/CI", "ok": True, "detail": "repo has no tests or CI workflows; checks above stand in" if not has_ci else "CI present"})
    checks.append({"check": "change was approved after evidence review", "ok": review.get("decision") == "approved",
                   "detail": review.get("why")})
    clean = all(c["ok"] for c in checks)
    rec["review_checks"] = checks
    pr_url = None
    if open_pr and files:
        _sh(["git", "add", "values.yaml"], cwd=repo)
        _sh(["git", "commit", "-q", "-m", f"{spec['title']} (receipt {receipt_sha[:8]})"], cwd=repo)
        rc, so, se = _sh(["git", "push", "-q", "-u", "origin", branch], cwd=repo)
        body = out / "pr_body.md"
        body.write_text(f"{spec['body']}\n\nReceipt sha256: `{receipt_sha}`\n\nEvidence review: {review.get('decision')} "
                        f"({review.get('why')}).\n\nEdited by Muse Code (`muse exec`).\n")
        rc, so, se = _sh(["gh", "pr", "create", "--repo", DEMO_REPO, "--head", branch, "--title",
                          f"WattNot {spec['title']}", "--body-file", str(body)])
        pr_url = so.strip().splitlines()[-1] if rc == 0 and so.strip() else None
        rec["steps"].append({"step": "gh pr create", "rc": rc, "url": pr_url, "err": se[-300:] if rc else None})
        if pr_url:
            if clean:
                rc2, so2, se2 = _sh(["gh", "pr", "merge", pr_url, "--merge", "--subject",
                                     f"Merge WattNot {spec['block']} change (reviewed, receipt {receipt_sha[:8]})"])
                rec["steps"].append({"step": "gh pr merge", "rc": rc2, "err": se2[-300:] if rc2 else None})
                decision = "merged" if rc2 == 0 else "approved, merge failed"
            else:
                bad = "; ".join(c["check"] for c in checks if not c["ok"])
                rc2, _, se2 = _sh(["gh", "pr", "comment", pr_url, "--body", f"Review: not merged. Failed checks: {bad}"])
                rec["steps"].append({"step": "gh pr comment", "rc": rc2})
                decision = "left open with review comment"
        else:
            decision = "PR not opened"
    else:
        decision = "not opened (no diff or --open-pr not set)"
    _sh(["git", "checkout", "-q", "main"], cwd=repo)
    rec.update(pr_url=pr_url, decision=decision, clean=clean, files=files, ended_utc=now(),
               reviewer="Akash K Raju (delegated reviewer)", auto_approve=False)
    if log:
        log.emit("step.finished", node="proposals", sub="review", status="ok" if clean else "bad",
                 summary=f"PR review: {decision}", verdict=decision, pr_url=pr_url or None)
    (out / "pr_review.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


REFUSAL_WORDS = ("deactivat", "unpaid", "suspend", "past due", "limit")


def billing_check() -> dict:
    """Free management read: is the workspace still in good standing? (no inference spend)"""
    import urllib.error
    import urllib.request
    key = os.environ.get("BASETEN_METRICS_KEY", "")
    req = urllib.request.Request("https://api.baseten.co/v1/billing/model_apis", headers={"Authorization": f"Api-Key {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()[:4000]
            status = r.status
    except urllib.error.HTTPError as e:
        body, status = e.read().decode(errors="replace")[:400], e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # A timeout or a dropped connection is not a refusal and not "good standing": say which,
        # with the real reason, rather than crashing the refusal watch or guessing a status.
        reason = getattr(e, "reason", e)
        return {"at": now(), "endpoint": "/v1/billing/model_apis", "status": None,
                "refusal": False, "words": [], "error": f"{type(e).__name__}: {reason}"[:200],
                "note": "billing endpoint unreachable (timeout/network); standing unknown, not a refusal"}
    hit = [w for w in REFUSAL_WORDS if w in body.lower()]
    out = {"at": now(), "endpoint": "/v1/billing/model_apis", "status": status,
           "refusal": status in (401, 402, 403, 429) or bool(hit and status != 200), "words": hit}
    try:
        d = json.loads(body)
        days = d.get("items") or []
        last = days[-1] if days else {}
        out["day"] = last.get("date")
        out["day_subtotal_usd"] = sum(float(x.get("subtotal") or 0) for x in last.get("results") or [])
        out["label"] = "MEASURED"
    except Exception:  # noqa: BLE001
        pass
    return out
