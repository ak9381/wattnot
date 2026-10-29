"""Pre-build the one-click EU efficiency report for the RUN2 measured receipt.

Writes web/dash/eu/: the HTML and CSV export from wattnot.report.eu, plus a copy
of the receipt the dashboard cards read. Static, so it opens on GitHub Pages too.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.report.eu import generate_report  # noqa: E402

RECEIPT = ROOT / "receipts/run2/rcpt_20261027_run2_cap350.json"


def main(out: Path = ROOT / "web/dash/eu") -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    c, h = generate_report(RECEIPT, out)
    stray = out / RECEIPT.name
    shutil.copy(RECEIPT, out / "run2-receipt.json")
    if stray.exists() and stray != out / "run2-receipt.json":
        stray.unlink()
    print("EU report:", h, c)
    return [h, c]


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "web/dash/eu")
