"""Render the sweep chart, let Muse Wonder read it, write the suggestion.

    python -m wattnot.wonderchart.run --fake --sweep runs/b2/sweep.json --out runs/b2

``--fake`` (the default when no ``--live``) uses the deterministic fallback: it renders
the chart and picks the next setting offline, with no network and no paid call, so it
runs inside the sweep loop and in CI. ``--live`` routes the chart image through the
guarded Muse Wonder client. Writes ``wonder_chart.png`` (the figure that was read) and
``wonder_chart.json`` (the receipt block + a short summary). Exit 0 on success, 1 on error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from wattnot.wonderchart.chart import build_points, chart_png_bytes, chart_sha256, render_chart
from wattnot.wonderchart.reader import read_chart, wonder_chart_entry

# A tiny SYNTHETIC demo sweep so ``--fake`` works with no input file.
_DEMO: list[dict] = [
    {"cap_w": 375, "clock_profile": "base", "joules_per_correct": 1.00, "quality": 0.99,
     "kept": True, "label": "SYNTHETIC"},
    {"cap_w": 350, "clock_profile": "base", "joules_per_correct": 0.94, "quality": 0.98,
     "kept": True, "label": "SYNTHETIC"},
    {"cap_w": 325, "clock_profile": "base", "joules_per_correct": 0.91, "quality": 0.97,
     "kept": True, "label": "SYNTHETIC"},
    {"cap_w": 300, "clock_profile": "base", "joules_per_correct": 0.95, "quality": 0.88,
     "kept": False, "label": "SYNTHETIC"},
]


def _load_trials(path: str | None) -> list[dict]:
    if not path:
        return list(_DEMO)
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        for key in ("trials", "history", "points", "sweep"):
            if isinstance(data.get(key), list):
                return list(data[key])
        raise ValueError(f"{path}: no trials/history/points list found")
    if isinstance(data, list):
        return list(data)
    raise ValueError(f"{path}: expected a list or an object with a trials list")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Wonder reads the sweep chart")
    ap.add_argument("--sweep", default=None, help="JSON file: a trials list or {trials:[...]}")
    ap.add_argument("--out", default=None, help="write wonder_chart.png and wonder_chart.json here")
    ap.add_argument("--live", action="store_true", help="route the chart through Muse Wonder (paid)")
    ap.add_argument("--fake", action="store_true", help="offline deterministic fallback (default)")
    args = ap.parse_args(argv)

    try:
        trials = _load_trials(args.sweep)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"wonder chart: cannot read sweep ({e})", file=sys.stderr)
        return 1

    points = build_points(trials)

    client = None
    if args.live and not args.fake:
        from wattnot.meta.client import GuardedClient
        client = GuardedClient()

    png: bytes | None = None
    sha: str | None = None
    try:
        png = chart_png_bytes(points)
        sha = chart_sha256(png)
    except Exception as e:  # noqa: BLE001 - Pillow missing: still pick a setting, hash the data
        print(f"wonder chart: render skipped ({type(e).__name__}); hashing plotted data",
              file=sys.stderr)

    suggestion = read_chart(points, png=png, client=client)
    entry = wonder_chart_entry(points, suggestion, chart_sha256=sha)

    summary = {
        "spec": "wattnot.wonderchart/1",
        "mode": "live" if client is not None else "fake",
        "source": suggestion.source,
        "suggestion": entry["suggestion"],
        "reason": entry["reason"],
        "trace_id": entry["trace_id"],
        "wonder_chart": entry,
    }

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        if png is not None:
            render_chart(points, out / "wonder_chart.png")
        (out / "wonder_chart.json").write_text(json.dumps(summary, indent=2) + "\n",
                                               encoding="utf-8")

    s = entry["suggestion"]
    nxt = "stop" if s["action"] == "stop" else f"{s['cap_w']}W/{s['clock_profile']}"
    print(f"wonder chart [{summary['mode']} · {suggestion.source}]: next = {nxt} — {entry['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
