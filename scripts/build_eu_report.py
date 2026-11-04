"""Pre-build the one-click EU efficiency report for the Oct 31 Glimmer rerun receipt.

The older RUN2 receipt served a non-Meta model and is no longer exported here.

Writes web/dash/eu/: the HTML and CSV export from wattnot.report.eu. Static, so it opens on GitHub Pages too.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.report.eu import generate_report  # noqa: E402

RECEIPT = ROOT / "receipts/rerun-1031/rcpt_20261031_rerun_cap325.json"


def main(out: Path = ROOT / "web/dash/eu") -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    c, h = generate_report(RECEIPT, out)
    stray = out / RECEIPT.name
    if stray.exists():
        stray.unlink()
    print("EU report:", h, c)
    return [h, c]


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "web/dash/eu")
