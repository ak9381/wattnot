"""One LIVE SAM 3.1 call for 'hotspot sets the cap' on the real (public, CC BY 2.0) rack photo.

Honest scope: the segmentation is LIVE. The photo is a normal rack photo, not a thermal image,
so which slot is "hot" is SIMULATED (demo slot-to-GPU mapping, see rack/real/ATTRIBUTION.md).
"""
import hashlib, json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import wattnot.sam.client as sc
from wattnot.meta.ledger import SpendLedger
from wattnot.sam.hotspot_cap import plan_hotspot_cap

seen = {}
_orig = sc._urlopen
def cap_open(req, timeout):
    st, raw = _orig(req, timeout)
    seen["status"] = st
    try:
        d = json.loads(raw); seen["response_id"] = d.get("id"); seen["usage"] = d.get("usage"); seen["model"] = d.get("model")
        seen["raw_text"] = sc.output_text(d)
    except ValueError:
        pass
    return st, raw
sc._urlopen = cap_open
# SamClient binds the opener at init from the module default argument; patch the class default too
_init = sc.SamClient.__init__
def init(self, *a, **k):
    k.setdefault("opener", cap_open); _init(self, *a, **k)
sc.SamClient.__init__ = init

photo = ROOT / "rack/real/rack.jpg"
led = SpendLedger(); before = led.summary().get("total_usd")
import wattnot.sam.hotspot_cap as hc
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "server"
_d = hc.detect_hotspot_mask
hc.detect_hotspot_mask = lambda *a, **k: _d(*a, **{**k, "prompt": PROMPT})
plan = plan_hotspot_cap(photo_path=photo, target_gpu=1, mock=False, live=True, ledger=led)
after = led.summary().get("total_usd")
doc = {"section": "sam_hotspot_live", "label": "LIVE",
       "labels": {"segmentation": plan["mask"]["label"], "heat_attribution": "SIMULATED", "cap_decision": "ADVICE"},
       "note": "Real SAM 3.1 call on a public rack photo (CC BY 2.0). Not a thermal image: the hot slot is SIMULATED; the cap change is advice, never applied (no GPU).",
       "photo": {"path": "rack/real/rack.jpg", "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()},
       "prompt": PROMPT, "earlier_attempt": "prompt 'thermal hotspot rack slot' returned no boxes (photo is not thermal)", "sam": {"model": seen.get("model") or "sam-3.1",
       "response_id": seen.get("response_id"), "http_status": seen.get("status"), "usage": seen.get("usage"),
       "raw_output": seen.get("raw_text")}, "mask": plan["mask"], "decision": plan["decision"],
       "spend_usd": round((after or 0) - (before or 0), 6), "ledger_total_usd": after,
       "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
(ROOT / "receipts/run2/sam_hotspot_live.json").write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps({k: doc[k] for k in ("labels", "sam", "mask", "spend_usd", "ledger_total_usd")}, indent=1)[:2500])
