"""`wattnot route ...`: choose effort and region per request class. Replay only.

    wattnot cloud demo --out cloud-demo                    # a recorded (SIMULATED) cloud run
    wattnot route replay --receipt cloud-demo/cloud_*.json  # router vs always-xhigh, $0
    wattnot route decide --receipt ... --class arith       # one decision, grid reading beside it
    wattnot route grid                                     # the grid readings in use
"""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.route import grid as _grid
from wattnot.route.replay import HOME_REGION, format_report, replay
from wattnot.route.router import choose, class_candidates, expand_regions


def _common(p) -> None:
    p.add_argument("--grid", choices=("fixture", "uk-api"), default="fixture",
                   help="fixture = offline static values (default); uk-api = live UK figure "
                        "for region gb, needs the network, OFF by default")
    p.add_argument("--grid-fixture", help="use your own grid fixture file")
    p.add_argument("--regions", help="comma list of regions for the what-if (default: all)")
    p.add_argument("--home-region", default=HOME_REGION,
                   help="where the always-xhigh baseline is assumed to run (default %(default)s)")
    p.add_argument("--tie-pct", type=float, default=3.0,
                   help="joules within this %% count as a tie; the cleaner grid wins")


def add_parser(sub) -> None:
    r = sub.add_parser("route", help="router: pick effort and region per class (replay only)")
    s = r.add_subparsers(dest="route_cmd")
    rp = s.add_parser("replay", help="replay a recorded cloud run: router vs always xhigh")
    rp.add_argument("--receipt", required=True, help="cloud receipt JSON")
    rp.add_argument("--out", help="write the report JSON here")
    rp.add_argument("--json", action="store_true", help="print the report as JSON")
    _common(rp)
    d = s.add_parser("decide", help="one class: the chosen candidate and why")
    d.add_argument("--receipt", required=True)
    d.add_argument("--class", dest="cls", required=True, help="request class name")
    d.add_argument("--json", action="store_true")
    _common(d)
    g = s.add_parser("grid", help="print the grid intensity readings")
    g.add_argument("--json", action="store_true")
    _common(g)


def _grid_from(a):
    return _grid.make_grid(a.grid, fixture=a.grid_fixture)


def _regions(a, grid) -> list[str]:
    if a.regions:
        return [x.strip() for x in a.regions.split(",") if x.strip()]
    return grid.region_names()


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _replay(a) -> int:
    grid = _grid_from(a)
    rep = replay(_load(a.receipt), grid, home_region=a.home_region, regions=_regions(a, grid),
                 tie_pct=a.tie_pct)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(rep, indent=2) if a.json else format_report(rep))
    return 0


def _decide(a) -> int:
    grid = _grid_from(a)
    cloud = _load(a.receipt)
    cls = next((c for c in cloud.get("classes", []) if c["class"] == a.cls), None)
    if cls is None:
        names = ", ".join(c["class"] for c in cloud.get("classes", []))
        raise ValueError(f"no class {a.cls!r} in that receipt (have: {names})")
    cands = expand_regions(class_candidates(cloud, cls), _regions(a, grid))
    d = choose(cands, grid, tie_pct=a.tie_pct)
    out = {"label": "SIMULATED" if cloud["label"] == "SIMULATED" else "ESTIMATE",
           "region_label": "SIMULATED", **d.to_dict()}
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"[{out['label']}] {a.cls}: {d.reason}")
        if d.grid:
            print(f"  grid [{d.grid['label']}] {d.grid['region']}: {d.grid['gco2_per_kwh']:g} "
                  f"gCO2/kWh, {d.grid['source']} ({d.grid['as_of']})")
        print("  region is a what-if [SIMULATED]; carbon only breaks ties.")
    return 0 if d.chosen else 1


def _show_grid(a) -> int:
    grid = _grid_from(a)
    rows = [grid.reading(r).to_dict() for r in grid.region_names()]
    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"[{r['label']}] {r['region']:<14}{r['gco2_per_kwh']:>6g} gCO2/kWh  "
                  f"{r['source']}")
        notes = sorted({r["note"] for r in rows if r["note"]})
        for n in notes:
            print(f"note: {n}")
    return 0


def dispatch(a) -> int:
    if a.route_cmd == "replay":
        return _replay(a)
    if a.route_cmd == "decide":
        return _decide(a)
    if a.route_cmd == "grid":
        return _show_grid(a)
    raise ValueError("use: wattnot route replay|decide|grid")
