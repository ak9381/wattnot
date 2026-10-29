# Long-run VM watchdogs: five layers, one rule

The H100 long run rents a VM by the minute from E2E (about Rs 451/h, see `long-run-plan.md`).
If the driver dies, or a block hangs, or the clock or the balance runs past its cap, something
has to delete that VM — otherwise the bill runs unbounded. RUN3 (28 Oct) is the cautionary tale:
a two-minute SSH blip was mistaken for a dead VM and the box was deleted with nothing copied off
it. `src/wattnot/proof/teardown.py` fixed the *when* (a lost link is not a dead VM). This doc is
about the *who* — making sure a teardown actually happens even if one watchdog is wedged, or the
whole control box dies.

Five layers, one rule. Layers 1-3 run **on the control box** (defence against software faults).
Layers 4-5 run **off the box** (defence against the box itself dying). Every layer reads the same
heartbeat and runs the same pure `decide` + idempotent teardown, so none can disagree with the
others, and all of them racing to delete the same VM is safe.

| # | Layer | Where it runs | Failure domain it covers | Code |
|---|-------|---------------|--------------------------|------|
| 1 | Local loop | control box | a wedged driver / hung block | `watchdog/local.py` |
| 2 | Agent CLI | control box | a crashed layer-1 process | `watchdog/agent.py` |
| 3 | Second agent CLI | control box | a misbehaving layer-2 | `watchdog/agent.py` |
| 4 | GitHub Actions cron | GitHub's runners | **the control box dying** | `watchdog/offbox.py` + `.github/workflows/e2e-watchdog.yml` |
| 5 | macOS watchdog | a Mac on a desk | the box dying **and** GitHub being unavailable | `scripts/mac_watchdog/` |

## The three on-box layers

All three run the **same deterministic check** (`wattnot.longrun.watchdog.check_and_teardown`),
which reads the driver's heartbeat, applies one pure rule (`decide`), and — unless it is a
dry run — deletes the VM through an idempotent client. No layer has its own private rule, so no
layer can decide to kill the VM for a reason the others would not.

| Layer | What it is | How it is invoked | Code |
|------|-----------|-------------------|------|
| 1 | The local watchdog loop on the control box | runs in-process / as a child of the driver | `watchdog/local.py` (`run_local_watchdog`); the live sibling is `hackathon/e2e-glimmer-test/ctl.py watchdog` |
| 2 | A headless coding-agent CLI | env `WATTNOT_WD2_CMD`, called every few minutes with a prompt that tells it to run the shared `watchdog check` command | `watchdog/agent.py` (`run_agent_watchdog`) |
| 3 | A second, *different* agent CLI | env `WATTNOT_WD3_CMD`, same contract | same |

Layers 2 and 3 are only *triggers*: the agent is instructed to run one command
(`python -m wattnot.longrun.watchdog check ...`) and report its JSON. The decision and the delete
live in the shared core, so a confused or creative agent cannot invent a different policy. Two
unrelated agent binaries give two independent triggers without duplicating logic. The command is
read from an env var precisely so no AI-vendor binary name is baked into the repo; a neutral
`WATTNOT_WATCHDOG_CMD` is the fallback for both layers.

### The one rule (`decide`)

Tear down if **any** cap is exceeded:

* **budget cap** — estimated spend in INR has reached the cap (spend = elapsed hours × Rs 451.2,
  or the exact `spent_inr` the driver records);
* **time cap** — the clock has reached the deadline (`launch_ts + max_hours`, or an explicit
  `deadline_ts`);
