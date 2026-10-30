"""Vision quality gate (the "eye test").

A small, fixed set of SYNTHETIC test images with known answers lives in the
repo (``data/vision/eyetest``). After a candidate serving change (power cap,
FP8, KV, batch) the served vision model answers them; any miss rejects the
change, so a quantisation that quietly breaks vision can't pass the text-only
gates. The gate returns the same ``GateResult`` shape as the other gates and
plugs into ``wattnot.gates.decide`` and the keep/reject loop.
"""

from wattnot.vision.assets import (
    DEFAULT_ASSETS_DIR,
    EST_WH_PER_IMAGE,
    EyeTestItem,
    load_eye_test,
)
from wattnot.vision.gate import grade_answers, vision_gate

__all__ = [
    "DEFAULT_ASSETS_DIR",
    "EST_WH_PER_IMAGE",
    "EyeTestItem",
    "grade_answers",
    "load_eye_test",
    "vision_gate",
]
