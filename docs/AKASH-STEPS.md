# AKASH-STEPS — arming the off-box watchdogs, by hand

Everything in this repo is **safe by default**: a fresh clone launches nothing, enables nothing,
deletes nothing, and makes no network call. This page is the exact, deliberate checklist to turn
the off-box backstops on (and off again) when — and only when — you decide to.

Read `watchdogs.md` first for the five-layer picture. The short version: the long run rents a GPU
VM by the minute (~Rs 451/h). Five layers all read one published heartbeat and obey one rule
(budget cap / time cap / stale heartbeat). Layers 4 (GitHub cron) and 5 (a Mac) run **off** the
control box so they survive the box dying. This page arms those two, plus the publisher that feeds
them, plus the read-only API check.

> **Ground rules for this work (do not break):** launch nothing paid, no GPU, enable nothing live,
> do not post anywhere, never delete any real VM or cloud resource, at most one local server at a
> time (and shut it down), do not change the water factor (0.36 L/kWh on-site cooling headline, up
> to 1.8 L/kWh including power-plant water, WUE ESTIMATE). Nothing below launches or
> deletes anything on its own — every live step is an explicit, reversible human action.

---

## 0. What talks to what

```
control box (driver)                         off the box
┌────────────────────────┐                   ┌──────────────────────────┐
│ driver writes           │  publisher pushes │ GitHub cron (layer 4)    │
│ heartbeat.json   ───────┼── git force-push ─┤ Mac watchdog (layer 5)   │
│ + publisher (dry-run)   │  to a branch/gist │ both READ the raw URL    │
└────────────────────────┘   raw.githubusercontent └──────────────────────────┘
```

- The **publisher** (`wattnot.longrun.watchdog.publisher`) runs on the control box and pushes the
  heartbeat to a dedicated branch (`watchdog-heartbeat`, one file, force-updated) or a gist.
- Layers 4 and 5 **read** that branch's raw URL **by default**; you only set a URL variable to
  override (e.g. point at a gist).

---

## 1. GitHub secrets and variables (exact names)

Set these under **Settings → Secrets and variables → Actions** on the repo.

### Secrets (never in the repo; only present if you add them)

| Secret | When | Value |
|--------|------|-------|
| `E2E_API_KEY` | only to arm a **live** teardown | your E2E API key |
| `E2E_AUTH_TOKEN` | only if your account uses a bearer token | the token |

### Variables

| Variable | Default (leave as-is unless arming) | Value when arming |
|----------|--------------------------------------|-------------------|
| `WATCHDOG_ENABLED` | **unset** (schedule is inert) | `true` to let the cron run (still dry-run) |
| `WATCHDOG_LIVE` | **unset / `false`** — KEEP FALSE until you truly mean it | `true` only to permit a real delete |
| `WATCHDOG_HEARTBEAT_URL` | **unset** → defaults to the published branch's raw URL | set only to override (e.g. a gist raw URL) |
| `WATCHDOG_HEARTBEAT_BRANCH` | **unset** → `watchdog-heartbeat` | the branch name if you changed it |
| `WATCHDOG_BUDGET_CAP_INR` | unset | e.g. `8122` |
| `WATCHDOG_MAX_HOURS` | unset | e.g. `18` |
| `E2E_VM_ID` | unset | **our** VM id — never anything else |
| `E2E_PROJECT_ID` | unset | e.g. `18993` |
| `E2E_LOCATION` | unset | e.g. `Delhi` |

**Which stay false / unset:** `WATCHDOG_ENABLED` and `WATCHDOG_LIVE` are the two switches. For a
dry-run-only cron, set `WATCHDOG_ENABLED=true` and leave `WATCHDOG_LIVE` **false**. Leave both
unset to keep the schedule completely inert (a fresh clone is already in this state).

### Publisher env (on the CONTROL BOX, not GitHub)

The driver's publisher reads these from its own environment. Default is dry-run; publishing is opt-in.

| Env var | Meaning |
|---------|---------|
| `WATCHDOG_PUBLISH_TOKEN` | a repo/gist-scoped token for the git push (never in the repo) |
| `WATCHDOG_PUBLISH_REPO` | `owner/name` for branch mode |
| `WATCHDOG_PUBLISH_BRANCH` | branch to force-update (default `watchdog-heartbeat`) |
| `WATCHDOG_PUBLISH_MODE` | `branch` (default) or `gist` |
| `WATCHDOG_PUBLISH_GIST_ID` | the gist id for gist mode |

---

## 2. Arm / disarm

### Dry-run cron (safe; recommended first step)
1. Set variable `WATCHDOG_ENABLED = true`. Leave `WATCHDOG_LIVE` false.
2. (Optional) set `WATCHDOG_BUDGET_CAP_INR` / `WATCHDOG_MAX_HOURS`.
3. **Actions → e2e-watchdog → Run workflow**, mode `dry-run`. Confirm the JSON shows
   `"dry_run": true` and either `"teardown": false` or `WOULD_DELETE`. No API call is made.

