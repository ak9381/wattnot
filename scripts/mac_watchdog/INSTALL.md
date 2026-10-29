# macOS off-box watchdog (layer 5) — install & uninstall

A tiny, stdlib-only Python watchdog you run on a Mac so a *second, independent* machine can tear
down the long-run GPU VM if the Linux control box dies. It polls the same heartbeat the run
publishes and obeys the same rule as every other layer (budget cap / time cap / stale heartbeat).
See `../../docs/watchdogs.md` for the full 5-layer picture and the honest limits.

> **Nothing here installs or runs anything automatically.** These are copy-paste steps a human
> performs deliberately. The default is **dry-run**; a real delete needs the explicit `--live`
> flag *and* an API key you store in the keychain yourself.

## What you need

- A Mac with the system `python3` (no pip packages required).
- The URL (or local path) where the run publishes `heartbeat.json` (a gist raw URL, or a file on
  a dedicated branch served by `raw.githubusercontent.com`).
- For a **live** teardown only: the E2E API key, the VM id, and the project id/location.

## 1. Copy the folder to the Mac

```sh
scp -r scripts/mac_watchdog you@mac:~/wattnot-watchdog
# or just copy watchdog.py — it is standalone.
```

## 2. Try it in dry-run first (always do this)

```sh
cd ~/wattnot-watchdog
python3 watchdog.py \
  --heartbeat-source "https://gist.githubusercontent.com/<you>/<id>/raw/heartbeat.json" \
  --budget-cap-inr 8122 --max-hours 18 --once --dry-run
```

You should see a JSON line with `"teardown": false` (healthy) or `"WOULD_DELETE (dry-run...)"`
(a cap tripped) — and **no** API call either way. Drop `--once` to poll in a loop.

## 3. (Live only) store the API key in the keychain

The watchdog reads the key from the login keychain, never from the repo or a plist:

```sh
security add-generic-password -s wattnot-e2e -a e2e-api-key -w   # prompts for the key, hidden
```

It is read back with `security find-generic-password -s wattnot-e2e -a e2e-api-key -w`. An auth
token, if your account uses one, goes in the env as `E2E_AUTH_TOKEN`. A live run also needs
`E2E_VM_ID` (and `E2E_PROJECT_ID`); set them as env vars or `--vm-id`.

## 4a. Run it under launchd (background agent)

```sh
sed \
  -e "s|@@WATCHDOG_DIR@@|$HOME/wattnot-watchdog|g" \
  -e "s|@@HEARTBEAT_URL@@|https://gist.../raw/heartbeat.json|g" \
  -e "s|@@BUDGET_CAP_INR@@|8122|g" \
  -e "s|@@MAX_HOURS@@|18|g" \
  -e "s|@@E2E_VM_ID@@|UNSET|g" \
  -e "s|@@E2E_PROJECT_ID@@|18993|g" \
  com.wattnot.watchdog.plist.template > ~/Library/LaunchAgents/com.wattnot.watchdog.plist

launchctl load ~/Library/LaunchAgents/com.wattnot.watchdog.plist
launchctl list | grep com.wattnot.watchdog        # confirm it is loaded
tail -f ~/wattnot-watchdog/watchdog.out.log        # watch its JSON decisions
```

The template runs `caffeinate -i` so the Mac does not idle-sleep the loop away, `--dry-run` by
default, and `KeepAlive` restarts it if it exits. To arm a real delete, edit the plist and change
`--dry-run` to `--live` (after step 3), then `launchctl unload` + `load` again.

## 4b. Or run it under tmux (foreground, visible)

```sh
HEARTBEAT_URL="https://gist.../raw/heartbeat.json" BUDGET_CAP_INR=8122 MAX_HOURS=18 \
  ./run-tmux.sh
tmux attach -t wattnot-watchdog
# live: HEARTBEAT_URL=... WATCHDOG_LIVE=1 E2E_VM_ID=... ./run-tmux.sh
```

## Uninstall

launchd:

```sh
launchctl unload ~/Library/LaunchAgents/com.wattnot.watchdog.plist
rm ~/Library/LaunchAgents/com.wattnot.watchdog.plist
rm -f ~/wattnot-watchdog/watchdog.out.log ~/wattnot-watchdog/watchdog.err.log
```

tmux:

```sh
tmux kill-session -t wattnot-watchdog
```

Remove the keychain entry (if you added one):

```sh
security delete-generic-password -s wattnot-e2e -a e2e-api-key
```

Then delete the folder: `rm -rf ~/wattnot-watchdog`.

## Honest limits

- A Mac that is **off, asleep past `caffeinate`, or offline** cannot help. `caffeinate -i` stops
  *idle* sleep; a closed lid on battery may still sleep depending on settings — prefer a Mac on
  power, or configure `pmset`. This is a cheap independent backstop, not a guarantee.
- It can only delete what the credentials allow. Point `E2E_VM_ID` at **our** VM and nothing else.
- Like every layer it keys off the published heartbeat; if that URL is wrong or never updated,
  the stale-heartbeat rule will (correctly) fire. Verify the dry-run output before going live.
