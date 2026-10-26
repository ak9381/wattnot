# WattNot

WattNot is an agent that cuts the energy and cost of each LLM inference request without making the model worse. It tries serving changes (batching, KV-cache settings, FP8, GPU power caps), keeps a change only if quality and p99 latency hold, and writes a signed receipt (Wh and $ per request) that anyone can re-check with the open verifier. It is built on Meta models, served with vLLM.

## Status

Built during the Meta Global AI Developer Hackathon, Oct 26 – Nov 4 2026. Before Oct 26 we had only planning docs, no code.

## Docs

- `docs/PRD-00-overview.md`: goals, scope and rules
- `docs/modules/`: one PRD and one TDD per module (M01–M13)
- `docs/schemas/`: run-event, receipt and ui-view schemas
- `docs/FRONTEND-SECTIONS.md`: the four app screens
- `docs/TASKS.md`: ordered build list

## License

MIT, see `LICENSE`.
