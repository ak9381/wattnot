# Start here

One command. No GPU, no keys, no money spent. Docker is not needed.

```bash
git clone https://github.com/ak9381/wattnot.git && cd wattnot
./start.sh            # macOS / Linux  (or: make start)
start.cmd             # Windows
```

In order, it will:

1. **Preflight:** check Python 3.11+, pip/venv, git, curl, disk, a free port and PyPI, and print a table.
2. **Install** what is missing (asks once first: Homebrew on Mac, winget on Windows, prints the apt/dnf line on Linux), then make `.venv` from the pinned `requirements-lock.txt`.
3. **Compatibility check:** OS, CPU arch, Python, `import wattnot`, a smoke test (`SMOKE PASS`), GPU info.
4. **Start** the dashboard on a recorded H100 run (REPLAY of MEASURED numbers) and wait until it is healthy.
5. **Open** it in your browser and print next steps.

| Command | What it does | Needs |
|---|---|---|
| `./start.sh` / `replay` | Steps 1 to 5 | nothing |
| `./start.sh check` | Steps 1 to 3 only | nothing |
| `./start.sh keys` | Enter keys (hidden), saved to `.env` mode 600 | |
| `./start.sh live` | Token savers on Meta models | `META_API_KEY` |
| `./start.sh baseten` | Reasoning-effort tuning on Baseten | `BASETEN_METRICS_KEY` |
| `./start.sh modal` | Same on your Modal app | `WATTNOT_MODAL_URL`, `MODAL_PROXY_TOKEN_ID/SECRET` |
| `./start.sh gpu` | Power caps on your own NVIDIA GPU + vLLM (Linux, root) | `WATTNOT_VLLM_URL` |

Windows: same commands with `start.cmd`. Full walkthrough, screenshots and troubleshooting:
**[GETTING-STARTED.md](GETTING-STARTED.md)**.
