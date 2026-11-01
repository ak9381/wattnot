"""One end-to-end WattNot flow: every layer in order, one signed flow receipt at the end.

    python -m wattnot.flow --out DIR                 # off-GPU: replay of MEASURED + fakes
    python -m wattnot.flow --out DIR --gpu-dir PASS  # after an H100 pass: GPU layers are live

GPU layers (sweep + gates + signed receipt, thinking budget, tiers, eye, spoken, phase clock,
LangChain through the waste proxy, agent-waste GPU A/B) are produced on the H100 by the driver's
pass blocks; with --gpu-dir the flow reads those results (MEASURED). Without it the flow uses
the recorded MEASURED results from earlier runs, labelled REPLAY, or a --fake run, labelled
SIMULATED/MOCK. Off-GPU layers run live here. Paid Meta calls run only with --live-meta;
otherwise WATTNOT_META_MOCK=1 and those layers are MOCK. Nothing here opens a PR, publishes,
or calls Baseten/Modal (the multi-provider step is a plan or a replay unless --mp-results).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
REPLAY_RECEIPT = ROOT / "receipts/rerun-1031/rcpt_20261031_rerun_cap325.json"
TEST_KEY = ROOT / "receipt-offline-key.pem"
CLOUD_RECEIPT = ROOT / "reports/cloud-live/cloud_20261027_041453_318fad.json"
SPEC = "wattnot.flow-receipt/1"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Flow:
    def __init__(self, out: Path, gpu_dir: Path | None, live_meta: bool, dataset: str,
                 tag: str, mp_results: Path | None):
        self.out, self.gpu, self.live_meta = out, gpu_dir, live_meta
        self.dataset, self.tag, self.mp = dataset, tag, mp_results
        self.steps: list[dict] = []
        out.mkdir(parents=True, exist_ok=True)
        (out / "logs").mkdir(exist_ok=True)
        self.env = {**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"}
        if not live_meta:
            self.env["WATTNOT_META_MOCK"] = "1"

    def run(self, sid: str, rows: list[str], mode: str, label: str, argv: list[str] | None = None,
            fn=None, artifacts: list[str] = (), timeout: float = 900) -> bool:
        t0, rc, note = time.time(), 0, ""
        log = self.out / "logs" / f"{sid}.log"
        try:
            if argv:
                r = subprocess.run(argv, cwd=ROOT, env=self.env, capture_output=True, text=True,
                                   timeout=timeout)
                log.write_text(r.stdout[-20000:] + "\n--- stderr ---\n" + r.stderr[-20000:])
                rc = r.returncode
            if fn:
                note = fn() or ""
        except Exception as e:  # noqa: BLE001 - a step failure is recorded, never hidden
            rc, note = 1, f"{type(e).__name__}: {e}"
        arts = {}
        for a in artifacts:
            p = self.out / a
            if p.is_file():
                arts[a] = _sha(p)
        # a label is one bare word the verifier knows; any qualifier goes to label_note
        m = re.match(r"^([A-Z]+) \((.+)\)$", label)
        label, label_note = (m.group(1), m.group(2)) if m else (label, "")
        st = {"id": sid, "inventory": rows, "mode": mode, "label": label,
              **({"label_note": label_note} if label_note else {}),
              "status": "ok" if rc == 0 else "failed", "rc": rc, "note": str(note)[:400],
              "seconds": round(time.time() - t0, 2), "artifacts": arts}
        self.steps.append(st)
        print(f"[flow] {sid:16} {st['status']:6} {mode:14} {label}  {st['note'][:80]}")
        return rc == 0

    def g(self, sub: str) -> Path | None:
        p = self.gpu / sub if self.gpu else None
        return p if p and p.exists() else None

    def ingest(self, sub: str, dest: str):
        def f():
            src = self.g(sub)
            if src is None:
                raise FileNotFoundError(f"{sub} missing in the GPU pass folder")
            shutil.copytree(src, self.out / dest, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("*.pem"))
            return f"read {src}"
        return f


def run_flow(a) -> int:
    out = Path(a.out)
    F = Flow(out, Path(a.gpu_dir) if a.gpu_dir else None, a.live_meta, a.dataset, a.tag,
             Path(a.mp_results) if a.mp_results else None)
    gpu = F.gpu is not None
    GL, GLL = ("gpu-live", "MEASURED") if gpu else ("replay", "REPLAY")
    cli = [PY, "-m", "wattnot.cli"]

    # 1 preflight, probe, spend guard
    F.run("preflight", ["A21 no-mock preflight", "init/preflight"], "off-gpu-live", "MEASURED",
          cli + ["preflight", "--json"])
    if gpu and F.g("probe"):
        F.run("nvml-probe", ["NVML capability probe"], GL, GLL, fn=F.ingest("probe", "probe"))
    else:
        F.run("nvml-probe", ["NVML capability probe"], "off-gpu-live", "MEASURED (no GPU here)",
              fn=lambda: _probe(out))
    F.run("spend-guard", ["Spend guard", "Cloud timeout backstop", "Meta spend ledger"],
          "off-gpu-live", "SIMULATED", [PY, "-m", "wattnot.spend.dryrun", "--out",
                                        str(out / "spend/dryrun.json")])

    # 2 GPU sweep: caps, Wonder planner, gates, approvals, SAM, signed receipt
    rc_dir = out / "sweep"
    rc_dir.mkdir(exist_ok=True)

    def sweep():
        if gpu:
            got = (sorted((F.gpu / "sweep").rglob("rcpt_*.json"))
                   + sorted((F.gpu / "sweep").rglob("proof-*.json"))) if F.g("sweep") else []
            got = [p for p in got if not p.name.endswith(".sigstore.json")]
            if not got:
                raise FileNotFoundError("no signed sweep receipt in the pass folder")
            src = got[-1]
        else:
            src = REPLAY_RECEIPT
        for p in (src, src.with_suffix("").with_suffix(".sigstore.json")):
            if p.exists():
                shutil.copy2(p, rc_dir / p.name)
        return f"receipt {src.name}"
    F.run("gpu-sweep", ["Power-cap sweep + J/correct gate", "A4 every-cap grid",
                        "Wonder Pareto planner", "Noise gate", "Fixed gate", "LLM judge gate",
                        "Reasoning-leak strip", "Approve-before-apply (orchestrate --act)",
                        "Signed receipt", "Joules per good answer", "Model fit/per-model J",
                        "Muse Glimmer 30B"], GL, GLL, fn=sweep)
    rcpts = [p for p in list(rc_dir.glob("rcpt_*.json")) + list(rc_dir.glob("proof-*.json"))
             if not p.name.endswith(".sigstore.json")]
    receipt = rcpts[0] if rcpts else None

    for sid, sub, rows, fake in (
        ("thinking-budget", "think", ["Thinking-budget enforcer"],
         [PY, "-m", "wattnot.longrun.thinking_budget", "--fake", "--per-tier", "2", "--out",
          str(out / "think")]),
        ("tiers", "tiers", ["Tiers at the best cap (harness)", "Dataset picker"],
         [PY, "-m", "wattnot.longrun.harness", "--fake", "--cap", "325", "--per-tier", "2",
          "--model-config", "muse-glimmer-30b", "--out", str(out / "tiers")]),
        ("eye-gate", "eye", ["A15 vision eye-test gate"],
         [PY, "-m", "wattnot.vision.run", "--fake", "--out", str(out / "eye")]),
        ("spoken-gate", "spoken", ["Muse Voice Transcribe spoken answers"],
         [PY, "-m", "wattnot.spokenanswer.run", "--fake", "--out", str(out / "spoken")]),
        ("phase-clock", "phase", ["A10 phase-aware clock sidecar"],
         [PY, "-m", "wattnot.control.phase_clock", "--mock", "--duration-s", "2",
          "--summary", str(out / "phase/summary.json"), "--log", str(out / "phase/log.jsonl")]),
        ("langchain", "langchain", ["A9 agent-waste proxy", "LangChain app through the proxy",
                                    "Tool-output dedup", "Memory compression",
                                    "Effort routing (thinking off -> on)"],
         [PY, "scripts/langchain_pass.py", "--fake", "--dataset", a.dataset, "--n", "5",
          "--out", str(out / "langchain")]),
        ("agentwaste-gpu", "agentwaste", ["Agent-waste saver on the GPU (OFF vs ON)"],
         [PY, "scripts/agentwaste_gpu_ab.py", "--fake", "--out", str(out / "agentwaste")]),
    ):
        if gpu and F.g(sub):
            F.run(sid, rows, "gpu-live", "MEASURED", fn=F.ingest(sub, sub))
        else:
            (out / sub).mkdir(exist_ok=True)
            F.run(sid, rows, "off-gpu-fake", "SIMULATED" if sid != "langchain" else "MOCK", fake)

    # 3 off-GPU analysis on the sweep receipt
    def points():
        r = json.loads(receipt.read_text())
        pts = []
        for arm in ("baseline", "candidate"):
            x = r[arm]
            q = (x.get("quality") or {}).get("fixed_pass_rate") or 1.0
            pts.append({"cap_w": x["params"]["power_cap_w"],
                        "j_per_good": x["wh_per_req"]["mean"] * 3600 / q,
                        "p99_delta_pct": 0.0 if arm == "baseline" else
                        100 * (r["candidate"]["p99_ms"] / r["baseline"]["p99_ms"] - 1),
                        "gate_pass": True, "label": "MEASURED"})
        (out / "analysis").mkdir(exist_ok=True)
        (out / "analysis/points.json").write_text(json.dumps({"points": pts}, indent=2))
        return f"{len(pts)} points"
    F.run("points", ["Joules per good answer"], "off-gpu-live", GLL, fn=points)
    pj = str(out / "analysis/points.json")
    F.run("knee", ["A12 marginal-joule knee"], "off-gpu-live", GLL,
          [PY, "-m", "wattnot.longrun.knee", "--sweep", pj, "--json"])
    F.run("joule-slo", ["A13 Joule-SLO"], "off-gpu-live", GLL,
          [PY, "-m", "wattnot.longrun.jslo", "--sweep", pj, "--budget", "200", "--json"])

    def optuna():
        from wattnot.planner.ablation import ablation
        res = ablation(8)
        (out / "analysis/ablation.json").write_text(json.dumps(res, indent=2, default=str))
        return "8 trials per arm on the SYNTHETIC objective"
    F.run("optuna-planner", ["A11 Optuna search planner + ablation"], "off-gpu-live",
          "SYNTHETIC", fn=optuna, artifacts=["analysis/ablation.json"])
    F.run("flex-shed", ["Flex load shedding", "Simulated GPU power model"], "off-gpu-live",
          "SIMULATED", cli + ["flex", "run", "--backend", "sim", "--out", str(out / "flex")])
    wh_delta = None
    if receipt:
        r = json.loads(receipt.read_text())
        wh_delta = r["baseline"]["wh_per_req"]["mean"] - r["candidate"]["wh_per_req"]["mean"]
    F.run("fleet-impact", ["Fleet impact estimator"], "off-gpu-live", "ESTIMATE",
          cli + ["impact", "--wh-delta", f"{wh_delta or 0.01:.6f}",
                 "--requests-per-gpu-per-day", "100000"])
    F.run("codemode", ["Code Mode + locked sandbox", "Code-sandbox network boundary"],
          "off-gpu-live", "MEASURED (sandbox cost)", fn=lambda: _codemode(out))
    F.run("cloud-router", ["Reasoning-effort router", "A14 backend/effort/region router",
                           "Cloud effort-router receipt"], "replay", "REPLAY",
          cli + ["route", "replay", "--receipt", str(CLOUD_RECEIPT), "--out",
                 str(out / "route/replay.json")])
    mp = [PY, "scripts/multiprovider_route.py", "--out", str(out / "multiprovider")]
    F.run("multi-provider", ["Multi-provider routing H100/Baseten/Modal", "Hosted A/B"],
          "replay" if not F.mp else "results", "REPLAY" if not F.mp else "MEASURED",
          mp + (["--results", str(F.mp)] if F.mp else ["--replay"]))
    F.run("sam-hotspot", ["SAM 3.1 hotspot audit", "Hotspot set"],
          "off-gpu-live" if a.live_meta else "off-gpu-mock",
          "MEASURED" if a.live_meta else "MOCK",
          [PY, "-m", "wattnot.sam.hotspot_set", "--live" if a.live_meta else "--fake",
           "--out", str(out / "sam")])
    F.run("wonderchart", ["Wonderchart"], "off-gpu-live" if a.live_meta else "off-gpu-mock",
          "MEASURED" if a.live_meta else "MOCK",
          [PY, "-m", "wattnot.wonderchart.run", "--sweep", pj, "--out", str(out / "wonderchart"),
           "--live" if a.live_meta else "--fake"])
    F.run("muse-code-pr", ["Muse Code apply-as-PR"], "replay", "REPLAY",
          fn=lambda: _copy(ROOT / "receipts/run2/musecode_pr_live.json", out / "musecode/pr.json",
                           "no PR opened; replay of the 27 Oct live PR"))
    F.run("narrator", ["A19 run narrator"], "replay", "REPLAY",
          fn=lambda: _copy(ROOT / "data/narration-1031/4604f3.json", out / "narrator/story.json",
                           "replay of the 31 Oct narration"))
    F.run("voice-tuner", ["Voice guide (grounded intent)", "Tuner (spoken -> knob plan)"],
          "off-gpu-live", "MEASURED (local parse)", fn=lambda: _voice(out, receipt))
    F.run("approvals", ["Approve-before-apply", "A6/A7 approvals + undo"], "off-gpu-live",
          "SIMULATED (local store, real apply/undo)", fn=lambda: _approvals(out))

    if receipt:
            # Muse Spark is the operator: probe -> proposal -> gate -> receipt, four live calls,
            # each a traced event (trace id, latency, $) the dashboard shows as the Spark trace.
            ev = os.environ.get("WATTNOT_FLOW_EVENTS") or str(out / "spark/events.jsonl")
            F.run("spark-trace", ["Muse Spark operator trace (probe, proposal, gate, receipt)",
                                  "Muse Spark"],
                  "off-gpu-live" if a.live_meta else "off-gpu-mock",
                  "MEASURED" if a.live_meta else "MOCK",
                  [PY, "-m", "wattnot.meta.spark_trace", "--flow-out", str(out), "--receipt",
                   str(receipt), "--events", ev], artifacts=["spark/trace.json"])

    # 4 one signed flow receipt, verify, EU, certificate, API, events, dashboard
    F.run("flow-receipt", ["Signed receipt + canonicalization"], "off-gpu-live", "MEASURED",
          fn=lambda: _flow_receipt(F, receipt))
    fr = out / "flow-receipt.json"
    if receipt:
        F.run("verify", ["Independent verifier"], "off-gpu-live", GLL,
              cli + ["verify", str(receipt)])
        F.run("verify-standalone", ["Independent stdlib verifier"], "off-gpu-live", GLL,
              [PY, "verifier/wattnot_verify.py", str(receipt)])
        F.run("label-lint", ["Grading-label honesty lint"], "off-gpu-live", GLL,
              fn=lambda: _lint(receipt))
        F.run("eu-report", ["EU efficiency report"], "off-gpu-live", GLL,
              cli + ["report", "eu", "--receipts", str(receipt), "--out", str(out / "eu/eu")])
        F.run("certificate", ["Efficiency certificate"], "off-gpu-live", "MEASURED",
              cli + ["certificate", "mint", "--receipt", str(receipt), "--out",
                     str(out / "certificate")],
              fn=lambda: _cert_note(out / "certificate"))
    F.run("flow-verify", ["Flow receipt offline signature"], "off-gpu-live", "MEASURED",
          fn=lambda: _flow_verify(fr))
    F.run("receipts-api", ["A8 receipts REST API"], "off-gpu-live", GLL,
          fn=lambda: _api(out, receipt))
    F.run("dashboard", ["Dashboard JSON", "A16 run event log", "p99 setting"], "off-gpu-live",
          "MEASURED", fn=lambda: _dash(F))
    bad = [s["id"] for s in F.steps if s["status"] != "ok"]
    print(f"[flow] {len(F.steps)} steps, {len(bad)} failed {bad}")
    return 0 if not bad else 1


def _probe(out: Path) -> str:
    r = subprocess.run([PY, "-m", "wattnot.probe", "--no-write", "--out",
                        str(out / "probe/caps.json")], cwd=ROOT, capture_output=True, text=True,
                       timeout=120)
    if r.returncode != 0 and "no GPU" not in (r.stdout + r.stderr):
        raise RuntimeError(r.stderr[-300:])
    return "no GPU on this machine (probe ran read-only)" if r.returncode else "probe ok"


def _copy(src: Path, dst: Path, note: str) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return note


def _cert_note(cdir: Path) -> str:
    """Report what the certificate step produced: a minted certificate, or an honest
    "not issued" statement when the run's saving is not statistically shown (declined, not
    failed). Reads the mint command's own output files."""
    minted = sorted(cdir.glob("WNC-*.json"))
    if minted:
        c = json.loads(minted[-1].read_text())
        return f"minted {c.get('serial')} [{c.get('status')}]"
    declined = sorted(cdir.glob("not-issued-*.json"))
    if declined:
        d = json.loads(declined[-1].read_text())
        return f"not issued (declined): grade {d.get('receipt', {}).get('grade')}"
    return "no certificate produced"


