# PRD-00: WattNot overview (build kit v1, Oct 26 2026)

Source of truth: this document. Module PRDs and TDDs follow it.

## 1. The product in one line
WattNot is an agent that makes LLM inference use less energy without making it worse, then proves each saving with a signed receipt anyone can re-check in their browser, and bills only on savings that clear the noise.

## 2. Users
| User | What they want | Where they touch WattNot |
|---|---|---|
| Small self-hosting team (1–50 GPUs, vLLM on Modal/Baseten or own boxes) | Lower Wh and $ per request, no quality loss | `wattnot run`, receipts, Pareto screen |
| Their finance / sustainability lead | A number they can trust and report | Receipt + verifier, EU report export |
| A hackathon judge (and any sceptic) | Check the claim without trusting us | Public WASM verifier URL, Rekor entry, published 10 Hz CSVs |

## 3. Judging rubric mapping (official rules: Impact 30%, Use of Meta AI tooling 25%, Technical depth & execution 20%, Demo 25%)
Source: Official Rules (event-details.md) and kickoff notes (`kickoff/KICKOFF-NOTES.md`). Weights are not equal. Impact is the biggest area and includes "whether it fits one of the problem statements". Top 5 per track go to finals.

**Problem-statement fit:** WattNot sits under the "Push the frontier" theme, which names energy directly. We cut the energy and cost of every LLM request without losing quality, and prove it.

| Criterion | What earns it | Modules |
|---|---|---|
| Impact 30% | Who benefits and by how much, said first: small self-hosting teams get lower Wh and $ per request with no quality loss. Real measured saving (container FP8/batching receipt, power-cap Pareto), honest null if no win, EU fields | M3, M4, M6, M12 |
| Use of Meta AI tooling 25% | Muse Spark 1.3 planner/explainer/judge, Muse Spark Open as the model tuned, Muse Code scripted workload, SAM 3.1 floor map, Glimmer/Llama 8B cheap tier | M5, M6, M11, M3 |
| Demo 25% | Opens on who benefits and the saving, then forge challenge in the verifier, live run canvas, Pareto with error bars. No chat-UI look | M8, M10, M13 |
| Technical depth & execution 20% | 10 Hz NVML traces, Welch test + bootstrap CI billing, Sigstore/Rekor, warm-start ablation with Wh budgets | M2, M6, M7, M5 |

## 3a. Official rules (Discord #rules/FAQ, Official Rules, kickoff Oct 26)
- **Eligible work:** only work built Oct 26 – Nov 4 counts. Build period starts after the Oct 26 kickoff.
- **Repo:** the submitted repo must be PUBLIC and a fresh standalone repo (not `ak9381/wattnot`, which stays private for planning only).
- **Prior work is allowed if we say what existed before.** Say it in the submission form and at the start of the demo video (3:00, required): "Before Oct 26 we had only planning docs, no code; everything shown was built Oct 26 – Nov 4."
- **Submit:** https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit by **Nov 4, 9 PM ET (Nov 5, 7:30 AM IST)**: public repo, demo video link, all team members. Read the form early. No Devpost.
- **Judging:** first round Nov 5–8. Live finals at the closing ceremony on Nov 9.
- **Bonus prizes (five; tick the ones we enter on the submission form):**
  - Most Viral: post the demo on X and add the post link to the form.
  - Best on Meta Open Weights: best project on Muse Glimmer or Muse Spark Open (ours: Muse Spark Open on vLLM).
  - Community Vote: share the project link with friends.
  - Best Agentic Use Case: $5k.
  - Best Creative Build: $5k.
- **Avoid looking "basic":** organizers called chat wrappers and simple RAG apps basic. The demo must not look like a chat UI.
- **Credits:** Modal form done (first 1,000 teams, $100). Baseten emails selected teams, so nothing to claim . Fireworks posts redeem steps in its channel.
- **Workshops (9:30 PM IST):** Oct 27 and Oct 28 (Meta workshop parts 1 and 2) · Oct 30 Fireworks. **Office hours:** #office-hours, weekday mornings ET.

### Submission checklist
- [ ] Fresh public repo, only Oct 26 – Nov 4 commits
- [ ] Prior work stated in the submission form, and the demo opens with the pre-existing vs built line
- [ ] Demo leads with who benefits and by how much, and names the problem statement
- [ ] Bonus prizes ticked on the form; X post link added (Most Viral)
- [ ] 3:00 demo video linked; demo posted on X (Most Viral)
- [ ] Submitted at the Cerebral Valley link before Nov 4, 9 PM ET (Nov 5, 7:30 AM IST)
- [ ] **AI tools disclosure:** check whether the rules require disclosing AI coding tools; quote the exact rule text here if it exists. Status Oct 26: no such rule found in the #rules/FAQ notes (official-resources.md); kickoff stream didn't mention one either; recheck #rules and the form before submitting.

