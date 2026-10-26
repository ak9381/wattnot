# Run screen

A live graph of one WattNot run, driven by the run event stream:
Read telemetry → Baseline → Muse Spark decides → Apply change → Run prompts → Gate → Keep or revert → Recommendations.

- `state.js` turns events into graph state (pure, tested by `state.test.mjs`).
- `app.js` draws it. Click a node for Muse Spark's reasoning, the logged model call, the prompts and the gate verdict.

Open it from the repo root with any static server, e.g. `python -m http.server`, then:

- `web/run/index.html` plays `demo-events.jsonl` (EXAMPLE numbers, fake backend, no Meta call).
- `?at=N` stops after N events, `?node=gate` opens a panel, `?rack=/rack/cisco/rackmap.sam.svg` fills the rack map slot.
- `?run=<run_id>` streams a real run from the API (`/api/runs/<id>/events`).

Regenerate the demo stream with `python scripts/demo_run.py`.
