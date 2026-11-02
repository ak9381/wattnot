# WattNot handoff: start here

Written 2 Nov 2026, late evening IST, for a bot (or person) with **zero context** who has to finish
the Meta Global AI Developer Hackathon entry. All times IST (UTC+5:30).

## The goal in one paragraph
**WattNot** is an agent that cuts the energy (and estimated water) each LLM request uses without
making the model worse. Muse Spark plans one serving change at a time (power cap, batching, KV
cache, FP8, prefix cache, context saver), a gate checks quality, p99 latency and noise, and every
kept change becomes a hash-chained, signed receipt that `wattnot verify` re-checks. Receipts also
fill an EU efficiency report (EU Delegated Reg. 2024/1364).
Owner: Akash K Raju (GitHub `ak9381`, Bengaluru). Indie track, solo.
Repo (public): https://github.com/ak9381/wattnot

## Deadline
**Submissions due Thu 5 Nov 2026, 7:30 AM IST** (Wed 4 Nov, 9:00 PM ET). Meta API credit claim
also closes then. Judging 5 to 8 Nov; closing ceremony Mon 9 Nov 12:00 PM ET.
Required: public repo, ~3-minute demo video showing only work built during the hackathon,
the submission form (draft answers in `/workspace/hackathon/submission/ANSWERS.md`, NOT submitted).

## Read in this order
1. [01-rules.md](01-rules.md): every rule. Read before doing anything.
2. [02-setup.md](02-setup.md): tools, Python env, gh, Modal, Baseten, E2E, Meta API.
3. [03-secrets.md](03-secrets.md): key names and where they live (never values).
4. [04-code-map.md](04-code-map.md): repo layout.
5. [05-branches.md](05-branches.md): branches, data branches, tags, what was merged.
6. [06-runs-and-results.md](06-runs-and-results.md): run history with MEASURED numbers.
7. [07-replays-and-eu.md](07-replays-and-eu.md): demo replays and EU reports.
8. [08-h100-plan.md](08-h100-plan.md): the 16 h H100 plan (needs Akash's go).
9. [10-open-todos.md](10-open-todos.md): what is left, in priority order.
10. [11-pitch-and-video.md](11-pitch-and-video.md): pitch and video plan.
11. [12-build-story.md](12-build-story.md): how we got here.

## Key places
| What | Where |
|---|---|
| Working notes, logs, raw run folders | `/workspace/hackathon/` on the box |
| Original context file | `/workspace/hackathon/HACKATHON-CONTEXT.md` |
| Obsidian vault project | `/workspace/obsidian-vault/Agents/Projects/Meta Hackathon/` |
| This handoff | vault `.../Meta Hackathon/Handoff/` and repo `docs/handoff/` |
| Main clone | `/workspace/wattnot-repo` (others under `/workspace/wn-*`, `/tmp/wn-*` are worktrees) |

## Note on the UI redesign
A UI redesign was started on 2 Nov and **cancelled by Akash at 11:34 PM IST**. The local branch
`ui/redesign-1103` and its worktree were deleted (never pushed). Do not restart it. Leftover
screenshots in `/workspace/hackathon/ui-redesign-shots/` are scratch only.
