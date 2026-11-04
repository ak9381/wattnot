"""Live Muse Spark call: plan the next setting on the GPU power-cap surface (SIMULATED surface)."""
import sys
from wattnot.meta.client import GuardedClient
from wattnot.meta.agent import next_trial
from wattnot.meta.scenarios import SCENARIOS

sc = SCENARIOS["gpu-power"]
start = sc.start
hist = [{"setting": start, "result": {k: v for k, v in sc.run(start).items() if k != "passed"} | {"passed": sc.run(start)["passed"]}}]
print(f"goal:   {sc.goal}")
print(f"knobs:  power_cap_w in {sc.knobs['power_cap_w']}")
print(f"tried:  {start}  (surface SIMULATED)")
print("asking Muse Spark (muse-spark-1.3, live) for the next setting ...", flush=True)
try:
    d = next_trial(GuardedClient(), sc.prompt_view(), hist)
except Exception as e:  # report the true error
    print(f"Muse Spark call FAILED: {type(e).__name__}: {e}"); sys.exit(1)
u = d.get("usage") or {}
print(f"\n  Spark says: {d['kind'].upper()} {d['knob']} = {d['value']}")
print(f"  why: {d['reason']}")
print(f"  tokens in/out: {u.get('input_tokens', u.get('prompt_tokens'))}/{u.get('output_tokens', u.get('completion_tokens'))}  [LIVE call]")
