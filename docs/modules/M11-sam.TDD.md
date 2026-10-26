# M11 TDD: SAM 3.1 floor map with manual fallback
Branch: `feat/m11-sam` · Hours: 5 · Depends on: M10 rack view

## Interfaces
`rack/masks.json`: `{"source":"sam3.1"|"manual","image":"rack.jpg","objects":[{"id":"rack-1","kind":"rack","polygon":[[x,y],…],"gpu_indices":[0]}],"iou_vs_manual":0.86,"status":"ok"|"cut"}`

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`wattnot/sam/{segment,compare}.py`, `rack/rack.jpg`, `rack/masks.manual.json`, `rack/masks.sam.json`, `tests/test_sam.py`

## Test plan (tests first; write, watch fail, then pass)
IoU function on known polygons; schema check of masks files; recorded SAM response fixture (no network in tests).

## Acceptance criteria
Decision logged Nov 2 by 12:00 PM IST; rack view shows either SAM masks or the labelled fallback.
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
M10 rack view

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
