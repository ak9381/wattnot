# Muse Image eye set runs (Nov 3, 2026)

Set: 25 SYNTHETIC images, "Muse Image generated" (5 gauges, 7 racks, 6 charts, 7 documents). Both gauge misses are kept in the set.

Gate rule: no worse than stock (WattNot correct >= stock correct on the same items), the same rule as the sealed holdout. Re-scored on Nov 3 from the saved results with `scripts/rescore_eye_runs.py`. Nothing was rerun. `strict_all_correct` keeps the old all-correct result (false on both runs).

| Run | Stock | WattNot | Wh per image, stock to WattNot | Saved | Verdict |
|---|---|---|---|---|---|
| H100, 700 W vs 350 W cap | 23/25 | 23/25 | 0.1339 to 0.1041 | 22.2% | KEEP |
| Modal A100, stock vs tuned vLLM | 23/25 | 23/25 | 0.1826 to 0.1795 | 1.7% | KEEP |

All accuracy and energy figures are MEASURED (NVML energy counter, including idle draw). Modal cost: $0.22 (ESTIMATE).

Both runs missed the same two images: gauge-temp-68 (read as 88) and gauge-hum-45 (read as 95). Both are seven-segment displays with faint unlit segments.

The signed Modal `attachment.json` is left unchanged, so its signature still verifies. It still shows "failed" from the old all-correct rule. This file and `eye/eye_test.json` hold the re-score.
