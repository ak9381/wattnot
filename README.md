# WattNot

WattNot is an agent that cuts the energy and cost of each LLM inference request without making the model worse. It tries serving changes (batching, KV-cache settings, FP8, GPU power caps), keeps a change only if quality and p99 latency hold, and writes a signed receipt (Wh and $ per request) that anyone can re-check with the open verifier. It is built on Meta models: Muse Spark picks each step, and the default served model is Meta's open-weights Llama 3.1 8B Instruct (`meta-llama/Llama-3.1-8B-Instruct`) on vLLM.

## Status

Built during the Meta Global AI Developer Hackathon, Oct 26 – Nov 4 2026. Before Oct 26 we had only planning docs, no code.

## Docs

- `docs/PRD-00-overview.md`: goals, scope and rules
- `docs/modules/`: one PRD and one TDD per module (M01–M13)
- `docs/schemas/`: run-event, receipt and ui-view schemas
- `docs/FRONTEND-SECTIONS.md`: the four app screens
- `docs/TASKS.md`: ordered build list

## Credits

Per-request energy is split by token share with a per-request cap, following the method Neuralwatt describes. See `src/wattnot/meter/attribution.py`.

## License

MIT, see `LICENSE`.

## Fleet impact (estimate)

Scale a measured Wh-per-request saving from a receipt to a monthly figure:

```
wattnot impact --wh-delta 0.01 --requests-per-gpu-per-day 100000 --gpus 8
```

Defaults: tariff 0.12 USD/kWh (an assumption), WUE 1.8 L/kWh, PUE 1.0, 30 days. All are flags.
Every output line is labelled ESTIMATE; only the Wh delta is measured.
