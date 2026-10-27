# Dashboard

A read-only page that is also the demo. It opens on the real product flow (configure, run, results) and keeps
Power, Experts and Architecture one click away as deep-dive drawers. No build step, no network needed.

    pip install -e .
    wattnot dash                  # or: python -m wattnot.dash
    # open http://127.0.0.1:8765

The server only reads. Approving a proposal still happens in `wattnot proposals`.

## Power

GPU power, energy per token, tokens per second, p99, the power cap, the approve-before-apply
proposals and the receipts. It replays the real H100 run (`data/dash/h100-replay.json`, packed
by `scripts/build_dash_replay.py` from the results folder). Pick a scene: the adaptive top-k
arms, the two power-cap sweeps, or the three load-shed events. Choosing "live run (API)" reads
`/api/runs/<id>/events` from `wattnot.api` in the same card layout.

A card shows a dash when the run never measured that number. Nothing is filled in.

## Experts

For a local mixture-of-experts model. Every expert in every layer is one *seat*. Seats are
grouped into tiers (hot, warm and cold, per layer) or four k-means clusters. Two linked views:

* **Flow.** input, router, one node per group, output. Dashed edges move; their width and speed
  follow the routed-token rate. Every group node can open into its seats. The adaptive top-k
  switch thins the edges at once and a faint ghost edge keeps the normal width for comparison.
* **Orchestra.** each seat is a dot, sections are groups, rows are layers. Brightness is how
  busy a seat is. Crossed-out seats are the ones adaptive top-k mostly skips.

Hover or click a step, a group or a section and the other view follows, with one plain line
saying what lit up and about how much energy it took.

### Where the numbers come from

| What | Source | Label |
| --- | --- | --- |
| Which experts each token went to, token counts | forward hooks on the router, captured by `scripts/moe_capture.py` | MEASURED |
| Energy per generated token, normal and adaptive | H100 NVML counter, 5 runs per arm | MEASURED |
| Window total energy | that rate times the generated tokens in the window | MEASURED |
| Energy per group, per seat, per step | window energy split by each unit's share of routed tokens | ESTIMATE |

One caveat: the bundled capture was run on a CPU with the small Granite model, so it has no
energy meter of its own. The energy figures use the H100's measured rate and are therefore the
GPU's rate applied to the tokens the CPU run generated. When a meter reading comes with a tick
(`energy_j`), that is used in its place.

### Live

    python scripts/moe_capture.py --out run.jsonl      # a local run writes ticks as it goes
    wattnot dash --follow run.jsonl                    # the page streams them

Without `--follow` the page replays `data/dash/granite-cpu-capture.jsonl.gz`, so it works the
same offline.

## The flow: configure, run, results

1. **Configure.** Workload, model, where it runs, the limits (how much p99 may rise, the quality bar),
   the levers allowed, a "why this run is complex" panel computed from the real workload, and a **Room**
   picker (example data center, your own photo or IR still, or none).
2. **Run.** A live graph of the real pipeline, with a plain-words narration column and a small room heat map:
   `Room scan` (SAM 3.1) -> `Baseline` -> `Quality gate` -> `Cap sweep` or `Adaptive top-k` -> `Keep or
   roll back` -> `Receipt`. A step opens while it runs, shows clickable sub-steps (inputs, outputs, real
   timing, numbers), folds when done and can be pinned. Marching ants run only along the step that is
   moving data; finished edges go still and dim; `prefers-reduced-motion` turns the motion into a static
   highlight. The same view is the **Architecture tab > Run pipeline**; `All modules` is the 21-module map.
3. **Results.** Before and after for cost, energy, water, p99 and quality kept, the room heat map before
   and after, and who did what (Meta, E2E, NVIDIA, Baseten, Hugging Face, Sigstore).

**Agent waste.** The run also passes through an `Agent waste` step (Meta Muse Spark): watch each
call, flag repeats and cycles, offer a done action, enforce budgets, exit early. The Results screen
shows its card under the lever cards, served from `GET /api/dash/agent-waste`. Its numbers come
from `data/agent-waste/results.json`, a benchmark of an agent loop with and without the saver plus a
standard harness that already stops by itself. Calls, tokens, wall time and success are MEASURED on
Muse Spark; energy and dollars are ESTIMATE, using RUN2's measured joules per token for Llama 3.1 8B
as a stand-in rate. A missing value is a dash.

`?tab=power|experts|arch` and `?tab=arch&filter=meta` still work.

### What is REPLAY and what is LIVE

Every plan from `POST /api/dash/run/plan` is a **REPLAY**: a recorded real run re-evaluated against the
limits that were chosen; no GPU and no network are used. `--events FILE` follows a stream live and labels it
from what the stream says: LIVE, REPLAY, EXAMPLE or MOCK (`run.started.data.mode`). A fake-meter stream
(`scripts/mock_run_events.py`) is always shown as MOCK with its power pill reading "fake meter".

Labels: MEASURED (read from a meter or a log), ESTIMATE (computed, assumption shown), SIMULATED,
MOCK or ADVICE; a missing value is a dash. In the room, only the H100 card's GPU power is MEASURED; every
other rack and all temperatures are SIMULATED; cooling and water are ESTIMATEs (PUE 1.56 from the Uptime
Institute 2024 survey, 0.36 L/kWh from LBNL 2024). Levers are never added together: they come from separate runs.

### Event stream

