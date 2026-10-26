# M10 PRD: Frontend: run canvas, Receipt+Verifier, Pareto+Planner, Rack/Floor map
Depends on: M09, M08 (WASM), designs from UI UX Designer bot · Estimate: 9 h · Contracts: `schemas/SCHEMAS.md`

## Why
Four pieces the judges see: an n8n-style canvas of the run, the receipt with the verifier, the Pareto chart with the planner trace, and the rack map. Designs come from the UI UX Designer bot per section; DESIGN.md's locked spec is the base.

## Requirements
- FR-UI-01 Run canvas (React Flow): one node per `step_id`, edges from `parent_step_id`; node shows label, agent/model, effort, status, duration, tokens, Meta $; trial nodes show Wh/req and p99; gate nodes show pass/fail with detail; live power sparkline from `telemetry.sample`.
- FR-UI-02 Receipt + Verifier: render receipt fields; drag-and-drop zone running the WASM verifier; step list; grade pill (VALID / ESTIMATE / NOISE / UNVERIFIABLE); side-by-side Neuralwatt-style sample.
- FR-UI-03 Pareto + Planner: scatter of Wh/req vs p99 with 5-rep error bars and front line; click a point → its params, gates, planner reason; ablation chart (cold/warm/grid: trials, seconds, Wh to best); optional p99 SLA slider.
- FR-UI-04 Rack/Floor map: rack photo with SAM masks or the manual overlay; per-GPU power and cap; banner 'MANUAL FALLBACK (SAM attempt in repo)' when cut.
- FR-UI-05 Every number shows its label (MEASURED/ESTIMATE); water always ESTIMATE.
- FR-UI-06 Works from fixture files with no backend (static demo build).

## Out of scope
Landing/marketing site, settings, auth, more than these 4 views.

## Done means
All 4 views work on the fixture run and on one real run; screenshot set for the video.

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
