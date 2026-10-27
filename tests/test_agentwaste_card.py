"""The Agent waste results card: trimmed benchmark numbers for the dashboard."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from wattnot.agentwaste.card import card, load_results

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "agent-waste" / "results.json"


def _mini() -> dict:
    arm = lambda runs, ok, pct, cpr, tpr, wall, usd, j: {  # noqa: E731
        "runs": runs, "success": ok, "success_pct": pct, "calls": cpr * runs,
        "calls_per_run": cpr, "total_tokens": tpr * runs, "tokens_per_run": tpr,
        "wall_s_per_run": wall, "usd": usd * runs, "usd_per_run": usd,
        "joules": j * runs}
    g = lambda cpr, ok: {"runs": 2, "success": ok, "calls_per_run": cpr,  # noqa: E731
                         "success_pct": 50.0 * ok}
    return {
        "schema": "wattnot.agent-waste/1",
        "arms": {
            "loop-off": {"all": arm(4, 3, 75.0, 12.0, 23000, 19.0, 0.019, 320.0),
                         "work": g(11.0, 1), "early": g(12.0, 0), "no_work": g(13.0, 1)},
            "loop-on": {"all": arm(4, 4, 100.0, 3.5, 5000, 2.8, 0.004, 53.0),
                        "work": g(3.5, 2), "early": g(3.0, 1), "no_work": g(4.0, 1)},
            "auto-ref": {"all": arm(4, 4, 100.0, 3.4, 4900, 2.7, 0.004, 51.0),
                         "work": g(3.8, 2), "early": g(2.8, 2), "no_work": g(3.6, 2)},
        },
        "on_vs_off": {"all": {"calls": -70.8, "total_tokens": -78.3, "wall_s_per_run": -85.3,
                              "usd": -78.9, "joules": -83.4}},
        "spend_usd_ledger": 1.23, "generated": "2026-10-27T00:00:00Z",
    }


def test_card_keeps_only_the_trimmed_view():
    c = card(_mini())
    assert c["partner"] == {"id": "meta", "name": "Meta Muse Spark"}
    assert "12.0" in c["headline"] and "3.5" in c["headline"] and "4 of 4" in c["headline"]
    assert set(c["rows"]) == {"calls per run", "tokens per run", "wall time per run (s)",
                              "energy per run (J)", "cost per run ($)"}
    assert c["rows"]["calls per run"] == {"off": 12.0, "on": 3.5, "change_pct": -70.8,
                                                  "tag": "MEASURED"}
    assert c["rows"]["energy per run (J)"]["tag"] == "ESTIMATE"
    assert c["rows"]["cost per run ($)"]["tag"] == "ESTIMATE"
    assert c["success"] == {"off_pct": 75.0, "on_pct": 100.0, "auto_pct": 100.0, "runs_per_arm": 4}
    assert c["groups"]["work"]["calls_on"] == 3.5
    assert c["reference"] == {"calls_per_run": 3.4, "success_pct": 100.0}
    assert len(c["notes"]) == 3
    assert c["spend_usd"] == 1.23 and c["generated"] == "2026-10-27T00:00:00Z"


def test_card_divides_totals_by_the_run_count():
    r = _mini()
    del r["arms"]["loop-on"]["all"]["tokens_per_run"]
    del r["arms"]["loop-on"]["all"]["usd_per_run"]
    del r["arms"]["loop-on"]["all"]["joules"]
    c = card(r)
    assert c["rows"]["tokens per run"]["on"] == 5000
    assert c["rows"]["cost per run ($)"]["on"] == 0.004
    assert c["rows"]["energy per run (J)"]["on"] is None


def test_load_results_needs_the_schema_and_real_file(tmp_path=None):
    assert load_results(REAL) is not None or not REAL.is_file()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "r.json"
        p.write_text('{"schema": "other/1"}')
        assert load_results(p) is None
        p.write_text("not json")
        assert load_results(p) is None
        assert load_results(Path(td) / "missing.json") is None


def test_real_file_card_labels_and_headline():
    if not REAL.is_file():
        return
    d = json.loads(REAL.read_text())
    c = card(load_results())
    off, on = d["arms"]["loop-off"]["all"], d["arms"]["loop-on"]["all"]
    assert str(off["calls_per_run"]) in c["headline"] and str(on["calls_per_run"]) in c["headline"]
    assert f"{on['success']} of {on['runs']}" in c["headline"]
    assert c["rows"]["calls per run"]["change_pct"] == d["on_vs_off"]["all"]["calls"]
    assert c["rows"]["tokens per run"]["change_pct"] == d["on_vs_off"]["all"]["total_tokens"]
    for k in ("calls per run", "tokens per run", "wall time per run (s)"):
        assert c["rows"][k]["tag"] == "MEASURED"
    for k in ("energy per run (J)", "cost per run ($)"):
        assert c["rows"][k]["tag"] == "ESTIMATE"
    assert c["success"]["runs_per_arm"] == 48
    assert set(c["groups"]) == {"work", "early", "no_work"}
