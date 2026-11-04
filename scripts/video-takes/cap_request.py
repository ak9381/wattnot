"""Ask WattNot to cap one GPU; the SAM audit decides. Inventory/budget are SIMULATED (rack/audit/fleet.json)."""
import sys
from wattnot.sam import physical as ph
specs, _ = ph.load_fleet("rack/audit/fleet.json")
on = ph.audit_fleet(specs, sam=True)
gpu, cap = int(sys.argv[1]), int(sys.argv[2])
print(f"request: power cap GPU {gpu} -> {cap} W")
try:
    lim = on.require(gpu)
    print(f"\033[1;32mALLOWED\033[0m  ceiling {lim.ceiling_w} W [ESTIMATE]" if hasattr(lim,'ceiling_w') else "\033[1;32mALLOWED\033[0m")
except ph.NotVerified as e:
    print(f"\033[1;31mREFUSED\033[0m  {e}  -> GPU {gpu} left untouched")
