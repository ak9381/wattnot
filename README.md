# WattNot

WattNot is an agent that cuts the energy and cost of each LLM inference request without making the model worse. It tries serving changes (batching, KV-cache settings, FP8, GPU power caps), keeps a change only if quality and p99 latency hold, and writes a signed receipt (Wh and $ per request) that anyone can re-check with the open verifier. It is built on Meta models: Muse Spark picks each step, and the default served model is Meta's open-weights Muse Glimmer 30B (`meta-models/Muse-Glimmer-30B`, Apache 2.0) on vLLM, with Llama 3.1 8B Instruct (`meta-llama/Llama-3.1-8B-Instruct`) as a comparison model. We also tune Muse Spark Open (NVFP4) on a large multi-GPU box, because it needs 700 GB+ of VRAM, more than one H100.

Token trimmers cut your bill, but not your watts. WattNot measures the joules each request burns on the GPU, and checks quality before it keeps any change.

H100 run: `bash scripts/h100_run.sh --dry-run` first, then see [docs/runbooks/H100.md](docs/runbooks/H100.md). Adaptive top-k ([docs/ADAPTIVE-TOPK.md](docs/ADAPTIVE-TOPK.md)) is not in the H100 run; it is planned on Muse Spark Open via Modal. Earlier top-k tests used non-Meta models and are kept only as dated history.

**Every model WattNot runs is a Meta model (Glimmer, Llama, Spark, SAM 3.1, Wonder).**

## For judges: check it in five minutes

- **Replay, no install:** https://ak9381.github.io/wattnot/ (a replay of measured logs; nothing runs live). Open the **Proof** tab.
- **Headline (RUN2, MEASURED):** -27.1% joules per correct answer at a 350 W cap on an H100 (17.03 to 12.42 J), pass rate unchanged at 93.7% on 666 checked answers. RUN2 quality was judged by fixed answer checks (deterministic string and number matching, no model as a judge); results with the Meta judge (hosted Muse Spark, with a Llama 3.1 8B fallback) are reported separately when measured.
- **What Spark decided:** every Muse Spark planner call in RUN2 (400 W, 350 W, 325 W, stop), with its reasoning, latency, tokens and the gate verdict. 325 W failed the p99 gate (+10.95% against a 10% limit); the (i) button explains why.
- **Verify a receipt yourself:**

  ```bash
  pip install -e .
  wattnot verify --chain verifier/samples/chain/      # hash, signature, labels, hash chain
  wattnot verify --rekor-online receipt.json          # also look the entry up in the public Rekor log
  python verifier/wattnot_verify.py --chain verifier/samples/chain/   # standalone, stdlib only
  ```

  The sample receipts are signed offline, so the Rekor step reports "not logged" until the owner publishes them.
- **Leaderboard (net joules per correct answer):** on the Proof tab of the replay, rebuilt by `scripts/reproduce_leaderboard.sh`. Method and how to submit: [docs/joules-per-correct-answer.md](docs/joules-per-correct-answer.md).
- **Context saver:** prefix caching A/B, memory compression and tool-output dedup, each behind the quality gate: [docs/CONTEXT-SAVER.md](docs/CONTEXT-SAVER.md).
- **Drop-in for agent frameworks:** set the base URL in openai-python, LangChain or LlamaIndex: [docs/frameworks.md](docs/frameworks.md) (tests are MOCK).
- **The whole journey on one page:** innovations, features, measured results, decisions and tests as a mind map and knowledge graph — [docs/JOURNEY.md](docs/JOURNEY.md).

| Setup | J / correct (mean ± sd) | vs default | p99 | Gate |
|---|---|---|---|---|
| Default vLLM (no cap) | 17.03 ± 0.14 (MEASURED) | +0.0% | +0.0% | baseline |
| WattNot power cap 500 W | 16.38 ± 0.01 (MEASURED) | -3.8% | +0.7% | passed |
| WattNot power cap 450 W | 14.69 ± 0.81 (MEASURED) | -13.7% | +1.8% | passed |
| WattNot power cap 400 W | 13.81 ± 0.01 (MEASURED) | -18.9% | +4.7% | passed |
| WattNot power cap 350 W | 12.42 ± 0.00 (MEASURED) | -27.1% | +7.7% | passed |
| WattNot power cap 325 W | 11.84 ± 0.01 (MEASURED) | -30.5% | +10.9% | failed: p99 up 10.9% (limit 10%) |
| WattNot small model first (Llama 3.1 8B, escalate to Muse Spark Open) | pending (MEASURED when run) |  |  |  |
| WattNot cap + token trimmer, same workload | pending (MEASURED when run) |  |  |  |
| Muse Spark Open, fewer experts | pending (MEASURED when run) |  |  |  |
| Baseten hosted Muse Spark Open | pending (ESTIMATE when run) |  |  |  |
| Modal hosted Muse Spark Open | pending (ESTIMATE when run) |  |  |  |

