"""Live SAM 3.1 call boxes the racks, then the R01 cap goes in as a proposal that waits for a human OK."""
import sys, tempfile
from pathlib import Path
from wattnot.sam import room
from wattnot.sam.client import SamClient
from wattnot.approvals import Approvals, model as m

print("SAM 3.1 (live): prompt 'server rack' on the room photo ...", flush=True)
out = Path(tempfile.mkdtemp(prefix="wn-room-"))
try:
    scan = room.build_scan("arsat", live_sam=SamClient(), out=out)
except Exception as e:
    print(f"SAM 3.1 call FAILED: {type(e).__name__}: {e}"); sys.exit(1)
pub = room.public(scan)
racks = pub["racks"]
print(f"\n  SAM boxed {len(racks)} racks  [MEASURED outlines; ids, GPUs and power SIMULATED]")
for r in racks:
    print(f"   {r['id']}  box {r.get('box') or r.get('bbox')}  -> {r['decision']}"
          + (f" to {r['cap_w']} W" if r.get('cap_w') else ""))
tgt = next((r for r in racks if r["id"] == "R01"), racks[0])
cap = tgt.get("cap_w") or 600
ap = Approvals(out / "proposals")
p = ap.propose({"kind": m.POWER_CAP, "title": f"{tgt['id']}: cap GPUs at {cap} W",
                "summary": f"SAM boxed {tgt['id']}; cap its GPUs", "change": {"target": tgt["id"],
                "before": {"power_cap_w": 700}, "after": {"power_cap_w": cap}, "changed": ["power_cap_w"]},
                "savings": [{"metric": "rack power", "value": 0.0, "unit": "%", "label": "SIMULATED"}]},
               auto=False)
print(f"\n  {tgt['id']}  cap {cap} W   status: {p['status'].upper()} -> waiting for OK (human approval)")
print(f"  proposal {p['id']}  hash {p['content_sha256'][:12]}  nothing changes until a person approves")
import json
Path("/tmp/wn-take-r01.json").write_text(json.dumps({"rack": tgt["id"], "cap_w": cap, "pid": p["id"], "sha": p["content_sha256"]}))
