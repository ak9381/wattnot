# WattNot

WattNot is an agent that cuts the energy and cost of each LLM inference request without making the model worse. It tries serving changes (batching, KV-cache settings, FP8, GPU power caps), keeps a change only if quality and p99 latency hold, and writes a signed receipt (Wh and $ per request) that anyone can re-check with the open verifier. It is built on Meta models: Muse Spark picks each step, and the default served model is Meta's open-weights Muse Glimmer 30B (`meta-models/Muse-Glimmer-30B`, Apache 2.0) on vLLM, with Llama 3.1 8B Instruct (`meta-llama/Llama-3.1-8B-Instruct`) as a comparison model. We also tune Muse Spark Open (NVFP4) on a large multi-GPU box, because it needs 700 GB+ of VRAM, more than one H100.

H100 run: `bash scripts/h100_run.sh --dry-run` first, then see [docs/runbooks/H100.md](docs/runbooks/H100.md). Optional, off by default: an adaptive top-k trial on a small MoE model (a known technique, [docs/ADAPTIVE-TOPK.md](docs/ADAPTIVE-TOPK.md)).

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
export BASETEN_API_KEY=...                 # env only, never a flag or a file
wattnot cloud tune --live --budget-usd 10 --record data/cloud/recorded-run1.json
```

What is and isn't checked yet is listed in [docs/runbooks/BASETEN.md](docs/runbooks/BASETEN.md). The bundled numbers are SIMULATED until a live run is recorded.

## Meta API usage (capped at $20)

Muse Spark makes WattNot's agent decisions: the next knob to try, the order of reasoning-effort levels, the judge verdicts and plain-words receipt notes. Every call goes through a spend guard that keeps a ledger (`~/.wattnot/meta_spend.json`), refuses a call that would pass **$20**, and warns at $15. `wattnot meta spend` shows the total, a per-purpose breakdown and what is left; `wattnot meta check` confirms the model answers. Details, the price used and the recorded session: [docs/runbooks/META-USAGE.md](docs/runbooks/META-USAGE.md).

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

Defaults: tariff 0.12 USD/kWh (an assumption), WUE 1.8 L/kWh, PUE 1.0, 30 days. All are flags.
Every output line is labelled ESTIMATE; only the Wh delta is measured.
