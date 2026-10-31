"""Reasoning-effort knobs for the vision/spoken/probe checks, plus a server capability probe.

Muse Glimmer is served with ``--reasoning-parser muse_glimmer``: it spends tokens thinking before
the answer and returns the thinking in a separate reasoning field (``stop_reason`` 200008 hints a
harmony-style template). The eye-test, spoken-answer and image-acceptance checks do not need that
thinking — they grade only ``message.content`` — so by default they ask the server for the least
reasoning it will give: ``reasoning_effort: "low"``.

Some chat templates also accept ``chat_template_kwargs {"enable_thinking": false}``. That is only
turned on when a startup capability probe against the LIVE server shows the server both ACCEPTS the
field (no HTTP 400) and that it actually reduces the reasoning-token count. The probe runs once in
t0-probe and writes its verdict to a JSON file on the VM; the check clients read that file and apply
whatever the live server honoured. Nothing here grades the reasoning — the checks read content only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Where t0-probe writes the capability verdict and the check clients read it. On the GPU host this
# sits next to the run results; override with WATTNOT_REASONING_CAPS for a local run or a test.
CAPS_ENV = "WATTNOT_REASONING_CAPS"
DEFAULT_CAPS_PATH = "/root/run2/results/reasoning_caps.json"
SPEC = "wattnot.reasoning-caps/1"

# The low-reasoning request field sent by default on every check.
DEFAULT_EFFORT = "low"
ENABLE_THINKING_FALSE = {"enable_thinking": False}
# A cheap text-only request used by the probe (one each way), so the capability check costs almost
# nothing before any measured block runs.
TINY_PROMPT = "Reply with just the number 5."


def caps_path(path: str | os.PathLike | None = None) -> Path:
    return Path(path or os.environ.get(CAPS_ENV) or DEFAULT_CAPS_PATH)


def default_apply() -> dict:
    """What a check sends when no probe verdict is on disk: reasoning_effort low, nothing else."""
    return {"reasoning_effort": DEFAULT_EFFORT}


def load_apply(path: str | os.PathLike | None = None) -> dict:
    """The request knobs the checks should merge in, read from the probe verdict if present.

    Always includes ``reasoning_effort`` (low by default). Adds ``chat_template_kwargs
    {"enable_thinking": false}`` only when the probe recorded that the live server accepted it and
    it reduced the reasoning-token count. A missing/unreadable file falls back to ``default_apply``.
    """
    try:
        caps = json.loads(caps_path(path).read_text())
    except (OSError, ValueError):
        return default_apply()
    apply = dict(caps.get("apply") or {})
    apply.setdefault("reasoning_effort", DEFAULT_EFFORT)
    return apply


def apply_to_body(body: dict, apply: dict | None = None) -> dict:
    """Merge the reasoning knobs into an OpenAI-compatible chat body (in place) and return it."""
    apply = default_apply() if apply is None else apply
    for k in ("reasoning_effort", "chat_template_kwargs"):
        if apply.get(k) is not None:
            body[k] = apply[k]
    return body


def reasoning_tokens(data: dict) -> tuple[int | None, bool]:
    """``(reasoning_tokens, measured)`` from a chat response.

    Uses ``usage.completion_tokens_details.reasoning_tokens`` when the server returns it (MEASURED);
    otherwise estimates from the reasoning text length at ~4 chars/token (ESTIMATE). ``None`` only
    when there is no usage and no reasoning text to estimate from.
    """
    usage = (data.get("usage") or {}) if isinstance(data, dict) else {}
    details = usage.get("completion_tokens_details") or {}
    rt = details.get("reasoning_tokens")
    if isinstance(rt, int):
        return rt, True
    msg = ((data.get("choices") or [{}])[0] or {}).get("message") or {} if isinstance(data, dict) \
        else {}
    text = msg.get("reasoning_content") or msg.get("reasoning") or ""
    return (len(text) // 4, False) if text else (None, False)


def token_share(reasoning_tokens: int, completion_tokens: int, measured: bool) -> dict:
    """The reasoning-token share (reasoning tokens / completion tokens) as a reported field.

    MEASURED when every reply's reasoning count came from server usage, else ESTIMATE. ``share`` is
    None when no completion tokens were seen (never a made-up 0).
    """
    share = round(reasoning_tokens / completion_tokens, 4) if completion_tokens else None
    return {"reasoning_tokens": int(reasoning_tokens),
            "completion_tokens": int(completion_tokens), "share": share,
            "label": "MEASURED" if measured else "ESTIMATE"}


def decide_apply(base_rt: int | None, think_ok: bool, think_rt: int | None) -> dict:
    """reasoning_effort low by default; enable_thinking:false only if accepted AND it reduced the
    reasoning-token count against the knob-free baseline."""
    apply = {"reasoning_effort": DEFAULT_EFFORT}
    if think_ok and think_rt is not None and base_rt is not None and think_rt < base_rt:
        apply["chat_template_kwargs"] = dict(ENABLE_THINKING_FALSE)
    return apply


def _probe_one(send, model: str, apply: dict | None) -> dict:
    body = {"model": model, "max_completion_tokens": 64, "temperature": 0,
            "messages": [{"role": "user", "content": TINY_PROMPT}]}
    if apply is not None:
        apply_to_body(body, apply)
    ok, data, detail = send(body)
    rt, measured = reasoning_tokens(data) if ok and isinstance(data, dict) else (None, False)
    return {"accepted": bool(ok), "reasoning_tokens": rt,
            "label": "MEASURED" if measured else "ESTIMATE", "detail": detail}


def probe_reasoning(send, model: str) -> dict:
    """Send one tiny request each way and record which the server honoured + reasoning counts.

    ``send(body) -> (ok, data, detail)``: ``ok`` False means the server rejected that request (an
    unknown field gives HTTP 400). Returns the capability verdict (also written to the caps file).
    """
    base = _probe_one(send, model, None)
    low = _probe_one(send, model, {"reasoning_effort": DEFAULT_EFFORT})
    think = _probe_one(send, model, {"chat_template_kwargs": dict(ENABLE_THINKING_FALSE)})
    apply = decide_apply(base["reasoning_tokens"], think["accepted"], think["reasoning_tokens"])
    return {"spec": SPEC, "model": model, "apply": apply,
            "probes": {"baseline": base, "reasoning_effort_low": low,
                       "enable_thinking_false": think}}


def write_caps(verdict: dict, path: str | os.PathLike | None = None) -> Path:
    p = caps_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(verdict, indent=2) + "\n")
    return p
