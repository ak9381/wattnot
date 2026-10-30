"""Wonder reads the chart.

After each cap/clock sweep, WattNot plots joules-per-correct-answer (x) against
quality (y) for every trial — kept trials green, rejected trials red — to a PNG,
then sends that *image* to Muse Wonder and asks which setting to try next. Wonder
only suggests; the gates still decide, and a deterministic fallback picks the next
setting offline (no network, no paid call) so the sweep never stalls. The chart's
hash, the suggestion and the plotted points are logged on the signed receipt.

* ``chart`` renders the scatter PNG and hashes it (Pillow; import is guarded so
  the rest of the module and the tests work without it).
* ``reader`` sends the chart to Muse Wonder (live) or falls back to the tested
  deterministic ``WonderSupervisor`` logic (offline), and shapes the receipt block.
* ``run`` is the ``python -m wattnot.wonderchart.run`` entry point used by the loop.
"""

from __future__ import annotations

from wattnot.wonderchart.chart import (
    EST_WH_CHART,
    ChartPoint,
    build_points,
    chart_png_bytes,
    chart_sha256,
    points_sha256,
    render_chart,
)
from wattnot.wonderchart.reader import (
    DEFAULT_MODEL,
    ChartSuggestion,
    WonderChartReader,
    fallback_suggestion,
    read_chart,
    wonder_chart_entry,
)

__all__ = [
    "DEFAULT_MODEL",
    "EST_WH_CHART",
    "ChartPoint",
    "ChartSuggestion",
    "WonderChartReader",
    "build_points",
    "chart_png_bytes",
    "chart_sha256",
    "fallback_suggestion",
    "points_sha256",
    "read_chart",
    "render_chart",
    "wonder_chart_entry",
]
