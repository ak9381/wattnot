"""r04 follow-ups: the Modal image carries optuna for the planner, and the gauge eye item stays
an exact read (the clearer drawing fixes the misread; the check is not loosened)."""
from pathlib import Path

from wattnot.vision.gate import _matches

ROOT = Path(__file__).resolve().parents[1]


def test_modal_full_stage_image_installs_optuna():
    src = (ROOT / "modal" / "full_stage.py").read_text()
    line = next(l for l in src.splitlines() if ".pip_install(" in l and "pynvml" in l)
    assert "optuna" in line


def test_gauge_read_is_exact():
    assert _matches("number", "60", "60 PSI")
    assert not _matches("number", "60", "63")
    assert not _matches("number", "60", "62")