- **Signed receipts on the replay:** https://ak9381.github.io/wattnot/receipts/ . They are signed offline with the repo's public test key, so grades stop at ESTIMATE. The RUN2 receipt carries the Spark trace ID and joules per model.
- **If the live demo's server times out,** the dashboard switches to the published replay and says so.

### Meta models used

| Model | Job | Measured cost |
|---|---|---|
| muse-spark-1.3 | planner: picks the next power cap; explains gate failures; agent-waste and coding-set runs | $23.08 over 3459 calls (MEASURED, API ledger total on 2026-10-28) |
| sam-3.1 | segments rack photos into units (kept off the main path) | $0.12 over 47 calls (MEASURED, API ledger) |
| Llama 3.1 8B Instruct | the served model in RUN2; small model in small-first; judge fallback | own H100, cost is GPU hours (not an API bill) |
| Muse Spark Open (Baseten) | big model when the small one fails a check; hosted planner | $0.004, 1 call (MEASURED) |

- **Endpoint health:** `wattnot health` probes each hosted endpoint and writes a FALLBACK ledger (`data/judge/health-*.json`).
- **Labels:** every number is MEASURED, ESTIMATE, SIMULATED, MOCK, REPLAY or ADVICE. SIMULATED and MOCK content is hidden by default; the "Illustrations" switch shows it.
- **Not measured yet (long GPU run):** small-model-first escalation rate and net savings, the hosted Muse Spark judge (Llama 3.1 8B fallback), Muse Spark Open with fewer experts, and the token trimmer and caps on one workload (the stacked figure is an ESTIMATE until then).

## Quickstart

```bash
python -m pytest -q                          # unit tests, no GPU or network
python -m wattnot.proof.run report --out results/proof   # rebuild a report, no GPU needed
```

On an H100 host with vLLM up:

```bash
python -m wattnot.proof.run control --out results/proof  # gate check first
python -m wattnot.proof.run step --cap 500 --out results/proof
python -m wattnot.proof.run full --out results/proof     # control, planner caps, grid, report
```

`full` runs the whole proof. `step` and `control` write one result each, so a planner on another machine can drive the host. The dry run on a fake meter lives in the tests: `python -m pytest tests/test_proof.py -q`.

## Cloud mode (hosted Muse Spark Open on Baseten)

No GPU box? On a hosted model you can't touch the batch size or the power cap, but you can change how hard the model thinks. `wattnot cloud tune` runs your prompts at `xhigh` (the default) as the reference, then tries `low`, `medium` and `high` per prompt class. It keeps the cheapest level whose answers still pass the same checks and whose p99 latency holds, counting any retry at `xhigh` when a cheap answer fails. The receipt then shows, per class, tokens, USD and latency (MEASURED), what the same requests would have cost at `xhigh`, and an energy range (ESTIMATE). `wattnot cloud audit` checks the receipt's token totals against Baseten's usage meter, and `cache-ab` / `probe` test prompt layout and cache lifetime.

```bash
wattnot cloud demo --out cloud-demo        # offline replay of a made-up fixture, every number SIMULATED
export BASETEN_METRICS_KEY=...                    # env only, never a flag or a file
wattnot cloud tune --live --budget-usd 10 --record data/cloud/recorded-run1.json
```

What is and isn't checked yet is listed in [docs/runbooks/BASETEN.md](docs/runbooks/BASETEN.md). The bundled numbers are SIMULATED until a live run is recorded.

## Meta open-weight models

- **Muse Spark Open on Baseten picks the power caps.** In the cap sweep (`--planner baseten-open`), each step sends Muse Spark Open (`meta-models/muse-spark-open`, served by Baseten) the caps still allowed and the measured results so far, and it answers with the next cap to try or "stop". Its pick only counts if it is on the allow-list; the gate still decides keep or roll back.
- **Every call goes in the receipt.** `planner_trace[].call` records the provider (`baseten`), the model id, a SHA-256 of the exact prompt, the latency in ms (MEASURED) and whether it fell back.
- **Safe fallback.** If the call fails or the answer is off the list, a deterministic bisect rule picks the cap instead. That step is logged as `FALLBACK` and marked `source: fallback` in the receipt, so it never passes as a model pick.
- One real test call (`scripts/spark_open_planner_smoke.py`, made-up SIMULATED history shaped like RUN2): Spark Open picked 350 W after 400 W passed and 325 W failed on p99, in 4.95 s, 575 in / 766 out tokens, about $0.004 (latency and tokens MEASURED, dollars ESTIMATE from list price). 350 W is the cap RUN2 kept on the H100. Record: [docs/runs/spark-open-planner-smoke.json](docs/runs/spark-open-planner-smoke.json).
- **Modal works the same way.** `--planner modal-open` runs the same planner on Modal's Shared Endpoint for Muse Spark Open, at the same list price. Every compare (`python -m wattnot.spark_open.h2h`) lists three placements on the same prompts: our GPU, Baseten and Modal. The router picks one per workload on quality and p99 and writes the reasons into the receipt. Setup: [docs/runbooks/MODAL.md](docs/runbooks/MODAL.md).
- The served models we tune are Meta open weights too: Muse Glimmer 30B and Llama 3.1 8B Instruct on vLLM.

