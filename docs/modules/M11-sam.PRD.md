# M11 PRD: SAM 3.1 floor map with manual fallback
Depends on: M10 rack view · Estimate: 5 h · Contracts: `schemas/SCHEMAS.md`

## Why
Use SAM 3.1 to segment racks/PDUs on one rack photo and map GPU power onto it. Hard cut at Nov 2 12:00 PM IST if masks are not clean.

## Requirements
- FR-SAM-01 One public or self-taken rack photo (no confidential sites). Prompted segmentation of racks/PDUs via the Meta Model API (SAM 3.1).
- FR-SAM-02 Save masks as `rack/masks.json` (polygons) plus overlay PNG; label which GPU maps to which slot by hand.
- FR-SAM-03 Manual fallback built first: hand-drawn polygons in the same `masks.json` format with `source: manual`.
- FR-SAM-04 Cut rule: at Nov 2 12:00 PM IST, if SAM masks fail the checklist (each rack a single clean mask, IoU ≥ 0.8 vs manual), ship manual with the visible label and keep the SAM attempt in the repo.
- FR-SAM-05 At most 5 SAM calls total; counted in the Meta spend log.

## Out of scope
Live camera, multiple photos, thermal imaging.

## Done means
Decision logged Nov 2 by 12:00 PM IST; rack view shows either SAM masks or the labelled fallback.

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