## 4. Scope on Nov 4 (all "Real" unless marked)
Container-mode saving (safe headline) · power/clock Pareto 5 vs 5 · independent re-run · idle power before/after · live cap controller on 1 GPU · signed receipts with planner trace · WASM verifier (3 real + 3 forged, Neuralwatt-style UNVERIFIABLE) · noise-floor billing · open receipt spec · cold/warm/grid ablation with time and Wh budgets · per-request attribution by token share · SAM floor map (go/no-go Nov 2 12:00 PM IST) · water ESTIMATE + SCI + EU fields · p99 SLA slider (optional).

## 5. Cuts (do not build)
ML.ENERGY submission · 2-GPU power-limit test · `wattnot-ci` Action · judge set above 30 items · web app beyond 3 screens + canvas · phase-aware clocks · carbon credits or "neutral" wording · Muse Image.

## 6. Hard rules (every agent, every session)
1. **No Meta API credits for building.** Meta API calls happen only in gate runs, demo runs and short scripted Muse Code test tasks.
2. **Nothing confidential goes to the Meta API.** Free-credit content may be used for product improvement. Only public prompts (replay set) and numbers.
4. **At most 2 demo servers and one heavy job at a time** (a GPU sweep, a model download, a full gate run each count as heavy).
5. **Push after every milestone** (each task in TASKS.md ends with a push of its branch).
6. **Paid GPU spend needs maintainer okay.** Total Meta spend stays inside the $150 credit.
7. **Rekor is public and permanent.** Receipts carry numbers, model ids and settings only. Never prompts, outputs or names.
8. **Labels never lie.** Every number is MEASURED or ESTIMATE. Water is always ESTIMATE. Null results are published.
9. Writing is human, simple yet technical.

## 7. Timeline (IST)
| Date | Milestone | Modules |
|---|---|---|
| Oct 27 | Repo skeleton, schemas frozen, `probe.py` on Modal/Baseten, 10 Hz meter, replay set | M1, M2, M9 schema |
| Oct 28 | vLLM knob runner, gates, receipts + signing; book rental (maintainer) | M3, M6, M7 |
| Oct 28–29 | **Container-mode saving receipt (safe headline), public** | M3, M6, M7 |
| Oct 29–30 | `wattnot verify` + WASM verifier, forge recording | M8 |
| Oct 30–31 | Muse Spark planner + Optuna warm start + ablation; rental runs (Pareto 5 vs 5) | M4, M5 |
| Nov 1 | Independent re-run, CSV publish, notebooks; event stream API done | M4, M9 |
| Nov 1–2 | Manual rack overlay + SAM attempt. **Nov 2 12:00 PM IST hard cut** | M11 |
| Nov 2–3 | Frontend: canvas, Receipt+Verifier, Pareto+Planner, Rack | M10 |
| Nov 3 | EU report export, README, SPEC, real-vs-simulated table | M12 |
| Nov 3–4 | Video script, record, edit (3:00) | M13 |
| Nov 4 | Buffer. Submit by Nov 5 7:30 AM IST (Nov 4 9 PM ET) | — |

## 8. Module index
| # | Module | PRD | TDD |
|---|---|---|---|
| M1 | probe.py, host capability check | modules/M01-probe.PRD.md | modules/M01-probe.TDD.md |
| M2 | Telemetry meter, NVML/DCGM, 10 Hz traces | M02-telemetry | M02-telemetry |
| M3 | vLLM knob runner (container mode) | M03-knobs | M03-knobs |
| M4 | Power-cap and clock controller (root) | M04-powercap | M04-powercap |
| M5 | Muse Spark planner, Optuna warm start, ablation | M05-planner | M05-planner |
| M6 | Quality/p99 gates, Spark judge, Welch noise floor | M06-gates | M06-gates |
| M7 | Receipt spec, schema, SHA-256, Sigstore, Rekor | M07-receipts | M07-receipts |
| M8 | `wattnot verify` CLI + WASM verifier | M08-verify | M08-verify |
| M9 | Run event stream and API | M09-events | M09-events |
| M10 | Frontend (canvas, Receipt+Verifier, Pareto+Planner, Rack) | M10-frontend | M10-frontend |
| M11 | SAM 3.1 floor map + manual fallback | M11-sam | M11-sam |
| M12 | EU report export | M12-eu | M12-eu |
| M13 | Demo and video support | M13-demo | M13-demo |

Canonical contracts every module uses: `schemas/run-event.schema.json`, `schemas/receipt.schema.json`, explained in `schemas/SCHEMAS.md`.
