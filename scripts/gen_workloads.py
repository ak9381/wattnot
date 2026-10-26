#!/usr/bin/env python3
"""One-time generator for the committed workload sets (STUB — not wired up yet).

The public replay set and the Muse Code task set v1 are generated once by a
maintainer and then committed, so every test and gate run replays the same
bytes. This script is the home for that one-time generation. The live parts are
left as clearly marked stubs for the maintainer to fill in and run later; this
file never calls a model API on its own.

Usage::

    python scripts/gen_workloads.py --check      # offline: validate committed sets
    python scripts/gen_workloads.py --generate    # live, maintainer-only (stub)

The ``--generate`` path is guarded by ``WATTNOT_LIVE=1`` and still raises until
the maintainer implements it, because it will:

  * replay set   — fetch a sample from a public, no-login dataset, recording the
    source and license per item (replaces the hand-written placeholder);
  * muse-code set — run a short, scripted coding test task against Muse Spark
    Open to produce v1 prompts, logging the tiny test spend.

Both outputs must stay public (never private prompts) and must round-trip
through ``wattnot.runner.workload.load_workload`` before being committed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from wattnot.runner import workload as wl  # noqa: E402


def check() -> int:
    """Validate the committed sets load cleanly. Fully offline."""

    ok = True
    for loader in (wl.load_replay_set, wl.load_muse_code_set):
        try:
            w = loader()
        except wl.WorkloadError as exc:
            print(f"FAIL {loader.__name__}: {exc}", file=sys.stderr)
            ok = False
            continue
        print(f"ok   {w.name} ({w.kind}): {len(w)} items")
    return 0 if ok else 1


def generate() -> int:
    """Regenerate the sets from live sources. Maintainer-only; not implemented."""

    if os.environ.get("WATTNOT_LIVE") != "1":
        print(
            "refusing to generate: set WATTNOT_LIVE=1 and run as the maintainer.",
            file=sys.stderr,
        )
        return 2
    raise NotImplementedError(
        "live workload generation is a stub. Implement the replay-set fetch and "
        "the scripted Muse Code task here, then commit the regenerated files."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="validate the committed sets (offline)"
    )
    mode.add_argument(
        "--generate",
        action="store_true",
        help="regenerate from live sources (maintainer-only; stub)",
    )
    args = parser.parse_args(argv)
    return generate() if args.generate else check()


if __name__ == "__main__":
    raise SystemExit(main())