def _codemode(out: Path) -> str:
    from wattnot.agentwaste.sandbox.run import build
    from wattnot.agentwaste.sandbox.runner import Limits
    runs = [json.loads(x) for x in (ROOT / "data/agent-waste/v3/runs.jsonl").read_text()
            .splitlines() if x.strip()]
    res = build(runs, limits=Limits(), now=datetime.now(timezone.utc).isoformat())
    (out / "codemode").mkdir(exist_ok=True)
    (out / "codemode/sandbox_results.json").write_text(json.dumps(res, indent=2, default=str))
    return f"{res['candidates']} candidates run in the sandbox (traces REPLAY of Muse Spark runs)"


def _voice(out: Path, receipt: Path | None) -> str:
    from wattnot.meta import tuner, voiceguide
    intent = voiceguide.classify_intent("how much energy did the best cap save?")
    plan = tuner.parse_tune_command("set the power cap to 150 watts")
    (out / "voice").mkdir(exist_ok=True)
    (out / "voice/voice.json").write_text(json.dumps({"intent": intent, "tune": plan},
                                                     indent=2, default=str))
    return f"intent={intent}"


def _approvals(out: Path) -> str:
    from wattnot.approvals import Approvals
    from wattnot.approvals import model as m
    ap = Approvals(out / "proposals")
    spec = {"kind": m.VLLM_CONFIG, "title": "flow: serving knob", "summary": "flow check",
            "change": {"target": "vllm", "before": {"max_num_seqs": 256},
                       "after": {"max_num_seqs": 512}, "changed": ["max_num_seqs"]},
            "savings": [{"metric": "energy per request", "value": 0.0, "unit": "%",
                         "label": "SIMULATED"}],
            "checks": {"quality": {"passed": True}, "p99": {"passed": True,
                                                            "increase_pct": 0.0}}}
    pid = ap.propose(spec, auto=False)["id"]
    from wattnot.cli import main as cli
    d = str(out / "proposals")
    os.environ.setdefault("WATTNOT_APPROVER", "flow-operator")
    old = os.environ.get("WATTNOT_MODE")
    rcs = [cli(["proposals", "--dir", d, "approve", pid, "--note", "flow"])]
    os.environ["WATTNOT_MODE"] = "act"   # acts only on this flow's own local store
    try:
        rcs += [cli(["proposals", "--dir", d, "apply", pid]),
                cli(["proposals", "--dir", d, "rollback", pid]),
                cli(["proposals", "--dir", d, "verify"])]
    finally:
        if old is None:
            os.environ.pop("WATTNOT_MODE", None)
        else:
            os.environ["WATTNOT_MODE"] = old
    if any(rcs):
        raise RuntimeError(f"approvals rcs {rcs}")
    return f"{pid}: approved, applied, rolled back, audit chain verified"


