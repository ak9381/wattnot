# Run screen

A live view of one WattNot run, drawn as a pan/zoom canvas. Plain ES modules with no build step.

```
python3 -m http.server 8781        # from the repo root
open http://127.0.0.1:8781/web/run/index.html
```

By default it replays `demo-events.jsonl`, an EXAMPLE stream from the fake backend
(`scripts/demo_run.py`). Pass `?run=<id>` to stream `/api/runs/<id>/events` over SSE.

## What you see

- **Canvas:** eight groups (telemetry, baseline, decide, apply, workload, quality gate,
  keep/revert, receipt). Every edge is labelled. Only the active node and the edges carrying
  data glow. The red edge is the auto-revert path, and dashed edges are optional.
- **Drawer:** click a node to see the decision reason, the logged model call, the prompts, the gate
  verdict and every try. When the demo runs on fallback rules, the Meta call counter stays at 0.
- **Timeline:** play/pause, step, scrub and speed (0.5–4×), synced to the GPU power line.
- **Live savings (dock on the right, `S` toggles):** running counters for power (W cap, MEASURED),
  energy (Wh/req, MEASURED), cost ($/month, ESTIMATE) and water (L/month, ESTIMATE). Each counter
  shows its delta vs the baseline and a sparkline. Tokens per Wh (MEASURED) rises only because
  Wh falls: tokens per answer do not change. Below that, a waterfall gives one bar per try
  (switch between Wh, $, L and W). Each try is measured against the last kept state. A reverted try
  shows its saving and a red bar giving it back, so it nets to 0. Each bar's "why" line comes from
  the events. Click a bar to focus its node and open the drawer; hover a node to highlight its bar.
  Cost and water use the same assumptions as `wattnot impact` (`savings.js`, `ASSUME`).
- **Results:** a checklist built from `recommend.py`. Every item carries its label (MEASURED,
  ESTIMATE or ADVICE) and one action button. Actions only copy a command or download a file.
  Nothing runs on any machine. Checked items are saved in localStorage for each run.

Keys: `space` play/pause, `f` fit, `s` savings, `←/→` step, `+/−` zoom, `Esc` close.
URL params: `?at=N`, `?node=<id>`, `?results=0`, `?run=<id>`, `?src=<jsonl>` (for example `?src=demo-events.live.jsonl` for the recorded Muse Spark run),
`?unit=wh|usd|water|w`, `?savings=0`.

## Tests

`node --test state.test.mjs checklist.test.mjs savings.test.mjs`. pytest also runs these through `tests/test_run_screen.py`.

Fonts: Inter and JetBrains Mono (OFL). Icons: a Lucide subset (ISC). Licences are in `vendor/`.
