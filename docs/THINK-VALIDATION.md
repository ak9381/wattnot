# Thinking-budget enforcer: what is validated locally, what waits for g0b

The thinking-budget enforcer (`wattnot.longrun.think_stream` + `wattnot.longrun.thinking_budget`)
reads a model's chain of thought from whatever channel the server exposes, cuts it at a per-tier
token budget, forces the answer, and writes the chosen transport mode into the receipt. The risky
part is the *transport*: a server with a working reasoning parser hides the `</think>` marker on a
`reasoning_content` side-channel, and the old marker-based logic would then force-close every item.

Two layers test this with **no network beyond loopback, no GPU, no money, and nothing launched**:

- `tests/test_think_stream.py` — pure unit tests driving `consume_thinking`, `StreamBudgetChat`,
  `classify_chunks` and `probe_mode` with hand-written chunk lists (no sockets).
- `tests/test_think_http_e2e.py` — a stdlib HTTP server on an ephemeral **localhost** port that
  speaks the chat-completions wire protocol (streaming SSE + non-streaming JSON) and emulates four
  server behaviours. The **real** probe, stream enforcer, non-stream fallback and `run()` block are
  driven against the socket — not mocks.

A separate, operator-run script, `scripts/think_probe_live.py`, is the only piece that touches a
real endpoint; the coding agent never runs it. It sends 3 short prompts with a tiny `max_tokens`
and a hard cost cap, and prints which channels (`reasoning_content` / `</think>` / `content`) the
replies use.

## Validated locally (real HTTP against the fake server)

The fake server reproduces the four shapes the enforcer must survive, each driven at a **budget hit
mid-thinking** and an **early answer inside the budget**:

| Server behaviour | What it emulates | Detected mode |
|---|---|---|
| `raw` | thinking leaks into `content` as `<think>…</think>` (parser off/failed) | `raw-marker` |
| `split` | a working reasoning parser; thinking on `reasoning_content`, close marker consumed | `parser-split` |
| `strip-close` | a half-working parser: `</think>` removed, whole thought + answer left in `content` | `raw-marker` (degraded) |
| `nothink` | a non-reasoning model: plain answer, no markers, no reasoning channel | `no-think` |

Asserted end to end over the socket:

1. **The self-probe picks the right mode.** `probe_mode` over real streaming HTTP returns the mode
   in the table above and reports `confident` for every live behaviour.
2. **The cut-off happens at the budget.** With a budget far below the 100-token thought, the stream
   enforcer cuts the thought (streaming, regardless of a hidden close marker) and flags a forced
   close. The non-stream `BudgetChat` fallback forces on `finish_reason == "length"` the same way.
3. **The answer is forced.** After a cut, phase B (`continue_final_message`) runs over HTTP and the
   returned answer is what the `Reply` carries. A tight answer cap is also shown to flag the answer
   itself as `cut` on `finish_reason == "length"`.
4. **The early answer is left alone.** With a roomy budget the thought finishes on its own, the
   answer streams in the same request, and nothing is forced or cut (no phase B at all).
5. **No-think is single-phase.** A non-reasoning reply is returned as the answer with no forcing.
6. **The receipt records the mode.** The full `run()` block runs over HTTP (`per_tier=1`, a
   software energy-meter stand-in — **not** a GPU) and writes `thinking_policy.json` whose
   `think_transport.mode` matches the probed mode and whose `enforcer` is `stream-reasoning-budget`.
   The tight easy cells (0.25x / 0.5x) show a forced close; the roomy 1x cell does not — proving the
   budget is enforced without over-forcing healthy thoughts.

`scripts/think_probe_live.py` is also validated for its **safety behaviour** only (dry-run default,
cost-cap abort) — it is not pointed at any endpoint here.

## Only confirmable in g0b on the H100 (not reproducible locally)

The fake server mimics the *protocol*; it cannot stand in for the real model or the real hardware.
These must be confirmed on the g0b shakedown (`scripts/glimmer_serve.sh` + the `g0b-shakedown`
block), and `scripts/think_probe_live.py` is the first check to run there:

- **Which mode the real server actually uses.** Whether Glimmer's reasoning parser is on and routes
  thinking to `reasoning_content`, or leaks `<think>…</think>` into `content`, or fails to
  initialise (the RUN1 "auto-initialization of reasoning token IDs failed" warning). The probe
  picks one of the emulated modes; only the live run says which.
- **The real `<think>`/`</think>` delimiters.** They are a guess until the shakedown
  (`WATTNOT_THINK_OPEN` / `WATTNOT_THINK_CLOSE`). The local tests use the placeholder markers.
- **The chars-per-token estimate vs. the real tokenizer.** The budget is cut on an accumulated
  character count (`WATTNOT_CHARS_PER_TOKEN`, ~4). Whether 4 is right for Glimmer's tokenizer, and
  how far the stream-character budget drifts from the server's own token accounting, is measurable
  only against the real model.
- **Streaming chunk granularity and batching.** Real vLLM may batch several tokens per delta or
  split a marker across chunks under load; the fake server emits fixed-size pieces.
- **Whether the model answers inside the budget at each tier.** The pass-rate gate, the J/correct
  numbers, forced-close rates and cut-off rates are all properties of the real model on real
  prompts. Local runs grade a canned answer, so the policy numbers here are structural, not real.
- **Energy.** Joules come from NVML on the H100. Locally they are a monotonic stand-in used only to
  exercise the gate's arithmetic; no energy or cost figure here is real.
- **Context-limit and timeout behaviour under real latency**, and the two-phase
  `continue_final_message` prefill being accepted by the live chat template.

In short: the wire-protocol handling, mode detection, budget cut, forced answer and receipt wiring
are proven locally; the model's actual transport, tokenizer, pass rates and energy are confirmed on
g0b.
