"""``python -m wattnot.dash``: same as ``wattnot dash``."""
from __future__ import annotations

import argparse
from pathlib import Path

from wattnot.dash.server import serve


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--capture", type=Path, help="expert capture to replay (jsonl or jsonl.gz)")
    p.add_argument("--follow", type=Path,
                   help="expert capture a local run is still writing; the page follows it live")
    p.add_argument("--results", type=Path,
                   help="H100 results folder to read directly (default: the bundled replay)")
    p.add_argument("--token",
                   help="require this access token (or set WATTNOT_DASH_TOKEN); for a hosted demo")
    p.add_argument("--events", type=Path,
                   help="a run's events.jsonl to follow on the Architecture view")
    p.add_argument("--replay-only", action="store_true",
                   help="hosted replay for judges: no GPU, no live Meta calls; "
                        "--events is the saved run (or set WATTNOT_HOSTED_REPLAY=1)")


def run(a: argparse.Namespace) -> int:
    return serve(a.host, a.port, capture=a.capture, follow=a.follow, results=a.results,
                 token=a.token, events=a.events, replay_only=a.replay_only or None)


def main() -> int:
    p = argparse.ArgumentParser(prog="wattnot dash", description=__doc__)
    add_args(p)
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
