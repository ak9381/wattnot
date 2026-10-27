"""Console entry point for the `wattnot` command.

Subcommands:
  impact   scale a measured Wh-per-request saving to monthly kWh, dollars
           and litres of water (all labelled ESTIMATE), then recommendations
  recommend  print "what this means for your data centre" from a run report
             JSON (start/final cap, Wh/request, p99)
  proposals  review, approve, apply and roll back changes WattNot proposes
             (see docs/APPROVALS.md)
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
    rp = sub.add_parser("report", help="exports built from receipts")
    rpsub = rp.add_subparsers(dest="report_cmd")
    eu = rpsub.add_parser("eu", help="EU 2024/1364 field export (CSV + HTML)")
    eu.add_argument("--receipts", nargs="+", required=True)
    eu.add_argument("--out", default="reports/eu-2024-1364", help="path prefix")
    eu.add_argument("--grid-gco2", type=float, help="grid intensity g/kWh (needs --grid-source)")
    eu.add_argument("--grid-source", help="where the grid figure comes from")
    vf = sub.add_parser("verify", help="verify receipts: schema, sha256, signature, rekor, grade")
    vf.add_argument("receipts", nargs="+", help="receipt JSON files (bundle: <id>.sigstore.json)")
    vf.add_argument("--bundle", help="bundle path (only with one receipt)")
    vf.add_argument("--trust", help="trusted-keys JSON (default verifier/trusted-keys.json)")
    vf.add_argument("--json", action="store_true", help="one JSON report per line")
    rs = rsub.add_parser("sign", help="sign a receipt (use --no-rekor for an offline test key)")
    rs.add_argument("receipt", help="receipts/<id>.json")
    rs.add_argument("--no-rekor", action="store_true",
                    help="offline: sign with a local test key, no upload (grade max ESTIMATE)")
    rs.add_argument("--key", help="Ed25519 PEM test key for --no-rekor")
    rj = rsub.add_parser("joules", help="print joules per answer and per good answer")
    rj.add_argument("receipt", help="receipt JSON file")
    rj.add_argument("--json", action="store_true", help="print the derived block as JSON")
    rcl = rsub.add_parser("from-cloud", help="adapt a cloud receipt to the receipt spec "
                                             "(grade capped at ESTIMATE)")
    rcl.add_argument("cloud_receipt", help="cloud-receipts/cloud_<id>.json")
    rcl.add_argument("--out", default="receipts", help="output directory (default %(default)s)")
    rcl.add_argument("--sign-key", help="Ed25519 test key: sign the new receipt offline")
    from wattnot.cloud import cli as _cloud

    _cloud.add_parser(sub)
    from wattnot.meta import cli as _meta

    _meta.add_parser(sub)
    from wattnot.flex import cli as _flex
    _flex.add_parser(sub)
    from wattnot.approvals import cli as _props
    _props.add_parser(sub)
    from wattnot.route import cli as _route
    _route.add_parser(sub)
    from wattnot.certificate import cli as _cert
    _cert.add_parser(sub)
    return parser


def _run_receipt(a: argparse.Namespace) -> int:
    from wattnot.receipt import build_from_dir

    if a.receipt_cmd == "sign":
        from wattnot.receipt.sign import sign
        out = sign(a.receipt, no_rekor=a.no_rekor, key_path=a.key)
        print(f"{out}  ({'offline, no Rekor' if a.no_rekor else 'Rekor'})")
        return 0
    if a.receipt_cmd == "joules":
        from wattnot.receipt.joules import check_joules, joules_block

        r = json.loads(Path(a.receipt).read_text(encoding="utf-8"))
        ok, detail = check_joules(r)
        block = joules_block(r)
        if a.json:
            print(json.dumps({"label": block["label"], "matches_stored": ok if "joules" in r
                              else None, "joules": block}, indent=2))
        else:
            c, b = block["candidate"], block["baseline"]
            print(f"[{block['label']}] baseline  {b['j_per_answer']} J per answer, "
                  f"{b['j_per_good_answer']} J per good answer")
            print(f"[{block['label']}] candidate {c['j_per_answer']} J per answer, "
                  f"{c['j_per_good_answer']} J per good answer")
            ch = block["change_pct"]["j_per_good_answer"]
            print(f"change per good answer: {ch}%"
                  + ("" if ok else "  (stored joules block does NOT match)"))
        return 0 if ok else 1
    if a.receipt_cmd == "from-cloud":
        from wattnot.receipt.cloud_adapter import from_cloud
        from wattnot.receipt.sign import sign

        cloud = json.loads(Path(a.cloud_receipt).read_text(encoding="utf-8"))
        r = from_cloud(cloud)
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{r['receipt_id']}.json"
        path.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
        if a.sign_key:
            sign(path, no_rekor=True, key_path=a.sign_key)
        print(f"{path}  grade {r['grade']}  sha256 {r['integrity']['sha256'][:16]}...")
        return 0
    if a.receipt_cmd != "build":
        raise ValueError("use: wattnot receipt build|sign|joules|from-cloud")
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


def _run_verify(a: argparse.Namespace) -> int:
    import json

    from wattnot.verify import format_report, load_trusted, verify_receipt
    if a.bundle and len(a.receipts) != 1:
        raise ValueError("--bundle works with one receipt")
    trusted = load_trusted(a.trust)
    bad = 0
    for path in a.receipts:
        r = verify_receipt(path, a.bundle, trusted=trusted)
        bad += r.grade == "UNVERIFIABLE"
        print(json.dumps(r.to_dict()) if a.json else format_report(r))
    return 1 if bad else 0


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
    if args.cmd == "report":
        try:
            from wattnot.report.eu import export
            if args.report_cmd != "eu":
                raise ValueError("use: wattnot report eu --receipts ...")
            c, h = export(args.receipts, args.out, grid_gco2_per_kwh=args.grid_gco2,
                          grid_source=args.grid_source)
            print(f"{c}\n{h}")
            return 0
        except (OSError, KeyError, ValueError) as e:
            parser.error(f"report: {e}")
    if args.cmd == "verify":
        try:
            return _run_verify(args)
        except (OSError, ValueError) as e:
            parser.error(f"verify: {e}")
    if args.cmd == "cloud":
        from wattnot.cloud import cli as _cloud

        return _cloud.dispatch(args)
    if args.cmd == "meta":
        from wattnot.meta import cli as _meta

        return _meta.dispatch(args)
    if args.cmd == "flex":
        try:
            from wattnot.flex import cli as _flex

            return _flex.dispatch(args)
        except (OSError, KeyError, TypeError, ValueError) as e:
            parser.error(f"flex: {e}")
    if args.cmd == "proposals":
        from wattnot.approvals import cli as _props

        return _props.dispatch(args)
    if args.cmd == "route":
        try:
            from wattnot.route import cli as _route

            return _route.dispatch(args)
        except (OSError, KeyError, TypeError, ValueError) as e:
            parser.error(f"route: {e}")
    if args.cmd == "certificate":
        try:
            from wattnot.certificate import cli as _cert

            return _cert.dispatch(args)
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as e:
            parser.error(f"certificate: {e}")
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
