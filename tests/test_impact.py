import pytest

from wattnot.cli import main
from wattnot.impact import estimate


def test_math():
    i = estimate(0.01, 100_000, 8)  # 0.01 Wh x 100k req x 8 GPUs x 30 days
    assert i.kwh == pytest.approx(240.0)
    assert i.inr == pytest.approx(1920.0) and i.litres == pytest.approx(432.0)
    j = estimate(0.01, 100_000, 8, pue=1.5, tariff_inr_per_kwh=10, wue_l_per_kwh=0, days=1)
    assert j.kwh == pytest.approx(12.0) and j.inr == pytest.approx(120) and j.litres == 0


def test_bad_inputs():
    with pytest.raises(ValueError):
        estimate(0.01, 1, 0)
    with pytest.raises(ValueError):
        estimate(0.01, 1, 1, pue=0.9)


def test_cli_every_line_is_estimate(capsys):
    assert main(["impact", "--wh-delta", "0.01", "--requests-per-gpu-per-day", "100000",
                 "--gpus", "8"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 4 and all(x.startswith("ESTIMATE") for x in lines)
    assert "240.0 kWh" in lines[0] and "INR 1,920" in lines[1] and "432 L" in lines[2]


def test_cli_negative_delta_says_added(capsys):
    main(["impact", "--wh-delta", "-0.002", "--requests-per-gpu-per-day", "1000"])
    assert "energy added: 0.1 kWh" in capsys.readouterr().out


def test_cli_requires_measured_delta(capsys):
    with pytest.raises(SystemExit):
        main(["impact", "--requests-per-gpu-per-day", "10"])
    with pytest.raises(SystemExit):
        main(["impact", "--wh-delta", "1", "--requests-per-gpu-per-day", "10", "--gpus", "0"])
