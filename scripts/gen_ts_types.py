#!/usr/bin/env python3
"""Regenerate web/types/schemas.ts from the frozen JSON Schemas.

Usage::

    python scripts/gen_ts_types.py          # write web/types/schemas.ts
    python scripts/gen_ts_types.py --check   # exit 1 if the file is stale

Run it after any schema change and commit the result.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from wattnot._tsgen import render_typescript  # noqa: E402
from wattnot.schemas import load_all_schemas  # noqa: E402

OUTPUT = REPO_ROOT / "web" / "types" / "schemas.ts"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit non-zero if the committed file is out of date",
    )
    args = parser.parse_args(argv)

    rendered = render_typescript(load_all_schemas())

    if args.check:
        current = OUTPUT.read_text() if OUTPUT.exists() else ""
        if current != rendered:
            print(f"{OUTPUT} is out of date; run scripts/gen_ts_types.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is up to date")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered)
    print(f"wrote {OUTPUT} ({rendered.count(chr(10)) + 1} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
