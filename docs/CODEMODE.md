# Code Mode detection

An agent that is handed a large tool list and takes many tool-call round trips per task
pays twice: it resends every tool's JSON schema on **every** turn, and each turn is a
separate model round trip (tokens, latency, dollars, and energy).

**Code Mode** is the alternative: instead of exposing the tools as a long list the model
picks from one call at a time, you hand the model the tools as an *API* and let it write
one short program that calls them. The tool schemas are sent **once** (as an API surface),
and the many round trips collapse into a single program turn.

This layer **detects** that pattern in recorded traffic and **models** the Code Mode
"after". It does not run a sandbox: there is no code-execution runtime here, so the "after"
numbers are an explicit model, labelled `ESTIMATE`.

## Prior art

- Cloudflare, *"Code Mode: the better way to use MCP"* — https://blog.cloudflare.com/code-mode/
  (let the model write code that calls tools, instead of exposing tools as individual calls).
- *Model Context Protocol — "Code execution with MCP"* (MCP engineering write-up): loading
  every tool definition into context on each turn is expensive; presenting tools as a
  code API and executing generated code cuts the tokens and round trips.

(Links are cited for the idea; the detector and the numbers here are WattNot's own, over
WattNot's own recorded runs.)

## What is detected

From a recorded run's trace (`data/agent-waste/v3/runs.jsonl`), per run:

- **round trips** — assistant turns that made ≥ 1 tool call. `MEASURED`.
- **tools** — distinct tools the run actually called. `MEASURED` as a *floor* on the offered
  list; if the trace records the real offered tool list (`tools_offered`), that is used and
  labelled a full `MEASURED`.

A run is a **Code Mode candidate** when it has *both* a big tool list
(`tools ≥ min_tools`) *and* many round trips (`round_trips ≥ min_round_trips`). One big tool
list taken in a single round trip is not Code Mode's problem; a long single-tool loop is a
different kind of waste (see [AGENT-WASTE.md](AGENT-WASTE.md)).

## What is measured vs modelled

| row | before | after |
|-----|--------|-------|
| round trips | `MEASURED` | `ESTIMATE` (collapsed to `after_round_trips`, default 1) |
| tool-schema tokens resent | `ESTIMATE` (`tools × schema_tokens_per_tool × round_trips`) | `ESTIMATE` (sent once) |
| total tokens | `MEASURED` | `ESTIMATE` (reduced by the schema that stops being resent) |
| wall time | `MEASURED` | `ESTIMATE` (scaled by the round-trip ratio) |
| cost ($) | `ESTIMATE` (price book) | `ESTIMATE` |
| energy (J) | `ESTIMATE` (RUN2 J/token on completion) or `MEASURED` if the trace has joules | **pending** |

Honesty notes baked into the results and the receipt:

- The per-tool schema size (`schema_tokens_per_tool`) is an **assumption**.
- The token saving credits **only** the schema that stops being resent — not the extra
  conversation context fewer turns would also save — so it is a floor, not a best case.
- If the upstream **caches** the tool schemas, part of this saving is already recovered and
  the real gain is smaller.
- The measured energy rate (RUN2) is per *generated* token, so it covers the completion
  side only. The prompt-side energy saving has **no measured rate here**, so it is left
  **pending** (shown dimmed with a dot in the UI), never invented.

## Thresholds (configurable)

`CodeModeConfig` — all fields have defaults and can be overridden (`from_dict` rejects
unknown keys):

| field | default | meaning |
|-------|---------|---------|
| `min_tools` | 5 | a tool list this size or larger is "big" |
| `min_round_trips` | 6 | this many tool-call round trips or more is "many" |
| `schema_tokens_per_tool` | 120 | ESTIMATE: average JSON-schema tokens for one tool |
| `after_round_trips` | 1 | ESTIMATE: Code Mode collapses to ~this many program turns |
| `model` | `muse-spark-1.3` | price-book key for the dollar estimate |

## Running it

```
python scripts/codemode_detect.py
```

Reads the recorded traces offline (no network, no model, no server), writes
`data/agent-waste/codemode/results.json` (the dashboard card reads this) and
`receipts/codemode/codemode-demo.json` — a before/after receipt **signed offline** through
the existing receipt path (`wattnot.receipt.sign`). Signing only fills the `integrity`
block, so it never changes the hash or lifts a grade.

## Tests

- `tests/test_codemode.py` — detection (both thresholds required), the before/after math,
  the conservative token clamp, the honest labels (energy-after stays pending), configurable
  thresholds, `analyze` over the real recorded traces, and sign + verify of the receipt.
- `tests/js/codemode.test.mjs` — the dashboard card (`web/dash/codemode.js`): colours by
  label, dims pending values with a dot, and never renders a fabricated "after" energy
  number. Registered in `tests/js/index.js`.
