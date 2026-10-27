# Dashboard

A read-only page that is also the demo. It opens on a six-scene story (about three minutes) and keeps
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

## The story

`Play demo` walks six scenes: the task and why it is hard; the stock run; WattNot steps in (Muse Spark
proposes, a person approves, the change is applied); the re-run; before and after; who did what.
Left and right arrows, space and the dots step through it by hand. `P` plays or pauses, `D` opens
Architecture, `1` to `6` jump to a scene, `Esc` closes whatever is open. URL options: `?scene=N`,
`?play=1`, `?tab=power|experts|arch`, `?tab=arch&filter=meta`.

Every number carries one label: MEASURED (read from a meter or a log), ESTIMATE (computed, with the
assumption shown), SIMULATED, MOCK or ADVICE. A value that was not recorded shows as a dash with no
label. The before and after screen never adds the three levers together: they come from separate runs.

Water is an ESTIMATE: measured kWh times a cited litres-per-kWh factor (the factor, formula and source are in a
tooltip). The per-million-requests and per-year lines are ESTIMATEs with the assumptions listed and the
request rate editable.

## Architecture

One live map of the real pipeline (`arch_spec.py`: every node and sub-step names a module and symbol
that a test opens). Each node shows its partner badges, what it does, and real stats; `Meta did this`
highlights the steps where Meta tooling did the work and lists each Meta product with its numbers.
Replay the two recorded runs (H100 sweep, hosted run) or follow a live run:

    wattnot dash --events path/to/events.jsonl     # the map follows a run as it writes events

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
