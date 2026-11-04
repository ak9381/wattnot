# Install and setup

Everything below already exists on the shared Grok Bot box. Re-run only what is missing.
The repo's own runbooks are the detailed source: `docs/runbooks/{MODAL,BASETEN,H100,META-USAGE,LONG-RUN-PLAN,SPARK1GPU}.md`.

## 1. Repo
```bash
cd /workspace && [ -d wattnot-repo ] || gh repo clone ak9381/wattnot wattnot-repo
cd /workspace/wattnot-repo && git fetch --all --prune && git switch main && git pull --ff-only
```
For parallel work use worktrees: `git worktree add /tmp/wn-<task> -b <branch> origin/main`.

## 2. Python
- Tools: `~/.local/bin/python` (3.11+). Package: `pip install -e '.[vision,frameworks]'` or `scripts/setup.sh` (makes `.venv`).
- Test venv: `/tmp/wn-venv` (Python 3.13, has pytest + Playwright). If `/tmp` was wiped:
  ```bash
  python3 -m venv /tmp/wn-venv && /tmp/wn-venv/bin/pip install -e '/workspace/wattnot-repo[vision,frameworks]' pytest playwright
  ```
  Playwright uses system Chrome at `/usr/bin/google-chrome` (`channel="chrome"` or `executable_path`).
- Full suite: `cd <worktree> && /tmp/wn-venv/bin/python -m pytest -q -x` (~12 min). Node UI tests: `node tests/js/index.js` (see `docs/TESTING.md`).
- Run anything from a checkout with `PYTHONPATH=src`.

## 3. GitHub as ak9381
`gh auth status` must show `ak9381` active (token in `~/.config/gh/hosts.yml`). Commits use
`ak9381 <ak9381@users.noreply.github.com>`. If logged out: `gh auth login` (needs Akash; see the sign-in skill).

## 4. Modal (account `akashkraju218`)
- CLI `~/.local/bin/modal` (v1.6.3), token in `~/.modal.toml`. Check: `modal profile current`, `modal secret list` (expect `wattnot-meta`).
- Proxy env for web endpoints: `set -a; . ~/.config/wattnot/modal.env; set +a`.
- Entry points: `modal/full_stage.py` (A100 full round), `scripts/e2e/modal_round.py`, `modal_block_run.py`, `modal_stage.py`. Paid runs need `WATTNOT_MODAL_GO=1` **and** Akash's okay. Always `--dry-run` first. Details: `docs/runbooks/MODAL.md`, `docs/MODAL-FULL-STAGE.md`.

## 5. Baseten
- Key `BASETEN_METRICS_KEY` is already in the env (injected). `scripts/e2e/baseten_round.py --dry-run` first; it drops `BASETEN_API_KEY`.
- Runbook `docs/runbooks/BASETEN.md`. Usage endpoints return 403 with this key (inference-only); billing is read from the dashboard deltas.

## 6. E2E Networks (H100 VM)
- Env `E2E_API_KEY`/`E2E_API_TOKEN` (injected). Controller `scripts/e2e/ctl.py` (launch/status/delete/balance watchdog); per-run folders `/workspace/hackathon/e2e-run{1,2,3}/` hold `vm.json` (run2 and run3 also have a `ctl.py` copy).
- SSH key `~/.ssh/e2e_h100`. VM setup `scripts/h100_vm_setup.sh`, server `scripts/glimmer_serve.sh`, driver `python -m wattnot.longrun.driver --live ...`.
- Launch rule: ₹451.2/h, keep ₹1,500 floor. Deleting the VM ends billing; ctl.py's watchdog deletes at the deadline. Runbook `docs/runbooks/H100.md`, `LONG-RUN-PLAN.md`.

## 7. Meta Model API
- Key in `~/.config/wattnot/env` (`META_API_KEY`); `muse` CLI at `~/.local/bin/muse` (login in `~/.config/muse/auth.json`).
- Mock everything with `WATTNOT_META_MOCK=1` (tests and replays always mock). Spend guard ledger `~/.wattnot/meta_spend.json`. Usage notes: `docs/runbooks/META-USAGE.md`.
- Models used: Muse Spark 1.3 (planner/judge), Muse Wonder (cap picker/charts), Muse Voice Transcribe (voice guide, hearing gate), SAM 3.1 (rack/hotspot vision), the 8B comparison model / Glimmer open weights (served model). Muse Image only for the pre-generated SYNTHETIC test images (no live call; it works in India as of Nov 4).

## 8. The dashboard
```bash
PYTHONPATH=src WATTNOT_META_MOCK=1 python -m wattnot.dash --replay-only --events replays/modal-r2/events.jsonl --port 8795
# open http://127.0.0.1:8795/index.html ; pages are web/dash/*.html
```
`--replay-only` never touches a GPU and forces the voice guide to MOCK.

## 9. Vault
`/workspace/obsidian-vault` is a git repo (remote via `~/.ssh/obsidian_vault`, host alias `github-vault`). Commit and push after edits.
