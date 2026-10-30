"""Second-round review fixes: driver clock anchor, EU zero pass rate, early-stop exit code."""
import copy
import json
from pathlib import Path

from wattnot.longrun import driver
from wattnot.report import eu

FIX = Path(__file__).parent / "fixtures" / "proof-receipt-rehearsal.json"


def test_vm_launch_wall_reads_ctl_record(tmp_path):
    p = tmp_path / "vm.json"
    p.write_text(json.dumps({"id": 1, "launch_ts": "2026-10-30T10:00:00"}))
    import datetime as dt
    assert driver.vm_launch_wall(p) == dt.datetime(2026, 10, 30, 10).timestamp()
    assert driver.vm_launch_wall(tmp_path / "missing.json") is None


def test_zero_pass_rate_is_reported_not_hidden():
    r = json.loads(FIX.read_text())
    r = copy.deepcopy(r)
    for s in r["steps"]:
        s["quality"]["cap_pct"] = 0.0
        s["quality"]["stock_pct"] = 0.0
    from wattnot.receipt.canon import canonical_sha256
    r["integrity"] = {"sha256": None, "signer": None, "rekor": None}
    r["integrity"]["sha256"] = canonical_sha256(r)
    f = {x["field"]: x for x in eu.proof_fields(r)}
    assert f["joules_per_correct_answer"]["label"] == "NOT_AVAILABLE"
    assert "0%" in str(f["joules_per_correct_answer"])