Runs are `wattnot.event/1` events (`schemas/run-event.schema.json`). The run view reads two optional
extras in `data`: `node` and `sub` (the pipeline step; without them `pipe_map` maps the raw event type)
and `rack` (the rack being acted on). `run.started.data.room` may carry the room scan (see below).

**Narration contract.** `step.started.data.narration = {text, source: "template"}` is the default line.
A later event for the same `step_id` carrying `data.narration = {text, source: "muse-spark", model}`
replaces it. No schema change; a worker that generates the lines only needs to emit that event.

### Following a real run's events (step routing)

The Architecture tab follows an events file live (`--events FILE`, SSE `/api/dash/arch/live?speed=N`). Events from the
proof run carry `data.node` and `data.sub`, which are Architecture-map ids (`sam.masks`, `e2e_rig.sweep`,
`agent.call`, `adaptive.hook`, `receipts.sign`, and so on, listed in `docs/RUN2-EVENTS.md`).

- `step.started` / `step.finished` route by `[data.node, data.sub]` when present, and by event type only when absent.
  The node lights with marching ants while a step runs and goes static and dim when it finishes. A parent step that
  finishes after its child does not pull the map back, and `run.finished` ends the last node.
- The `recommendations` event is advice, so it never lights the EU node once a stream is tagged.
- Two views follow the stream. **All modules** lights the 21-node map (sam, e2e_rig, agent and adaptive included).
  **Run pipeline** maps the `step_id` stages to its nodes (`room_scan`→Room scan, `baseline`, `quality_gate`→Quality
  gate, `cap_sweep`, `adaptive_topk`, `keep_rollback`, `receipt`) and narrates every sub-step. With an events file
  loaded it follows it by default, and a button switches to the bundled recorded run.
- A `step.progress` event with `data.kind == "room_scan"` is drawn with `mountNode` from `web/room/node.js`
  (`web/dash/roomnode.js` fills in the picture size and image names from the saved room folder, since the payload is
  trimmed). `phase` `before` then `after` switches the view. `/room` is a read-only static mount of `web/room`.
- A stream whose `telemetry.sample` source or label says mock or fake shows a MOCK badge and "fake meter".

### Room scan interface (`wattnot.room/1`)

    {schema, source, label, size:[w,h], image,
     racks:[{id, polygon:[[x,y]], temp_c, temp_label, power_w, power_label, gpu, decision, reason}], note}

`decision` is `act`, `watch` or `skip`; labels are MEASURED, ESTIMATE or SIMULATED. `pipeline.example_room`
is a 12-rack SIMULATED stub; `validate_room_scan` rejects bad input, and the plan endpoint accepts a
`room_scan` in the body. A real SAM module only has to return this shape. An uploaded picture stays in the
browser; SAM is not run on it here.

## Hosted replay-only mode (for judges)

    WATTNOT_HOSTED_REPLAY=1 wattnot dash --host 0.0.0.0 --events saved-run.jsonl [--token "$JUDGE_TOKEN"]
    # or: wattnot dash --replay-only --events saved-run.jsonl

- No GPU: `health.gpu_attached` is forced false and the server never starts a run.
- No live Meta call: `/api/dash/meta/health` returns a fixed answer without touching the network (the top bar
  says "Meta API: not called (hosted replay)"); nothing in the server holds or reads a model key.
- Saved results only: the run to replay is the saved `events.jsonl` (the real H100 RUN2 once it exists). Its
  Muse Spark picks and its SAM room scan (`run.started.data.room`) are read from the file; there is no SAM or
  Muse call path in the server. Without a file, the bundled recorded runs are replayed.
- A permanent REPLAY label: the top bar reads "REPLAY · saved run · no GPU, no live Meta calls" and the run
  view's badge says REPLAY.
- Judge sign-in is optional: without `--token` the page is open; with it, a one-field sign-in form.
- The flag is `replay_only` in `create_app` / `--replay-only` / `WATTNOT_HOSTED_REPLAY`; `health.replay_only`
  tells the page.

## Architecture

One live map of the real pipeline (`arch_spec.py`: every node and sub-step names a module and symbol
that a test opens). Each node shows its partner badges, what it does, and real stats; `Meta did this`
highlights the steps where Meta tooling did the work and lists each Meta product with its numbers.
Replay the two recorded runs (H100 sweep, hosted run) or follow a live run:

    wattnot dash --events path/to/events.jsonl     # the map follows a run as it writes events
    python scripts/mock_run_events.py /tmp/mock.jsonl   # a MOCK (fake meter) stream to try it with

Steps expand while they run to show their sub-steps, collapse when done, and can be pinned.

### Rebuilding the bundle

The map and the story read `data/dash/story.json`, built read-only from the recorded runs and totals
of the Meta spend ledger (no key, no prompt text):

    PYTHONPATH=src python3 scripts/build_dash_arch.py --e2e <results dir of the H100 run>

## Hosting

    wattnot dash --host 0.0.0.0 --port 8765 --token "$WATTNOT_DASH_TOKEN"

With a token the page shows a small sign-in form (`/login`, `/logout`); the API answers 401 without
it. With no GPU attached the top bar reads "Replay of recorded runs"; everything on the page still works.
The top bar also has a Meta API chip: it checks the key, DNS, the secure connection and one read-only
call to the models list (no model call, no cost) and says in plain words what to do when it fails, for
example when the API refuses data-center addresses.

## Third-party

`web/dash/vendor/gsap.min.js` is GSAP 3.15 under the GreenSock "Standard no charge" licence
(https://gsap.com/standard-license). It is bundled unmodified.
