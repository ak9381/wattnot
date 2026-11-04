"""Server-side levers on a self-hosted vLLM (FP8 weights, FP8 KV cache / gpu_memory_utilization,
batching / max_num_seqs), one at a time, each with the live answer check, MEASURED.

The three bugs this guards against (seen in the gateway on the H100, 3 Nov):
* stacked settings: a trial changes exactly ONE lever from stock; anything else is refused.
* kept without a check: a setting is kept only if AnswerCheck accepts it against stock answers
  (same answers, lower J/answer, p99 within the limit). No check given means nothing is kept.
* settings that never applied: after the restart, the running server's argv must contain the new
  flags and /v1/models must answer; otherwise it is "not applied" and stock is restored.
Any error restores the stock server and re-raises.
"""
from __future__ import annotations

import dataclasses
import time

from wattnot.runner.vllm_server import TrialParams, server_args

# lever -> (TrialParams field, candidate value). Power cap lives in node/agent.py.
LEVERS = {
    "fp8": ("quantization", "fp8"),
    "kv_cache": ("kv_cache_dtype", "fp8"),
    "kv_mem": ("gpu_mem_util", 0.95),
    "batching": ("max_num_seqs", 512),
}


def changed_fields(stock: TrialParams, trial: TrialParams) -> list[str]:
    return [f.name for f in dataclasses.fields(TrialParams) if getattr(stock, f.name) != getattr(trial, f.name)]


def flags_present(argv: list[str], want: list[str]) -> list[str]:
    """Flags (and their values) from `want` that are missing in the running argv."""
    missing, i = [], 0
    while i < len(want):
        a = want[i]
        if a.startswith("--") and i + 1 < len(want) and not want[i + 1].startswith("--"):
            pair, i = (a, want[i + 1]), i + 2
            ok = any(argv[j] == pair[0] and j + 1 < len(argv) and argv[j + 1] == pair[1] for j in range(len(argv))) \
                or f"{pair[0]}={pair[1]}" in argv
            if not ok:
                missing.append(f"{pair[0]} {pair[1]}")
        else:
            i += 1
            if a.startswith("--") and a not in argv:
                missing.append(a)
    return missing


def try_lever(lever: str, *, model: str, stock: TrialParams, restart, running_argv, healthy,
              check=None, log=None, value=None, wait_s: float = 900, sleep=time.sleep) -> dict:
    """restart(argv) relaunches vLLM; running_argv() -> argv of the live server process;
    healthy() -> True once /v1/models answers; check: an AnswerCheck whose .ref is the stock round."""
    log = log or (lambda **kw: None)
    field, default = LEVERS[lever]
    trial = dataclasses.replace(stock, **{field: default if value is None else value})
    diff = changed_fields(stock, trial)
    if len(diff) != 1:
        log(step="refused", lever=lever, why=f"changes {diff}: one setting at a time")
        return {"lever": lever, "kept": False, "why": "stacked settings refused"}
    if check is None:
        log(step="refused", lever=lever, why="no live answer check given: stays at stock")
        return {"lever": lever, "kept": False, "why": "no answer check"}
    stock_argv, want = server_args(model, stock), server_args(model, trial)

    def up(argv) -> bool:
        restart(argv)
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait_s:
            if healthy():
                return True
            sleep(5)
        return False

    try:
        if check.ref is None:
            check.stock()
        ok = up(want)
        missing = flags_present(list(running_argv() or []), want[3:]) if ok else ["server did not come up"]
        if missing:
            log(step="not_applied", lever=lever, missing=missing)
            up(stock_argv)
            return {"lever": lever, "kept": False, "why": f"not applied: {missing}"}
        log(step="applied", lever=lever, field=field, value=getattr(trial, field), argv=want[3:])
        st = check.at(lever)
        log(step="answer_check", lever=lever, **st)
        if not st["accepted"]:
            up(stock_argv)
            log(step="rolled_back", lever=lever, failed=st["why_not"])
            return {"lever": lever, "kept": False, "why": "; ".join(st["why_not"]), "check": st}
        up(stock_argv)   # one at a time: every lever is judged against stock, never stacked
        log(step="passed", lever=lever, energy_change_pct=st["energy_change_pct"], label=st["label"])
        return {"lever": lever, "kept": True, "check": st, "label": st["label"]}
    except BaseException:
        up(stock_argv)
        log(step="rolled_back", lever=lever, failed=["error during the trial"])
        raise
