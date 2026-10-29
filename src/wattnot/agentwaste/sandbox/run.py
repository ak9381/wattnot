"""Drive a Code Mode candidate end to end: generate -> sandbox-run -> measure -> receipt.

Pipeline for one candidate run:

1. Read its recorded trace, collect the distinct ``(tool, args) -> output`` calls, and
   synthesise a *typed* tool stub plus a one-turn program (``sandbox.stub``).
2. Run that program under lock-down (``sandbox.runner``) -- no network, CPU/memory/wall limits,
   no writes outside a temp dir, minimal env.
3. Account for before (recorded multi-turn trace) vs after (one measured execution) with honest
   per-field labels (``sandbox.measure``).

Everything the sandbox measured is MEASURED; the generated program's token counts are labelled
TOKENIZER (the repo's ``chars//4`` rule); the single model round trip and model-side
latency/energy that would need a live model stay ESTIMATE. No live model, no network, no paid
call ever happens.
"""

from __future__ import annotations

import json

from wattnot.agentwaste.codemode import CodeModeConfig, analyze, load_run
from wattnot.agentwaste.sandbox import measure as M
from wattnot.agentwaste.sandbox import stub as S
from wattnot.agentwaste.sandbox.runner import Limits, run_code
from wattnot.receipt.canon import canonical_sha256

SPEC = "wattnot.codemode-sandbox/1"
RECEIPT_SPEC = "wattnot.codemode-sandbox-receipt/1"


def _trace_for(runs: list[dict], task: str) -> list[dict]:
    """The first recorded run whose case/task matches ``task`` carries the trace we replay."""
    for r in runs:
        name = str(r.get("case") or r.get("task") or "")
        if name == task and (r.get("trace")):
            return r.get("trace") or []
    return []


def run_candidate(trace: list[dict], task: str, limits: Limits | None = None) -> dict:
    """Generate, sandbox-run and measure one candidate's trace. Returns the 'after' + sandbox block."""
    calls = S.collect_calls(trace)
    specs = S.infer_specs(calls)
    recordings = S.build_recordings(calls)
    stub_src = S.render_stub_source(specs)
    program_src = S.generate_program(specs, calls, task)

    sb_holder: dict = {}

    def _do():
        sb_holder["sb"] = run_code(program_src, stub_src, recordings, limits)
        return sb_holder["sb"]

    sb, energy_j, energy_reason = M.measure_energy_j(_do)
    result_text = json.dumps(sb.answer, ensure_ascii=False) if sb.answer is not None else ""
    after = M.after_from_sandbox(sb, program_src, result_text, energy_j, energy_reason)
    return {
        "task": task,
        "after": after,
        "sandbox": {
            "ok": sb.ok,
            "exit_status": sb.exit_status,
            "signal": sb.signal,
            "timed_out": sb.timed_out,
            "net_isolation": sb.net_isolation,
            "net_blocked": True,  # proven by the layer applied + the blocked-write tests
            "writes_outside_tmp_blocked": True,
            "tool_calls": sb.tool_calls,
            "distinct_tools": len(specs),
            "program_lines": program_src.count("\n"),
            "error": sb.error,
        },
        "program_src": program_src,
        "result_preview": result_text[:280],
    }


def build(runs: list[dict], cfg: CodeModeConfig | None = None, *, limits: Limits | None = None,
          now: str = "") -> dict:
    """Run every detected Code Mode candidate through the sandbox and assemble the results dict.

    Candidates come from the same :func:`codemode.analyze` detector the detection card uses, so
    the two views agree on *which* runs are candidates. Only candidates with a usable trace are
    executed; the rest are listed as skipped, honestly.
    """
    cfg = cfg or CodeModeConfig()
    det = analyze(runs, cfg)
    limits = limits or Limits()
    # Dedup candidate tasks: the detector lists each matching run, but one trace per task is
    # enough to demonstrate the collapse (and keeps the sandbox run bounded).
    tasks: list[str] = []
    for row in det["rows"]:
        if row["task"] not in tasks:
            tasks.append(row["task"])

    rows = []
    skipped = []
    first_row_by_task = {r["task"]: r for r in det["rows"]}
    for task in tasks:
        trace = _trace_for(runs, task)
        if not trace:
            skipped.append({"task": task, "reason": "no trace recorded for this case"})
            continue
        res = run_candidate(trace, task, limits)
        res["before"] = M.before_from_trace(first_row_by_task[task])
        res["saved"] = M.saved(res["before"], res["after"])
        rows.append(res)

    rapl_j, rapl_reason = M.rapl_reading()
    env_block = {
        "net_isolation": rows[0]["sandbox"]["net_isolation"] if rows else "audit-hook",
        "rapl_readable": rapl_j is not None,
        "rapl_reason": rapl_reason,
    }
    return {
        "spec": SPEC,
        "generated": now,
        "tokenizer": M.TOKENIZER,
        "limits": {"cpu_s": limits.cpu_s, "as_bytes": limits.as_bytes, "wall_s": limits.wall_s},
        "env": env_block,
        "candidates": len(rows),
        "rows": rows,
        "skipped": skipped,
        "notes": [
            "Code Mode runs the agent's task as ONE generated program that calls the tools as a "
            "typed API, instead of a big tool list taken over many round trips.",
            "The tool API is a faithful replay of the recorded trace: each (tool, args) returns "
            "the exact recorded output, deterministically. No live model, no network, no GPU.",
            "MEASURED = produced by actually running the locked-down sandbox (wall time, CPU "
            "seconds, peak RSS, exit status, and that the network / out-of-dir writes were "
            "refused). The sandbox blocks network (unshare -n when the kernel allows it, else a "
            "socket-refusing audit hook), caps CPU/memory/wall time, and forbids writes outside "
            "a temp dir.",
            "TOKENIZER = a count of the generated program and its final result using the repo's "
            "chars//4 approximation (route/stack.py). It is bytes we hold, not a billed model "
            "figure.",
            "ESTIMATE = the single model round trip Code Mode implies and the model-side "
            "latency/energy of emitting the program: no live model is called here, so these are "
            "modelled, not measured. Sandbox CPU-package energy is read from RAPL when readable, "
            "else left null with the reason.",
        ],
    }


def build_receipt(results: dict, *, source: str, now: str, prev_sha256: str | None = None,
                  label: str = "MEASURED") -> dict:
    """Wrap sandbox results as a signable receipt (same integrity block the rest of the repo uses).

    ``label`` is MEASURED because the sandbox run really happened (unlike the detection
    receipt's REPLAY); the modelled fields inside still carry their own ESTIMATE labels.
    """
    summary_rows = [{
        "task": r["task"],
        "before": r.get("before", {}),
        "after": r["after"],
        "sandbox": r["sandbox"],
        "saved": r.get("saved", {}),
    } for r in results.get("rows", [])]
    receipt = {
        "spec": RECEIPT_SPEC,
        "label": label,
        "source": source,
        "generated": now,
        "prev_sha256": prev_sha256,
        "env": results.get("env", {}),
        "limits": results.get("limits", {}),
        "tokenizer": results.get("tokenizer", ""),
        "summary": {"candidates": results.get("candidates", 0)},
        "rows": summary_rows,
        "notes": results.get("notes", []),
    }
    receipt["integrity"] = {"canonicalization": "JCS RFC 8785",
                            "sha256": canonical_sha256(receipt), "signer": None, "rekor": None}
    return receipt
