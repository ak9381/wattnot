"""Console entry point for the `wattnot` command.

Subcommands:
  impact   scale a measured Wh-per-request saving to monthly kWh, dollars
           and litres of water (all labelled ESTIMATE), then recommendations
  recommend  print "what this means for your data centre" from a run report
             JSON (start/final cap, Wh/request, p99)
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from wattnot import __version__
from wattnot import impact as _impact
from wattnot import recommend as _rec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wattnot",
        description="Cut LLM inference energy and prove each saving with a signed receipt.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"wattnot {__version__}",
    )
    sub = parser.add_subparsers(dest="cmd")
    imp = sub.add_parser("impact", help="scale a measured Wh/request saving (ESTIMATE)",
                         description=_impact.__doc__,
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    imp.add_argument("--wh-delta", type=float, required=True,
                     help="measured Wh saved per request, from a receipt (required)")
    imp.add_argument("--requests-per-gpu-per-day", type=float, required=True,
                     help="traffic per GPU per day (required)")
    imp.add_argument("--gpus", type=int, default=1, help="number of GPUs (default 1)")
    imp.add_argument("--tariff-usd-per-kwh", type=float, default=_impact.TARIFF_USD_PER_KWH,
                     help="electricity price (assumed default %(default)s USD/kWh)")
    imp.add_argument("--wue-l-per-kwh", type=float, default=_impact.WUE_L_PER_KWH,
                     help="water per kWh (default %(default)s L/kWh)")
    imp.add_argument("--pue", type=float, default=_impact.PUE,
                     help="facility overhead (default %(default)s, GPU energy only)")
    imp.add_argument("--days", type=float, default=_impact.DAYS,
                     help="period length (default %(default)s)")
    rec = sub.add_parser("recommend", help="recommendations from a run report JSON")
    rec.add_argument("report", help="run report JSON (see tests/fixtures/runs/example_report.json)")
    rec.add_argument("--json", action="store_true", help="print the recommendations as JSON")
    rc = sub.add_parser("receipt", help="build receipts from finished runs")
    rsub = rc.add_subparsers(dest="receipt_cmd")
    rb = rsub.add_parser("build", help="build a receipt from runs/<id>/run.json")
    rb.add_argument("--run", required=True, help="run directory (runs/<id>)")
    rb.add_argument("--out", default="receipts", help="output directory (default %(default)s)")
    return parser


def _run_receipt(a: argparse.Namespace) -> int:
    from wattnot.receipt import build_from_dir

    if a.receipt_cmd != "build":
        raise ValueError("use: wattnot receipt build --run runs/<id>")
    path = build_from_dir(a.run, a.out)
    r = json.loads(Path(path).read_text())
    print(f"{path}  grade {r['grade']}  sha256 {r['integrity']['sha256'][:16]}...")
    return 0


def _run_recommend(a: argparse.Namespace) -> int:
    import json

    with open(a.report, encoding="utf-8") as f:
        r = json.load(f)
    keys = ("start_cap_w", "final_cap_w", "gate_passed", "base_wh_per_req",
            "final_wh_per_req", "base_p99_ms", "final_p99_ms", "knob")
    rec = _rec.with_summary(_rec.for_run(**{k: r[k] for k in keys if k in r},
                                         **r.get("assumptions", {})), None)
    if a.json:
        print(json.dumps(rec.to_dict(), indent=2))
    else:
        if r.get("label"):
            print(f"[{r['label']}]")
        print("\n".join(rec.lines()))
    return 0


def _run_impact(a: argparse.Namespace) -> int:
    i = _impact.estimate(a.wh_delta, a.requests_per_gpu_per_day, a.gpus,
                         tariff_usd_per_kwh=a.tariff_usd_per_kwh, wue_l_per_kwh=a.wue_l_per_kwh,
                         pue=a.pue, days=a.days)
    print("\n".join(_impact.report(i, gpus=a.gpus, days=a.days)))
    print()
    print("\n".join(_rec.for_impact(i, gpus=a.gpus, days=a.days).lines()))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "impact":
        try:
            return _run_impact(args)
        except ValueError as e:
            parser.error(str(e))
    if args.cmd == "recommend":
        try:
            return _run_recommend(args)
        except (OSError, KeyError, TypeError, ValueError) as e:
            parser.error(f"bad report: {e}")
    if args.cmd == "receipt":
        try:
            return _run_receipt(args)
        except (OSError, KeyError, TypeError, ValueError) as e:
            parser.error(f"receipt: {e}")
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
