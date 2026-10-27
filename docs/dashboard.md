# Dashboard

A read-only live page with two tabs. It starts with no build step and no network.

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
