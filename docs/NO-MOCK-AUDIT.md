# No-mock audit of the live 2 h / long-run path

Date: 2026-10-30. Owner's hard rule: nothing in the 2 h test or the long run may be mocked,
faked, SIMULATED, placeholder or stubbed. Every live number must be MEASURED on the real H100 /
Muse Glimmer 30B, or come from a real Meta API call. Dry-run (rehearsal) modes may stay, but they
must be clearly separated (an explicit flag, outputs labelled MOCK/SIMULATED) and impossible to
reach from the live path.

This is the audit behind `wattnot.longrun.nomock`, the preflight that runs at the start of every
`--live` driver run and at `scripts/e2e/ctl.py launch`. It lists where a mock could leak into a
live run, what the guard now checks, and the honest state of each input today.

## How the guard separates dry from live

- The driver carries two command lines per block: `dry` (the `--fake` rehearsal, labelled
  MOCK/SIMULATED) and `live` (the real command, never `--fake`). `--dry-run` runs the first,
  `--live` the second; the two are mutually exclusive and one is required.
- The guard only inspects the **live** command of each block, plus the asset files the live
  commands read. A rehearsal is allowed to be mock; the live path is not.

## Findings and fixes

Legend: FIXED = the live path no longer uses the mock; GUARDED = still possible to create a mock
asset, but the preflight now refuses to launch on it; BY-DESIGN = intentionally mock, never on the
live measured path.

| # | Area | file:line | What could leak | Fix / guard |
|---|------|-----------|-----------------|-------------|
| 1 | Driver 2 h plan | `src/wattnot/longrun/driver.py` (old `plan_glimmer_test`) | Every block called `--fake`; there was no live entry point at all. | FIXED: each block now has a real `live` argv (no `--fake`), and `--live` runs them. A test asserts no live argv contains `--fake`/`--dry-run`. |
| 2 | Orchestrate | `proof/orchestrate.py:192,239` (`mock = mode=="rehearsal"`, adds `--fake`) | A live sweep started in `rehearsal` would be mock. | FIXED: the live sweep block passes `--mode live --host … --sign-key …`; guard bans `--fake` in live argv. Plain `--auto-approve` is allowed (logged as a flag), `--auto-approve-as-proof` is banned. |
| 3 | Proof run | `proof/run.py:209,238` (`FakeNvml`, `label="MOCK"`) | `--fake` swaps in a fake GPU + oracle model. | FIXED: live harness/orchestrate never pass `--fake`; guard bans it. |
| 4 | SAM room scan | `proof/orchestrate.py:328,336` (`--sam replay`), `proof/roomscan.py:27` (`live_sam=False`) | `--sam replay` replays saved detections instead of a live SAM call. | GUARDED: guard bans `--sam replay` / bare `replay` in live argv. The 2 h plan does not run the room scan. |
| 5 | Eye-test images | `data/vision/eyetest/answers.json`, `scripts/_make_hotspot_placeholders.py`, `scripts/gen_vision_assets.py:10` | The committed PNGs are offline PLACEHOLDERS drawn by PIL, not real images. | GUARDED: manifest now carries an honest `source`. Committed = `placeholder-offline` → guard FAILS. `gen_vision_assets.py` stamps `source=GENERATED-META` after the real (paid, his-okay) generation → guard passes. |
| 6 | Hotspot set | `data/vision/hotspots/scenes.json` | Same: offline placeholder PNGs. | GUARDED: `source=placeholder-offline` added; not on the 2 h live path (detector self-test only). |
| 7 | Spoken WAVs | `data/voice/spoken/answers.json`, 20 x 364 B `*.wav` | The WAVs were 364 B stubs; transcripts were SYNTHETIC. | GUARDED + FIXED tooling: manifest `source=stub-offline`; guard FAILS on `source` and on any WAV < 10 KB. `scripts/gen_spoken_assets.py` now synthesizes REAL audio from the question text with an offline TTS and stamps `source=TTS-<engine>`; the transcript of each take is the question text. |
| 8 | Certificates | `src/wattnot/certificate/*` (`SERIAL_PREFIX="WNC-MOCK-"`, watermark MOCK) | Every certificate is a MOCK by design. | BY-DESIGN: the live 2 h / long-run proof uses signed SHA-256 **receipts** (`orchestrate receipt`, `--sign-key`), not the MOCK certificate. The guard bans any live argv that mints a mock certificate. |
| 9 | Wonder chart | `src/wattnot/wonderchart/run.py:23,67` (`_DEMO`, `--fake`) | `--fake`/no input uses a SYNTHETIC demo sweep. | GUARDED: off the 2 h live path; live use passes `--live` with a real sweep file. Guard bans `--fake`. |
| 10 | Voice guide / SAM hotspot cap | `meta/voiceguide.py:93`, `sam/hotspot_cap.py:63` (`WATTNOT_META_MOCK=1` forces mock even when live) | An env var could force mock during a live call. | NOTE: `WATTNOT_META_MOCK` must be unset for a live run (documented here and in the runbook). Not used by the 2 h plan's blocks. |
| 11 | Agentwaste / sandbox | `agentwaste/server.py:74`, `sandbox/run.py:128` | Replays a recorded trace. | BY-DESIGN demo; off-box and not on the 2 h live measured path. |

## Honest state today (2026-10-30)

Running the preflight now on the committed tree **fails**, on purpose:

- eye-test images are `placeholder-offline` — the real images need the paid `gen_vision_assets.py`
  and his okay;
- the 20 spoken WAVs are 364 B stubs — `gen_spoken_assets.py` is ready to synthesize real audio,
  but no offline TTS (espeak-ng / espeak / pico2wave / flite) is installed on this box and system
  packages cannot be installed here without sudo.

This is the correct behaviour: the live 2 h run cannot start until those two inputs are real. Once
the images are generated (his okay) and a TTS is on PATH, both stamp an honest `source` and the
preflight passes.

## Second pass (2026-10-30, evening)

| # | Area | What could leak | Fix |
|---|------|-----------------|-----|
| 12 | `t2-fp8` | The block ran the harness with the FP8 config but never restarted the server, so it would have measured BF16 and labelled it FP8 (`serve_args` in `harness.py` is not used to start a server). | FIXED: the live command restarts Glimmer with `glimmer_serve.sh fp8` first. A test asserts it. |
| 13 | `t1-sweep` | `orchestrate --sam` defaults to `replay`, so a live sweep replayed saved SAM masks without saying so. The guard only caught an explicit `--sam replay`. | FIXED: the live sweep passes `--sam live`; the guard now flags a live orchestrate run that does not ask for live SAM. |
| 14 | Guard | A mock switch inside a `bash -c "..."` string was not seen. | FIXED: the guard splits shell strings before checking. |
