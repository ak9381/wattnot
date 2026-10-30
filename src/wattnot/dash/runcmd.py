"""The Configure screen's "Command for this run" panel: selection -> shell commands.

Pure text builder. It turns the dashboard selection (model, levers, limits)
into the exact argv lists for `python -m wattnot.proof.run` and, for the
whole room-to-receipt flow, `python -m wattnot.proof.orchestrate`, each as a
list plus a shell-quoted string, with plain-words notes. It never executes
anything; the dashboard endpoint returns these strings for a person to copy
onto an H100 host.

Only flags that exist in those modules' argparse parsers are emitted.
"""

from __future__ import annotations

import shlex

# Dashboard model id -> served repo id (README, src/wattnot/models.py).
MODELS = {
    "glimmer": "meta-models/Muse-Glimmer-30B",
    "llama": "meta-llama/Llama-3.1-8B-Instruct",
}
# Dashboard model id -> adaptive-trial repo id (the argparse default in proof/run.py).
ADAPTIVE_MODELS = {
    "granite": "ibm-granite/granite-3.1-3b-a800m-instruct",
}
CAP_MODELS = frozenset(MODELS)
TOPK_MODELS = frozenset(ADAPTIVE_MODELS)
LEVERS = ("cap", "topk")
WORKLOADS = ("coding", "moe", "classes")
HOST = "<h100-host>"  # placeholder: the GPU host's address goes here


def _limits(selection: dict) -> tuple[float, float, bool]:
    try:
        p99 = float(selection.get("p99_slack_pct", 10))
        qual = float(selection.get("min_quality_pct", 95))
    except (TypeError, ValueError):
        raise ValueError("limits must be numbers") from None
    if not 0 <= p99 <= 100:
        raise ValueError("p99_slack_pct must be between 0 and 100")
    if not 0 <= qual <= 100:
        raise ValueError("min_quality_pct must be between 0 and 100")
    return p99, qual, bool(selection.get("strict_quality", False))


def command_for(selection: dict) -> dict:
    """Build the shell commands for a Configure-screen selection. Raises ValueError."""
    if not isinstance(selection, dict):
        raise ValueError("selection must be an object")
    model = selection.get("model")
    if model == "qwen3":
        raise ValueError("model 'qwen3' has no recorded repo id for these commands")
    if model == "spark_open":
        raise ValueError("Muse Spark Open runs on a hosted API, not these commands")
    if model not in CAP_MODELS and model not in TOPK_MODELS:
        raise ValueError(f"unknown model {model!r}")
    levers = selection.get("levers")
    if isinstance(levers, (str, bytes)) or not isinstance(levers, (list, tuple, set)):
        raise ValueError("levers must be a list")
    levers = list(levers)
    if not levers:
        raise ValueError("pick at least one lever")
    for lv in levers:
        if lv not in LEVERS:
            raise ValueError(f"unknown lever {lv!r}")
        if lv == "cap" and model not in CAP_MODELS:
            raise ValueError(f"model {model!r} does not serve a cap sweep")
        if lv == "topk" and model not in TOPK_MODELS:
            raise ValueError(f"model {model!r} has no recorded adaptive trial")
    place = selection.get("place", "h100")
    if place != "h100":
        raise ValueError("only the H100 run has shell commands")
    workload = selection.get("workload", "coding")
    if workload not in WORKLOADS:
        raise ValueError(f"unknown workload {workload!r}")
    p99, qual, _strict = _limits(selection)
    out = selection.get("out")
    if out is not None and (not isinstance(out, str) or not out.strip()):
        raise ValueError("out must be a folder path")

    if "cap" in levers:
        run_argv = ["python", "-m", "wattnot.proof.run", "full",
                    "--model", MODELS[model], "--planner", "muse"]
        run_what = (f"Runs the full power-cap sweep of {MODELS[model]} on one H100: "
                    "gate check, planner caps, grid fill, report.")
    else:
        run_argv = ["python", "-m", "wattnot.proof.run", "adaptive",
                    "--adaptive-model", ADAPTIVE_MODELS[model]]
        run_what = (f"Runs the adaptive top-k trial of {ADAPTIVE_MODELS[model]} on one H100: "
                    "normal vs adaptive arms, agreement and p99 checks.")
    flow_argv = ["python", "-m", "wattnot.proof.orchestrate",
                 "--mode", "live", "--host", HOST,
                 "--planner", "muse", "--sam", "replay"]
    if model in MODELS:
        flow_argv += ["--model", MODELS[model]]
    if "topk" in levers:
        flow_argv += ["--adaptive-model", ADAPTIVE_MODELS[model]]
    else:
        flow_argv += ["--no-adaptive"]
    if out is not None:
        run_argv += ["--out", out]
        flow_argv += ["--out", out]
    notes = [
        run_what,
        ("Runs the whole room-to-receipt flow from the control machine: room scan, "
         "baseline, quality gate, sweep, keep or roll back, signed receipt."),
        f"Needs an H100 host: replace {HOST} with its address.",
        "The dashboard never starts it. Copy the command onto the host and run it there.",
        "Start the model server first, or add --serve to the first command.",
        ("--planner muse uses a Meta key when one is on the host, "
         "else a plain bisect rule."),
        (f"Your limits (slowest 1% may rise {p99:g}%, agreement at least {qual:g}%) are "
         "checked when the dashboard replays the recorded run. The proof's own gates "
         "decide what is kept."),
    ]
    return {"run": {"argv": run_argv, "shell": shlex.join(run_argv)},
            "flow": {"argv": flow_argv, "shell": shlex.join(flow_argv)},
            "notes": notes}
