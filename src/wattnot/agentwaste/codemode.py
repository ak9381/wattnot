"""Code Mode detection for the agent-waste layer.

Code Mode is the pattern where, instead of exposing a large tool list and taking many
tool-call round trips, the agent is handed the tools as an *API* and writes one short
program that calls them. The tool schemas are sent to the model once (as an API surface),
and the many request/response round trips collapse into a single program turn.

Prior art (links in docs/codemode.md): Cloudflare's "Code Mode" write-up and the Model
Context Protocol "code execution with MCP" notes. There is **no sandbox runtime here**: the
"after" numbers are an explicit model, not a measured run, and are labelled ESTIMATE.

Honest labels, the same vocabulary the rest of the layer uses:

* ``MEASURED`` - read straight from the recorded trace: round trips, billed tokens, wall
  time, the distinct tools the run actually called, and joules when the trace recorded them.
* ``ESTIMATE`` - anything modelled: the per-tool schema token size, the collapsed "after"
  round trips and tokens, and cost/energy derived from assumed rates.

A number we cannot produce honestly is ``None`` (pending), never invented. The dashboard
card draws pending values dimmed with a dot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wattnot.agentwaste.bench.summary import MODEL, j_per_token
from wattnot.meta.ledger import cost_usd
from wattnot.receipt.canon import canonical_sha256

RECEIPT_SPEC = "wattnot.codemode-receipt/1"


@dataclass(frozen=True)
class CodeModeConfig:
    """Thresholds and modelling knobs for Code Mode detection. All configurable.

    ``min_tools`` / ``min_round_trips`` decide when a run is flagged a Code Mode candidate.
    The rest shape the modelled "after"; every number derived from them is an ESTIMATE.
    """

    min_tools: int = 5               # a tool list this size or larger is "big"
    min_round_trips: int = 6         # this many tool-call round trips or more is "many"
    schema_tokens_per_tool: int = 120  # ESTIMATE: average JSON-schema tokens for one tool
    after_round_trips: int = 1       # ESTIMATE: Code Mode collapses to ~this many program turns
    model: str = MODEL               # price book key for the dollar estimate

    @classmethod
    def from_dict(cls, d: dict) -> CodeModeConfig:
        """Build from a dict. Unknown keys raise ValueError (same contract as WasteConfig)."""
        known = set(cls.__dataclass_fields__)
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown CodeModeConfig keys: {sorted(unknown)}")
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class RunTrace:
    """One recorded agent run, trimmed to what Code Mode detection needs.

    ``tools_offered`` is the size of the tool list the agent was given, when the trace
    recorded it (MEASURED). When it did not, it is None and we fall back to ``tools_seen``
    (the distinct tools actually called) as a floor.
    """

    task: str
    round_trips: int               # assistant turns that made >= 1 tool call (MEASURED)
    tools_seen: int                # distinct tool names called in this run (MEASURED)
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int = 0
    wall_s: float | None = None
    joules: float | None = None    # MEASURED only if the trace carried a joule reading
    tools_offered: int | None = None

    @property
    def tools(self) -> int:
        """The tool-list size to reason about: the offered list, or the seen floor."""
        return self.tools_offered if self.tools_offered is not None else self.tools_seen

    @property
    def tools_measured(self) -> bool:
        """True when the tool count is the real offered list, not just the seen floor."""
        return self.tools_offered is not None


def load_run(d: dict) -> RunTrace:
    """Parse one recorded run (a line of the agent-waste runs.jsonl) into a RunTrace.

    Round trips and distinct tools come from the ``trace`` field (per-step ``calls``). Token
    and time counts are read as billed. ``joules`` is only set if the trace recorded it, so
    energy stays honest (MEASURED vs modelled).
    """
    trace = d.get("trace") or []
    tools: set[str] = set()
    round_trips = 0
    for step in trace:
        calls = step.get("calls") or []
        if calls:
            round_trips += 1
        for call in calls:
            if call:
                tools.add(call[0])
    prompt = int(d.get("prompt_tokens") or 0)
    completion = int(d.get("completion_tokens") or 0)
    total = int(d.get("total_tokens") or (prompt + completion))
    offered = d.get("tools_offered")
    return RunTrace(
        task=str(d.get("case") or d.get("task") or "run"),
        round_trips=round_trips,
        tools_seen=len(tools),
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cached_tokens=int(d.get("cached_tokens") or 0),
        wall_s=d.get("wall_s"),
        joules=d.get("joules"),
        tools_offered=int(offered) if isinstance(offered, int) else None,
    )


@dataclass
class Detection:
    """Whether a run looks like a Code Mode candidate, and why."""

    candidate: bool
    reasons: list[str] = field(default_factory=list)
    round_trips: int = 0
    tools: int = 0


def detect(run: RunTrace, cfg: CodeModeConfig) -> Detection:
    """Flag a run with a big tool list *and* many round trips as a Code Mode candidate.

    Both conditions must hold: a big tool list taken in one round trip is not Code Mode's
    problem, and a long single-tool loop is a different kind of waste.
    """
    reasons: list[str] = []
    big_list = run.tools >= cfg.min_tools
    many_trips = run.round_trips >= cfg.min_round_trips
    if big_list:
        seen = "" if run.tools_measured else " seen (floor)"
        reasons.append(f"big tool list: {run.tools} tools{seen} >= {cfg.min_tools}")
    if many_trips:
        reasons.append(f"many round trips: {run.round_trips} >= {cfg.min_round_trips}")
    return Detection(candidate=big_list and many_trips, reasons=reasons,
                     round_trips=run.round_trips, tools=run.tools)


# A value with its honesty label, so the card can colour it. value may be None (pending).
def _v(value, label: str) -> dict:
    return {"value": value, "label": label}


def measure(run: RunTrace, cfg: CodeModeConfig) -> dict:
    """The waste as recorded, before any change. Labels: MEASURED where read from the trace,
    ESTIMATE where a rate or per-tool schema size was assumed.

    ``schema_tokens`` is one request's worth of tool schemas (ESTIMATE). ``schema_resent`` is
    that carried on every round trip -- the tokens Code Mode would stop resending.
    """
    schema_once = run.tools * cfg.schema_tokens_per_tool
    schema_resent = schema_once * run.round_trips
    usd = cost_usd(cfg.model, run.prompt_tokens, run.completion_tokens, run.cached_tokens)
    # Energy: the measured rate (RUN2) is per *generated* token only, so it captures the
    # completion side only. Prompt-side energy has no measured rate here, so it stays pending.
    joules = run.joules if run.joules is not None else round(run.completion_tokens * j_per_token(), 1)
    joules_label = "MEASURED" if run.joules is not None else "ESTIMATE"
    return {
        "round_trips": _v(run.round_trips, "MEASURED"),
        "tools": _v(run.tools, "MEASURED" if run.tools_measured else "MEASURED-floor"),
        "schema_tokens_once": _v(schema_once, "ESTIMATE"),
        "schema_tokens_resent": _v(schema_resent, "ESTIMATE"),
        "total_tokens": _v(run.total_tokens, "MEASURED"),
        "wall_s": _v(run.wall_s, "MEASURED"),
        "usd": _v(round(usd, 6), "ESTIMATE"),
        "joules": _v(joules, joules_label),
    }


def model_after(run: RunTrace, cfg: CodeModeConfig) -> dict:
    """The modelled Code Mode "after". Every number here is an ESTIMATE: schema sent once,
    round trips collapsed to ``after_round_trips``, tokens reduced by just the schema resend.

    The token model is deliberately conservative -- it credits only the schema that stops
    being resent, not the extra conversation context fewer turns would also save -- so the
    saving is a floor, not a best case.
    """
    after_trips = min(cfg.after_round_trips, run.round_trips)
    schema_once = run.tools * cfg.schema_tokens_per_tool
    schema_after = schema_once * after_trips
    schema_saved = schema_once * (run.round_trips - after_trips)
    # Tokens drop by the schema we stop resending; never below the completion tokens we keep.
    total_after = max(run.total_tokens - schema_saved, run.completion_tokens)
    # Dollars: same completion, prompt reduced by the saved schema (not below zero).
    prompt_after = max(run.prompt_tokens - schema_saved, 0)
    cached_after = min(run.cached_tokens, prompt_after)
    usd_after = cost_usd(cfg.model, prompt_after, run.completion_tokens, cached_after)
    # Wall time: model/network latency is round-trip bound, so scale by the round-trip ratio.
    # This excludes program-execution time in a sandbox (there is no sandbox runtime here).
    wall_after = (round(run.wall_s * after_trips / run.round_trips, 3)
                  if run.wall_s is not None and run.round_trips else None)
    return {
        "round_trips": _v(after_trips, "ESTIMATE"),
        "tools": _v(run.tools, "MEASURED" if run.tools_measured else "MEASURED-floor"),
        "schema_tokens_once": _v(schema_once, "ESTIMATE"),
        "schema_tokens_resent": _v(schema_after, "ESTIMATE"),
        "total_tokens": _v(total_after, "ESTIMATE"),
        "wall_s": _v(wall_after, "ESTIMATE"),
        "usd": _v(round(usd_after, 6), "ESTIMATE"),
        # Prompt-side energy saving has no measured rate here, so it is pending on purpose.
        "joules": _v(None, "PENDING"),
    }


def _saved(before: dict, after: dict, key: str) -> dict:
    b, a = before[key]["value"], after[key]["value"]
    if b is None or a is None:
        return _v(None, "PENDING")
    # ESTIMATE whenever either side was modelled, else MEASURED.
    label = "MEASURED" if before[key]["label"] == after[key]["label"] == "MEASURED" else "ESTIMATE"
    return _v(round(b - a, 6) if isinstance(b, float) or isinstance(a, float) else b - a, label)


def compare(run: RunTrace, cfg: CodeModeConfig) -> dict:
    """One run's before/after plus the saving per row, each with an honest label."""
    before = measure(run, cfg)
    after = model_after(run, cfg)
    keys = ("round_trips", "schema_tokens_resent", "total_tokens", "wall_s", "usd", "joules")
    saved = {k: _saved(before, after, k) for k in keys}
    det = detect(run, cfg)
    return {"task": run.task, "candidate": det.candidate, "reasons": det.reasons,
            "before": before, "after": after, "saved": saved}


