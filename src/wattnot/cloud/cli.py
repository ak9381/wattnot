"""`wattnot cloud ...` commands. Wired into the main parser by `add_parser`."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from wattnot.cloud import PROVIDER
from wattnot.cloud import audit as au
from wattnot.cloud import cache as ch
from wattnot.cloud import receipt as rc
from wattnot.cloud import run as rn
from wattnot.cloud.pricing import Rates
from wattnot.cloud.transport import CloudError, key_prefix, read_key
from wattnot.report import cloud as view

OUT = "cloud-receipts"


def _src(p: argparse.ArgumentParser, *, record: bool = False) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--live", action="store_true",
                   help="call Baseten (key from env BASETEN_API_KEY; spends real money)")
    g.add_argument("--fixture", help="replay a fixture file, no network")
    g.add_argument("--demo", action="store_true", help="replay the bundled SYNTHETIC fixture")
    if record:
        p.add_argument("--record", help="with --live: save replies to a fixture for later replay")


def add_parser(sub) -> None:
    c = sub.add_parser("cloud", help="cloud mode: tune reasoning effort on a hosted model")
    s = c.add_subparsers(dest="cloud_cmd")
    t = s.add_parser("tune", help="find the cheapest reasoning effort that keeps answers right")
    t.add_argument("--provider", default=PROVIDER, choices=[PROVIDER])
    _src(t, record=True)
    t.add_argument("--workload", default=str(rn.WORKLOAD))
    t.add_argument("--classes", help="comma list, default all")
    t.add_argument("--efforts", help="comma list of levels to try (xhigh always runs as reference)")
    t.add_argument("--repeats", type=int, default=2)
    t.add_argument("--max-drop-pts", type=float, default=1.0)
    t.add_argument("--p99-slack", type=float, default=0.10)
    t.add_argument("--budget-usd", type=float, help="stop starting new levels near this spend")
    t.add_argument("--no-planner", action="store_true", help="skip the model-proposed order")
    t.add_argument("--judge", action="store_true", help="add the model judge as a third gate")
    t.add_argument("--no-affinity", action="store_true")
    t.add_argument("--confirmed-rates", help="in/cached/out USD per 1M, e.g. 1.25,0.15,4.25")
    t.add_argument("--sign-key", help="Ed25519 test key: sign the receipt offline")
    t.add_argument("--out", default=OUT)
    sh = s.add_parser("show", help="print a receipt as a table")
    sh.add_argument("receipt")
    rp = s.add_parser("report", help="write the receipt as one HTML page")
    rp.add_argument("receipt")
    rp.add_argument("--out", help="default: next to the receipt")
    a = s.add_parser("audit", help="reconcile a receipt with Baseten's usage meter")
    a.add_argument("receipt")
    a.add_argument("--meter-fixture", help="usage JSON file instead of the live endpoint")
    a.add_argument("--tolerance", type=float, default=au.TOLERANCE)
    ab = s.add_parser("cache-ab", help="A/B the prompt layout for cached input")
    _src(ab)
    ab.add_argument("--n", type=int, default=8)
    ab.add_argument("--out", default=OUT)
    pr = s.add_parser("probe", help="cheap cache half-life probe")
    _src(pr)
    pr.add_argument("--waits", default="10,30,60,180,600")
    pr.add_argument("--out", default=OUT)
    d = s.add_parser("demo", help="offline end to end: tune, audit, report (SIMULATED)")
    d.add_argument("--out", default="cloud-demo")


def _fail(msg: str) -> int:
    print(f"wattnot cloud: {msg}", file=sys.stderr)
    return 2


def _rates(spec: str | None) -> Rates:
    if not spec:
        return Rates()
    a, b, c = (float(x) for x in spec.split(","))
    return Rates(input_per_m=a, cached_per_m=b, output_per_m=c, confirmed=True,
                 source="confirmed by the user against an invoice or the console")


def _transport(a, *, force_demo: bool = False):
    if force_demo or getattr(a, "demo", False):
        return rn.open_transport(live=False, fixture=rn.DEMO_FIXTURE)
    if getattr(a, "live", False):
        read_key()  # fail early, with a message that never shows the key
        return rn.open_transport(live=True, fixture=None, record=getattr(a, "record", None))
    if getattr(a, "fixture", None):
        return rn.open_transport(live=False, fixture=a.fixture)
    raise ValueError("pick one: --live, --fixture FILE or --demo")


def _load(path: str) -> tuple[dict, list[dict], Path]:
    p = Path(path)
    r = json.loads(p.read_text(encoding="utf-8"))
    ok, want, got = rc.verify_hash(r)
    if not ok:
        raise ValueError(f"receipt hash does not match its content ({want[:12]} vs {got[:12]})")
    cp = p.with_name(f"{r['receipt_id']}.calls.jsonl")
    rows = [json.loads(x) for x in cp.read_text(encoding="utf-8").splitlines()] \
        if cp.is_file() else []
    if rows and rc.calls_digest(rows) != r["calls"]["sha256"]:
        raise ValueError("calls file does not match the receipt")
    side = p.with_name(f"{r['receipt_id']}.audit.json")
    view_r = dict(r)
    if side.is_file():
        view_r["meter_audit"] = json.loads(side.read_text(encoding="utf-8"))
    return view_r, rows, p


def _tune(a) -> int:
    transport, mode, rec = _transport(a)
    opts = rn.Options(
        workload=Path(a.workload), classes=a.classes.split(",") if a.classes else None,
        efforts=a.efforts.split(",") if a.efforts else None, repeats=a.repeats,
        max_drop_pts=a.max_drop_pts, p99_slack=a.p99_slack, budget_usd=a.budget_usd,
        planner=not a.no_planner, judge=a.judge, affinity=not a.no_affinity,
        rates=_rates(a.confirmed_rates))
    prefix = key_prefix(read_key()) if a.live else None
    try:
        path, r = rn.run(opts, transport, mode, out_dir=a.out, api_key_prefix=prefix,
                         sign_key=a.sign_key)
    except BaseException:
        if rec is not None and rec.replies:  # keep what was paid for, even on a stop
            rec.save(a.record)
            print(f"recorded {len(rec.replies)} replies to {a.record} before stopping",
                  file=sys.stderr)
        raise
    if rec is not None:
        rec.save(a.record)
        print(f"recorded replies to {a.record} (reasoning text is not stored)")
    page = path.with_suffix(".html")
    page.write_text(view.render(r), encoding="utf-8")
    print("\n".join(view.summary_lines(r)))
    print(f"\nreceipt {path}\nreport  {page}")
    return 0


def _show(a) -> int:
    r, _, _ = _load(a.receipt)
    print("\n".join(view.summary_lines(r)))
    return 0


def _report(a) -> int:
    r, _, p = _load(a.receipt)
    out = Path(a.out) if a.out else p.with_suffix(".html")
    out.write_text(view.render(r), encoding="utf-8")
    print(out)
    return 0


def _audit(a, *, receipt: str | None = None, fixture: str | None = None) -> int:
    r, rows, p = _load(receipt or a.receipt)
    fx = fixture or getattr(a, "meter_fixture", None)
    tol = getattr(a, "tolerance", au.TOLERANCE)
    label = None
    if fx:
        items = json.loads(Path(fx).read_text(encoding="utf-8")).get("items") or []
        res = au.audit(r, rows, items, tolerance=tol)
        label = "SIMULATED"
    else:
        try:
            start, end = au.window_for(r)
            res = au.audit(r, rows, au.fetch_usage(start, end), tolerance=tol)
        except (CloudError, ValueError) as e:
            res = au.unavailable(str(e))
    if label and res["status"] != "unavailable":
        res["label"] = label
    side = p.with_name(f"{r['receipt_id']}.audit.json")
    side.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    if res["status"] == "unavailable":
        print(f"[NOT_AVAILABLE] meter audit could not run: {res['reason']}")
        return 3
    for x in res["rows"]:
        print(f"{x['field']:<24}{x['receipt']:>12,}{x['meter']:>12,}  gap {x['gap_pct']:>6}%  "
              f"{'ok' if x['ok'] else 'OVER'}")
    print(f"[{res['label']}] {res['status']} (tolerance {res['tolerance_pct']:g}%, "
          f"worst gap {res['worst_gap_pct']}%)\nwrote {side}")
    return 0 if res["status"] == "match" else 1


def _relabel(doc: dict, mode: str) -> dict:
    if mode == "replay-synthetic":
        doc["label"] = "SIMULATED"
    return doc


def _cache_ab(a) -> int:
    transport, mode, rec = _transport(a)
    res = _relabel(ch.cache_ab(transport, Rates(), n=a.n), mode)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    out = Path(a.out) / f"cache-ab-{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}.json"
    out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(f"[{res['label']}] prompt layout vs cached input ({a.n} calls per arm)")
    for x in res["arms"]:
        print(f"  {x['arm']:<22} cached {x['cached_share']:>6.1%} (after 1st "
              f"{x['cached_share_after_first']:.1%})  {view.usd(x['usd'])}  "
              f"total saving {x['saving_vs_volatile_pct']:.1f}%, input-only saving "
              f"{x['input_saving_vs_volatile_pct']:.1f}%")
    print(f"wrote {out}")
    return 0


def _probe(a) -> int:
    transport, mode, _ = _transport(a)
    waits = [float(w) for w in a.waits.split(",")]
    kw = {} if mode == "live" else {"sleep": lambda s: None}
    res = _relabel(ch.probe_half_life(transport, Rates(), waits=waits, **kw), mode)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    out = Path(a.out) / f"cache-probe-{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}.json"
    out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(f"[{res['label']}] cached share of a repeated prefix after a wait")
    for p in res["curve"]:
        print(f"  {p['wait_s']:>6g} s  {p['cached_share']:>6.1%}")
    print(f"{res['reading']}\nwrote {out}")
    return 0


def _demo(a) -> int:
    transport, mode, _ = rn.open_transport(live=False, fixture=rn.DEMO_FIXTURE)
    path, r = rn.run(rn.Options(), transport, mode, out_dir=a.out)
    _audit(argparse.Namespace(tolerance=au.TOLERANCE), receipt=str(path),
           fixture=str(rn.DEMO_METER))
    r, _, _ = _load(str(path))
    page = path.with_suffix(".html")
    page.write_text(view.render(r), encoding="utf-8")
    print("\n" + "\n".join(view.summary_lines(r)))
    print(f"\nreceipt {path}\nreport  {page}\n"
          "Everything above is SIMULATED (made-up fixture, no API call).")
    return 0


def dispatch(a) -> int:
    fn = {"tune": _tune, "show": _show, "report": _report, "audit": _audit,
          "cache-ab": _cache_ab, "probe": _probe, "demo": _demo}.get(a.cloud_cmd)
    if fn is None:
        return _fail("use one of: tune, show, report, audit, cache-ab, probe, demo")
    try:
        return fn(a)
    except (CloudError, OSError, KeyError, ValueError) as e:
        return _fail(str(e))
