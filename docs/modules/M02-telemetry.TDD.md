# M02 TDD: Telemetry meter (NVML/DCGM) and 10 Hz traces
Branch: `feat/m02-telemetry` · Hours: 5 · Depends on: M01

## Interfaces
```python
class Meter(Protocol):
    def start(self, label: str) -> None
    def stop(self) -> Window      # Window(wh: float, seconds: float, mean_w: float, csv_path: str, sha256: str, source: str)
    def idle(self, seconds: int = 60) -> float  # mean watts
def attribute(window: Window, tokens_per_req: list[int], cap: float = 0.5) -> list[float]  # Wh per request
```
CSV columns: `t_s,gpu,power_w,energy_mj,sm_mhz,mem_mhz,temp_c,util_pct`

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`wattnot/meter/{base,nvml,dcgm,zeus}.py`, `wattnot/meter/attribution.py`, `tests/test_meter.py`, `tests/data/trace_h100_sample.csv`

## Test plan (tests first; write, watch fail, then pass)
Fake NVML with a known power curve: Wh from counter vs trapezoid within 1%. Sample rate >= 9.5 Hz over 30 s on the fake clock. Attribution sums to window Wh and respects the cap. CSV hash recomputes.

## Acceptance criteria
10 Hz CSV produced for every rep; `energy_mj` used when available; Wh matches a hand calculation on the sample trace.
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
M01

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