* **stale heartbeat** — the driver has not refreshed its heartbeat for `max_stale_s`
  (default 15 min, matching `proof/teardown.py`'s dead window).

`decide` is pure — it takes numbers and returns a decision; it reads nothing and deletes nothing.
Every branch is unit-tested without a VM.

### The heartbeat

The driver (`wattnot.longrun.driver.Driver`) writes `heartbeat.json` at the start of the run and
before each block: `{ts, launch_ts, deadline_ts, spent_inr, block}`. A missing heartbeat is
treated as "unknown — do not delete" (it could just mean the run has not started yet); staleness
can only trip once a heartbeat has actually been written.

### Idempotent teardown

Teardown goes through an injectable `E2EClient` (`exists()` / `delete()`). `delete()` is safe to
call when the VM is already gone, so all three layers racing to delete the same box is harmless:
the first delete wins and the rest see "already gone". The live adapter (`cli.E2EApiClient`)
mirrors the E2E delete path in `ctl.py`, but is **only** constructed for a real, non-dry-run
teardown; `--dry-run` never builds a client and never calls the API.

### Running them

```
# dry run, no API, prints the decision as JSON
python -m wattnot.longrun.watchdog check --heartbeat run/heartbeat.json \
    --budget-cap-inr 8122 --max-hours 18 --dry-run

# layer 1 loop (dry run)
python -m wattnot.longrun.watchdog.local --heartbeat run/heartbeat.json \
    --budget-cap-inr 8122 --max-hours 18 --dry-run

# layers 2 and 3 (need WATTNOT_WD2_CMD / WATTNOT_WD3_CMD set to an agent CLI)
python -m wattnot.longrun.watchdog.agent --layer 2 --heartbeat run/heartbeat.json \
    --budget-cap-inr 8122 --max-hours 18 --dry-run

# layer 4 (off-box engine, dry-run; source is a URL or a local file)
python -m wattnot.longrun.watchdog.offbox \
    --heartbeat-source https://gist.../raw/heartbeat.json --budget-cap-inr 8122 --max-hours 18

# layer 5 (macOS, dry-run by default; --live needs a keychain key)
python3 scripts/mac_watchdog/watchdog.py \
    --heartbeat-source https://gist.../raw/heartbeat.json --budget-cap-inr 8122 --max-hours 18 --once
```

## The two off-box layers (4 and 5)

Layers 1-3 are defence in depth against *process* faults, but they are **one failure domain**:
all three die if the control box loses power, loses network, is terminated, or has a wrong clock.
Layers 4 and 5 fix that by running the same check from somewhere the box cannot take down with it.
They poll the same heartbeat the driver writes, now **published to a URL** (a gist raw URL, or a
file on a dedicated branch served by `raw.githubusercontent`) instead of a local file, and call
the same `decide` + idempotent teardown. Both are **built, dry-run-tested, and disabled**; enabling
either is a deliberate human step.

### Layer 4 — GitHub Actions cron (`watchdog/offbox.py`, `.github/workflows/e2e-watchdog.yml`)

A scheduled workflow on GitHub's runners fetches the published heartbeat and runs
`python -m wattnot.longrun.watchdog.offbox`, which reuses the shared `decide`/`check_and_teardown`
(no logic is duplicated — it writes the fetched beat to a temp file and calls the same routine the
on-box layers call). It is **safe by default** in three independent ways:

* the **scheduled** run is a no-op unless the repo variable `WATCHDOG_ENABLED == 'true'` (unset by
  default), so a fresh clone never acts on the cron;
* every run defaults to **dry-run**; a real delete needs the dispatch input `mode=live` **and** the
  repo variable `WATCHDOG_LIVE == 'true'`;
* the credentials (`E2E_API_KEY` / `E2E_AUTH_TOKEN`) live only in GitHub **secrets**, never in the
  repo, and the VM id is a repo variable — point it only at **our** VM.

This is genuinely off-box: GitHub, not the control box, is the failure domain.

### Layer 5 — macOS watchdog (`scripts/mac_watchdog/`)

A stdlib-only Python script (`watchdog.py`) you run on a Mac — a second, independent machine. It
polls the same heartbeat source and obeys the same rule (the rule is mirrored in the script so it
can run on a bare `python3`; a test asserts it does not drift from the shared `decide`). The API
key is read from the **macOS keychain** (`security find-generic-password`) or an env var, **never**
from the repo; a live run without a key is a hard refusal, not a silent no-op. `--dry-run` is the
default and never calls the API. It ships with a **launchd** plist template, a **tmux** alternative,
and `caffeinate -i` to keep the Mac awake, plus an `INSTALL.md` with install/uninstall steps.
Nothing is installed by this repo.

## The honest limits — read before trusting any of this

* **Layers 1-3 are one failure domain.** Do not read "three watchdogs" as "three independent
  domains" — the box dying takes all three. That is exactly why 4 and 5 exist.
* **Layer 4 is only as available as GitHub, and as timely as its cron.** A `*/15` schedule can run
  late (GitHub does not guarantee cron punctuality under load), and a GitHub outage disables it.
  It also cannot act on the schedule at all until a human sets `WATCHDOG_ENABLED`/`WATCHDOG_LIVE`.
* **Layer 5 is only as alive as the Mac.** A Mac that is off, asleep past `caffeinate`, or offline
  cannot help; `caffeinate -i` stops *idle* sleep, not a lid-closed battery sleep.
* **The live E2E auth scheme is unverified here.** The off-box HTTP clients mirror the control
  host's delete path but the exact header/query auth must be confirmed against the control host's
  `post.call` before either is armed. They are never exercised in tests or dry-run.
* **Every layer trusts the published heartbeat.** If that URL is wrong or never updated, the
  stale-heartbeat rule will (correctly) fire; verify the dry-run output before going live.
* **None of this is provider-side.** The cleanest backstop would be a provider TTL (see below);
  until E2E confirms one, these are the strongest we have.

## Does E2E help here? (researched; unknown where not documented)

From the E2E material in this repo (`long-run-plan.md`, `hackathon/e2e-glimmer-test/ctl.py`) and
the hackathon notes:

* **Billing** is by the minute in practice (RUN2 billed 87.9 min). The launch *gate* requires the
  prepaid balance to cover the whole lifetime at list price (24 h → Rs 9,097).
* **Delete API**: `DELETE gpu/notebooks/{id}/` exists and is used by `ctl.py` — this is what the
  watchdogs call.
* **Auto-expiry / scheduled delete / a TTL on a VM**: **not documented** in anything in this repo.
  The only lifetime bound we implement is our own watchdog. Treat provider-side auto-expiry as
  **unknown** until confirmed against the E2E console or API docs.
* **Does stopping (vs deleting) a VM cost less?** **Unknown / not documented.** `ctl.py` only ever
  *deletes*; there is no evidence here that E2E has a cheaper "stopped" state, and GPU providers
  commonly still bill reserved capacity (and always bill attached disk) while a VM is merely
  stopped. Until a bill proves otherwise, assume **delete, not stop**, is what frees the money.

If any of these turn out to be supported, a provider-side TTL would be the cleanest box-independent
backstop and should be added.

## What is built vs still proposed

The two off-box layers above (4 = GitHub cron, 5 = macOS watchdog) are **built here as dry-run
code and left disabled**. One more box-independent fix is **still proposed, not yet built**:

1. **On-VM shutdown timer.** On the GPU VM itself, at setup, arm a hard stop:
   `sudo shutdown -h +N` (N = max minutes), or a transient systemd timer
   (`systemd-run --on-active=Nmin /sbin/poweroff`). This bounds the lifetime even if the control
   box vanishes, because the VM turns *itself* off. **Caveat:** a powered-off VM is not a deleted
   VM — per the "stop vs delete" unknown above, **E2E may keep billing a stopped VM** (and will
   keep billing its disk). So this is a safety net that caps compute, not a guarantee the bill
   stops; it must be paired with a real delete (which any of layers 1-5 performs).

An earlier, simpler curl-only template of the GitHub cron remains at
`.github/workflows/longrun-watchdog.example.yml` (named `*.example.yml` with no firing trigger, so
Actions ignores it). The canonical layer 4 is `e2e-watchdog.yml` + `watchdog/offbox.py`, which
reuses the shared rule instead of duplicating a curl; prefer it. Neither fires as committed.

## Testing

`tests/test_watchdog.py` covers the on-box layers (1-3): the pure rule on each cap; heartbeat
read/write and the missing/corrupt cases; **each layer independently tearing down on budget, time
and stale heartbeat**; double-delete idempotency and three layers racing on one VM; the flaky-API
retry; and that **dry-run never calls the client**.

`tests/test_watchdog_offbox.py` covers the off-box layers (4-5): fetching the heartbeat from a
**fake opener (URL) and a temp file**, tearing down on each cap through the shared fake client,
the **default being dry-run**, missing/corrupt sources never deleting, two off-box runners racing
to delete once, the layer-4 CLI, and — for layer 5 — that its embedded rule **matches the shared
`decide`** (a drift guard), the **keychain lookup mocked** (parse success, not-found, and no
`security` binary), env-over-keychain credential precedence, and a live run **refusing without a
key**. All of it runs on CPU with fakes — no network, no GPU, no VM, no real keychain.
