import json, sys
sys.path.insert(0, "/workspace/wn-meta6/src")
from wattnot.musecode.apply_pr import apply_pr
r = json.load(open("/workspace/wn-meta6/receipts/run2/rcpt_20261027_run2_cap350.json"))
sha = r["integrity"]["sha256"]
change = {"power_cap_w": 350, "saving_pct": 27.09, "p99_delta_ms": 277,
          "receipt_url": "https://ak9381.github.io/wattnot/dash/report.html?run=run2-llama-350w"}
res = apply_pr(change, "/workspace/hackathon/meta6/demo2", receipt_sha256=sha,
               out_dir="/workspace/hackathon/meta6/apply_out2",
               labels={"power_cap_w": "MEASURED", "saving_pct": "MEASURED", "p99_delta": "MEASURED"},
               mock=False, open_pr=True)
print(json.dumps({"pr_url": res.pr_url, "branch": res.branch_name, "session": res.session_id,
                  "trace": res.trace_id, "usage": res.usage, "patch_sha256": res.patch_sha256}, indent=1))
# log Muse Code spend in the Meta ledger (muse-spark-1.3-contributor prices)
from wattnot.meta.ledger import SpendLedger
led = SpendLedger(); u = res.usage or {}
eid, _ = led.reserve({"model": "muse-spark-1.3-contributor", "max_tokens": 1, "messages": []}, purpose="muse-code:apply-pr-live")
led.settle(eid, {"prompt_tokens": u.get("input_tokens") or 0, "completion_tokens": u.get("output_tokens") or 0},
           model="muse-spark-1.3-contributor", status="ok")
print("ledger", led.summary().get("total_usd"))
