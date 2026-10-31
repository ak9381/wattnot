"""Layers 2 and 3: headless coding-agent watchdogs.

Each agent layer is a loop that, every `interval_s`, invokes a configurable agent CLI (env
WATTNOT_WD2_CMD for layer 2, WATTNOT_WD3_CMD for layer 3) and hands it a short prompt. The
prompt tells the agent to run exactly one command — the shared `watchdog check` — and report
its JSON. So the agent is only a trigger; the decision and the delete live in the same
deterministic core every layer shares. Two *different* agent CLIs give two independent triggers
without duplicating the rule.

The command is read from an env var so no AI-vendor binary name is hard-coded. A neutral
WATTNOT_WATCHDOG_CMD is the fallback for both layers.

The loop takes an injectable `runner`, so tests simulate "the agent ran the check" by having
the runner call `check_and_teardown` directly — proving the layer tears down on its own.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable

ENV_BY_LAYER = {2: "WATTNOT_WD2_CMD", 3: "WATTNOT_WD3_CMD"}
FALLBACK_ENV = "WATTNOT_WATCHDOG_CMD"


def resolve_cmd(layer: int, env: dict | None = None) -> str | None:
    """The agent CLI command for a layer, from its env var or the neutral fallback."""
    env = os.environ if env is None else env
    return env.get(ENV_BY_LAYER.get(layer, FALLBACK_ENV)) or env.get(FALLBACK_ENV)


def build_check_command(
    *, heartbeat_path, budget_cap_inr, max_hours, max_stale_s, dry_run: bool,
    python: str = sys.executable or "python3",
) -> list[str]:
    """The exact shared command the agent must run (`watchdog check ...`)."""
    cmd = [python, "-m", "wattnot.longrun.watchdog", "check", "--heartbeat", str(heartbeat_path)]
    if budget_cap_inr is not None:
        cmd += ["--budget-cap-inr", str(budget_cap_inr)]
    if max_hours is not None:
        cmd += ["--max-hours", str(max_hours)]
    if max_stale_s is not None:
        cmd += ["--max-stale-s", str(max_stale_s)]
    if dry_run:
        cmd += ["--dry-run"]
    return cmd


def build_prompt(check_cmd: list[str]) -> str:
    """A tight prompt: run one command, report its JSON, do nothing else."""
    joined = " ".join(shlex.quote(c) for c in check_cmd)
    return (
        "You are a teardown watchdog for a rented GPU VM. Do EXACTLY this and nothing else:\n"
        f"  1. Run this command verbatim:\n     {joined}\n"
        "  2. Print the command's JSON output.\n"
        "Do not launch, modify, or delete anything yourself. The command already performs the\n"
        "check and any teardown. If its JSON shows \"teardown\": true and \"deleted\": true, the\n"
        "VM has been torn down and you are done."
    )


def _subprocess_runner(cmd: str, prompt: str) -> dict:
    """Default live runner: invoke the agent CLI, feed it the prompt, parse its last JSON line.

    `cmd` may contain a `{prompt}` placeholder; otherwise the prompt is sent on stdin.
    """
    argv = shlex.split(cmd)
    stdin = None
    if any("{prompt}" in a for a in argv):
        argv = [a.replace("{prompt}", prompt) for a in argv]
    else:
        stdin = prompt
    try:
        out = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=600)
        text = out.stdout
    except Exception as e:  # noqa: BLE001 - a dead agent must not crash the watchdog
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "teardown": False,
                "deleted": None}
    parsed = _last_json(text)
    return parsed if parsed is not None else {"ok": True, "raw": text, "teardown": False,
                                              "deleted": None}


def _last_json(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def _done(result: dict) -> bool:
    """Stop the loop once the shared check reports a completed teardown."""
    return bool(result.get("teardown")) and (result.get("deleted") is True
                                              or result.get("dry_run"))


def run_agent_watchdog(
    *,
    cmd: str,
    check_cmd: list[str],
    interval_s: float = 180.0,
    runner: Callable[[str, str], dict] = _subprocess_runner,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    max_ticks: int | None = None,
    is_done: Callable[[dict], bool] = _done,
    label: str = "agent",
    log: Callable[[str], None] = print,
) -> dict:
    """Trigger the agent every `interval_s` until the shared check reports teardown done.

    `runner(cmd, prompt)` runs the agent CLI and returns the JSON the check printed. Tests pass
    a runner that calls `check_and_teardown` directly to simulate the agent executing it.
    """
    prompt = build_prompt(check_cmd)
    tick = 0
    last: dict = {}
    while True:
        tick += 1
        last = runner(cmd, prompt)
        log(json.dumps({"layer": label, "tick": tick, "result": last}))
        if is_done(last):
            return last
        if max_ticks is not None and tick >= max_ticks:
            return last
        sleep(interval_s)


def _main(argv=None) -> int:
    import argparse

    from .core import DEFAULT_MAX_STALE_S

    ap = argparse.ArgumentParser(description="Layer 2/3 agent watchdog loop")
    ap.add_argument("--layer", type=int, choices=(2, 3), required=True)
    ap.add_argument("--heartbeat", required=True)
    ap.add_argument("--budget-cap-inr", type=float, default=None)
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--max-stale-s", type=float, default=DEFAULT_MAX_STALE_S)
    ap.add_argument("--interval-s", type=float, default=180.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cmd = resolve_cmd(a.layer)
    if not cmd:
        env = ENV_BY_LAYER[a.layer]
        print(f"no agent command set: export {env} (or {FALLBACK_ENV})")
        return 2
    check_cmd = build_check_command(
        heartbeat_path=a.heartbeat, budget_cap_inr=a.budget_cap_inr, max_hours=a.max_hours,
        max_stale_s=a.max_stale_s, dry_run=a.dry_run,
    )
    res = run_agent_watchdog(cmd=cmd, check_cmd=check_cmd, interval_s=a.interval_s,
                             label=f"layer{a.layer}-agent")
    return 0 if res.get("teardown") else 0


if __name__ == "__main__":
    raise SystemExit(_main())
