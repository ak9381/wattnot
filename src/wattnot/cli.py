"""Console entry point for the `wattnot` command.

Subcommands:
  impact   scale a measured Wh-per-request saving to monthly kWh, dollars
           and litres of water (all labelled ESTIMATE)
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from wattnot import __version__
from wattnot import impact as _impact


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
    return parser


def _run_impact(a: argparse.Namespace) -> int:
    i = _impact.estimate(a.wh_delta, a.requests_per_gpu_per_day, a.gpus,
                         tariff_usd_per_kwh=a.tariff_usd_per_kwh, wue_l_per_kwh=a.wue_l_per_kwh,
                         pue=a.pue, days=a.days)
    print("\n".join(_impact.report(i, gpus=a.gpus, days=a.days)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "impact":
        try:
            return _run_impact(args)
        except ValueError as e:
            parser.error(str(e))
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