def analyze(runs: list[dict], cfg: CodeModeConfig | None = None) -> dict:
    """Detect candidates across recorded runs and model the Code Mode saving for each.

    Returns a results dict ready to serialise (the receipt and the dashboard card both read
    it). Only candidates are modelled; non-candidates are listed so the count is honest.
    """
    cfg = cfg or CodeModeConfig()
    parsed = [load_run(r) for r in runs]
    rows = [compare(r, cfg) for r in parsed if detect(r, cfg).candidate]
    skipped = [{"task": r.task, "reasons": detect(r, cfg).reasons}
               for r in parsed if not detect(r, cfg).candidate]

    def tot(section: str, key: str):
        vals = [row[section][key]["value"] for row in rows]
        return sum(v for v in vals if v is not None) if vals else 0

    totals = {}
    for key in ("round_trips", "schema_tokens_resent", "total_tokens", "usd"):
        b, a = tot("before", key), tot("after", key)
        totals[key] = {"before": _v(round(b, 6), "ESTIMATE" if "usd" in key or "schema" in key else "MEASURED"),
                       "after": _v(round(a, 6), "ESTIMATE"),
                       "saved": _v(round(b - a, 6), "ESTIMATE")}
    return {
        "spec": "wattnot.codemode/1",
        "config": {"min_tools": cfg.min_tools, "min_round_trips": cfg.min_round_trips,
                   "schema_tokens_per_tool": cfg.schema_tokens_per_tool,
                   "after_round_trips": cfg.after_round_trips, "model": cfg.model},
        "runs_examined": len(parsed),
        "candidates": len(rows),
        "rows": rows,
        "skipped": skipped,
        "totals": totals,
        "notes": [
            "Code Mode = the agent writes one program that calls the tools as an API, instead "
            "of a big tool list taken over many round trips (prior art in docs/codemode.md).",
            "Round trips, tokens, wall time and the tools called are MEASURED from the recorded "
            "trace. The 'after' is a model (ESTIMATE): no sandbox runtime was run here.",
            "The per-tool schema size (schema_tokens_per_tool) is an assumption. Token savings "
            "credit only the schema that stops being resent each turn; if the upstream caches "
            "the tool schemas, part of this saving is already recovered and the real gain is "
            "smaller.",
            "Prompt-side energy has no measured joules-per-token rate here, so the energy saving "
            "is left pending, not estimated.",
        ],
    }


def build_receipt(results: dict, *, source: str, now: str,
                  prev_sha256: str | None = None, label: str = "REPLAY") -> dict:
    """Wrap an :func:`analyze` result as a signable receipt.

    The body carries the before/after totals and the per-run rows. The ``integrity`` block is
    filled with the JCS/SHA-256 of everything except itself, so :func:`sign_run_receipt` can
    sign it offline without changing the hash. ``label`` is REPLAY because the input is a
    recorded trace re-read, not a live model call; the modelled "after" stays ESTIMATE inside.
    """
    receipt = {
        "spec": RECEIPT_SPEC,
        "label": label,
        "source": source,
        "generated": now,
        "prev_sha256": prev_sha256,
        "config": results.get("config", {}),
        "summary": {
            "runs_examined": results.get("runs_examined", 0),
            "candidates": results.get("candidates", 0),
            "totals": results.get("totals", {}),
        },
        "rows": results.get("rows", []),
        "notes": results.get("notes", []),
    }
    receipt["integrity"] = {"canonicalization": "JCS RFC 8785",
                            "sha256": canonical_sha256(receipt), "signer": None, "rekor": None}
    return receipt
