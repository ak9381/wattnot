# Hosting the replay for judges

The hosted copy is a read-only replay of a saved run. It shows the same
dashboard as a local run: the Configure screen, the run pipeline with its
narration and room heat map, the Results screen, and the "From the signed
receipt" block when the saved run wrote a receipt. Every number keeps the
label it had when recorded. The top bar reads "REPLAY · saved run".

## What it never does

- No GPU. `health.gpu_attached` is forced false. The server never starts a run.
- No live Meta call. `/api/dash/meta/health` returns a fixed answer without
  touching the network. Nothing in the server holds or reads a model key.
- No keys. The image holds no key, token or certificate. Do not pass any in.

## Run it

```bash
docker build -t wattnot-replay .
docker run --rm -p 8765:8765 wattnot-replay
# open http://127.0.0.1:8765
```

With a sign-in token (optional):

```bash
docker run --rm -p 8765:8765 -e WATTNOT_DASH_TOKEN=pick-one wattnot-replay
```

## Put the saved real run in

Copy the run's events file and its signed receipt into one folder, then mount
it read-only at `/app/saved`:

```bash
mkdir -p saved
cp <out>/runs/<run_id>/events.jsonl saved/run.events.jsonl
cp <out>/proof-*.json saved/
docker run --rm -p 8765:8765 -v "$PWD/saved:/app/saved:ro" wattnot-replay
```

When `/app/saved/run.events.jsonl` exists the server follows it. Without it
the bundled recorded runs are replayed. The receipt block appears when a
`proof-*.json` receipt sits next to the events file.

## Why replay only

The Meta API refuses some data-center addresses, so a cloud server often
cannot reach it at all (the dashboard's Meta chip reports this as blocked).
The hosted copy therefore makes no live calls of any kind. The Muse Spark
picks and the SAM 3.1 room scan it shows are the saved outputs of a real run
(`run.started.data.room`), read from the events file. There is no SAM or Muse
call path in the server.
