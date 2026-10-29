# Code Mode detection

An agent that is handed a large tool list and takes many tool-call round trips per task
pays twice: it resends every tool's JSON schema on **every** turn, and each turn is a
separate model round trip (tokens, latency, dollars, and energy).

**Code Mode** is the alternative: instead of exposing the tools as a long list the model
picks from one call at a time, you hand the model the tools as an *API* and let it write
one short program that calls them. The tool schemas are sent **once** (as an API surface),
and the many round trips collapse into a single program turn.

This layer **detects** that pattern in recorded traffic and **models** the Code Mode
"after" (that modelled view is labelled `ESTIMATE`). It is paired with a **sandbox runtime**
(see *The sandbox runtime* below) that actually *runs* each candidate's task as one program
in a locked-down subprocess and reports what the run really cost — so the "after" can be
`MEASURED` where we ran it, not only modelled.

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

## The sandbox runtime (measured run)

`src/wattnot/agentwaste/sandbox/` takes a detected candidate and actually *runs* its task,
collapsed to **one program**, instead of only modelling the "after". Nothing runs live and
nothing is paid: the tools are a replay of the recorded trace.

**What it does, per candidate:**

1. **Typed tool stub from the trace** (`sandbox/stub.py`). The distinct tool names become
   methods on a generated `Tools` class, each taking exactly the keyword arguments the trace
   recorded for it (a wrong call is a `TypeError`, like a real typed client). Every
   `(tool, args)` the trace saw maps to the **exact recorded output**, keyed by canonical
   (sorted-key) args — so a call **replays deterministically** and the same inputs always
   return the same bytes. It is a faithful replay, not an estimate.
2. **One-turn program, generated from the trace.** The multi-turn trace repeated and
   re-queried; the Code Mode rewrite issues each *distinct* `(tool, args)` once, collects the
   outputs, and composes a single answer — a straight-line equivalent of the loop a model
   would emit, generated deterministically (no live model).
3. **Locked-down subprocess** (`sandbox/runner.py`), defence in depth:
   - **No network** — `unshare -n` drops the child into an empty network namespace when the
     kernel allows unprivileged user namespaces; where it does not (most CI/containers), the
     child installs a Python **audit hook** that refuses every `socket.*` event. Which layer
     applied is recorded (`net_isolation`).
   - **Resource limits** — `RLIMIT_CPU` (SIGXCPU/kill on overrun) and `RLIMIT_AS`
     (address-space cap → `MemoryError`) set in a `preexec_fn`, plus a parent **wall-clock
     timeout** that kills the whole process group if the child wedges without burning CPU.
   - **No stray writes** — the child runs in a throwaway temp dir; the audit hook refuses any
     `open` for writing whose resolved path is outside that dir (reads are left alone).
   - **Minimal environment** — only `PATH` and a fixed `PYTHONHASHSEED`, so none of the
     parent's secrets leak in.

**Honest labels, shown side by side on the card:**

| label | what it covers |
|-------|----------------|
| `MEASURED` | produced by actually running the sandbox: wall time, CPU seconds, peak RSS, exit status, and that the network / out-of-dir writes were refused |
| `TOKENIZER` | a count of the generated program + its final result using the repo's `len(text)//4` rule (`route/stack.py`). It is bytes we hold, **not** a billed model figure |
| `ESTIMATE` | the single model round trip Code Mode implies, and the model-side latency/energy of emitting the program — no live model is called, so these are modelled |
| `null` + reason | CPU-package energy is read from RAPL (`/sys/class/powercap`) when readable; where it is not (containers, no permission) the value is `null` with the reason recorded, never invented |

### Running it

```
python scripts/codemode_sandbox.py
```

Runs each candidate in the sandbox offline (no network, no model, no server) and writes
`data/agent-waste/codemode/sandbox_results.json` (the sandbox card reads this) and
`receipts/codemode/codemode-sandbox.json` — a before/after receipt **signed offline** through
the same receipt path (`wattnot.receipt.sign`). `build_pages.py` copies the results next to
the dashboard (`dash/codemode/sandbox_results.json`). The receipt's `label` is `MEASURED`
(the run really happened); the modelled fields inside still carry their own `ESTIMATE` labels.

## Tests

- `tests/test_codemode.py` — detection (both thresholds required), the before/after math,
  the conservative token clamp, the honest labels (energy-after stays pending), configurable
  thresholds, `analyze` over the real recorded traces, and sign + verify of the receipt.
- `tests/test_codemode_sandbox.py` — the sandbox runtime: it actually runs the subprocess and
  asserts the network is blocked, the CPU/memory/wall limits are enforced, writes outside the
  temp dir are refused, the typed stub replays deterministically, energy stays null-with-reason
  when RAPL is unreadable, and the receipt signs + verifies.
- `tests/js/codemode.test.mjs` — the detection card (`web/dash/codemode.js`): colours by
  label, dims pending values with a dot, and never renders a fabricated "after" energy
  number. Registered in `tests/js/index.js`.
- `tests/js/codemode_sandbox.test.mjs` — the sandbox card: renders MEASURED (the run) and
  ESTIMATE (the model-side fields) side by side, colours the TOKENIZER counts distinctly, and
  never fabricates a model latency or energy number.
