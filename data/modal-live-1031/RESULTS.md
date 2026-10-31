# Modal live bench: Muse Glimmer 30B on our own vLLM (2026-10-31)

**Outcome: ran live.** One A100 80GB PCIe, image `vllm/vllm-openai:muse-glimmer`, thinking off (effort low), 24 requests. Modal app `ap-anTYTYureK8JDK7G0v3o56` finished at 13:21 IST. Afterwards, `modal app list` and `modal container list` showed nothing running.

| Metric | Value | Label |
|---|---|---|
| Mean energy per request | 1,397.3 J | MEASURED (NVML total-energy counter) |
| Total energy, 24 requests | 33,535 J | MEASURED |
| Energy per completion token | ~10.9 J | MEASURED (33,535 J / 3,072 tokens) |
| Throughput | 27.3 tokens/s | MEASURED |
| Idle power | 51.4 W | MEASURED |
| Power caps | refused (no privileged power-limit control on Modal) | MEASURED |
| Modal cost | about $1.1 for the bench, plus a few cents for 3 failed image builds and 1 orphaned image unpack | ESTIMATE (list price × minutes; not checked against the bill) |

**Caveat:** every request produced exactly 128 tokens, so all 24 answers hit the max_tokens limit. Treat the energy per request as energy per 128-token completion. Answer quality was not graded.

Three fixes were needed to get it running:
- the image has no `python` binary, so the bench image links `python3` to `python` and clears the entrypoint;
- the local launch needs `PYTHONPATH=src`;
- the stale "unservable" preflight was replaced by the pinned-image check.
