# M12 PRD: EU report export (Delegated Reg. 2024/1364 fields)
Depends on: M07 · Estimate: 3 h · Contracts: `schemas/SCHEMAS.md`

## Why
Export the fields software can actually fill from receipts and telemetry, clearly separating MEASURED from ESTIMATE and leaving the rest blank with a reason.

## Requirements
- FR-EU-01 CSV + PDF/HTML export of: IT energy for the measured window, energy per request, savings, idle power, water (ESTIMATE, wet-bulb method cited), SCI per request, heat/water readiness score.
- FR-EU-02 Each field has `value`, `unit`, `label` (MEASURED/ESTIMATE/NOT_AVAILABLE) and `source`.
- FR-EU-03 Fields needing facility data (PUE, renewable share, waste heat reuse) are NOT_AVAILABLE with an explanation.
- FR-EU-04 No 'carbon neutral' or offset wording anywhere.

## Out of scope
CSRD pack (later paid add-on), filing to any authority.

## Done means
One export from the real receipts, linked from the receipt view.

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
