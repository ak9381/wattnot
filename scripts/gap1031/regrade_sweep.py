#!/usr/bin/env python3
"""Gap test 6: offline re-grade of the Oct 31 H100 sweep answers (no API, no GPU).

The parser fix is not on fix/vm-toolkit-debs yet, so this uses its own final-answer parser:
strip any <think>...</think> (or text before a lone </think>), then read the number after the
last '####' (fallback: the last number). Math items only: QA answers were all cut (empty), and
code items need execution, which this offline pass does not do. A cut answer (finish_cut, empty
text) stays wrong: the reasoning ate the token budget, so no parser can recover it.
"""
import glob, json, re, sys
from pathlib import Path
from common import OUT, ROOT

RAW = Path("/workspace/hackathon/e2e-glimmer-test/live-1031d/runs/t1-sweep/vm/raw")
gold = {}
for l in open(ROOT / "workloads/proof-v1.gold.jsonl"):
    g = json.loads(l); gold[g["id"]] = g["output"]

def final_answer(text: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "</think>" in t:
        t = t.split("</think>")[-1]
    return t.strip()

def num(t):
    m = re.findall(r"####\s*\$?(-?[\d,]*\.?\d+)", t)
    if not m:
        m = re.findall(r"-?\d[\d,]*\.?\d*", t)
    if not m:
        return None
    try:
        return float(m[-1].replace(",", "").rstrip("."))
    except ValueError:
        return None

out = {}
for f in sorted(RAW.glob("*.jsonl")):
    rs = [json.loads(l) for l in open(f)]
    math = [r for r in rs if r["id"].startswith("math-")]
    fin = [r for r in math if not r["finish_cut"] and r["text"]]
    ok = sum(num(final_answer(r["text"])) == num(gold[r["id"].split("#")[0]]) for r in fin)
    out[f.stem] = {"all_items": len(rs), "cut_all": sum(r["finish_cut"] for r in rs),
                   "cut_by_kind": {k: sum(r["finish_cut"] for r in rs if r["id"].startswith(k))
                                   for k in ("math", "qa", "code")},
                   "math_items": len(math), "math_finished": len(fin), "math_correct": ok,
                   "math_pass_pct_all": round(100 * ok / len(math), 1),
                   "math_pass_pct_finished": round(100 * ok / len(fin), 1) if fin else None,
                   "label": "MEASURED (re-grade of MEASURED H100 outputs)"}
def pooled(prefix):
    v = [x for k, x in out.items() if k.startswith(prefix)]
    c, f, m = sum(x["math_correct"] for x in v), sum(x["math_finished"] for x in v), sum(x["math_items"] for x in v)
    return {"blocks": len(v), "math_pass_pct_all": round(100*c/m, 1), "math_pass_pct_finished": round(100*c/f, 1),
            "finished_share_pct": round(100*f/m, 1)}
res = {"spec": "wattnot.gap-1031.regrade-sweep/1", "source": str(RAW),
       "parser": "own final-answer parser (fix branch had none yet)", "blocks": out,
       "pooled": {"stock_375_pairs": pooled("cap375-stock"), "cap_375": pooled("cap375-cap"),
                  "control_stock": pooled("control-stock")}}
(OUT / "regrade").mkdir(parents=True, exist_ok=True)
(OUT / "regrade" / "sweep.json").write_text(json.dumps(res, indent=1))
print(json.dumps(res["pooled"], indent=1)); print({k: (v["cut_all"], v["cut_by_kind"]) for k, v in out.items() if "cap375" in k})
