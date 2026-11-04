# Secrets: names and locations only

**Never write, print, paste or commit a value.** Check a key exists with
`[ -n "$NAME" ] && echo set || echo missing`, or list names with `cut -d= -f1`.

| Name | Service | Where it is stored | How to load |
|---|---|---|---|
| `BASETEN_METRICS_KEY` | Baseten model APIs (the **only** Baseten key allowed; workspace key, paid) | Grok Bot injected box secret (listed in `$CLOUD_AGENT_INJECTED_SECRET_NAMES`); already in every box shell's env | nothing to do; `scripts/e2e/baseten_round.py` and `wattnot.cloud.transport.read_key` read it |
| `BASETEN_API_KEY` | Baseten hackathon key | injected box secret | **dead (401), never use**; code pops/refuses it |
| `E2E_API_KEY`, `E2E_API_TOKEN` | E2E Networks (H100 VM control API) | injected box secrets | env; read by `scripts/e2e/ctl.py` (copies in `/workspace/hackathon/e2e-run2/` and `e2e-run3/`) |
| `E2E_BACKUP_CODES` | E2E Networks account 2FA backup codes | injected box secret | only for an E2E web sign-in, ask Akash first |
| `HF_TOKEN` | Hugging Face (gated 8B comparison weights download) | injected box secret | env; pass to the VM/container, never echo |
| `PINGGY_TOKEN` | Pinggy tunnel (exposing a demo server) | injected box secret | env |
| `TYS_DEMO_PASSWORD` | demo-site password (not WattNot core) | injected box secret | env |
| `META_API_KEY` | Meta Model API (Muse Spark/Wonder/Voice Transcribe) | file `~/.config/wattnot/env` (line `META_API_KEY=...`); the Muse CLI also keeps its login in `~/.config/muse/auth.json` (`providers` section); Modal secret **`wattnot-meta`** holds it for containers | `set -a; . ~/.config/wattnot/env; set +a`. Mock instead with `WATTNOT_META_MOCK=1` |
| `MODAL_PROXY_TOKEN_ID`, `MODAL_PROXY_TOKEN_SECRET`, `MODAL_ENDPOINT_URL` | Modal web endpoint proxy auth | file `~/.config/wattnot/modal.env` (+ `~/.config/wattnot/modal_proxy.json`) | `set -a; . ~/.config/wattnot/modal.env; set +a` |
| Modal CLI token (`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`) | Modal account `akashkraju218` | `~/.modal.toml`, profile `akashkraju218` | the `modal` CLI reads it automatically |
| Modal secret `wattnot-meta` | Meta key inside Modal containers | Modal dashboard > Secrets (created 2 Nov) | referenced as `META_SECRET` in `modal/full_stage.py`; check with `modal secret list` |
| GitHub token for `ak9381` | GitHub push/PRs | `~/.config/gh/hosts.yml` (gh CLI) | `gh auth status`; git uses gh as credential helper over https |
| `~/.ssh/e2e_h100` | SSH key to the H100 VM | `~/.ssh/e2e_h100` (+ `.pub`), host aliases in `~/.ssh/config` | `ssh -i ~/.ssh/e2e_h100 ...`; default `--key` in `wattnot.proof.orchestrate` |
| `~/.ssh/obsidian_vault` | push access to the Obsidian vault git repo | `~/.ssh/obsidian_vault` | used by the vault repo's remote |
| Receipt/certificate signing key | signs WattNot certificates (local key) | `~/.wattnot/cert-signing-key.pem` (`test-key.pem` for tests) | path override env `WATTNOT_CERT_KEY` (`wattnot.receipt.certkey`); never publish to Rekor |
| `WATTNOT_API_TOKEN`, `WATTNOT_DASH_TOKEN` | optional bearer tokens for the WattNot API / dash | not set by default; set in env if hosting | env |
| Spend ledgers (not secrets) | Meta / Baseten spend guards | `~/.wattnot/meta_spend.json`, `~/.wattnot/baseten_h2h_spend.json` | read by the spend guards |
