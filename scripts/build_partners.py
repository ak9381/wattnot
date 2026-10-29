"""Build data/dash/partners.json from the saved usage logs.

Reads the Meta, Baseten and Modal markdown usage tables and a JSON file with the
rented-GPU runs, then writes the `wattnot.partners/1` snapshot the dashboard
serves. No secrets, no prompts, no absolute paths in the output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wattnot.dash.partners import build  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--meta-log", type=Path, default=None)
    ap.add_argument("--baseten-log", type=Path, default=None)
    ap.add_argument("--modal-log", type=Path, default=None)
    ap.add_argument("--e2e-runs", type=Path, default=None,
                    help="JSON list of rented-GPU runs (or an object with a 'runs' list)")
    ap.add_argument("--agent-waste-results", type=Path,
                    default=Path("data/agent-waste/results.json"),
                    help="Agent waste results; its Muse Spark spend shows on the Meta card")
    ap.add_argument("--out", type=Path, default=Path("data/dash/partners.json"))
    a = ap.parse_args(argv)
    meta_text = a.meta_log.read_text(encoding="utf-8") if a.meta_log else None
    baseten_text = a.baseten_log.read_text(encoding="utf-8") if a.baseten_log else None
    modal_text = a.modal_log.read_text(encoding="utf-8") if a.modal_log else None
    runs = None
    if a.e2e_runs:
        doc = json.loads(a.e2e_runs.read_text(encoding="utf-8"))
        runs = doc.get("runs") if isinstance(doc, dict) else doc
    waste_usd = None
    if a.agent_waste_results and a.agent_waste_results.is_file():
        try:
            doc = json.loads(a.agent_waste_results.read_text(encoding="utf-8"))
            waste_usd = doc.get("spend_usd_ledger")
        except ValueError:
            waste_usd = None
    snap = build(meta_text=meta_text, baseten_text=baseten_text, e2e_runs=runs,
                 agent_waste_usd=waste_usd, modal_text=modal_text)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
    providers = [p["id"] for p in snap["partners"]]
    print(f"wrote {a.out} ({', '.join(providers)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
