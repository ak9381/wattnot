# Flex mode: cut GPU draw when the grid asks

A grid operator sometimes asks big power users to draw less for a few minutes. A GPU
server can do that: lower the power limit, and the card runs slower and draws less. The
catch is latency and answer quality. Flex mode is the controller that cuts as far as the
target asks, stops where p99 latency or quality would break, and writes a signed receipt
saying exactly what happened.

> **Everything the offline demo prints is SIMULATED.** It uses a small software power model
> with guessed constants and a replayed event file. The same code drives a real GPU
> (`--backend gpu`), and a real run is labelled MEASURED only then.

## Try it

```bash
wattnot flex run --out flex-out --sign-key verifier/samples/test-key.pem
wattnot flex show flex-out/<receipt>.json
python verifier/wattnot_verify.py flex-out/<receipt>.json     # standalone check, grade SIMULATED
```

Output for the two bundled events (numbers move a little with `--seed`):

```text
[SIMULATED] FULL: asked 25%, delivered ~27% in ~20s; p99 within budget; ramp-back ~4s
[SIMULATED] PARTIAL: asked 35%, delivered ~27%, stopped by cap_floor
```

The second event is the honest one. The model card's lowest allowed limit is 200 W, so a 35%
cut is out of reach and the run says so instead of hiding it.

## What happens in a run

One event = three phases, one reading per second:

1. **before** (30 s): measure normal draw, p99 and pass rate. This is the baseline.
2. **event** (the event's length): the controller steps the power limit down 10 W at a time,
   waiting 2 s between moves.
3. **after** (90 s): the limit is raised in steps until draw and p99 are back to normal.

Controller rules (`src/wattnot/flex/controller.py`):

- First move goes straight to the limit that starts to bite (the baseline draw), not down
  from the card maximum.
- Lower the limit while smoothed p99 is under 92% of the budget.
- Raise it by two steps when p99 passes 98% of the budget (two ticks in a row), or when the
  pass rate drops more than 1 point. That limit becomes a floor for the rest of the event.
- Stop at the card's lowest limit or at the floor. The receipt records why: `cap_floor`,
  `p99` or `quality`.
- The p99 budget is the baseline p99 times 1.5 (`--sla-slack 0.5`).

The limit is always put back when the run ends, even on a crash (`CapGuard` on a real GPU).

## The software power model (SIMULATED)

`src/wattnot/flex/power_model.py`. Four guesses, all labelled in every receipt:

- idle draw is fixed (70 W); working draw rises as `clock^2.5`
- speed rises as `clock^0.6` (decode waits on memory, so it slows less than power falls)
- a power limit forces the clock down until draw fits; it can't go below 35% clock
- p99 = base / speed x (1 + busy / (1 - busy))

Nothing is fitted to a GPU. The H100 run replaces the model with NVML readings.

## The receipt

`wattnot.receipt/1` with `profile: "flex"`. The frozen schema is unchanged; the extra `flex`
block is covered by the hash. The one-second trace is saved next to it
(`<id>.trace.csv`, plus `<id>.trace.json` for charts) and pinned by sha256.

- Grade: SIMULATED for the model. A measured run grades ESTIMATE, never VALID: one event has
  no significance test, so the verifier will not call it VALID.
- `baseline` is the window before the event. `candidate` is the settled part of the event.
  Both carry Wh per request and the `joules` block (J per answer, J per good answer).
- `flex.scope`: "GPU draw only. Cooling, CPUs, networking and the rest of the site are not
  shed." `flex.not_claimed`: no site or grid saving, no carbon claim, no programme enrolment.
- `wattnot flex show` re-hashes the trace files and fails if either was changed.

JSON shapes for the frontend are in [docs/moonshot/API-SHAPES.md](moonshot/API-SHAPES.md).

## On a real GPU (the H100 run)

See the "Flex check" step in [runbooks/H100.md](runbooks/H100.md). `--backend gpu` reads
power from NVML and p99 and pass rate from the load generator's progress file.
