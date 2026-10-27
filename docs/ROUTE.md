# Router: effort, backend and region per request class

For each class of request (extract, classify, arith, ...) the router picks the candidate that
answers correctly for the fewest joules. It runs on recorded data only, so replaying it
costs nothing and makes no API call.

> Region is a **SIMULATED** what-if. Hosted APIs (Baseten, Meta's Model API) do not let us
> choose where a request runs, so we ask "if this backend were in region X, what would the
> carbon be?". Energy is an ESTIMATE range from a FLOP model. Grid numbers in the bundled
> fixture are rounded, illustrative and not live.

## The order of the decision

1. **Gate first.** A candidate that failed the quality gate or the p99 limit is out, however
   cheap. Carbon never rescues a failed gate.
2. **Fewest joules per good answer** (`Wh per request x 3600 / pass rate`). A cheap answer
   that fails more often costs more per good answer. Ranked by the middle of the ESTIMATE range.
3. **Tie-break.** Candidates within 3% of the best on joules are a tie. The cleaner grid wins
   the tie, then the lower USD, then the id (so the answer never flips between runs).

It never routes on carbon alone.

## Try it

```bash
wattnot cloud demo --out cloud-demo                       # recorded SIMULATED cloud run
wattnot route replay --receipt cloud-demo/cloud_*.json    # router vs always xhigh
wattnot route decide --receipt cloud-demo/cloud_*.json --class arith
wattnot route grid                                        # grid readings in use
```

Replay compares the same recorded requests three ways: always `xhigh` in the home region
(the baseline), the router choosing among the efforts that were tried (effort only), and the
router with the region what-if. Example (demo fixture, all SIMULATED):

```text
always xhigh               USD 0.3096   62270 J [ESTIMATE]   CO2 6.57 g
router, effort only        USD 0.1367   28440 J [ESTIMATE]   CO2 3.00 g
router + region what-if    USD 0.1367   28440 J [ESTIMATE]   CO2 0.28 g
```

Read it this way: choosing the effort is what saves dollars and joules. Region only moves the
carbon column, and that column is a what-if on rounded average intensities.

## Candidates

Built from the cloud receipt: every effort the tuner actually tried per class (skipped efforts
have no data, so they are not candidates), plus the `xhigh` reference. A failed effort stays in
the list, marked excluded, so the report shows what was refused and why. The router also takes
any list of `Candidate` objects, so an H100 box with a MEASURED Wh per request can be added as
another candidate once that run exists. No such candidate is bundled; we don't invent one.

## Grid sources

| Source | Default | Network | Label |
|---|---|---|---|
| `fixture` (`data/route/grid-fixture.json`) | yes | never | SIMULATED |
| `uk-api` (api.carbonintensity.org.uk, region `gb` only) | no, `--grid uk-api` | yes | ESTIMATE |

No key and no sign-up. If the UK call fails, that region falls back to the fixture and the
reading says so. Readings are cached for 30 minutes. Add your own source by returning
`GridReading` from a `reading(region)` method. Intensity is an average, not a marginal figure.

## Not modelled

Latency and data-residency differences between regions; marginal emissions; network and cache
effects of moving a request. The decision records are not signed receipts. The replay report
pins its source receipt by sha256.

JSON shapes for the frontend are in [moonshot/API-SHAPES.md](moonshot/API-SHAPES.md).
