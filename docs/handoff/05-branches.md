# Branches, data branches and tags (`ak9381/wattnot`)

## main
- `main` is the only integration branch. Every change lands by `git merge --no-ff` after a full
  green suite, with a `pre-<thing>-<date>` tag on main first.
- Merge night 2-3 Nov: tag `pre-merge-20261102` (old main 0da21ff) -> all fix/docs branches merged ->
  `pre-demo-replays-1103` -> feat/demo-replays (3208092) -> `pre-submission-readme-1103` ->
  docs/submission-readme (fa5e2eb) -> fix/ui-bounds-settle (04add68) -> docs/h100-plan (525763b, docs only). Fix and docs merges are 523ccfb. See `git log --first-parent main`.

## Data branches (never delete, never merge into main)
Raw run outputs, receipts, logs and signatures. Replays and `docs/RUN_HISTORY.md` point into them.
`data/a100-smoke-1101`, `data/baseten-1102-full`, `data/baseten-1102-langchain`,
`data/baseten-1102-own-e2e`, `data/baseten-1102-own-rerun`, `data/baseten-stage-1101`,
`data/modal-full-1101`, `data/modal-r05-attribution`, `data/modal-r05-eye-hearing`,
`data/modal-r05-langchain-fix`, `data/modal-stage-1101`; H100 retest data is in `receipts/rerun-1031/` on main.
Read a file without checkout: `git show origin/data/modal-full-1101:<path>`.

## Not merged on purpose (leave them)
`archive/main-local-1029` (old local main snapshot), `feat/baseten-true-e2e-1101` (older variant; `feat/baseten-true-e2e-1101b` is the one merged),
`feat/moonshot-ui`, `feat/old27-port`, `integrate/oct27`, `muse/batch-2`, `test/gap-runs-1031`,
`watchdog-heartbeat` (old WIP/experiments), `gh-pages` (site).

## Cleanup done 3 Nov
All 46 `fix/*` branches fully merged into main were deleted (remote + local) with their worktrees.
List: `/tmp/deleted-fix-branches.txt` on the box (commits are all reachable from main).
Merged `feat/*` and `docs/*` branches were left in place.

## Tags
`pre-*` tags mark main before each risky merge; never delete them. `git tag -l 'pre-*'`.
