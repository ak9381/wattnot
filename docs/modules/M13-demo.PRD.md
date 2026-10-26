# M13 PRD: Demo and video script support
Depends on: all · Estimate: 6 h · Contracts: `schemas/SCHEMAS.md`

## Why
Make the 3:00 video reproducible: scripted demo run from fixtures, the forge challenge recording, and an honest real-vs-simulated table.

## Requirements
- FR-DEMO-00 The script leads with who benefits and by how much (small self-hosting teams, Wh and $ saved per request), and says which problem statement we fit ("Push the frontier", energy). Impact is 30% of the score, so this comes before the engineering.
- FR-DEMO-01 `make demo` replays the best real run at 4× into the canvas, opens the receipt and Pareto views.
- FR-DEMO-02 Forge challenge script: real → VALID; one edited number → FAIL at SHA-256; Neuralwatt-style JSON → UNVERIFIABLE (pre-recorded; live page is backup).
- FR-DEMO-03 Shot list follows the f3 video table (0:00 forge open … 2:45 business + vision).
- FR-DEMO-04 README: real vs simulated table, how to verify, credits (LLAMBO, Neuralwatt attribution method, Zeus).
- FR-DEMO-05 Max 2 demo servers during recording.
- FR-DEMO-06 No chat-UI look. Show the run canvas, receipt and Pareto, never a chat box.
- FR-DEMO-07 The opening line says what existed before Oct 26 (prior-work rule). Judging weights to aim at: Impact 30, Meta tooling 25, Technical depth 20, Demo 25.

## Out of scope
Posting the video anywhere (done by the maintainer).

## Done means
Two clean takes recorded by Nov 4; README links verifier URL, Rekor entries and CSVs.

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
