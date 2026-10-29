"""Run one cloud tuning pass end to end and write the receipt.

    load workload -> ask the agent for an order -> route each class -> receipt

Modes:

* live: calls Baseten with the key from BASETEN_API_KEY.
* replay: reads a fixture file, makes no network call. A synthetic fixture
  (the bundled demo) is SIMULATED in every label. A fixture recorded from a live
  run (`--record`) is MEASURED, because its numbers came from real calls.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from wattnot.cloud import MODEL
from wattnot.cloud import receipt as rc
from wattnot.cloud import tune as tn
from wattnot.cloud.pricing import Rates
from wattnot.cloud.transport import (
    FixtureTransport,
    LiveTransport,
    Recorder,
    Request,
    Transport,
)
from wattnot.gates.judge import judge as _judge
from wattnot.runner.workload import load_workload

WORKLOAD = Path(__file__).resolve().parents[3] / "workloads" / "cloud-effort-v1.jsonl"
DEMO_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "cloud" / "demo-fixture.json"
DEMO_METER = Path(__file__).resolve().parents[3] / "data" / "cloud" / "demo-meter.json"


@dataclass
class Options:
    workload: Path = WORKLOAD
    classes: Sequence[str] | None = None
    repeats: int = 2
    efforts: Sequence[str] | None = None  # restrict the levels tried (reference is always run)
    max_drop_pts: float = 1.0
    p99_slack: float = 0.10
    budget_usd: float | None = None
    planner: bool = True
    judge: bool = False
    affinity: bool = True
    rates: Rates = Rates()


def open_transport(*, live: bool, fixture: str | Path | None,
                   record: str | Path | None = None,
                   provider: str = "baseten") -> tuple[Transport, str, Recorder | None]:
    """Returns (transport, mode, recorder). mode: live | replay-synthetic | replay-recorded."""

    if live:
        if provider == "modal":
            from wattnot.cloud.transport import ModalLiveTransport

            t: Transport = ModalLiveTransport()
        else:
            t = LiveTransport()
        rec = Recorder(t) if record else None
        return (rec or t), "live", rec
    if fixture is None:
        raise ValueError("replay needs a fixture (use --demo for the bundled one)")
    ft = FixtureTransport.load(fixture)
    return ft, ("replay-synthetic" if ft.source == "synthetic" else "replay-recorded"), None


def host_info(transport) -> dict | None:
    """Provider, model, endpoint and key env of a live transport (None for a replay)."""
    inner = getattr(transport, "inner", transport)
    if not hasattr(inner, "base_url"):
        return None
    return {"provider": getattr(inner, "provider", "baseten"), "model": inner.model,
            "endpoint": inner.base_url, "key_env": getattr(inner, "key_env", None)}


def _judge_gate(transport: Transport, runner: tn.Runner) -> Callable:
    def gate(name, effort, ref_out, out, items):
        ids = [k for k in out if k in ref_out]
        by_id = {f"{i.id}#{r}": i for i in items for r in range(32)}
        rows = [{"id": k, "question": by_id[k].prompt} for k in ids]

        def send(body: dict) -> dict:
            req = Request(f"judge|{name}|{effort}", body)
            reply = transport(req)
            runner.calls.append(tn.parse_reply(reply, req=req, task_id=f"judge-{name}",
                                               cls="judge", effort="low", repeat=0,
                                               rates=runner.rates))
            runner.spent += runner.calls[-1].usd
            if reply.status != 200:
                raise RuntimeError(f"judge HTTP {reply.status}")
            return reply.body

        return _judge(ref_out, out, rows, transport=send, model=MODEL)
    return gate


def run(opts: Options, transport: Transport, mode: str, *, out_dir: str | Path,
        now: float | None = None, api_key_prefix: str | None = None,
        sign_key: str | Path | None = None) -> tuple[Path, dict]:
    wl = load_workload(opts.workload)
    items = list(wl.items)
    runner = tn.Runner(transport, opts.rates, budget_usd=opts.budget_usd,
                       affinity=opts.affinity)
    by_class: dict[str, list] = {}
    for i in items:
        by_class.setdefault(i.meta.get("class", "default"), []).append(i)
    if opts.classes:
        by_class = {c: by_class[c] for c in opts.classes if c in by_class}
    proposal = tn.propose(by_class, transport if opts.planner else None, opts.rates)
    if opts.efforts:
        keep = set(opts.efforts)
        proposal["orders"] = {c: [e for e in o if e in keep] for c, o in
                              proposal["orders"].items()}
    result = tn.tune(items, runner, proposal=proposal, classes=opts.classes,
                     repeats=opts.repeats, max_drop_pts=opts.max_drop_pts,
                     p99_slack=opts.p99_slack,
                     judge=_judge_gate(transport, runner) if opts.judge else None)
    rows = [c.row() for c in result["calls"]]
    sent = [c.sent_at for c in result["calls"] if c.sent_at]
    window = None
    if sent:
        end = max(c.sent_at + c.latency_s for c in result["calls"] if c.sent_at)
        window = {"start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(min(sent))),
                  "end": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end))}
    text = opts.workload.read_bytes()
    workload = {"name": wl.name, "items": len(items), "classes": sorted(by_class),
                "sha256": hashlib.sha256(text).hexdigest()}
    settings = {"repeats": opts.repeats, "max_drop_pts": opts.max_drop_pts,
                "p99_slack": opts.p99_slack, "budget_usd": opts.budget_usd,
                "planner": opts.planner, "judge": opts.judge, "affinity": opts.affinity,
                "efforts_tried": list(opts.efforts) if opts.efforts else None,
                "temperature": 0}
    pre = (hashlib.sha256(api_key_prefix.encode()).hexdigest()[:16]
           if api_key_prefix else None)
    r = rc.build_receipt(result, mode=mode, rates=opts.rates, workload=workload,
                         settings=settings, calls_sha256=rc.calls_digest(rows), now=now,
                         prev_sha256=rc.last_in_chain(out_dir), window=window,
                         api_key_prefix_sha=pre, host=host_info(transport))
    path = rc.write(r, rows, out_dir)
    # Every run receipt (Baseten or Modal) is signed offline with the local key, with no
    # network and nothing public. If no key is configured the receipt is clearly marked
    # unsigned and the run carries on; signing only fills the integrity block, so it never
    # changes the hash or lifts the grade (Baseten/Modal energy stays an ESTIMATE).
    from wattnot.receipt.sign import sign_run_receipt

    sign_run_receipt(path, key_path=sign_key)
    return path, json.loads(path.read_text())
