import pytest
from wattnot.proof import orchestrate


def test_live_grid_below_floor_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        orchestrate.main(["--mode", "live", "--grid", "375,250", "--out", str(tmp_path)])


def test_live_wonder_lo_below_floor_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        orchestrate.main(["--mode", "live", "--wonder-lo", "250", "--out", str(tmp_path)])
