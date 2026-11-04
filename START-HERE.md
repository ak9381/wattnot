# Start here

One command. No GPU, no keys, no money spent.

```bash
git clone https://github.com/ak9381/wattnot.git && cd wattnot
./start.sh            # or: make start
```

It will:

1. Make a `.venv` and install WattNot (about a minute the first time; Python 3.11+).
2. Run a 1-minute smoke check with no GPU and no keys, and print `SMOKE PASS`.
3. Open the dashboard on a recorded H100 run (REPLAY of MEASURED numbers) and print next steps.
   Press Ctrl+C to stop it.

Every number is labelled MEASURED, ESTIMATE, SIMULATED or REPLAY.

## Other commands

| Command | What it does |
|---|---|
| `./start.sh check` (`make start-check`) | Setup and smoke check only. No browser, nothing left running. |
| `./start.sh keys` | Creates `.env` from `.env.example` (gitignored, mode 600) and shows which keys are set. Never shows values. |
| `./start.sh live` | Live token savers on Meta models with your own `META_API_KEY`. No GPU needed. |
| `.venv/bin/wattnot verify <receipt.json>` | Checks a signed receipt (hash, signature, Rekor). |

## Add keys for live Meta runs

1. `./start.sh keys`
2. Open `.env` and set `META_API_KEY`. Set `WATTNOT_MAX_SPEND` to cap spend in USD.
3. `./start.sh live`

Keys stay in `.env`, which is never committed. WattNot never prints them.

## Live on a GPU

Power caps need your own NVIDIA GPU, a vLLM server and root. Set `WATTNOT_VLLM_URL` in `.env`,
then run `./run-local.sh gpu`. `./run-local.sh doctor` checks your machine and changes nothing.
