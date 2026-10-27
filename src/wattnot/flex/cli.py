"""`wattnot flex ...`: shed GPU draw on a grid event and sign a receipt.

    wattnot flex run --out flex-out            replay the demo events on the software model
    wattnot flex show flex-out/<id>.json       print a flex receipt and check its trace
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from wattnot.flex import signal as _signal
from wattnot.flex.controller import FlexConfig
from wattnot.flex.receipt import check_trace, write_flex_receipt
from wattnot.flex.run import SimBackend, run_flex


def add_parser(sub) -> None:
    f = sub.add_parser("flex", help="flex mode: cut GPU draw on a grid event (SIMULATED today)")
    s = f.add_subparsers(dest="flex_cmd")
    r = s.add_parser("run", help="replay grid events through the shed controller")
    r.add_argument("--signal", help="events file (default: the bundled SIMULATED demo events)")
    r.add_argument("--event", help="only this event id")
    r.add_argument("--out", default="flex-out", help="output directory (default %(default)s)")
    r.add_argument("--sign-key", help="Ed25519 test key: sign each receipt offline")
    r.add_argument("--backend", choices=("sim", "gpu"), default="sim",
                   help="sim = software model (SIMULATED); gpu = a real GPU through NVML "
                        "(needs --progress; for the H100 run)")
    r.add_argument("--progress", help="gpu backend: load generator progress file (JSON lines)")
    r.add_argument("--gpu", type=int, default=0, help="gpu backend: device index")
    r.add_argument("--nvml", help="gpu backend: path to libnvidia-ml (default: system)")
    r.add_argument("--seed", type=int, default=7, help="noise seed for the model")
    r.add_argument("--pre-s", type=int, default=30)
    r.add_argument("--post-s", type=int, default=90)
    r.add_argument("--sla-slack", type=float, default=0.5,
                   help="p99 budget = baseline p99 x (1 + slack) (default %(default)s)")
    r.add_argument("--created-at", help="fixed timestamp, for repeatable output")
    r.add_argument("--json", action="store_true", help="print one JSON summary")
    r.add_argument("--proposals-dir", help="gpu backend: where proposals and the audit trail "
                   "live (default $WATTNOT_PROPOSALS_DIR or ./proposals)")
    r.add_argument("--pre-approved", metavar="NAME",
                   help="gpu backend: a person approved these sheds in advance; NAME goes in "
                        "the audit trail. Without it each shed waits for "
                        "`wattnot proposals approve`")
    r.add_argument("--approval-wait-s", type=float, default=600.0,
                   help="gpu backend: wait this long for an approval, then stop (default 600)")
    ld = s.add_parser("load", help="load generator: writes the progress file for --backend gpu")
    ld.add_argument("--progress", required=True, help="progress file to write (JSON lines)")
    ld.add_argument("--url", help="OpenAI-compatible server, e.g. http://127.0.0.1:8000")
    ld.add_argument("--model", help="model name the server expects")
    ld.add_argument("--api-key-env", help="name of an environment variable holding an API key")
    ld.add_argument("--prompts", help="JSONL of {id, prompt, expect:[...]} (default: bundled)")
    ld.add_argument("--rps", type=float, default=8.0,
                    help="requests per second (default %(default)s)")
    ld.add_argument("--concurrency", type=int, default=32)
    ld.add_argument("--duration", type=float, default=0.0,
                    help="seconds to run; 0 = until Ctrl-C or SIGTERM (default)")
    ld.add_argument("--window", type=float, default=10.0,
                    help="seconds of history in each progress line (default %(default)s)")
    ld.add_argument("--max-tokens", type=int, default=64)
    ld.add_argument("--seed", type=int, default=7)
    ld.add_argument("--append", action="store_true", help="keep an existing progress file")
    ld.add_argument("--fake", action="store_true",
                    help="no server: made-up latency and answers (SIMULATED), for dry runs")
    w = s.add_parser("show", help="print a flex receipt and check its trace files")
    w.add_argument("receipt")
    w.add_argument("--json", action="store_true")


def _line(o: dict, label: str) -> str:
    d = o["delivered_pct"]
    t = o["seconds_to_target"]
    why = "" if o["status"] == "full" else f", stopped by {o['limit']}"
    when = f" in {t:g}s" if t is not None else ""
    p = o["p99_ms"]
    return (f"[{label}] {o['status'].upper()}: asked {o['target_pct']:g}%, delivered {d:g}%{when}"
            f"{why}; p99 {p['event_mean']:g} ms vs budget {p['sla']:g} ms; "
            f"ramp-back {o['ramp_back_s']}s")


def _estimate(ev, cfg, seed: int) -> dict:
    """A dry run on the software model: what the proposal can honestly say (SIMULATED)."""
    r = run_flex(SimBackend(seed=seed), ev, cfg).result
    p99 = r["p99_ms"]
    return {"delivered_pct": r["delivered_pct"], "quality_ok": r["limit"] != "quality",
            "p99_ok": r["limit"] != "p99",
            "p99_increase_pct": round(100 * (p99["event_mean"] / p99["before_mean"] - 1), 1)}


def _run_gpu(a, ev, cfg, sig, nvml, prev):
    """Real GPU: the shed is a proposal first. Nothing touches the cap until it is approved."""
    from wattnot.approvals import ApprovalGate, Approvals, CallbackApplier, NotApproved
    from wattnot.approvals import model as m
    from wattnot.approvals.appliers import nvml_appliers
    from wattnot.approvals.proposers import flex_shed_spec
    from wattnot.flex.hw import run_on_gpu

    if not a.progress:
        raise ValueError("--backend gpu needs --progress FILE")
    apps = Approvals(a.proposals_dir)
    apps.appliers.update(nvml_appliers(nvml))
    gate = ApprovalGate(apps, mode="pre_approved" if a.pre_approved else "ask",
                        name=a.pre_approved or "", wait_s=a.approval_wait_s)
    cap = apps.appliers[m.POWER_CAP]
    before = cap.get(a.gpu)
    spec = flex_shed_spec(ev.to_dict(), estimate=_estimate(ev, cfg, a.seed), gpu=a.gpu,
                          source={"signal": sig.source})
    # The event itself is run below, by this process. Applying only records the cap to restore.
    marker = CallbackApplier(lambda p: {"gpu": a.gpu, "restored_cap_w": before},
                             lambda t: cap.set(a.gpu, before))
    prop = gate.request(spec, applier=marker)

    def permit(_w: int) -> None:  # a revoked or expired approval stops the next cap change
        if apps.get(prop["id"])["status"] != m.APPLIED:
            raise NotApproved(f"{prop['id']} was revoked; stopping")

    run = run_on_gpu(nvml, ev, a.progress, gpu=a.gpu, cfg=cfg, sleep=time.sleep, permit=permit)
    return run, apps, prop


def _run(a) -> int:
    sig = _signal.load(a.signal)
    events = [e for e in sig.events if not a.event or e.id == a.event]
    if not events:
        raise ValueError(f"no event with id {a.event!r}")
    cfg = FlexConfig(pre_s=a.pre_s, post_s=a.post_s, sla_slack=a.sla_slack)
    nvml = None
    if a.backend == "gpu":
        from wattnot import probe

        nvml = probe.load_nvml(a.nvml) if a.nvml else probe.open_nvml()
    try:
        return _run_events(a, sig, events, cfg, nvml)
    finally:
        if nvml is not None:
            nvml.shutdown()


def _run_events(a, sig, events, cfg, nvml) -> int:
    out = []
    prev = None
    for ev in events:
        approval = apps = prop = None
        if a.backend == "gpu":
            from wattnot.approvals import NotApproved

            try:
                run, apps, prop = _run_gpu(a, ev, cfg, sig, nvml, prev)
            except NotApproved as e:
                print(f"not approved, nothing changed: {e}")
                return 50
            approval = apps.receipt_block(prop["id"])
        else:
            run = run_flex(SimBackend(seed=a.seed), ev, cfg)
        w = write_flex_receipt(run, sig, a.out, created_at=a.created_at, prev_sha256=prev,
                               sign_key=a.sign_key, approval=approval)
        r = w["receipt"]
        if apps is not None:
            apps.link_receipt(prop["id"], r)
        prev = r["integrity"]["sha256"]
        out.append({"event": ev.id, "path": w["path"], "grade": r["grade"],
                    "outcome": r["flex"]["outcome"], "sha256": prev})
        if not a.json:
            print(_line(r["flex"]["outcome"], r["flex"]["label"]))
            print(f"  {w['path']}  grade {r['grade']}  sha256 {prev[:16]}...")
    if a.json:
        print(json.dumps(out, indent=2))
    return 0


def _show(a) -> int:
    r = json.loads(Path(a.receipt).read_text(encoding="utf-8"))
    fx = r.get("flex")
    if not fx:
        raise ValueError("not a flex receipt")
    ok, problems = check_trace(a.receipt)
    if a.json:
        print(json.dumps({"label": fx["label"], "grade": r["grade"], "outcome": fx["outcome"],
                          "trace_ok": ok, "problems": problems}, indent=2))
    else:
        print(_line(fx["outcome"], fx["label"]))
        print(f"grade {r['grade']}; trace {'ok' if ok else 'PROBLEM: ' + '; '.join(problems)}")
        print(f"scope: {fx['scope']}")
    return 0 if ok else 1


def _load(a) -> int:
    import os
    import signal as _sig
    import threading

    from wattnot.flex import load as ld

    prompts = ld.load_prompts(a.prompts or ld.DEFAULT_PROMPTS)
    if a.fake:
        send = ld.fake_sender({p.prompt: p.expect for p in prompts}, seed=a.seed)
        label = "SIMULATED"
    else:
        if not a.url or not a.model:
            raise ValueError("give --url and --model, or --fake for a dry run")
        key = os.environ.get(a.api_key_env) if a.api_key_env else None
        send = ld.http_sender(a.url, a.model, api_key=key)
        label = "MEASURED"
    stop = threading.Event()
    for sg in (_sig.SIGINT, _sig.SIGTERM):
        _sig.signal(sg, lambda *_: stop.set())
    print(f"load: {a.rps:g} req/s, window {a.window:g}s -> {a.progress} ({label})", flush=True)
    s = ld.run_load(send, prompts, a.progress, rps=a.rps, concurrency=a.concurrency,
                    duration_s=a.duration, window_s=a.window, max_tokens=a.max_tokens,
                    seed=a.seed, label=label, append=a.append, stop=stop)
    print(json.dumps(s.to_dict(), indent=2))
    return 0


def dispatch(a) -> int:
    if a.flex_cmd == "load":
        return _load(a)
    if a.flex_cmd == "run":
        return _run(a)
    if a.flex_cmd == "show":
        return _show(a)
    raise ValueError("use: wattnot flex run|show|load")
