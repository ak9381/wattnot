"""Console entry point for the `wattnot` command.

This is a stub. Subcommands (run, verify, receipt, report) land in later
modules; for now the CLI only reports its version so packaging and the
console script can be wired up and tested.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from wattnot import __version__


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    # No subcommands yet: print a short note so the bare command does something.
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