## Meta API usage (capped at $50)

Muse Spark makes WattNot's agent decisions: the next knob to try, the order of reasoning-effort levels, the judge verdicts and plain-words receipt notes. Every call goes through a spend guard that keeps a ledger (`~/.wattnot/meta_spend.json`), refuses a call that would pass **$50**, and warns at $40. `wattnot meta spend` shows the total, a per-purpose breakdown and what is left; `wattnot meta check` confirms the model answers. Details, the price used and the recorded session: [docs/runbooks/META-USAGE.md](docs/runbooks/META-USAGE.md).

**Spend to date (MEASURED, 2026-10-28).** The Meta API ledger totals **$23.20** (3,506 billed calls: $23.08 for the Muse Spark planner, $0.12 for SAM), well under the **$50** cap. Hosted endpoints are billed separately and tracked in their own small ledgers: the Baseten head-to-head (one planner call plus the compare) is about **$0.005**, and the hosted A/B (Baseten + Modal arms of Muse Spark Open) totals **$0.14**. All three are MEASURED from billed token counts at list price.

## Flex mode (grid events, SIMULATED until the H100 run)

`wattnot flex run` replays a grid event ("cut GPU draw by 25% for 5 minutes") through a shed
controller and writes a signed receipt: how much it cut, how fast, whether p99 and quality
held, and how long ramp-back took. Today it runs on a software power model, so every number is
SIMULATED; see [docs/FLEX.md](docs/FLEX.md).

## Approve before apply

WattNot does not change a power cap, a vLLM setting, a routing policy or a flex shed by
itself. It files a proposal: what changes, the saving with its label, the quality and p99
checks, and a receipt preview. A person runs `wattnot proposals approve <id>`, and only then
`apply`. Every step goes in a hash-chained audit trail linked to the signed receipt. There is
a local JSON API for a frontend, an optional auto-approve policy (off by default), expiry and
rollback. See [docs/APPROVALS.md](docs/APPROVALS.md). The unattended H100 script takes an
explicit `--pre-approved` flag.

## Router (replay on recorded runs, region SIMULATED)

`wattnot route replay` takes a recorded cloud run and picks the effort per request class that
answers correctly for the fewest joules, then shows what always-`xhigh` would have cost next to
it. A failed quality gate always rules a candidate out. Region is a what-if on rounded grid
numbers, labelled SIMULATED. See [docs/ROUTE.md](docs/ROUTE.md).

## Mock certificates (always MOCK)

`wattnot certificate mint` turns one verified receipt into a one-page statement with the
receipt hash, a QR code and a permanent MOCK / NOT TRADABLE / NOT A CARBON CREDIT watermark.
NOISE and unverifiable receipts are refused, and a receipt can be certified once. It is a
mock for a roadmap slide, not a product. See [docs/CERTIFICATE.md](docs/CERTIFICATE.md).

## Status

Built during the Meta Global AI Developer Hackathon, Oct 26 – Nov 4 2026. Before Oct 26 we had only planning docs, no code.

## Docs

- `docs/JOURNEY.md`: the build journey as a mind map and knowledge graph — innovations, features, measured results, decisions and tests
- `docs/PRD-00-overview.md`: goals, scope and rules
- `docs/modules/`: one PRD and one TDD per module (M01–M13)
- `docs/schemas/`: run-event, receipt and ui-view schemas
- `docs/FRONTEND-SECTIONS.md`: the four app screens
- `docs/TASKS.md`: ordered build list
- `docs/runbooks/BASETEN.md`: cloud mode on Baseten (effort router, receipt, meter audit, cache)

## Credits

Per-request energy is split by token share with a per-request cap, following the method Neuralwatt describes. See `src/wattnot/meter/attribution.py`.

The default rack image in `rack/ai/` is an AI-generated illustration, not a photo of a real rack. The real Cisco photo in `rack/cisco/` is kept as a fallback.

## License

MIT, see `LICENSE`.

## Fleet impact (estimate)

Scale a measured Wh-per-request saving from a receipt to a monthly figure:

```
wattnot impact --wh-delta 0.01 --requests-per-gpu-per-day 100000 --gpus 8
```

Defaults: tariff 0.12 USD/kWh (an assumption), PUE 1.0, 30 days. All are flags.
Water: 0.36 L/kWh (on-site cooling, ESTIMATE); up to 1.8 L/kWh including power-plant water (ESTIMATE).
Every output line is labelled ESTIMATE; only the Wh delta is measured.
