"""Grid carbon intensity sources for the router. Pluggable, offline by default.

A source answers one question: "how many grams of CO2 per kWh is the grid in this region?"
The number is an average intensity, not a marginal one, and the router only uses it to break
ties between candidates that are equal on joules. It never decides alone.

* `StaticGrid` reads `data/route/grid-fixture.json`. Rounded illustrative values, labelled
  SIMULATED. This is the default and it never touches the network.
* `UkApiGrid` reads the UK national figure from the public Carbon Intensity API
  (api.carbonintensity.org.uk, no key, no sign-up) for region `gb` only. It is OFF unless you
  ask for it (`--grid uk-api`). If the call fails it falls back to the fixture and says so.
  Its readings are labelled ESTIMATE (the figure is itself a modelled national estimate).

Write your own by returning `GridReading` from `reading(region)`.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "route" / "grid-fixture.json"
UK_URL = "https://api.carbonintensity.org.uk/intensity"


class GridError(ValueError):
    pass


@dataclass(frozen=True)
class GridReading:
    region: str
    gco2_per_kwh: float
    label: str  # SIMULATED or ESTIMATE
    source: str
    as_of: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class StaticGrid:
    def __init__(self, path: str | Path | None = None) -> None:
        p = Path(path) if path else DEFAULT_FIXTURE
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            self.regions = {k: float(v["gco2_per_kwh"]) for k, v in doc["regions"].items()}
            self.label = str(doc.get("label", "SIMULATED"))
            self.source = str(doc.get("source", "fixture"))
            self.as_of = str(doc.get("as_of", "static fixture"))
            self.caveat = str(doc.get("caveat", ""))
        except (OSError, KeyError, TypeError, ValueError) as e:
            raise GridError(f"cannot read grid fixture {p}: {e}") from None
        if self.label not in ("SIMULATED", "ESTIMATE"):
            raise GridError("grid fixture label must be SIMULATED or ESTIMATE")
        if any(v < 0 for v in self.regions.values()):
            raise GridError("grid intensity cannot be negative")

    def region_names(self) -> list[str]:
        return sorted(self.regions)

    def reading(self, region: str | None) -> GridReading | None:
        if region is None or region not in self.regions:
            return None
        return GridReading(region, self.regions[region], self.label, self.source, self.as_of,
                           self.caveat)


class UkApiGrid:
    """The UK national figure from the public API, for region `gb`. Everything else: fixture."""

    def __init__(self, fallback: StaticGrid | None = None, *, ttl_s: float = 1800.0,
                 opener: Callable[[str, float], bytes] | None = None,
                 now: Callable[[], float] = time.time, timeout_s: float = 5.0) -> None:
        self.fallback = fallback or StaticGrid()
        self.ttl_s, self._now, self.timeout_s = ttl_s, now, timeout_s
        self._open = opener or self._urlopen
        self._cache: tuple[float, GridReading] | None = None

    @staticmethod
    def _urlopen(url: str, timeout: float) -> bytes:  # pragma: no cover - network, off by default
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URL
            return r.read()

    def region_names(self) -> list[str]:
        return self.fallback.region_names()

    def reading(self, region: str | None) -> GridReading | None:
        if region != "gb":
            return self.fallback.reading(region)
        t = self._now()
        if self._cache and t - self._cache[0] < self.ttl_s:
            return self._cache[1]
        try:
            doc = json.loads(self._open(UK_URL, self.timeout_s))
            row = doc["data"][0]
            v = row["intensity"]
            val = v.get("actual") if v.get("actual") is not None else v["forecast"]
            r = GridReading("gb", float(val), "ESTIMATE", "api.carbonintensity.org.uk",
                            str(row.get("from", "")), "average national intensity, not marginal")
        except Exception as e:  # noqa: BLE001 - any failure falls back to the fixture
            fb = self.fallback.reading("gb")
            if fb is None:
                return None
            return GridReading(fb.region, fb.gco2_per_kwh, fb.label, fb.source, fb.as_of,
                               f"UK API unavailable ({type(e).__name__}); used the fixture")
        self._cache = (t, r)
        return r


def make_grid(kind: str = "fixture", *, fixture: str | Path | None = None):
    if kind == "fixture":
        return StaticGrid(fixture)
    if kind == "uk-api":
        return UkApiGrid(StaticGrid(fixture))
    raise GridError(f"unknown grid source {kind!r}; use fixture or uk-api")
