# Rules (all of them)

## From Akash (owner); these override everything
1. **No posting, DMs, reactions or emails** anywhere (Discord, email, WhatsApp, X, socials) without
   Akash's explicit okay for that exact message. Discord is read-only as `akashkraju218`.
   Drafts are fine; sending is not.
2. **Any paid spend needs Akash's okay first** (GPU, API, VM). The default is no spend.
   - Only `BASETEN_METRICS_KEY` may be used for Baseten. `BASETEN_API_KEY` is dead (401 since 29 Oct)
     and the code refuses it.
   - E2E H100 launch rule: keep balance above the ₹1,500 floor; a 24 h launch needs ≥ ₹9,097
     (₹451.2/h). Last known balance ₹8,801.29 (MEASURED, 2 Nov), so a full 24 h launch is NOT
     possible without a top-up; the 16 h plan fits.
3. **Never use the NYU `work1` Google account** (connectors suffixed `--work1`).
4. **schema-ingest is confidential.** Never write any of its details anywhere.
5. Writing style: human, simple yet technical. No AI-sounding filler.
6. Score ideas with the four-judge council (Claude, Gemini, Grok, Meta AI); see `LLM-COUNCIL-PLAYBOOK.md`.
   (For the current build phase Akash said: do the coding yourself, no Claude Code, no agy/Gemini.)
7. **Commit and push after every milestone.**
8. Max 2 demo servers at once; one heavy job at a time.
9. Access or connector problems go to Connector Bot.

## Engineering rules in force (2 Nov)
- **No AI names in commits** (no "Claude", "Gemini", "GPT", co-author trailers). Commit as `ak9381`.
- **Label every number** MEASURED (read from GPU, billing, logs), ESTIMATE (computed), MOCK (fake
  backend), or REPLAY (recomputed on CPU from saved data). Water is always ESTIMATE.
- **Never invent numbers or reviews.** "not recorded" beats a guess.
- **Never print secrets** (no `env`, `cat` of key files, or echo of values; list names only).
- Merges into main: tag main first (`pre-<thing>-<date>`), run the full suite, `git merge --no-ff`, push.
- **Same failure twice: stop** and report, do not loop.
- Do not publish to Rekor (transparency log); signing is local only.
- Data branches (`data/*`) and tags are never deleted. Delete nothing in replays/archives.
- Python: `~/.local/bin/python`; tests: `/tmp/wn-venv/bin/python -m pytest` (recreate the venv if /tmp was wiped, see 02-setup).
- Design skills in `/workspace/hackathon/design-skills/`: use the markdown guidance only, don't run their scripts.
- The UI branch `ui/redesign-1103` is **not** to be merged without Akash's okay.

## Hackathon rules (participant guide, `/workspace/hackathon/EVENT-PAGE-AND-SUBMIT-FORM.md`)
- Repo must be public. Team max 4 (we are solo).
- The demo must show only what was built during the hackathon, and make that clear.
  (Before 26 Oct there were planning docs only.)
- Must use Meta models (Muse Spark series, Muse Wonder, Muse Voice Transcribe, SAM, Llama open weights).
  **Never use a VPN to get around region limits.** Muse Image now works in India (confirmed Nov 4); it only made the pre-generated SYNTHETIC test images, so don't claim a live Muse Image call unless a run really makes one.
- Judging: stage 1 pass/fail fit; stage 2 rubric 0-10; stage 3 top 5 per track judged on the video.