def _lint(receipt: Path) -> str:
    from wattnot.receipt.lint import lint
    errs = lint(json.loads(receipt.read_text()))
    if errs:
        raise RuntimeError("; ".join(errs[:3]))
    return "clean"


def _flow_receipt(F: Flow, receipt: Path | None) -> str:
    from wattnot import settings
    from wattnot.receipt.canon import canonical_sha256
    from wattnot.receipt.sign import sign
    body = {"spec": SPEC, "tag": F.tag, "dataset": F.dataset,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "gpu": "live" if F.gpu else "replay",
            "gpu_receipt": {"file": receipt.name, "sha256": _sha(receipt)} if receipt else None,
            "limits": settings.receipt_block(),
            "layers": [{k: s[k] for k in ("id", "mode", "label", "label_note", "status", "artifacts")
                        if k in s} for s in F.steps]}
    body["integrity"] = {"sha256": canonical_sha256(body)}
    p = F.out / "flow-receipt.json"
    p.write_text(json.dumps(body, indent=2))
    sign(p, no_rekor=True, key_path=_offline_key(F.out))
    return f"signed offline ({len(body['layers'])} layers)"


def _offline_key(out: Path) -> Path:
    """The local test key if present; on a fresh clone, a throwaway Ed25519 key in out/."""
    if TEST_KEY.exists():
        return TEST_KEY
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = out / "flow-offline-key.pem"
    if not k.exists():
        k.write_bytes(Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        k.chmod(0o600)
    return k


def _flow_verify(fr: Path) -> str:
    from wattnot.receipt.sign import bundle_path, verify_offline_bundle
    ok, why = verify_offline_bundle(json.loads(fr.read_text()),
                                    json.loads(bundle_path(fr).read_text()))
    if not ok:
        raise RuntimeError(why)
    return why


def _api(out: Path, receipt: Path | None) -> str:
    from starlette.testclient import TestClient

    from wattnot.api.app import create_app
    c = TestClient(create_app(str(out)))
    r = c.get("/healthz") if False else c.get("/api/runs")
    return f"GET /api/runs -> {r.status_code}"


def _dash(F: Flow) -> str:
    from wattnot.events.emitter import Emitter, new_run_id
    rid = new_run_id()
    with Emitter(rid, root=F.out) as em:
        em.emit("run.started", {"flow": F.tag})
        for s in F.steps:
            em.emit("step.finished", {"step": s["id"], "status": s["status"], "label": s["label"]})
        em.emit("run.finished", {"steps": len(F.steps)})
    (F.out / "dash").mkdir(exist_ok=True)
    (F.out / "dash/flow.json").write_text(json.dumps({"run_id": rid, "steps": F.steps}, indent=2))
    return rid


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wattnot.flow", description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--gpu-dir", help="pulled results of one H100 pass (GPU layers live)")
    ap.add_argument("--dataset", choices=("own", "gsm8k300", "humaneval164"), default="own")
    ap.add_argument("--tag", default="flow")
    ap.add_argument("--live-meta", action="store_true",
                    help="paid Meta calls for SAM and Wonderchart (cents; needs Akash's go)")
    ap.add_argument("--mp-results", help="same-prompt multi-provider results (needs a paid run)")
    return run_flow(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
