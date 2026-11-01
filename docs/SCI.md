# One receipt for energy, carbon and water (SCI format)

Every receipt the builder or the long-run proof sweep writes now carries a signed `sci` block.
It puts energy, carbon and water for one correct answer in the format of the Green Software
Foundation's Software Carbon Intensity spec:

    SCI = (E x I + M) per R

Spec: GSF SCI 1.0, published as ISO/IEC 21031:2024, https://sci.greensoftware.foundation/

| Term | What we put there | Label |
|---|---|---|
| R | one correct answer (an answer that passed the quality gate) | - |
| E | kWh per request from the GPU's NVML energy counter, divided by the graded pass rate for kWh per correct answer | MEASURED on a real GPU run (the receipt's own label otherwise) |
| I | grid gCO2e/kWh for the region the GPU ran in, with source and year | ESTIMATE |
| M | embodied emissions | `NOT_AVAILABLE` (status, not a label) unless a cited figure is given, then ESTIMATE |
| water | kWh x WUE: 0.36 L/kWh on-site cooling (LBNL 2024), upper bound 1.8 L/kWh with power-plant water | ESTIMATE |

## Grid factor (I)

Default region is India, because the E2E Networks H100 VM is in Delhi:
727 gCO2e/kWh, CEA CO2 Baseline Database for the Indian Power Sector v20.0 (Dec 2024),
weighted average for FY 2023-24 (0.727 tCO2/MWh),
https://cea.nic.in/cdm-co2-baseline-database/?lang=en. It counts CO2 only (no CH4, N2O),
so the true CO2e figure is a little higher.

Change it per run:

    WATTNOT_GRID_GCO2E_PER_KWH=400 WATTNOT_GRID_SOURCE="<publisher, dataset, year>" \
    WATTNOT_GRID_YEAR=2024 WATTNOT_GRID_REGION=XX wattnot ...

A number without a source is refused.

## How it is checked

The block is inside the SHA-256 and the signature. `wattnot verify` (and the standalone
`verifier/wattnot_verify.py`) re-derive every number from the receipt's own energy and pass
rate plus the stored I and WUE, and fail the `sci` step on any mismatch (relative 1e-8). A
mismatch grades the receipt UNVERIFIABLE even when the hash and signature are good. The CLI
prints the SCI line, the EU export adds `grid_intensity`, `sci_per_request`,
`energy_per_correct`, `sci_per_correct`, `water_per_correct` and `sci_embodied_m`, and the
dashboard EU card shows a Carbon (SCI) box when the block is present.

## What it does not say

It is an intensity per correct answer, operational only while M is NOT_AVAILABLE. It is not a
total footprint and makes no claim about balancing emissions; the EU export's wording check
(BANNED list) still rejects such words.
