#!/usr/bin/env python3
"""Gap test 5: the 10-image eye test on Muse Wonder (real API, generous token budget).
Same images, questions and grader (wattnot.vision.gate.grade_answers) as the H100 t2-eye block."""
import json, time
from datetime import UTC, datetime
from common import OUT, ROOT, JobClient
from wattnot.vision.assets import load_eye_test
from wattnot.vision.client import ServedVisionAnswerer
from wattnot.vision.gate import grade_answers

MODEL = "muse-wonder-1.0"
job = JobClient(reserve_usd=0.2)
s0 = job.own()
et = load_eye_test(ROOT / "data/vision/eyetest", require_files=True)
ans_obj = ServedVisionAnswerer(ROOT / "data/vision/eyetest", model=MODEL, client=None)
answers, usage = {}, []
for it in et:
    body = ans_obj._body(it); body.pop("max_completion_tokens"); body["max_completion_tokens"] = 8000
    t = time.monotonic()
    r = job(body, purpose="gap-1031-eye-wonder")
    answers[it.id] = str(r["choices"][0]["message"].get("content") or "").strip()
    usage.append({"id": it.id, "latency_s": round(time.monotonic() - t, 2), **(r.get("usage") or {})})
    print(it.id, repr(answers[it.id]), flush=True)
rows = grade_answers(answers, list(et))
res = {"spec": "wattnot.gap-1031.eye-wonder/1", "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "model": MODEL, "images_label": "SYNTHETIC (Muse Image)", "answers_label": "MEASURED",
       "passed_count": sum(r["ok"] for r in rows), "total": len(rows), "rows": rows, "usage": usage,
       "spend_usd": round(job.own() - s0, 4), "spend_label": "MEASURED (Meta spend ledger)"}
(OUT / "eye").mkdir(parents=True, exist_ok=True)
(OUT / "eye" / "wonder.json").write_text(json.dumps(res, indent=1))
print(res["passed_count"], "/", res["total"], "spend", res["spend_usd"])
