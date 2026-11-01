from wattnot.eligibility import build, FEATURES_JSON
from pathlib import Path
import sys
d = build(Path(__file__).resolve().parents[3] / "docs" / "WALKTHROUGH.md")
print(f"{d['count']} features written", file=sys.stderr)
