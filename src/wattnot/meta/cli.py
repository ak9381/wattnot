"""`wattnot meta ...`: spend, check, session."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from wattnot.meta.client import GuardedClient, MetaError, load_key
from wattnot.meta.ledger import CAP_USD, SOFT_USD, BudgetExceeded, SpendLedger, price_for

ROOT = Path(__file__).resolve().parents[3]
EARLIER = (  # recorded usage from before the ledger existed: (file, purpose, model)
    ("tests/fixtures/spark/spend.json", "earlier:planner-fixtures", "muse-spark-1.3"),
    ("workloads/muse-code-v1.spend.json", "earlier:workload-generation", "muse-spark-1.3"),
)


def seed_earlier(ledger: SpendLedger, root: Path = ROOT) -> int:
    """Count usage recorded in the repo before the ledger existed, once."""

    rows = []
    for rel, purpose, model in EARLIER:
        p = root / rel
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        t = d.get("totals") or d
        rows.append({"key": f"earlier:{rel}", "purpose": purpose, "model": d.get("model", model),
                     "input_tokens": int(t.get("prompt_tokens", 0)),
                     "output_tokens": int(t.get("completion_tokens", 0)),
                     "note": f"{d.get('calls', '?')} calls recorded in {rel}"})
    return ledger.seed(rows)


def add_parser(sub) -> None:
    m = sub.add_parser("meta", help="Meta API usage: spend, check, session, wonder (hard cap $100)")
    s = m.add_subparsers(dest="meta_cmd")
    sp = s.add_parser("spend", help="total, per-purpose breakdown and remaining budget")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--ledger", help="ledger file (default ~/.wattnot/meta_spend.json)")
    ck = s.add_parser("check", help="one tiny call to confirm the model answers")
    ck.add_argument("--model", default="muse-spark-1.3")
    ck.add_argument("--ledger")
    se = s.add_parser("session", help="run the real agent session on fixtures and public sets")
    se.add_argument("--steps", default="next-knob,ladder,coding,judge,receipts")
    se.add_argument("--full-ladder", action="store_true",
                    help="run every effort level, not only until the first one that passes")
    se.add_argument("--cache-dir", help="keep model answers here so a rerun does not pay twice")
    se.add_argument("--out", help="write the session JSON here")
    se.add_argument("--repeats", type=int, default=3)
    se.add_argument("--ledger")
    wo = s.add_parser("wonder", help="Muse Wonder cap/clock Pareto supervisor")
    wo.add_argument("--input", help="compact per-trial JSON summaries file")
    wo.add_argument("--out", default="wonder_out", help="output directory for summary (json + md)")
    wo.add_argument("--model", default="muse-wonder-1.0", help="Wonder model id")
    wo.add_argument("--threshold", type=float, default=0.01, help="Pareto improvement threshold")
    wo.add_argument("--patience", type=int, default=3, help="Pareto patience in trials")
    wo.add_argument("--mock", dest="mock", action="store_true", default=True, help="mock mode (default)")
    wo.add_argument("--live", dest="mock", action="store_false", help="live calls (requires key)")
    wo.add_argument("--ledger")


def _bar(frac: float, width: int = 30) -> str:
    n = max(0, min(width, round(frac * width)))
    return "#" * n + "." * (width - n)


def format_spend(s: dict) -> str:
    lines = [f"Meta API spend   ${s['total_usd']:.4f} of ${s['cap_usd']:.2f} hard cap  "
             f"[{_bar(s['total_usd'] / s['cap_usd'])}]",
             f"remaining        ${s['remaining_usd']:.4f}   (soft warning at ${s['soft_usd']:.2f})",
             f"calls            {s['calls']}  ({s['input_tokens']:,} in / "
             f"{s['output_tokens']:,} out tokens"
             f"{', ' + str(s['pending']) + ' pending' if s['pending'] else ''}"
             f"{', ' + str(s['errors']) + ' rejected' if s['errors'] else ''})"]
    if s["warning"]:
        lines.append(f"WARNING: over the ${s['soft_usd']:.2f} soft limit")
    lines += ["", f"{'purpose':<36}{'calls':>6}{'in tok':>10}{'out tok':>10}{'USD':>10}"]
    for k, v in s["per_purpose"].items():
        lines.append(f"{k:<36}{v['calls']:>6}{v['input']:>10,}{v['output']:>10,}"
                     f"{v['usd']:>10.4f}")
    return "\n".join(lines)


def dispatch(a) -> int:
    ledger = SpendLedger(getattr(a, "ledger", None))
    if a.meta_cmd == "spend":
        seed_earlier(ledger)
        s = ledger.summary()
        print(json.dumps(s, indent=2) if a.json else format_spend(s))
        return 0
    if a.meta_cmd == "check":
        seed_earlier(ledger)
        price, assumed = price_for(a.model)
        body = {"model": a.model, "reasoning_effort": "minimal", "max_tokens": 200,
                "messages": [{"role": "user", "content": "Reply with the single word: ok"}]}
        try:
            raw = GuardedClient(ledger=ledger)(body, purpose="model-check")
        except (MetaError, BudgetExceeded) as e:
            print(f"{a.model}: FAILED: {e}")
            return 1
        print(f"{a.model}: HTTP 200, answered as {raw.get('model')}, usage {raw.get('usage')}")
        print(f"price used: ${price.input_per_m}/M in, ${price.output_per_m}/M out"
              f"{' (ASSUMED, model not in the price table)' if assumed else ''}")
        return 0
    if a.meta_cmd == "session":
        if not load_key():
            print("no API key: set META_API_KEY or log in with `muse login`", file=sys.stderr)
            return 1
        from wattnot.meta.session import Session

        seed_earlier(ledger)
        sess = Session(GuardedClient(ledger=ledger), ledger, repeats=a.repeats,
                       full_ladder=a.full_ladder,
                       cache_dir=Path(a.cache_dir) if a.cache_dir else None,
                       log=lambda m: print(m, flush=True))
        out = sess.run(tuple(x.strip() for x in a.steps.split(",") if x.strip()))
        if a.out:
            Path(a.out).write_text(json.dumps(out, indent=2) + "\n")
        print(format_spend(ledger.summary()))
        return 1 if out.get("stopped") else 0
    if a.meta_cmd == "wonder":
        from wattnot.meta.wonder import WonderSupervisor

        seed_earlier(ledger)
        trials = []
        if getattr(a, "input", None):
            p = Path(a.input)
            if p.exists():
                trials = json.loads(p.read_text())
                if isinstance(trials, dict) and "trials" in trials:
                    trials = trials["trials"]

        client = GuardedClient(ledger=ledger) if not a.mock else None
        sup = WonderSupervisor(
            client=client,
            model=a.model,
            threshold=a.threshold,
            patience=a.patience,
            mock=a.mock,
        )
        decision = sup.next_trial(trials)
        json_p, md_p = sup.write_summary(trials, a.out, decision.reason, ledger=ledger)
        print(f"Wonder action: {decision.action} ({decision.reason})")
        if decision.action == "trial":
            print(f"Next trial: cap_w={decision.cap_w}, clock_profile={decision.clock_profile}")
        print(f"Summary written to {json_p} and {md_p}")
        return 0
    print("use: wattnot meta spend|check|session|wonder", file=sys.stderr)
    return 2


__all__ = ["CAP_USD", "SOFT_USD", "add_parser", "dispatch", "format_spend", "seed_earlier"]
