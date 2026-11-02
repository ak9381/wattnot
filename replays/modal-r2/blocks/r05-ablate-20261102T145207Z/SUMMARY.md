# r05 attribution run (Modal A100, same 100 items)

| Arm | Setup | Correct | J per correct | Wall |
|---|---|---|---|---|
| A | stock (no WattNot routing), 32 parallel, 128 slots, no prefix cache | 94/100 | 1,054.4 | - |
| B | WattNot, 32 parallel, 128 slots, no prefix cache | 98/100 | 459.0 | 135 s |
| C | WattNot, 32 parallel, 128 slots, prefix cache on | 97/100 | 672.3 | 200 s |

All numbers MEASURED (NVML power on the GPU, graded answers).

Reference r05 main arms (MEASURED): without WattNot (4 parallel, 64 slots) 2,954.6 J/correct; with WattNot (32/128, cache on) 678.4 J/correct. Arm C matches the r05 with-arm (672.3 vs 678.4).

## What each piece is worth
- Server setup (4 -> 32 parallel, 64 -> 128 slots), no WattNot: 2,954.6 -> 1,054.4, -64%. Note: A is a separate run from the r05 without-arm, so this mixes in run-to-run variance.
- WattNot routing (thinking budget + effort routing) at equal server setup: 1,054.4 -> 459.0, -56%. Accuracy also went 94 -> 98.
- Prefix cache on top: 459.0 -> 672.3, +46% (worse). Wall time rose 135 s -> 200 s. One run each, so this may be partly noise (cold cache, scheduling), but there is no sign the cache helps on this set.

Log-share of the full 77% cut (ESTIMATE, the factors multiply): server setup ~70%, WattNot routing ~57%, prefix cache ~-26%.

Takeaway: WattNot's own routing halves energy per correct answer even when batching is already tuned. The prefix cache should not be claimed as a saver.