### Arm a real delete (deliberate; reversible)
1. Do the dry-run above and confirm the decision looks right.
2. Add secrets `E2E_API_KEY` (+ `E2E_AUTH_TOKEN` if used).
3. Set variables `E2E_VM_ID` (**our** VM), `E2E_PROJECT_ID`, `E2E_LOCATION`.
4. Set `WATCHDOG_LIVE = true`.
5. To actually fire live, **dispatch with mode `live`** — live needs BOTH the dispatch input
   `live` AND `WATCHDOG_LIVE=true`. The scheduled cron still runs dry-run.

### Disarm (fully)
- Set `WATCHDOG_LIVE = false` (or delete it) → back to dry-run.
- Unset `WATCHDOG_ENABLED` → the schedule stops acting.
- Remove `E2E_API_KEY` / `E2E_VM_ID` → live is impossible even if a switch is flipped.
- On the control box, unset `WATCHDOG_PUBLISH_TOKEN` → publishing stops.

---

## 3. Mac watchdog (layer 5) install

Full steps in `../scripts/mac_watchdog/INSTALL.md`. The essentials:

1. Copy the folder: `scp -r scripts/mac_watchdog you@mac:~/wattnot-watchdog`.
2. **Dry-run first** (no key needed). The source now **defaults** to the published branch — pass
   `--repo-slug owner/name` (or set `WATCHDOG_PUBLISH_REPO`) and it builds the raw URL for you:
   ```sh
   python3 watchdog.py --repo-slug <owner>/<name> --budget-cap-inr 8122 --max-hours 18 --once
   ```
   Or pass an explicit `--heartbeat-source <url-or-path>`. Expect a JSON line with
   `"teardown": false` or `WOULD_DELETE`, and **no** API call.
3. **Live only:** store the key in the keychain (never the repo):
   ```sh
   security add-generic-password -s wattnot-e2e -a e2e-api-key -w
   ```
   then set `E2E_VM_ID` / `E2E_PROJECT_ID` and run with `--live`.
4. Keep it running under **launchd** (`com.wattnot.watchdog.plist.template`, uses `caffeinate -i`)
   or **tmux** (`run-tmux.sh`). Uninstall steps are in INSTALL.md.

---

## 4. The read-only E2E check (auth + VM lookup)

`scripts/e2e_api_check.py` confirms the API key authenticates and a VM can be looked up, using
**GET only** — it has no create/modify/delete code path (the HTTP verb is hard-whitelisted to
`GET`). **Do not run it live as part of this task**; it is here so you can verify auth safely later.

```sh
# dry-run (DEFAULT): prints the exact GETs it WOULD make, apikey redacted, no network call
python scripts/e2e_api_check.py --vm-id <id>

# live read-only check (only when you choose to; needs the key in env, never the repo)
E2E_API_KEY=... E2E_PROJECT_ID=18993 E2E_LOCATION=Delhi \
  python scripts/e2e_api_check.py --live --vm-id <id>
```

It reports the balance (auth proof), the node list, and whether `<id>` is **ours**.

### The DELETE call shape — and how to confirm a delete safely

The control host deletes a VM with (this script **never** issues it):

```
DELETE  https://api.e2enetworks.com/myaccount/api/v1/gpu/notebooks/{id}/?apikey=...&project_id=...&location=...
```

i.e. the same path as the per-id GET, with the verb `DELETE`. To confirm a delete safely without
issuing one:

1. Run the read-only check (`--live --vm-id <id>`) and confirm the `id`/`status` — make sure it is
   **our** VM and nothing else.
2. A delete is **confirmed done** when a follow-up `GET gpu/notebooks/{id}/` returns **404** *and*
   the id no longer appears in `GET gpu/notebooks/` (the list). That is exactly the verify loop in
   `hackathon/e2e-glimmer-test/ctl.py::delete()`; this script performs only the GET half, so you
   can check a VM's state before and after a delete made elsewhere.

The real delete stays in `ctl.py` / an armed watchdog, behind their own go-ahead gates. **Never**
delete a VM you did not launch.

---

## 5. Tests

Run the related tests on CPU (no network, no GPU, no VM):

```sh
python -m pytest -q tests/test_watchdog_publisher.py tests/test_watchdog_offbox.py \
    tests/test_launch_guard.py tests/test_e2e_api_check.py tests/test_longrun_driver.py
```

All use fakes/temp dirs/local bare git repos. The publisher tests skip automatically if `git` is
absent.

---

## 6. Double-launch safety (context)

The launcher is guarded against creating two paid VMs at once
(`wattnot.proof.launch_guard`, mirrored inline in `ctl.py`):

1. an **O_EXCL lock** so two launchers can't both POST,
2. an **idempotency check** (list VMs; adopt an existing one with our name instead of creating a
   second), and
3. **intent persisted before the POST** so a crash mid-create leaves a breadcrumb to find and tear
   down the orphan.

A stale lock is **never** auto-stolen — clear it by hand after confirming no VM is running.
